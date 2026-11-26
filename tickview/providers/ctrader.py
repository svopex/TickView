"""Datový zdroj cTrader Open API (Spotware): forex, kovy a CFD s tick volume.

Data dodává broker s platformou cTrader (Pepperstone, IC Markets, Purple
Trading…) přes jeho demo nebo živý účet. Objem svíček je tick volume (počet
změn ceny) jako v TradingView. Zdroj je volitelný: aktivuje se, když jsou
v config.yaml (sekce "ctrader") údaje aplikace Open API a přístupový token.
Slouží i jako zdroj objemu pro IBKR forex, kovy a CFD (viz services/volume).

Spojení je jedno sdílené: JSON zprávy přes WebSocket (port 5036), heartbeat
každých 10 s, požadavky se párují podle clientMsgId a události (spot, živé
bary) se rozdělují odběratelům. Protokol: https://help.ctrader.com/open-api/
"""

import asyncio
import itertools
import json
import time as time_module
from typing import Any, AsyncIterator, Iterable, Optional

import httpx
import websockets

from tickview.config import load_config, update_config_section
from tickview.i18n import tr
from tickview.providers.base import (
    TIMEFRAME_MS,
    Candle,
    DataProvider,
    RateLimiter,
    StreamItem,
    StreamStatus,
    SymbolInfo,
    utc_now_ms,
)
from tickview.providers.ibkr import forex_parts

# Servery JSON protokolu (WebSocket); demo a živé účty mají oddělené servery
DEMO_URL = "wss://demo.ctraderapi.com:5036"
LIVE_URL = "wss://live.ctraderapi.com:5036"
# Obnova přístupového tokenu (HTTP) – vrací nový access i refresh token
TOKEN_URL = "https://openapi.ctrader.com/apps/token"

# Typy zpráv (payloadType z OpenApiModelMessages.proto / OpenApiCommonModelMessages.proto)
ERROR_RES = 50
HEARTBEAT_EVENT = 51
APPLICATION_AUTH_REQ = 2100
ACCOUNT_AUTH_REQ = 2102
SYMBOLS_LIST_REQ = 2114
SYMBOL_BY_ID_REQ = 2116
SUBSCRIBE_SPOTS_REQ = 2127
UNSUBSCRIBE_SPOTS_REQ = 2129
SPOT_EVENT = 2131
SUBSCRIBE_LIVE_TRENDBAR_REQ = 2135
UNSUBSCRIBE_LIVE_TRENDBAR_REQ = 2136
GET_TRENDBARS_REQ = 2137
OA_ERROR_RES = 2142
ACCOUNTS_TOKEN_INVALIDATED_EVENT = 2147
CLIENT_DISCONNECT_EVENT = 2148
GET_ACCOUNTS_BY_TOKEN_REQ = 2149
ACCOUNT_DISCONNECT_EVENT = 2164

# Perioda trendbarů (ProtoOATrendbarPeriod) pro kanonické timeframy aplikace
PERIODS = {"1m": 1, "2m": 2, "5m": 5, "10m": 6, "15m": 7, "1h": 9, "4h": 10, "1D": 12, "1W": 13}
# Nejdelší interval jednoho dotazu na trendbary podle periody (ms; hodnoty
# z oficiálního SDK Spotware – dokumentace je neuvádí)
MAX_RANGE_MS = {1: 302_400_000, 2: 302_400_000, 5: 302_400_000, 6: 21_168_000_000, 7: 21_168_000_000,
                9: 21_168_000_000, 10: 31_622_400_000, 12: 31_622_400_000, 13: 158_112_000_000}
# Ceny v protokolu jsou celá čísla v jednotkách 1/100000
PRICE_SCALE = 100_000

# Heartbeat (server spojení bez zpráv ukončí); čekání na odpověď na požadavek
HEARTBEAT_S = 10.0
REQUEST_TIMEOUT_S = 20.0
# Seznam symbolů účtu se mění zřídka – v paměti na 6 h
SYMBOLS_TTL_S = 6 * 3600
# Ceny watchlistu (z denních barů) – v paměti na 60 s, ať se historie nestahuje při každé obnově
TICKERS_TTL_S = 60.0
# Po neúspěšném připojení se další pokus (dotazy historie, objem) odloží
CONNECT_COOLDOWN_S = 60.0
# Horní mez čekání mezi pokusy o znovupřipojení kvůli živým odběrům
RECONNECT_MAX_BACKOFF_S = 30.0
# Nejvýš kolik stránek trendbarů jeden úsek historie stáhne (pojistka proti smyčce)
MAX_PAGES = 200
# Chybové kódy, po kterých se zkusí obnovit přístupový token
TOKEN_ERRORS = ("OA_AUTH_TOKEN_EXPIRED", "CH_ACCESS_TOKEN_INVALID", "ACCESS_TOKEN_INVALID")

# Měny forexových párů (rozlišení druhu instrumentu podle názvu)
CURRENCIES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "DKK", "PLN", "HUF", "CZK",
    "TRY", "ZAR", "MXN", "SGD", "HKD", "CNH", "ILS", "RUB", "THB",
}
# Kovy a komodity podle začátku názvu (zlato, stříbro, platina, palladium, ropa, plyn)
COMMODITY_PREFIXES = ("XAU", "XAG", "XPT", "XPD", "XTI", "XBR", "XNG", "USOIL", "UKOIL", "BRENT", "WTI", "NATGAS")


# Kandidáti názvů instrumentů IBKR u cTrader brokerů (Pepperstone, IC Markets,
# Purple Trading…) v pořadí preference; forexové páry se odvodí ze symbolu
CTRADER_CANDIDATES: dict[str, tuple[str, ...]] = {
    "XAUUSD": ("XAUUSD", "GOLD"),
    "XAGUSD": ("XAGUSD", "SILVER"),
    "IBUSOIL": ("XTIUSD", "USOIL", "WTI", "SPOTCRUDE", "CRUDE"),
    "IBUKOIL": ("XBRUSD", "UKOIL", "BRENT", "SPOTBRENT"),
    "IBUS500": ("US500", "SPX500", "USA500", "SP500"),
    "IBUS30": ("US30", "DJ30", "USA30", "WS30"),
    "IBUST100": ("USTEC", "NAS100", "US100", "NDX100", "USTECH"),
    "IBDE40": ("DE40", "GER40", "DE30", "GER30", "DAX40"),
    "IBGB100": ("UK100", "FTSE100"),
    "IBEU50": ("STOXX50", "EUSTX50", "EU50", "ESTX50"),
    "IBFR40": ("F40", "FRA40", "FR40", "CAC40"),
    "IBJP225": ("JP225", "JPN225", "NIKKEI225"),
    "IBCH20": ("CH20", "SWI20", "SMI20"),
    "IBES35": ("ES35", "SPA35", "IBEX35"),
    "IBNL25": ("NETH25", "NL25", "AEX25"),
    "IBHK50": ("HK50", "HSI50"),
    "IBAU200": ("AUS200", "AU200", "ASX200"),
}

# Nejdelší přípona názvu u brokera, která se při hledání toleruje ("US500.cash")
MAX_SYMBOL_SUFFIX = 4


def _normalize(name: str) -> str:
    """Název symbolu bez oddělovačů velkými písmeny ("EUR/USD" → "EURUSD")."""
    return "".join(ch for ch in name.upper() if ch.isalnum())


def ctrader_candidates(ib_symbol: str) -> list[str]:
    """Kandidáti názvu symbolu u cTrader brokera pro symbol IBKR.

    Forexový pár "EUR.USD" → ["EURUSD"]; kovy a CFD podle tabulky
    CTRADER_CANDIDATES; neznámý symbol → [] (objem se nedoplňuje).
    """
    if ib_symbol in CTRADER_CANDIDATES:
        return list(CTRADER_CANDIDATES[ib_symbol])
    parts = forex_parts(ib_symbol)
    return ["".join(parts)] if parts else []


def resolve_ctrader_symbol(ib_symbol: str, available: Iterable[str], overrides: Optional[dict] = None) -> Optional[str]:
    """Název symbolu u brokera pro symbol IBKR, nebo None, když broker instrument nemá.

    Ruční přiřazení z konfigurace (overrides) má přednost; jinak první kandidát,
    jehož název broker má – shodně, nebo s krátkou příponou ("US500.cash").

    Parametry:
        ib_symbol: symbol IBKR ("EUR.USD", "IBUS500").
        available: názvy symbolů účtu u brokera.
        overrides: {symbol IBKR: název u brokera} z config.yaml.
    """
    by_normalized: dict[str, str] = {}
    for name in available:
        by_normalized.setdefault(_normalize(name), name)
    if overrides and ib_symbol in overrides:
        return by_normalized.get(_normalize(str(overrides[ib_symbol])))
    candidates = ctrader_candidates(ib_symbol)
    for candidate in candidates:
        if candidate in by_normalized:
            return by_normalized[candidate]
    # Název s příponou brokera: nejkratší shoda pro prvního kandidáta, který nějakou má
    for candidate in candidates:
        suffixed = [
            key for key in by_normalized
            if key.startswith(candidate) and 0 < len(key) - len(candidate) <= MAX_SYMBOL_SUFFIX
        ]
        if suffixed:
            return by_normalized[min(suffixed, key=len)]
    return None


class CTraderError(RuntimeError):
    """Chybová odpověď serveru cTrader (errorCode + popis)."""

    def __init__(self, code: str, description: str = "") -> None:
        super().__init__(f"{code}: {description}" if description else code)
        self.code = code


def symbol_key(name: str) -> str:
    """Symbol aplikace z názvu u brokera: velkými písmeny bez lomítek a mezer ("EUR/USD" → "EURUSD")."""
    return "".join(ch for ch in name.upper() if ch.isalnum() or ch == ".")


def guess_kind(key: str) -> str:
    """Druh instrumentu podle názvu: forexový pár, komodita, jinak index (CFD)."""
    letters = key.split(".")[0]
    if len(letters) == 6 and letters[:3] in CURRENCIES and letters[3:] in CURRENCIES:
        return "forex"
    if letters.startswith(COMMODITY_PREFIXES):
        return "commodity"
    if any(ch.isdigit() for ch in letters):
        return "index"
    return ""


def decode_trendbar(bar: dict, digits: int) -> Candle:
    """Svíčka z trendbaru: ceny = (low + delta) / 100000 zaokrouhlené na přesnost symbolu.

    Chybějící delta (výchozí 0) znamená cenu rovnou minimu; čas baru je
    v minutách od epochy (utcTimestampInMinutes).
    """
    low = int(bar.get("low", 0))

    def price(delta_key: str) -> float:
        return round((low + int(bar.get(delta_key, 0))) / PRICE_SCALE, digits)

    return Candle(
        open_time=int(bar.get("utcTimestampInMinutes", 0)) * 60_000,
        open=price("deltaOpen"),
        high=price("deltaHigh"),
        low=round(low / PRICE_SCALE, digits),
        close=price("deltaClose"),
        volume=float(bar.get("volume", 0)),
    )


class CTraderClient:
    """Sdílené spojení s cTrader Open API: přihlášení, požadavky, živé odběry.

    Spojení se otevře s prvním požadavkem nebo odběrem a drží se heartbeatem;
    po výpadku se s aktivními odběry samo obnoví a odběry znovu přihlásí.

    Parametry:
        config: sekce "ctrader" z config.yaml (client_id, client_secret,
            access_token, refresh_token, account_id, live).
        persist_tokens: po obnově tokenu zapsat nové tokeny do config.yaml.
    """

    def __init__(self, config: dict, persist_tokens: bool = True) -> None:
        self._client_id = str(config.get("client_id") or "").strip()
        self._client_secret = str(config.get("client_secret") or "").strip()
        self._access_token = str(config.get("access_token") or "").strip()
        self._refresh_token = str(config.get("refresh_token") or "").strip()
        self._wanted_account = str(config.get("account_id") or "").strip()
        self._live = bool(config.get("live"))
        self._persist_tokens = persist_tokens
        self.account_id: Optional[int] = None
        self.broker = ""
        self._ws: Any = None
        self._reader: Optional[asyncio.Task] = None
        self._heartbeat: Optional[asyncio.Task] = None
        self._reconnect: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future] = {}
        self._msg_ids = itertools.count(1)
        self._failed_at = 0.0
        self._last_error = ""
        # Historické dotazy: server povoluje 5 za sekundu na spojení
        self._history_limiter = RateLimiter(min_interval=0.25)
        # Symboly účtu: symbol aplikace -> lehký popis ({symbolId, symbolName, description})
        self._symbols: dict[str, dict] = {}
        self._symbols_loaded_at = 0.0
        self._digits: dict[int, int] = {}
        # Živé odběry: (symbolId, perioda) -> fronty odběratelů
        self._subscribers: dict[tuple[int, int], set[asyncio.Queue]] = {}

    @property
    def configured(self) -> bool:
        """Zdroj je použitelný s údaji aplikace a přístupovým (nebo obnovovacím) tokenem."""
        return bool(self._client_id and self._client_secret and (self._access_token or self._refresh_token))

    # ---------- spojení a přihlášení ----------

    async def ensure_connected(self) -> None:
        """Otevře a přihlásí spojení, pokud neběží; po nedávném selhání hned vyhodí chybu."""
        if self._ws is not None:
            return
        async with self._connect_lock:
            if self._ws is not None:
                return
            if time_module.monotonic() - self._failed_at < CONNECT_COOLDOWN_S:
                raise RuntimeError(self._last_error or tr("cTrader: připojení nedávno selhalo"))
            try:
                await self._connect()
            except Exception as exc:
                self._failed_at = time_module.monotonic()
                self._last_error = tr("cTrader: {error}").format(error=exc)
                await self._drop_connection()
                raise

    async def _connect(self) -> None:
        """Připojení, přihlášení aplikace a účtu (s obnovou tokenu) a obnovení odběrů."""
        if not self.configured:
            raise RuntimeError(tr("chybí údaje aplikace Open API nebo token"))
        self._ws = await websockets.connect(LIVE_URL if self._live else DEMO_URL, open_timeout=10, max_size=None)
        self._reader = asyncio.create_task(self._read_loop(self._ws))
        self._heartbeat = asyncio.create_task(self._heartbeat_loop(self._ws))
        await self.request(APPLICATION_AUTH_REQ, {"clientId": self._client_id, "clientSecret": self._client_secret})
        try:
            await self._authorize_account()
        except CTraderError as exc:
            # Prošlý token: obnova přes refresh token a druhý pokus
            if not self._refresh_token or not any(code in exc.code for code in TOKEN_ERRORS):
                raise
            await self._refresh_access_token()
            await self._authorize_account()
        self._failed_at = 0.0
        # Odběry z doby před výpadkem se přihlásí znovu
        for symbol_id, period in list(self._subscribers):
            await self._subscribe_remote(symbol_id, period)

    async def _authorize_account(self) -> None:
        """Vybere účet tokenu (z konfigurace, jinak první demo / živý podle serveru) a přihlásí ho."""
        if not self._access_token:
            raise CTraderError("ACCESS_TOKEN_INVALID", tr("chybí přístupový token"))
        response = await self.request(GET_ACCOUNTS_BY_TOKEN_REQ, {"accessToken": self._access_token})
        accounts = response.get("ctidTraderAccount") or []
        chosen = None
        for account in accounts:
            if self._wanted_account and str(account.get("ctidTraderAccountId")) == self._wanted_account:
                chosen = account
                break
            if not self._wanted_account and bool(account.get("isLive")) == self._live:
                chosen = account
                break
        if chosen is None:
            raise RuntimeError(
                tr("token nemá žádný {kind} účet").format(kind=tr("živý") if self._live else tr("demo"))
                if not self._wanted_account
                else tr("token nemá účet {account}").format(account=self._wanted_account)
            )
        self.account_id = int(chosen["ctidTraderAccountId"])
        self.broker = str(chosen.get("brokerTitleShort") or "")
        await self.request(ACCOUNT_AUTH_REQ, {"ctidTraderAccountId": self.account_id, "accessToken": self._access_token})

    async def _refresh_access_token(self) -> None:
        """Obnoví přístupový token (HTTP); nové tokeny uloží do config.yaml (starý refresh token přestane platit)."""
        params = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(TOKEN_URL, params=params)
            data = response.json()
        if data.get("errorCode") or not data.get("accessToken"):
            raise RuntimeError(tr("obnova tokenu selhala: {error}").format(
                error=data.get("description") or data.get("errorCode") or response.status_code
            ))
        self._access_token = data["accessToken"]
        self._refresh_token = data.get("refreshToken") or self._refresh_token
        if self._persist_tokens:
            update_config_section("ctrader", {"access_token": self._access_token, "refresh_token": self._refresh_token})

    async def _drop_connection(self) -> None:
        """Zavře spojení a zruší čekající požadavky (výpadek, chyba přihlášení)."""
        ws, self._ws = self._ws, None
        for task in (self._heartbeat, self._reader):
            if task is not None and task is not asyncio.current_task():
                task.cancel()
        self._heartbeat = self._reader = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError(tr("spojení s cTrader přerušeno")))
        self._pending.clear()
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    async def close(self) -> None:
        """Ukončí spojení i obnovu (ukončení aplikace, změna konfigurace)."""
        if self._reconnect is not None:
            self._reconnect.cancel()
            self._reconnect = None
        await self._drop_connection()

    async def _heartbeat_loop(self, ws: Any) -> None:
        # Server spojení bez provozu ukončí – heartbeat každých 10 s
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await ws.send(json.dumps({"payloadType": HEARTBEAT_EVENT, "payload": {}}))

    async def _read_loop(self, ws: Any) -> None:
        """Čte zprávy: odpovědi párované podle clientMsgId, události spot a odpojení."""
        try:
            async for raw in ws:
                message = json.loads(raw)
                payload_type = message.get("payloadType")
                payload = message.get("payload") or {}
                future = self._pending.pop(message.get("clientMsgId") or "", None)
                if future is not None:
                    # Odpověď na požadavek (čekající mezitím mohl vypršet)
                    if future.done():
                        continue
                    if payload_type in (OA_ERROR_RES, ERROR_RES):
                        future.set_exception(CTraderError(str(payload.get("errorCode")), payload.get("description") or ""))
                    else:
                        future.set_result(payload)
                elif payload_type == SPOT_EVENT:
                    self._dispatch_spot(payload)
                elif payload_type in (ACCOUNTS_TOKEN_INVALIDATED_EVENT, CLIENT_DISCONNECT_EVENT, ACCOUNT_DISCONNECT_EVENT):
                    # Server účet odhlásil (token zneplatněn, údržba) – spojení otevřeme znovu
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        if self._ws is ws:
            await self._drop_connection()
            self._on_disconnect()

    async def request(self, payload_type: int, payload: dict) -> dict:
        """Pošle požadavek a počká na odpověď (chyba serveru = CTraderError)."""
        if self._ws is None:
            raise ConnectionError(tr("spojení s cTrader není otevřené"))
        msg_id = f"tv{next(self._msg_ids)}"
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._ws.send(json.dumps({"clientMsgId": msg_id, "payloadType": payload_type, "payload": payload}))
        try:
            return await asyncio.wait_for(future, REQUEST_TIMEOUT_S)
        finally:
            self._pending.pop(msg_id, None)

    async def _account_request(self, payload_type: int, payload: dict, connect: bool = True) -> dict:
        """Požadavek k účtu (doplní ctidTraderAccountId).

        Parametry:
            connect: připojit se, pokud spojení neběží (False = jen v otevřeném
                spojení – odhlášení a obnova odběrů při připojování).
        """
        if connect:
            await self.ensure_connected()
        return await self.request(payload_type, {"ctidTraderAccountId": self.account_id, **payload})

    # ---------- symboly ----------

    async def symbols(self) -> dict[str, dict]:
        """Symboly účtu: symbol aplikace -> {symbolId, symbolName, description}; v paměti na 6 h."""
        if self._symbols and time_module.monotonic() - self._symbols_loaded_at < SYMBOLS_TTL_S:
            return self._symbols
        response = await self._account_request(SYMBOLS_LIST_REQ, {})
        symbols = {}
        for item in response.get("symbol") or []:
            if item.get("enabled", True) is False or not item.get("symbolName"):
                continue
            symbols.setdefault(symbol_key(item["symbolName"]), item)
        self._symbols = symbols
        self._symbols_loaded_at = time_module.monotonic()
        return symbols

    async def symbol_id(self, symbol: str) -> int:
        """symbolId pro symbol aplikace; neznámý symbol = ValueError."""
        item = (await self.symbols()).get(symbol)
        if item is None:
            raise ValueError(tr("cTrader nezná symbol {symbol}").format(symbol=symbol))
        return int(item["symbolId"])

    async def digits(self, symbol_id: int) -> int:
        """Počet desetinných míst ceny symbolu (podrobnosti symbolu; cachuje se)."""
        if symbol_id not in self._digits:
            response = await self._account_request(SYMBOL_BY_ID_REQ, {"symbolId": [symbol_id]})
            for item in response.get("symbol") or []:
                self._digits[int(item["symbolId"])] = int(item.get("digits", 5))
        return self._digits.get(symbol_id, 5)

    # ---------- historie ----------

    async def trendbars(self, symbol: str, period: int, start_ms: int, end_ms: int) -> list[Candle]:
        """Bary symbolu v intervalu (UTC ms, včetně), seřazené podle času.

        Interval se dělí na úseky podle nejdelšího povoleného rozsahu periody;
        odpověď s příznakem hasMore se dostahuje stránkami směrem do minulosti.
        """
        symbol_id = await self.symbol_id(symbol)
        digits = await self.digits(symbol_id)
        bars: dict[int, Candle] = {}
        max_range = MAX_RANGE_MS[period]
        chunk_end = end_ms
        while chunk_end >= start_ms:
            chunk_start = max(start_ms, chunk_end - max_range + 1)
            to_ms = chunk_end
            for _ in range(MAX_PAGES):
                await self._history_limiter.acquire()
                response = await self._account_request(GET_TRENDBARS_REQ, {
                    "fromTimestamp": chunk_start, "toTimestamp": to_ms, "period": period, "symbolId": symbol_id,
                })
                page = [decode_trendbar(bar, digits) for bar in response.get("trendbar") or []]
                for candle in page:
                    if start_ms <= candle.open_time <= end_ms:
                        bars[candle.open_time] = candle
                # Další stránka končí těsně před nejstarším barem této stránky
                if not response.get("hasMore") or not page:
                    break
                to_ms = min(candle.open_time for candle in page) - 1
                if to_ms < chunk_start:
                    break
            chunk_end = chunk_start - 1
        return [bars[time] for time in sorted(bars)]

    # ---------- živé odběry ----------

    async def subscribe(self, symbol: str, period: int) -> tuple[tuple[int, int], asyncio.Queue, int]:
        """Přihlásí odběr živého baru symbolu; vrací (klíč odběru, frontu, přesnost cen)."""
        symbol_id = await self.symbol_id(symbol)
        digits = await self.digits(symbol_id)
        key = (symbol_id, period)
        queue: asyncio.Queue = asyncio.Queue()
        is_new = key not in self._subscribers
        self._subscribers.setdefault(key, set()).add(queue)
        if is_new:
            await self._subscribe_remote(symbol_id, period)
        return key, queue, digits

    async def unsubscribe(self, key: tuple[int, int], queue: asyncio.Queue) -> None:
        """Odhlásí odběratele; poslední odběratel ruší odběr baru (a spotů symbolu) na serveru."""
        subscribers = self._subscribers.get(key)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if subscribers:
            return
        del self._subscribers[key]
        if self._ws is None:
            return
        symbol_id, period = key
        try:
            await self._account_request(UNSUBSCRIBE_LIVE_TRENDBAR_REQ, {"period": period, "symbolId": symbol_id}, connect=False)
            if not any(other_id == symbol_id for other_id, _ in self._subscribers):
                await self._account_request(UNSUBSCRIBE_SPOTS_REQ, {"symbolId": [symbol_id]}, connect=False)
        except Exception:
            # Spojení padá nebo odběr už neexistuje – nic dalšího není potřeba
            pass

    async def _subscribe_remote(self, symbol_id: int, period: int) -> None:
        """Na serveru přihlásí spoty symbolu (podmínka živých barů) a živý bar periody."""
        for payload_type, payload in (
            (SUBSCRIBE_SPOTS_REQ, {"symbolId": [symbol_id]}),
            (SUBSCRIBE_LIVE_TRENDBAR_REQ, {"period": period, "symbolId": symbol_id}),
        ):
            try:
                await self._account_request(payload_type, payload, connect=False)
            except CTraderError as exc:
                # Spoty symbolu už odebíráme kvůli jiné periodě
                if exc.code != "ALREADY_SUBSCRIBED":
                    raise

    def _dispatch_spot(self, payload: dict) -> None:
        """Živé bary ze spot události rozdělí odběratelům (symbol + perioda)."""
        symbol_id = int(payload.get("symbolId", 0))
        for bar in payload.get("trendbar") or []:
            key = (symbol_id, int(bar.get("period", 1)))
            for queue in self._subscribers.get(key, ()):
                queue.put_nowait(bar)

    def _broadcast_status(self, status: StreamStatus) -> None:
        # Stavová zpráva (výpadek / obnovení) všem odběratelům
        for queues in self._subscribers.values():
            for queue in queues:
                queue.put_nowait(status)

    def _on_disconnect(self) -> None:
        # S aktivními odběry spojení obnovujeme na pozadí s narůstajícím odstupem
        if self._subscribers and (self._reconnect is None or self._reconnect.done()):
            self._reconnect = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        # Výpadek ohlásíme odběratelům jednou, po obnovení spojení (a odběrů) také
        self._broadcast_status(StreamStatus(False, tr("spojení s cTrader přerušeno")))
        backoff = 1.0
        while self._subscribers:
            await asyncio.sleep(backoff)
            self._failed_at = 0.0
            try:
                await self.ensure_connected()
            except Exception:
                backoff = min(backoff * 2, RECONNECT_MAX_BACKOFF_S)
                continue
            self._broadcast_status(StreamStatus(True, tr("cTrader znovu připojen")))
            return


class CTraderProvider(DataProvider):
    """cTrader – forex, kovy a CFD brokera s platformou cTrader (živě, tick volume)."""

    id = "ctrader"
    name = "cTrader"
    supports_stream = True
    is_live = True

    def __init__(self) -> None:
        self.timeframes = {timeframe: str(period) for timeframe, period in PERIODS.items()}
        self._config = load_config().get("ctrader") or {}
        self.client = CTraderClient(self._config)
        # Dohledané symboly objemu (symbol IBKR -> symbol u brokera / None) a ceny watchlistu
        self._volume_symbols: dict[str, Optional[str]] = {}
        self._tickers: dict[str, tuple[float, dict]] = {}

    @property
    def configured(self) -> bool:
        """Zdroj je zapnutý jen s údaji aplikace Open API a tokenem."""
        return self.client.configured

    def reload_config(self) -> None:
        """Po uložení Settings: při změně sekce ctrader nové údaje a nové spojení
        (odběry se obnoví při dalším načtení grafu)."""
        config = load_config().get("ctrader") or {}
        if config == self._config:
            return
        self._config = config
        self._volume_symbols.clear()
        old = self.client
        self.client = CTraderClient(config)
        try:
            asyncio.get_running_loop().create_task(old.close())
        except RuntimeError:
            # Bez běžící smyčky (testy) není co zavírat
            pass

    async def close(self) -> None:
        await self.client.close()

    def symbol_kind(self, symbol: str) -> str:
        return guess_kind(symbol)

    def currencies(self, symbol: str) -> tuple[Optional[str], str]:
        """Forexový pár: základní a kotovaná měna podle názvu; ostatní v USD."""
        if guess_kind(symbol) == "forex":
            letters = symbol.split(".")[0]
            return letters[:3], letters[3:]
        return None, "USD"

    async def volume_symbol(self, ib_symbol: str) -> Optional[str]:
        """Symbol u brokera pro instrument IBKR bez objemu (None = broker ho nemá); cachuje se.

        Ruční přiřazení: config.yaml → ctrader → symbols ({IBUS500: US500}).
        """
        if ib_symbol not in self._volume_symbols:
            overrides = self._config.get("symbols")
            names = await self.client.symbols()
            self._volume_symbols[ib_symbol] = resolve_ctrader_symbol(
                ib_symbol, names, overrides if isinstance(overrides, dict) else None
            )
        return self._volume_symbols[ib_symbol]

    async def describe(self, symbol: str) -> dict:
        """Název instrumentu ze seznamu symbolů a broker účtu."""
        item = (await self.client.symbols()).get(symbol) or {}
        exchange = f"cTrader · {self.client.broker}" if self.client.broker else self.name
        return {"name": item.get("description") or item.get("symbolName") or symbol, "exchange": exchange}

    async def search(self, query: str, limit: int = 30) -> list[SymbolInfo]:
        """Symboly účtu podle začátku symbolu, pak podle výskytu v symbolu nebo popisu."""
        symbols = await self.client.symbols()
        q = symbol_key(query)
        if not q:
            popular = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "US500", "NAS100", "USTEC", "GER40"]
            keys = [key for key in popular if key in symbols]
        else:
            text = query.strip().upper()
            prefix = sorted(key for key in symbols if key.startswith(q))
            contains = sorted(
                key for key, item in symbols.items()
                if not key.startswith(q) and (q in key or text in str(item.get("description") or "").upper())
            )
            keys = prefix + contains
        results = []
        for key in keys[:limit]:
            item = symbols[key]
            description = str(item.get("description") or "")
            label = f"{key} – {description}" if description else key
            results.append(SymbolInfo(self.id, key, label, description, guess_kind(key), self.client.broker or self.name))
        return results

    async def history(self, symbol: str, timeframe: str, start_ms: int, end_ms: int) -> list[Candle]:
        """Svíčky s tick volume z trendbarů brokera."""
        return await self.client.trendbars(symbol, PERIODS[timeframe], start_ms, end_ms)

    async def tickers(self, symbols: list[str]) -> dict[str, dict]:
        """Poslední cena a denní změna z posledních dvou denních barů (pro watchlist; 60 s v paměti)."""
        result: dict[str, dict] = {}
        now_ms = utc_now_ms()
        for symbol in symbols:
            cached = self._tickers.get(symbol)
            if cached and time_module.monotonic() - cached[0] < TICKERS_TTL_S:
                result[symbol] = cached[1]
                continue
            try:
                bars = await self.client.trendbars(symbol, PERIODS["1D"], now_ms - 7 * TIMEFRAME_MS["1D"], now_ms)
            except Exception:
                continue
            if not bars:
                continue
            price = bars[-1].close
            previous = bars[-2].close if len(bars) > 1 else bars[-1].open
            result[symbol] = {"price": price, "change_pct": ((price / previous) - 1.0) * 100.0 if previous else 0.0}
            self._tickers[symbol] = (time_module.monotonic(), result[symbol])
        return result

    def stream(self, symbol: str, timeframe: str) -> AsyncIterator[StreamItem]:
        """Živá (rozpracovaná) svíčka timeframu ze spot událostí a stavové zprávy spojení."""
        return self._stream(symbol, PERIODS[timeframe])

    async def _stream(self, symbol: str, period: int) -> AsyncIterator[StreamItem]:
        key, queue, digits = await self.client.subscribe(symbol, period)
        try:
            while True:
                item = await queue.get()
                yield item if isinstance(item, StreamStatus) else decode_trendbar(item, digits)
        finally:
            await self.client.unsubscribe(key, queue)

    @staticmethod
    async def test_connection(config: dict) -> str:
        """Zkušební připojení s údaji z dialogu Settings; vrací popis účtu (chyba = výjimka).

        Tokeny obnovené při testu se neukládají (dialog může mít neuložené hodnoty).
        """
        client = CTraderClient(config, persist_tokens=False)
        try:
            await client.ensure_connected()
            symbols = await client.symbols()
            return tr("cTrader připojen: {broker}, účet {account}, {count} symbolů.").format(
                broker=client.broker or "?", account=client.account_id, count=len(symbols)
            )
        finally:
            await client.close()
