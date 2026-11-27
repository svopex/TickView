#!/usr/bin/env bash
# Spuštění TickView na macOS/Linux: najde podporovaný Python (3.12–3.13), a když
# chybí, stáhne samostatný Python do složky projektu .python (bez administrátorských
# práv a bez Homebrew), připraví .venv, doinstaluje závislosti a předá případné
# parametry do main.py (např. ./run.sh --port 8090 --open).

set -euo pipefail

# Pracujeme vždy v kořenu projektu, i když skript spustíme odjinud
cd "$(dirname "$0")"

# Samostatný Python k automatické instalaci: sestavení python-build-standalone
# (Astral, používá ho i uv) – vydání a verze; při změně ověřit, že vydání
# obsahuje soubory cpython-<verze>+<vydání>-<platforma>-install_only.tar.gz
PBS_RELEASE="20261026"
PBS_PYTHON="3.13.18"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE"
# Složka s Pythonem staženým tímto skriptem (necommituje se)
LOCAL_PYTHON=".python/bin/python3"

# Podporovanou verzi ověřuje tools/python_supported.py (tam je i rozsah verzí)
supported() {
    "$1" tools/python_supported.py >/dev/null 2>&1
}

# Najde podporovaný Python a vypíše jeho příkaz (nic = nenalezen); konkrétní
# verze mají přednost před python3, který může ukazovat na příliš novou verzi
find_python() {
    local candidate
    for candidate in python3.13 python3.12 python3 "$LOCAL_PYTHON"; do
        if command -v "$candidate" >/dev/null 2>&1 && supported "$candidate"; then
            echo "$candidate"
            return
        fi
    done
}

# Stáhne URL do souboru (curl, jinak wget – minimální Linux nemusí mít curl)
download() {
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -q --show-progress -O "$2" "$1"
    else
        echo "Chybí curl i wget – nainstalujte jeden z nich." >&2
        return 1
    fi
}

# Nainstaluje samostatný Python do .python pro tento projekt; chybu vypíše
# a vrátí nenulový kód (nepodporovaná platforma, stažení, kontrolní součet).
# Tělo běží v subshellu, aby dočasnou složku spolehlivě uklidil trap EXIT
install_python() (
    local arch platform asset tmp expected actual
    # Architektura procesoru podle pojmenování sestavení
    case "$(uname -m)" in
        arm64|aarch64) arch="aarch64" ;;
        x86_64|amd64) arch="x86_64" ;;
        *) echo "Automatická instalace Pythonu nepodporuje procesor $(uname -m)." >&2; return 1 ;;
    esac
    # Systém: macOS, nebo Linux s glibc (sestavení pro musl/Alpine zde nepoužíváme)
    case "$(uname -s)" in
        Darwin) platform="$arch-apple-darwin" ;;
        Linux)
            if ldd --version 2>&1 | grep -qi musl; then
                echo "Automatická instalace Pythonu nepodporuje Linux s musl (Alpine)." >&2
                return 1
            fi
            platform="$arch-unknown-linux-gnu"
            ;;
        *) echo "Automatická instalace Pythonu nepodporuje systém $(uname -s)." >&2; return 1 ;;
    esac
    asset="cpython-$PBS_PYTHON+$PBS_RELEASE-$platform-install_only.tar.gz"

    # Stažení do dočasné složky v projektu (stejný disk jako .python, smaže se
    # i při chybě) a kontrola SHA-256 podle souboru SHA256SUMS z téhož vydání
    tmp="$(mktemp -d .python-download.XXXXXX)" || return 1
    trap 'rm -rf "$tmp"' EXIT
    echo "Stahuji Python $PBS_PYTHON ($platform)…"
    download "$PBS_URL/SHA256SUMS" "$tmp/SHA256SUMS" || return 1
    # Znak + se v URL kóduje jako %2B
    download "$PBS_URL/${asset//+/%2B}" "$tmp/python.tar.gz" || return 1
    expected="$(awk -v name="$asset" '$2 == name { print $1 }' "$tmp/SHA256SUMS")"
    # macOS má shasum, Linux sha256sum
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$tmp/python.tar.gz" | cut -d' ' -f1)"
    else
        actual="$(shasum -a 256 "$tmp/python.tar.gz" | cut -d' ' -f1)"
    fi
    if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
        echo "Kontrolní součet staženého Pythonu nesouhlasí – instalaci ruším." >&2
        return 1
    fi

    # Archiv obsahuje složku python/ – rozbalí se vedle a přesune na .python
    tar -xzf "$tmp/python.tar.gz" -C "$tmp" || return 1
    rm -rf .python
    mv "$tmp/python" .python || return 1
    echo "Python $PBS_PYTHON nainstalován do složky .python."
)

# .venv s nepodporovaným Pythonem (např. 3.14, když je to výchozí python3,
# nebo po smazání .python) se vytvoří znovu
if [ -x .venv/bin/python ] && ! supported .venv/bin/python; then
    echo "Virtuální prostředí .venv má nepodporovanou verzi Pythonu, vytvářím ho znovu…"
    rm -rf .venv
fi

# Virtuální prostředí vytvoříme při prvním spuštění; bez vhodného Pythonu ho
# nejdřív nainstalujeme do .python
if [ ! -x .venv/bin/python ]; then
    python="$(find_python)"
    if [ -z "$python" ]; then
        echo "Nenašel jsem Python 3.12 nebo 3.13, instaluji Python $PBS_PYTHON jen pro tento projekt…"
        install_python || true
        python="$(find_python)"
    fi
    if [ -z "$python" ]; then
        echo "Python se nepodařilo nainstalovat automaticky."
        echo "macOS: brew install python@3.13, nebo instalátor z https://www.python.org/downloads/"
        echo "Linux: nainstalujte python3.13 nebo python3.12 (a balíček venv) z vaší distribuce."
        exit 1
    fi
    echo "Vytvářím virtuální prostředí .venv ($("$python" -V))…"
    "$python" -m venv .venv
fi

# Závislosti se doinstalují jen při změně requirements.txt (společné s run-windows.bat)
.venv/bin/python tools/ensure_requirements.py

# Spuštění aplikace; všechny argumenty skriptu jdou přímo do main.py
exec .venv/bin/python main.py "$@"
