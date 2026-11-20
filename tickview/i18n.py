"""Překlady uživatelského rozhraní: čeština (výchozí) a angličtina.

Zdrojové texty v kódu jsou česky a slouží zároveň jako klíče slovníku. Anglické
překlady jsou ve slovnících `EN` v modulech balíčku `tickview.locales` (každý
modul pokrývá jednu část aplikace). Překládá se až při zobrazení funkcí `tr`,
takže texty v konstantách modulů se označí jen `N_` a přeloží se v místě
použití – změna jazyka pak platí pro vše, co se vykreslí po ní.

Pravidla pro texty:
- uživatelský text: `tr("Uložit")`; s hodnotami `tr("Graf {symbol}").format(symbol=s)`
  (ne f-string – klíč slovníku musí být pevný řetězec),
- text v konstantě modulu (popisky, výčty): `N_("Svíčky")` a při zobrazení `tr(hodnota)`,
- volby výběru {hodnota: popisek z N_}: `tr_options(VOLBY)`,
- tvary podle počtu: `plural(count, N_("graf"), N_("grafy"), N_("grafů"))`,
- stejný český text s různými významy: `trc("weekday", "Ne")` / `NC_("weekday", "Ne")`
  (klíč slovníku "weekday|Ne"; bez něj by „Ne“ = neděle kolidovalo s „Ne“ = ne),
- v JavaScriptu `tvTr("Skrýt")` – do stránky se vkládá slovník tickview/locales/js.py
  (js_bootstrap), takže texty JS patří do něj.
Úplnost překladů kontroluje `tools/i18n_check.py` (a test tests/test_i18n.py).
"""

import importlib
import json
import pkgutil
from functools import cache

# Podporované jazyky (kód => název v daném jazyce) a klíč nastavení v databázi
LANGUAGES = {"cs": "Čeština", "en": "English"}
DEFAULT_LANGUAGE = "cs"
LANGUAGE_SETTING = "language"

# Aktuální jazyk procesu: aplikace běží lokálně pro jednoho uživatele, takže jazyk
# je společný pro stránku i služby na pozadí (alerty, Telegram, scanner)
_language = DEFAULT_LANGUAGE


def set_language(language: str | None) -> str:
    """Nastaví jazyk rozhraní; neznámý nebo prázdný kód = výchozí čeština.

    Parametry:
        language: kód jazyka ("cs", "en").
    Vrací skutečně nastavený kód jazyka.
    """
    global _language
    _language = language if language in LANGUAGES else DEFAULT_LANGUAGE
    return _language


def get_language() -> str:
    """Vrátí kód aktuálního jazyka rozhraní."""
    return _language


async def load_language(cache) -> str:
    """Nastaví jazyk rozhraní podle uloženého nastavení a vrátí jeho kód.

    Parametry:
        cache: úložiště nastavení (KlineCache) s metodou get_setting.
    """
    return set_language(await cache.get_setting(LANGUAGE_SETTING, DEFAULT_LANGUAGE))


def locale_modules() -> list[tuple[str, dict[str, str]]]:
    """Dvojice (název modulu, slovník EN) všech modulů balíčku tickview.locales podle abecedy."""
    import tickview.locales as locales

    modules = []
    for info in sorted(pkgutil.iter_modules(locales.__path__), key=lambda item: item.name):
        module = importlib.import_module(f"{locales.__name__}.{info.name}")
        modules.append((info.name, getattr(module, "EN", {})))
    return modules


@cache
def catalog() -> dict[str, str]:
    """Anglický slovník (český text => překlad) sloučený ze všech modulů tickview.locales.

    Načte se jednou za běh procesu.
    """
    merged: dict[str, str] = {}
    for _name, words in locale_modules():
        merged.update(words)
    return merged


def tr(text: str) -> str:
    """Přeloží český text do aktuálního jazyka (bez překladu vrátí originál).

    Parametry:
        text: český zdrojový text (klíč slovníku).
    """
    if _language == DEFAULT_LANGUAGE or not text:
        return text
    return catalog().get(text, text)


def tr_options(options) -> dict:
    """Volby výběru s popisky přeloženými do jazyka rozhraní (hodnoty beze změny).

    Parametry:
        options: slovník nebo dvojice (hodnota, český popisek označený N_).
    """
    return {value: tr(label) for value, label in dict(options).items()}


def plural(count: int, one: str, few: str, many: str) -> str:
    """Počet se správným tvarem slova: 1 graf, 2–4 grafy, 5+ grafů (i 0 grafů).

    Tvary se předávají česky (označené N_) a zvolený se přeloží do jazyka UI;
    anglický slovník má pro 2–4 i 5+ stejný tvar množného čísla.
    """
    word = one if count == 1 else few if 2 <= count <= 4 else many
    return f"{count} {tr(word)}"


def N_(text: str) -> str:
    """Označí text k překladu bez okamžitého překladu (konstanty modulů).

    Text se přeloží až při zobrazení voláním `tr`; značka slouží kontrole
    úplnosti překladů (tools/i18n_check.py).
    """
    return text


def trc(context: str, text: str) -> str:
    """Přeloží text s kontextem (stejný český text s různými významy).

    Klíč slovníku je "kontext|text", bez něj se použije obyčejný překlad textu.

    Parametry:
        context: krátký anglický název kontextu, např. "weekday".
        text: český zdrojový text.
    """
    if _language == DEFAULT_LANGUAGE:
        return text
    return catalog().get(f"{context}|{text}") or tr(text)


def NC_(context: str, text: str) -> str:
    """Označí text s kontextem k překladu bez okamžitého překladu (viz trc a N_)."""
    return text


def js_bootstrap() -> str:
    """Vrátí <script> pro hlavičku stránky: jazyk a slovník pro funkci tvTr v JS."""
    return _js_bootstrap(_language)


@cache
def _js_bootstrap(language: str) -> str:
    """Skript s jazykem a slovníkem JS (tickview/locales/js.py) – sestaví se jednou na jazyk.

    Ostatní texty přicházejí z Pythonu už přeložené, proto se posílá jen slovník
    JS. Česky se slovník neposílá (tvTr vrací originál); `</` se escapuje, aby
    text slovníku nemohl ukončit značku <script>.
    """
    from tickview.locales import js

    words = js.EN if language != DEFAULT_LANGUAGE else {}
    payload = json.dumps(words, ensure_ascii=False).replace("</", "<\\/")
    return (
        f"<script>window.TV_LANG={json.dumps(language)};window.TV_I18N={payload};"
        f"document.documentElement.lang={json.dumps(language)};</script>"
    )
