# Verify Python ≥ 3.11 is available. Sets globals:
#   PYTHON_BIN  absolute path to the python binary to use
#   PY_VERSION  "3.11.0", "3.12.3", etc.
#
# Strategy:
#   1. Try `python3` on PATH
#   2. Try common alternative locations (/usr/bin/python3, pyenv shims)
#   3. If still not found and on macOS → prompt to install (or error in --auto)
#   4. If on Linux without python3.11 → try `python3.11` explicitly, else error
#
# Does NOT auto-install Python — that requires sudo or pyenv and is
# interactive. The installer surfaces a clear message instead.

MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11

python_check() {
    local candidate=""
    local version=""

    # Try candidates in order
    for c in python3 python3.11 python3.12 python3.13; do
        if command -v "$c" >/dev/null 2>&1; then
            candidate="$(command -v "$c")"
            version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null || echo "")"
            if [[ -n "$version" ]]; then
                break
            fi
        fi
    done

    # Common absolute paths on Linux distros
    if [[ -z "$candidate" ]]; then
        for p in /usr/bin/python3 /usr/bin/python3.11 /usr/bin/python3.12 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
            if [[ -x "$p" ]]; then
                candidate="$p"
                version="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>/dev/null || echo "")"
                if [[ -n "$version" ]]; then
                    break
                fi
            fi
        done
    fi

    if [[ -z "$candidate" || -z "$version" ]]; then
        echo "❌ [python_check] Python 3.11+ not found on PATH." >&2
        echo "" >&2
        echo "  Install with:" >&2
        echo "    Linux:   sudo apt install python3.11 python3.11-venv  (Debian/Ubuntu)" >&2
        echo "             sudo dnf install python3.11                  (RHEL/Fedora)" >&2
        echo "    macOS:   brew install python@3.11" >&2
        echo "             or: pyenv install 3.11" >&2
        echo "    Windows: https://python.org/downloads/  (3.11+)" >&2
        return 1
    fi

    # Version check
    local major minor
    major="$(echo "$version" | cut -d. -f1)"
    minor="$(echo "$version" | cut -d. -f2)"

    if [[ "$major" -lt "$MIN_PYTHON_MAJOR" ]] || \
       { [[ "$major" -eq "$MIN_PYTHON_MAJOR" ]] && [[ "$minor" -lt "$MIN_PYTHON_MINOR" ]]; }; then
        echo "❌ [python_check] Found Python $version at $candidate, but need ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+" >&2
        echo "" >&2
        echo "  Install a newer Python or use pyenv to manage multiple versions." >&2
        return 1
    fi

    PYTHON_BIN="$candidate"
    PY_VERSION="$version"
    export PYTHON_BIN PY_VERSION

    # Sanity check: can we import venv?
    if ! "$PYTHON_BIN" -c "import venv, ensurepip" >/dev/null 2>&1; then
        echo "❌ [python_check] $candidate lacks 'venv' module." >&2
        echo "  Install: python3.11-venv (Debian/Ubuntu) or python3-virtualenv." >&2
        return 1
    fi

    return 0
}
