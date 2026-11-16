"""Vstupní bod aplikace TickView."""

import argparse

from nicegui import app, ui

from tickview.config import DB_PATH, PROJECT_ROOT, ensure_dirs
from tickview.logic.cache import KlineCache
from tickview.providers import PROVIDERS
from tickview.ui import main_window


def parse_args() -> argparse.Namespace:
    """Zpracuje parametry příkazové řádky."""
    parser = argparse.ArgumentParser(
        description="TickView – burzovní a krypto grafy ve stylu TradingView"
    )
    parser.add_argument("--port", type=int, default=8080, help="Port webového serveru (výchozí 8080)")
    parser.add_argument("--native", action="store_true", help="Spustit v nativním okně (vyžaduje pywebview)")
    parser.add_argument("--open", action="store_true", help="Otevřít prohlížeč automaticky")
    return parser.parse_args()


def main() -> None:
    """Připraví cache, statické soubory a spustí NiceGUI aplikaci."""
    args = parse_args()
    ensure_dirs()
    cache = KlineCache(DB_PATH)

    # Databáze se otevře asynchronně při startu serveru
    app.on_startup(cache.init)

    async def shutdown() -> None:
        # Při ukončení zavřeme síťová spojení zdrojů (HTTP, websockety, TWS) i databázi
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

    main_window.register(cache)

    ui.run(title="TickView", port=args.port, reload=False, native=args.native, show=args.open)


if __name__ == "__main__":
    main()
