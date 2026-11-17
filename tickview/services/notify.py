"""Odesílání notifikací alertů: Telegram bot a webhook (HTTP POST).

Telegram: token bota a chat id jsou v config.yaml (sekce telegram) – nikdy
v kódu ani v databázi. Webhook se posílá jako v TradingView: zpráva, která je
platný JSON, jde jako application/json, jinak jako text/plain.
"""

from typing import Optional

import httpx

from tickview.config import load_config
from tickview.logic.alerts import webhook_payload

# Adresa Telegram Bot API (token se doplní do cesty)
TELEGRAM_API = "https://api.telegram.org"
# Časový limit jednoho odeslání (s) – pomalá služba nesmí blokovat další alerty
SEND_TIMEOUT_S = 10.0


def telegram_settings(config: Optional[dict] = None) -> tuple[str, str]:
    """Vrátí (bot_token, chat_id) z config.yaml; chybějící hodnoty jako prázdné řetězce."""
    section = (config if config is not None else load_config()).get("telegram") or {}
    return str(section.get("bot_token") or "").strip(), str(section.get("chat_id") or "").strip()


def telegram_configured(config: Optional[dict] = None) -> bool:
    """Je Telegram nastavený (token i chat id)?"""
    token, chat_id = telegram_settings(config)
    return bool(token and chat_id)


def _api_error(response: httpx.Response) -> str:
    # Popis chyby z odpovědi Bot API ({"ok": false, "description": …}), jinak HTTP stav
    try:
        description = response.json().get("description")
    except ValueError:
        description = None
    return description or f"HTTP {response.status_code}"


async def send_telegram(client: httpx.AsyncClient, token: str, chat_id: str, text: str) -> None:
    """Pošle zprávu přes Telegram bota (sendMessage); chyba vyhodí RuntimeError s popisem.

    Parametry:
        client: sdílený HTTP klient.
        token: token bota od @BotFather.
        chat_id: id chatu / skupiny / kanálu, kam bot píše.
        text: text zprávy (Telegram povoluje nejvýš 4096 znaků).
    """
    response = await client.post(
        f"{TELEGRAM_API}/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text[:4096], "disable_web_page_preview": True},
        timeout=SEND_TIMEOUT_S,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Telegram: {_api_error(response)}")


async def find_telegram_chat_id(client: httpx.AsyncClient, token: str) -> Optional[str]:
    """Zjistí chat id z poslední zprávy, kterou uživatel poslal botovi (getUpdates).

    Uživatel musí botovi předtím napsat (např. /start); vrací None, když
    bot žádnou zprávu nemá. Chyba (neplatný token) vyhodí RuntimeError.
    """
    response = await client.get(f"{TELEGRAM_API}/bot{token}/getUpdates", timeout=SEND_TIMEOUT_S)
    if response.status_code != 200:
        raise RuntimeError(f"Telegram: {_api_error(response)}")
    # Hledáme od nejnovější aktualizace: zpráva, příspěvek v kanálu nebo úprava zprávy
    for update in reversed(response.json().get("result") or []):
        for key in ("message", "channel_post", "edited_message", "my_chat_member"):
            chat = (update.get(key) or {}).get("chat")
            if chat and chat.get("id") is not None:
                return str(chat["id"])
    return None


async def send_webhook(client: httpx.AsyncClient, url: str, message: str) -> None:
    """Pošle zprávu alertu HTTP POSTem na webhook; chyba vyhodí RuntimeError s popisem."""
    body, content_type = webhook_payload(message)
    response = await client.post(url, content=body, headers={"Content-Type": content_type}, timeout=SEND_TIMEOUT_S)
    if response.status_code >= 400:
        raise RuntimeError(f"Webhook: HTTP {response.status_code}")
