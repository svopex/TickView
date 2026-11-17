"""Vyhodnocování alertů na pozadí serveru (nezávisle na otevřené stránce).

AlertEngine drží aktivní alerty z databáze, pro každou trojici (zdroj,
symbol, timeframe) odebírá jeden živý stream zdroje a každou svíčku předá
trackerům alertů (tickview.logic.alerts.AlertTracker). Spuštěný alert
zapíše do databáze a logu, pošle notifikace (Telegram, webhook) a ohlásí
se otevřeným stránkám (toast, zvuk).
"""

import asyncio
import json
import logging
from typing import Callable, Optional

import httpx

from tickview.logic.alerts import (
    AlertTracker,
    is_expired,
    line_price,
    message_context,
    render_message,
)
from tickview.logic.cache import KlineCache
from tickview.providers import get_provider
from tickview.providers.base import TIMEFRAME_MS, Candle, StreamStatus, utc_now_ms
from tickview.services.listeners import Listeners
from tickview.services.notify import send_telegram, send_webhook, telegram_settings

logger = logging.getLogger(__name__)

# Pauza před znovupřipojením streamu po chybě (s): začíná na první hodnotě, zdvojnásobuje se
RECONNECT_DELAY_S = 5.0
MAX_RECONNECT_DELAY_S = 300.0
# Jak často se kontroluje vypršení alertů, i když stream nic neposílá (s)
EXPIRY_CHECK_S = 30.0

# Klíč streamu: (zdroj, symbol, timeframe)
StreamKey = tuple[str, str, str]


class AlertEngine(Listeners):
    """Vyhodnocuje aktivní alerty nad živými streamy zdrojů.

    Parametry:
        cache: úložiště alertů, logu a kreseb.
        provider_lookup: funkce id zdroje -> zdroj (testy dosadí vlastní).
        client: HTTP klient pro notifikace (výchozí vlastní httpx.AsyncClient).
    """

    def __init__(
        self,
        cache: KlineCache,
        provider_lookup: Callable = get_provider,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        super().__init__()
        self._cache = cache
        self._provider_lookup = provider_lookup
        self._client = client
        self._alerts: dict[int, dict] = {}
        # Stav vyhodnocení alertů: id -> (otisk definice, tracker); změněný
        # alert (jiný otisk) dostane nový tracker
        self._trackers: dict[int, tuple[str, AlertTracker]] = {}
        self._watchers: dict[StreamKey, asyncio.Task] = {}
        # Kresby alertů: id kresby -> kresba / None (smazaná); změnu kresby
        # hlásí UI přes drawing_changed
        self._drawings: dict[int, Optional[dict]] = {}
        # Silné reference na úlohy odesílání notifikací (jinak je GC může zrušit)
        self._tasks: set[asyncio.Task] = set()
        self._expiry_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # ---------- životní cyklus ----------

    async def start(self) -> None:
        """Načte aktivní alerty, spustí jejich streamy a kontrolu vypršení."""
        if self._client is None:
            self._client = httpx.AsyncClient()
        await self.reload()
        self._expiry_task = asyncio.create_task(self._expiry_loop())

    async def stop(self) -> None:
        """Ukončí streamy, kontrolu vypršení a HTTP klienta (při ukončení aplikace)."""
        tasks = list(self._watchers.values()) + list(self._tasks)
        if self._expiry_task:
            tasks.append(self._expiry_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._watchers.clear()
        if self._client is not None:
            await self._client.aclose()

    async def reload(self) -> None:
        """Znovu načte aktivní alerty z databáze (po vytvoření, úpravě, smazání).

        Nezměněné alerty si ponechají stav vyhodnocení (předchozí cenu),
        streamy se spustí jen pro nové trojice zdroj/symbol/timeframe a
        nepotřebné se ukončí.
        """
        async with self._lock:
            alerts = await self._cache.alerts(active_only=True)
            self._alerts = {alert["id"]: alert for alert in alerts}
            for alert_id, alert in self._alerts.items():
                fingerprint = json.dumps(
                    [alert["source"], alert["symbol"], alert["timeframe"], alert["condition"], alert["trigger"], alert["target"]],
                    sort_keys=True,
                )
                if alert_id not in self._trackers or self._trackers[alert_id][0] != fingerprint:
                    tracker = AlertTracker(alert["condition"], alert["trigger"], TIMEFRAME_MS[alert["timeframe"]])
                    self._trackers[alert_id] = (fingerprint, tracker)
            # Stav smazaných / neaktivních alertů zahodíme
            for alert_id in set(self._trackers) - set(self._alerts):
                del self._trackers[alert_id]
            self._drawings.clear()
            self._sync_watchers()
        self._emit({"type": "alerts_changed"})

    def _sync_watchers(self) -> None:
        # Jeden stream na trojici (zdroj, symbol, timeframe) aktivních alertů
        needed = {self._key(alert) for alert in self._alerts.values()}
        for key in set(self._watchers) - needed:
            self._watchers.pop(key).cancel()
        for key in needed - set(self._watchers):
            self._watchers[key] = asyncio.create_task(self._watch(key))

    @staticmethod
    def _key(alert: dict) -> StreamKey:
        return (alert["source"], alert["symbol"], alert["timeframe"])

    def drawing_changed(self, drawing_id: int) -> None:
        """Kresba se posunula nebo smazala (volá UI) – alert si ji příště načte znovu."""
        self._drawings.pop(drawing_id, None)

    # ---------- streamy a vyhodnocení ----------

    async def _watch(self, key: StreamKey) -> None:
        """Odebírá živý stream trojice a předává svíčky alertům; po chybě se připojí znovu."""
        provider_id, symbol, timeframe = key
        delay = RECONNECT_DELAY_S
        while True:
            try:
                provider = self._provider_lookup(provider_id)
                if not provider.supports_stream:
                    await self._fail_key(key, f"Zdroj {provider.name} nepodporuje živá data.")
                    return
                async for item in provider.stream(symbol, timeframe):
                    if isinstance(item, StreamStatus):
                        # Výpadek / obnovení spojení řeší zdroj sám – alerty jen čekají
                        continue
                    delay = RECONNECT_DELAY_S
                    await self._on_candle(key, item)
                    # Poslední alert trojice se vypnul (spuštění, vypršení) – stream končí
                    if self._watchers.get(key) is not asyncio.current_task():
                        return
            except asyncio.CancelledError:
                raise
            except KeyError:
                # Zdroj už neexistuje (např. Alpaca bez klíčů po restartu)
                await self._fail_key(key, f"Neznámý zdroj {provider_id}.")
                return
            except Exception:
                logger.exception("Stream alertů %s selhal", key)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RECONNECT_DELAY_S)

    async def _on_candle(self, key: StreamKey, candle: Candle) -> None:
        """Vyhodnotí svíčku pro všechny aktivní alerty dané trojice."""
        now_ms = utc_now_ms()
        for alert in [a for a in self._alerts.values() if self._key(a) == key]:
            entry = self._trackers.get(alert["id"])
            if entry is None:
                continue
            tracker = entry[1]
            if is_expired(alert, now_ms):
                await self._deactivate(alert, "expired")
                continue
            level_at = await self._level_function(alert)
            if level_at is None:
                await self._deactivate(alert, "error")
                continue
            price = tracker.update(candle, now_ms, level_at)
            if price is not None:
                await self._fire(alert, price, now_ms)

    async def _level_function(self, alert: dict) -> Optional[Callable[[float], Optional[float]]]:
        """Funkce čas (s) -> úroveň alertu; None, když kresba alertu už neexistuje."""
        target = alert["target"]
        if target["type"] == "value":
            price = float(target["price"])
            return lambda _time_s: price
        drawing = await self._drawing(target["id"])
        if drawing is None:
            return None
        kind, points = drawing["kind"], drawing["points"]
        return lambda time_s: line_price(kind, points, time_s)

    async def _drawing(self, drawing_id: int) -> Optional[dict]:
        # Kresba z databáze jen poprvé a po změně (drawing_changed)
        if drawing_id not in self._drawings:
            self._drawings[drawing_id] = await self._cache.get_drawing(drawing_id)
        return self._drawings[drawing_id]

    async def _expiry_loop(self) -> None:
        # Vypršení se kontroluje i u alertů, jejichž stream nic neposílá (zavřený trh)
        while True:
            await asyncio.sleep(EXPIRY_CHECK_S)
            now_ms = utc_now_ms()
            for alert in list(self._alerts.values()):
                if is_expired(alert, now_ms):
                    await self._deactivate(alert, "expired")

    async def _fail_key(self, key: StreamKey, reason: str) -> None:
        # Alerty trojice bez použitelného streamu označíme chybou
        logger.warning("Alerty %s nelze vyhodnocovat: %s", key, reason)
        for alert in [a for a in self._alerts.values() if self._key(a) == key]:
            await self._deactivate(alert, "error")

    async def _deactivate(self, alert: dict, status: str, fired_at: Optional[int] = None) -> None:
        """Vypne alert se stavem (expired / error / triggered) a ohlásí změnu stránkám.

        Parametry:
            alert: alert k vypnutí.
            status: nový stav alertu.
            fired_at: čas spuštění (jednorázový alert se vypíná spuštěním).
        """
        self._alerts.pop(alert["id"], None)
        self._trackers.pop(alert["id"], None)
        await self._cache.set_alert_state(alert["id"], False, status, fired_at=fired_at)
        # Stream bez dalších alertů ukončíme (nevoláme z vlastní úlohy streamu)
        key = self._key(alert)
        if not any(self._key(a) == key for a in self._alerts.values()):
            task = self._watchers.pop(key, None)
            if task and task is not asyncio.current_task():
                task.cancel()
        self._emit({"type": "alerts_changed"})

    async def _fire(self, alert: dict, price: float, now_ms: int) -> None:
        """Spuštění alertu: stav v databázi, ohlášení stránkám a odeslání notifikací."""
        try:
            exchange = self._provider_lookup(alert["source"]).name
        except KeyError:
            exchange = alert["source"]
        message = render_message(alert["message"], message_context(alert, price, exchange, now_ms))
        once = alert["trigger"] == "once"
        if once:
            # Jednorázový alert se po spuštění vypne
            await self._deactivate(alert, "triggered", fired_at=now_ms)
        else:
            await self._cache.set_alert_state(alert["id"], True, "active", fired_at=now_ms)
        event = {
            "type": "fired",
            "alert_id": alert["id"],
            "source": alert["source"],
            "symbol": alert["symbol"],
            "timeframe": alert["timeframe"],
            "price": price,
            "message": message,
            "fired_at": now_ms,
            "toast": alert["notify"].get("toast", True),
            "sound": alert["notify"].get("sound", False),
        }
        self._emit(event)
        # Telegram a webhook na pozadí – pomalá služba nesmí zdržet další svíčky
        task = asyncio.create_task(self._deliver(alert, event))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _deliver(self, alert: dict, event: dict) -> None:
        """Odešle notifikace spuštěného alertu a zapíše spuštění do logu s výsledkem."""
        delivery = await self.send_notifications(alert["notify"], event)
        # Notifikace bez ceny (scanner, připomínka) má v logu cenu 0 (sloupec je povinný)
        price = event["price"] if event.get("price") is not None else 0.0
        await self._cache.add_alert_log({**event, "price": price, "delivery": delivery})
        self._emit({"type": "log_changed"})

    async def notify_external(self, notify: dict, *, source: str, symbol: str, timeframe: str, message: str) -> None:
        """Notifikace mimo uložené alerty (scanner, časová připomínka).

        Stránkám se ohlásí událostí "notification" (toast, zvuk – seznam alertů
        se nemění), Telegram a webhook se pošlou podle notify a záznam přibude
        do logu alertů (alert_id 0, bez ceny).

        Parametry:
            notify: nastavení notifikací ({"telegram", "webhook", "toast", "sound"}).
            source: původ do logu ("scanner", "reminder").
            symbol, timeframe: popis do logu a zprávy Telegramu.
            message: text notifikace.
        """
        event = {
            "type": "notification",
            "alert_id": 0,
            "source": source,
            "symbol": symbol,
            "timeframe": timeframe,
            "price": None,
            "message": message,
            "fired_at": utc_now_ms(),
            "toast": notify.get("toast", True),
            "sound": notify.get("sound", False),
        }
        self._emit(event)
        await self._deliver({"notify": notify}, event)

    async def send_notifications(self, notify: dict, event: dict) -> list[dict]:
        """Pošle Telegram a webhook podle nastavení alertu; vrátí výsledky [{"channel", "ok", "error"}].

        Parametry:
            notify: nastavení notifikací alertu ({"telegram", "webhook", …}).
            event: událost spuštění ({"message", "symbol", "timeframe", "price", …}).
        """
        jobs: list[tuple[str, object]] = []
        delivery: list[dict] = []
        if notify.get("telegram"):
            token, chat_id = telegram_settings()
            if token and chat_id:
                text = f"⏰ {event['message']}\n{event['symbol']} · {event['timeframe']}"
                if event.get("price") is not None:
                    text += f" · {event['price']:g}"
                jobs.append(("telegram", send_telegram(self._client, token, chat_id, text)))
            else:
                # Kanál bez nastavení se v logu ukáže jako neodeslaný
                delivery.append({"channel": "telegram", "ok": False, "error": "Telegram není nastavený (Settings → Telegram)."})
        if notify.get("webhook"):
            jobs.append(("webhook", send_webhook(self._client, notify["webhook"], event["message"])))
        results = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
        for (channel, _), result in zip(jobs, results):
            if isinstance(result, BaseException):
                delivery.append({"channel": channel, "ok": False, "error": str(result) or type(result).__name__})
            else:
                delivery.append({"channel": channel, "ok": True, "error": ""})
        return delivery
