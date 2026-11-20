"""Kontrola úplnosti anglických překladů UI (spuštění: python tools/i18n_check.py [soubor…]).

Hlásí:
- české texty (s diakritikou) mimo tr()/N_() v Pythonu a mimo tvTr() v JS,
- klíče tr()/N_()/tvTr() bez anglického překladu ve slovnících tickview/locales,
- překlady, které nemají stejné zástupné symboly {jmeno} jako originál,
- volání tr()/tvTr() s f-stringem nebo šablonou (klíč musí být pevný text).
Bez parametrů kontroluje celý balíček tickview; s cestami jen zadané soubory
(chybějící překlady i tak hledá podle klíčů v zadaných souborech).
Výjimky: docstringy, texty v logovacích voláních (log.info, …), SQL příkazy (komentáře
ve schématu) a testy indikátorů.
Pozor: české texty bez diakritiky (např. „Barva“) kontrola nepozná – ty je nutné
projít ručně.
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tickview.i18n import catalog, locale_modules  # noqa: E402

PACKAGE = ROOT / "tickview"
# České znaky s diakritikou – podle nich se poznají nepřeložené texty
CZECH = re.compile(r"[áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ]")
# Zástupné symboly str.format, např. {symbol} nebo {count:d}
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)[^{}]*\}")
# Metody loggeru, jejichž texty se nepřekládají (jdou jen do konzole)
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
# Volání, jejichž první parametr je klíč překladu, a volání s kontextem (kontext, text)
TRANSLATE_FUNCS = {"tr", "N_"}
CONTEXT_FUNCS = {"trc", "NC_"}
# SQL příkazy (schéma databáze s komentáři „-- …“) se nepřekládají
SQL = re.compile(r"^\s*(CREATE|SELECT|INSERT|UPDATE|DELETE|ALTER|PRAGMA|WITH|DROP)\b", re.I)
# Soubory JS s UI (vendored knihovna grafu se nekontroluje)
JS_SKIP = {"lightweight-charts.standalone.production.js", "i18n.js"}


def python_files() -> list[Path]:
    """Python soubory balíčku kromě slovníků překladů, testů indikátorů a i18n.py.

    i18n.py obsahuje názvy jazyků záměrně v jejich vlastním jazyce („Čeština“).
    """
    return [
        path
        for path in sorted(PACKAGE.rglob("*.py"))
        if "locales" not in path.parts and "tests" not in path.parts and path.name != "i18n.py"
    ]


def js_files() -> list[Path]:
    """JS soubory UI (statické skripty a komponenty) kromě vendored knihovny."""
    return [p for p in sorted(PACKAGE.rglob("*.js")) if p.name not in JS_SKIP]


def call_name(node: ast.Call) -> str:
    """Jméno volané funkce nebo metody (tr, N_, info, …), jinak prázdný řetězec."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def check_python(path: Path) -> tuple[list[str], set[str]]:
    """Zkontroluje jeden Python soubor.

    Vrací (seznam problémů, množinu klíčů překladu nalezených v souboru).
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    problems: list[str] = []
    keys: set[str] = set()
    exempt: set[int] = set()

    # Docstringy modulů, tříd a funkcí se nepřekládají
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                exempt.add(id(body[0].value))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        # Texty logovacích volání jdou jen do konzole
        if name in LOG_METHODS and isinstance(node.func, ast.Attribute):
            for sub in ast.walk(node):
                exempt.add(id(sub))
            continue
        # Překlad s kontextem: klíč "kontext|text"
        if name in CONTEXT_FUNCS and len(node.args) >= 2:
            context, text = node.args[0], node.args[1]
            if all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in (context, text)):
                keys.add(f"{context.value}|{text.value}")
                exempt.update({id(context), id(text)})
            continue
        if name in TRANSLATE_FUNCS and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                keys.add(first.value)
                exempt.add(id(first))
            elif isinstance(first, ast.JoinedStr):
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}() s f-stringem – použijte {name}(\"…{{x}}…\").format(x=…)")
                for sub in ast.walk(first):
                    exempt.add(id(sub))

    # Zbylé české texty jsou nepřeložené
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in exempt:
            if CZECH.search(node.value) and not SQL.match(node.value):
                text = node.value.replace("\n", "\\n")
                problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: nepřeložený text: {text[:80]!r}")
    return problems, keys


# Řetězec v JS: '…', "…" nebo `…` (bez víceřádkových šablon s ${…} uvnitř řetězce)
JS_STRING = re.compile(r"""(['"`])((?:\\.|(?!\1).)*?)\1""", re.S)
JS_CALL = re.compile(r"tvTr\(\s*$")


# Řetězec JS (zachová se) nebo komentář // … / /* … */ (nahradí se mezerami)
JS_STRING_OR_COMMENT = re.compile(r"""(['"`])(?:\\.|(?!\1).)*?\1|//[^\n]*|/\*.*?\*/""", re.S)


def strip_js_comments(source: str) -> str:
    """Nahradí komentáře JS mezerami (řádky i pozice textu zůstanou na svých místech)."""
    return JS_STRING_OR_COMMENT.sub(
        lambda match: match.group(0) if match.group(1) else re.sub(r"[^\n]", " ", match.group(0)),
        source,
    )


def check_js(path: Path) -> tuple[list[str], set[str]]:
    """Zkontroluje jeden JS soubor (české řetězce mimo tvTr a klíče tvTr)."""
    source = strip_js_comments(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    keys: set[str] = set()
    for match in JS_STRING.finditer(source):
        quote, text = match.group(1), match.group(2)
        before = source[max(0, match.start() - 40):match.start()]
        line = source.count("\n", 0, match.start()) + 1
        inside_call = bool(JS_CALL.search(before))
        if inside_call:
            if quote == "`" and "${" in text:
                problems.append(f"{path.relative_to(ROOT)}:{line}: tvTr() se šablonou ${{…}} – použijte pevný text a .replace()")
            else:
                # Escapované uvozovky v JS (\' a \") jsou za běhu obyčejné uvozovky
                keys.add(text.replace("\\'", "'").replace('\\"', '"'))
            continue
        if CZECH.search(text):
            problems.append(f"{path.relative_to(ROOT)}:{line}: nepřeložený text v JS: {text[:80]!r}")
    return problems, keys


def check_translations(keys: set[str], words: dict[str, str] | None = None) -> list[str]:
    """Klíče bez překladu a překlady s jinými zástupnými symboly než originál.

    Parametry:
        keys: klíče překladu nalezené v kódu.
        words: slovník, ve kterém se hledají (výchozí = celý katalog).
    """
    words = catalog() if words is None else words
    problems = []
    for key in sorted(keys):
        if key not in words:
            problems.append(f"chybí překlad: {key!r}")
            continue
        if set(PLACEHOLDER.findall(key)) != set(PLACEHOLDER.findall(words[key])):
            problems.append(f"jiné zástupné symboly v překladu: {key!r} => {words[key]!r}")
    return problems


def check_conflicts() -> list[str]:
    """Stejný český text přeložený v různých modulech slovníku různě."""
    seen: dict[str, tuple[str, str]] = {}
    problems = []
    for name, words in locale_modules():
        for key, value in words.items():
            if key in seen and seen[key][1] != value:
                problems.append(f"různé překlady {key!r}: {seen[key][0]}={seen[key][1]!r}, {name}={value!r}")
            seen.setdefault(key, (name, value))
    return problems


def run(paths: list[Path] | None = None) -> list[str]:
    """Spustí kontrolu zadaných souborů (bez zadání celého balíčku) a vrátí problémy."""
    targets = paths or [*python_files(), *js_files()]
    problems: list[str] = []
    keys: set[str] = set()
    js_keys: set[str] = set()
    for path in targets:
        if path.suffix == ".js":
            file_problems, file_keys = check_js(path)
            js_keys |= file_keys
        else:
            file_problems, file_keys = check_python(path)
            keys |= file_keys
        problems += file_problems
    # Texty JS musí být ve slovníku tickview/locales/js.py – do stránky se posílá jen ten
    js_words = dict(locale_modules()).get("js", {})
    return problems + check_translations(keys) + check_translations(js_keys, js_words) + check_conflicts()


def main() -> int:
    """Vypíše problémy; návratový kód 1, když nějaké jsou."""
    paths = [Path(arg).resolve() for arg in sys.argv[1:]] or None
    problems = run(paths)
    for problem in problems:
        print(problem)
    print(f"Problémů: {len(problems)}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
