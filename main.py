"""Vstupní bod aplikace TickView."""

import argparse

from nicegui import app, ui

from tickview.config import DB_PATH, PROJECT_ROOT, ensure_dirs
from tickview.i18n import load_language
from tickview.logic.cache import KlineCache
from tickview.providers import PROVIDERS
from tickview.services import datafeeds
from tickview.services.alerts import AlertEngine
from tickview.services.reminders import ReminderService
from tickview.services.scanner import ScannerService
from tickview.ui import main_window
from tickview.ui.welcome_dialog import WELCOME_PENDING_KEY


def parse_args() -> argparse.Namespace:
    """Zpracuje parametry příkazové řádky."""
    parser = argparse.ArgumentParser(
        description="TickView – burzovní a krypto grafy ve stylu TradingView"
    )
    parser.add_argument("--port", type=int, default=8095, help="Port webového serveru (výchozí 8095)")
    parser.add_argument("--native", action="store_true", help="Spustit v nativním okně (vyžaduje pywebview)")
    parser.add_argument("--open", action="store_true", help="Otevřít prohlížeč automaticky")
    return parser.parse_args()


def main() -> None:
    """Připraví cache, statické soubory a spustí NiceGUI aplikaci."""
    args = parse_args()
    ensure_dirs()
    cache = KlineCache(DB_PATH)
    # Externí data indikátorů (COT report CFTC) se ukládají do stejné databáze
    datafeeds.configure(cache)
    # Alerty, scanner a časové připomínky běží na serveru i bez otevřené stránky
    alert_engine = AlertEngine(cache)
    scanner = ScannerService(cache, alert_engine)
    reminders = ReminderService(cache, alert_engine)

    async def startup() -> None:
        # Nejdřív databáze (schéma), pak služby, které z ní čtou; jazyk UI platí
        # od startu i pro texty služeb na pozadí (alerty, Telegram, scanner)
        await cache.init()
        # Po instalaci (nová databáze) se při otevření stránky ukáže přehled zdrojů dat
        if cache.created:
            await cache.set_setting(WELCOME_PENDING_KEY, "1")
        await load_language(cache)
        await alert_engine.start()
        await scanner.start()
        await reminders.start()

    app.on_startup(startup)

    async def shutdown() -> None:
        # Při ukončení zastavíme alerty a zavřeme síťová spojení zdrojů (HTTP,
        # websockety, TWS) i databázi
        await reminders.stop()
        await scanner.stop()
        await alert_engine.stop()
        for provider in PROVIDERS:
            try:
                await provider.close()
            except Exception:
                # Chyba při úklidu jednoho zdroje nesmí zablokovat ostatní
                pass
        await cache.close()

    app.on_shutdown(shutdown)

    # Statické soubory UI: styly, JS komponenta grafu a vendored knihovna
    app.add_static_files("/static", str(PROJECT_ROOT / "tickview" / "ui" / "static"))

    main_window.register(cache, alert_engine, scanner, reminders)

    ui.run(title="TickView", port=args.port, reload=False, native=args.native, show=args.open)


if __name__ == "__main__":
    main()
