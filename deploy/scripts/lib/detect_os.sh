# Detect OS family and set OS_FAMILY + OS_PRETTY globals.
#
# Sets:
#   OS_FAMILY  ∈ {linux, macos, windows}
#   OS_PRETTY  human-readable string like "Ubuntu 22.04" / "macOS 14.5 (arm64)"
#
# Detection order matters because WSL identifies as both linux AND windows:
#   1. WSL (via /proc/version + WSL env var)
#   2. Windows (via $OSTYPE = "msys" / "cygwin" / "win32" — git-bash)
#   3. macOS (via uname)
#   4. Linux (fallback)

detect_os() {
    # 1. WSL — Microsoft reports it as linux but with WSL signature
    if [[ -n "${WSL_DISTRO_NAME:-}" ]] || [[ -n "${WSLENV:-}" ]]; then
        OS_FAMILY="linux"
        OS_PRETTY="WSL ($(grep -m1 PRETTY /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || echo unknown))"
        return
    fi

    # 2. Windows native shells (git-bash, msys2, cygwin)
    case "${OSTYPE:-}" in
        msys*|cygwin*|win32*)
            OS_FAMILY="windows"
            OS_PRETTY="Windows ($(uname -s))"
            return
            ;;
    esac

    # 3. macOS
    if [[ "$(uname -s 2>/dev/null)" == "Darwin" ]]; then
        OS_FAMILY="macos"
        local ver
        ver="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
        local arch
        arch="$(uname -m)"
        OS_PRETTY="macOS $ver ($arch)"
        return
    fi

    # 4. Linux (fallback)
    if [[ "$(uname -s 2>/dev/null)" == "Linux" ]]; then
        OS_FAMILY="linux"
        if [[ -r /etc/os-release ]]; then
            local name ver
            name="$(. /etc/os-release 2>/dev/null && echo "${NAME:-Linux}")"
            ver="$(. /etc/os-release 2>/dev/null && echo "${VERSION:-}")"
            OS_PRETTY="$name $ver"
        else
            OS_PRETTY="Linux $(uname -r)"
        fi
        return
    fi

    # Unknown — fail loud
    echo "❌ [detect_os] unsupported OS: OSTYPE=${OSTYPE:-unset}, uname=$(uname -s)" >&2
    exit 1
}
