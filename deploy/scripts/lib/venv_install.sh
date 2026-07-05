# Create venv at $1 and pip install -e the sweeper from $2.
#
# Args:
#   $1  venv directory (e.g. ~/.ipracticom-sweeper/venv)
#   $2  repo directory (the dir containing pyproject.toml)
#
# Idempotent: reuses existing venv if it points to the same Python version.

venv_install() {
    local venv_dir="$1"
    local repo_dir="$2"

    if [[ ! -d "$repo_dir" ]] || [[ ! -f "$repo_dir/pyproject.toml" ]]; then
        echo "❌ [venv_install] repo not found at $repo_dir (missing pyproject.toml)" >&2
        return 1
    fi

    # If venv exists, verify the Python version matches
    if [[ -x "$venv_dir/bin/python" ]]; then
        local existing_version
        existing_version="$("$venv_dir/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "")"
        local target_version
        target_version="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        if [[ "$existing_version" != "$target_version" ]]; then
            echo "⚠️  [venv_install] existing venv is Python $existing_version, target is $target_version — recreating" >&2
            rm -rf "$venv_dir"
        fi
    fi

    if [[ ! -x "$venv_dir/bin/python" ]]; then
        echo "  creating venv at $venv_dir"
        "$PYTHON_BIN" -m venv "$venv_dir"
    fi

    # Upgrade pip + install sweeper + supervisor
    "$venv_dir/bin/python" -m pip install --quiet --upgrade pip wheel setuptools

    # Install sweeper in editable mode
    "$venv_dir/bin/pip" install --quiet -e "$repo_dir"

    # Install supervisor (cross-platform service manager)
    "$venv_dir/bin/pip" install --quiet supervisor

    # Verify
    "$venv_dir/bin/python" -c "
from ipracticom_sweeper import __version__ if hasattr(__import__('ipracticom_sweeper'), '__version__') else 'unknown'
import supervisor
print('sweeper + supervisor installed OK')
"
}
