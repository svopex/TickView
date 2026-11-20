"""Dialog O aplikaci: informace o produktu, autorovi a systému, odkazy na web projektu
a na hlášení chyb na GitHubu."""

import base64
import platform
import sys
from functools import cache
from importlib.metadata import PackageNotFoundError, version

from nicegui import ui

from tickview import __version__
from tickview.config import DATA_DIR
from tickview.ui.templates import settings_header

# Web projektu (GitHub Pages), zdrojový kód a hlášení chyb
WEB_URL = "https://svopex.github.io/TickView/"
GITHUB_URL = "https://github.com/svopex/TickView"
ISSUES_URL = f"{GITHUB_URL}/issues"
LICENSE_URL = f"{GITHUB_URL}/blob/main/LICENSE"

# Web autora a kontaktní e-mail; e-mail je ve zdrojovém kódu obrácený a v base64,
# aby ho v repozitáři na GitHubu nenašli roboti; stejný řetězec je v data-email
# tlačítka Poslat e-mail v index.html (při změně upravit obojí)
AUTHOR_URL = "https://svopex.eu"
CONTACT_EMAIL = base64.b64decode("dWUueGVwb3ZzQHdlaXZrY2l0").decode()[::-1]


def package_version(name: str) -> str:
    """Vrátí nainstalovanou verzi balíčku, nebo pomlčku, když balíček chybí.

    Parametry:
        name: název distribuce v pip (např. "nicegui").
    """
    try:
        return version(name)
    except PackageNotFoundError:
        return "–"


# Údaje se za běhu procesu nemění – zjistí se jen jednou, ne při každém
# sestavení stránky (dotazy na metadata balíčků čtou disk)
@cache
def system_rows() -> list[tuple[str, str]]:
    """Vrátí dvojice (popisek, hodnota) s údaji o systému a prostředí aplikace."""
    return [
        # Systém s verzí, např. „macOS-15.1“, „Windows-11“
        ("Systém", platform.platform(terse=True)),
        ("Architektura", platform.machine() or "–"),
        ("Python", f"{platform.python_version()} ({platform.python_implementation()})"),
        ("NiceGUI", package_version("nicegui")),
        ("pandas", package_version("pandas")),
        ("pandas-ta", package_version("pandas-ta")),
        ("Interpret", sys.executable),
        ("Složka dat", str(DATA_DIR)),
    ]


def info_table(rows: list[tuple[str, ...]]) -> None:
    """Vykreslí dvousloupcovou tabulku popisků a hodnot (volá se v kontextu stránky).

    Parametry:
        rows: trojice (popisek, hodnota[, URL]); s URL je hodnota odkazem.
    """
    with ui.grid(columns="auto 1fr").classes("about-grid w-full"):
        for label, value, *url in rows:
            ui.label(label).classes("about-key")
            # Hodnota s URL se zobrazí jako odkaz do nové záložky; mailto: otevře
            # poštovního klienta, nová záložka by zůstala prázdná
            if url:
                new_tab = not url[0].startswith("mailto:")
                ui.link(value, url[0], new_tab=new_tab).classes("about-value about-link")
            else:
                ui.label(value).classes("about-value")


def build_about_dialog() -> ui.dialog:
    """Vytvoří (zavřený) dialog O aplikaci v aktuálním kontextu stránky.

    Vrací dialog; otevírá se ikonou (i) dole v pravé svislé liště.
    """
    with ui.dialog() as dialog, ui.card().classes("tv-settings about-dialog"):
        settings_header(dialog, "O aplikaci")
        # Produkt: název, verze, krátký popis a odkaz na web projektu
        with ui.row().classes("about-product w-full"):
            ui.icon("candlestick_chart").classes("about-logo")
            with ui.column().classes("about-product-text"):
                ui.label("TickView").classes("about-name")
                ui.label(f"Verze {__version__}").classes("about-version")
        ui.label(
            "Burzovní a krypto grafy ve stylu TradingView – pouze pro čtení, bez obchodování."
        ).classes("about-description")
        with ui.row().classes("about-links w-full"):
            ui.link("Web projektu", WEB_URL, new_tab=True).classes("about-link")
            ui.link("Zdrojový kód na GitHubu", GITHUB_URL, new_tab=True).classes("about-link")
            ui.link("Nahlásit chybu na GitHubu", ISSUES_URL, new_tab=True).classes("about-link")

        # Autor a licence
        ui.label("Autor a licence").classes("tv-settings-section")
        info_table(
            [
                ("Autor", "© 2026 Svopex"),
                ("Web autora", "svopex.eu", AUTHOR_URL),
                ("E-mail", "Poslat e-mail", f"mailto:{CONTACT_EMAIL}"),
                ("Licence", "Apache License 2.0", LICENSE_URL),
            ]
        )

        # Systém a prostředí, ve kterém aplikace běží (pro hlášení chyb)
        ui.label("Systém").classes("tv-settings-section")
        info_table(system_rows())

        with ui.row().classes("tv-settings-actions w-full"):
            ui.space()
            ui.button("Zavřít", color=None, on_click=dialog.close).props("flat no-caps").classes("tv-btn-cancel")
    return dialog
