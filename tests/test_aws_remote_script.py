"""Guards for the embedded AWS SSM remote-collection script.

``REMOTE_COLLECT_SCRIPT`` is shipped verbatim to remote EC2 instances via
``python3 -c '<script>'``. Nothing on the remote imports the agent package, so
the script must be fully self-contained: it may only use its own imports and
Python builtins, and it must actually *call* ``collect()`` (otherwise SSM gets
an empty payload and every fleet snapshot fails to parse).

These tests reproduce two real defects found in review:
  1. The script defined ``collect()`` but never invoked it → empty stdout.
  2. The ``except`` handlers called ``log_suppressed(..., level=logging.DEBUG)``
     — neither name exists on the remote → NameError the moment any /proc read
     failed, crashing the whole snapshot instead of degrading gracefully.
"""
from __future__ import annotations

import ast
import builtins
import json
import subprocess
import sys

from ipracticom_sweeper.fleet.aws_connector import (
    REMOTE_COLLECT_SCRIPT,
    _build_collect_script,
)


def _undefined_globals(src: str) -> set[str]:
    """Names loaded in ``src`` that are neither builtins, imports, nor defined
    anywhere in the script. A non-empty result means the script references a
    name that will not exist on the remote host."""
    tree = ast.parse(src)
    defined: set[str] = set(dir(builtins))
    loaded: set[str] = set()

    class V(ast.NodeVisitor):
        def visit_FunctionDef(self, n: ast.FunctionDef) -> None:
            defined.add(n.name)
            self.generic_visit(n)

        def visit_Import(self, n: ast.Import) -> None:
            for a in n.names:
                defined.add((a.asname or a.name).split(".")[0])

        def visit_ImportFrom(self, n: ast.ImportFrom) -> None:
            for a in n.names:
                defined.add(a.asname or a.name)

        def visit_arg(self, n: ast.arg) -> None:
            defined.add(n.arg)
            self.generic_visit(n)

        def visit_Name(self, n: ast.Name) -> None:
            if isinstance(n.ctx, ast.Store):
                defined.add(n.id)
            else:
                loaded.add(n.id)

    V().visit(tree)
    return loaded - defined


def test_remote_script_compiles() -> None:
    compile(REMOTE_COLLECT_SCRIPT, "<remote_collect_script>", "exec")


def test_remote_script_has_no_undefined_names() -> None:
    """Guards against handlers referencing agent-only names (log_suppressed,
    logging, structlog, ...) that do not exist on the remote host.

    The raw script is a template with a ``__FS_ENABLED__`` placeholder that is
    substituted before it ever ships, so we check the *materialised* forms that
    SSM actually sends — both FreeSWITCH on and off.
    """
    for fs_enabled in (True, False):
        offenders = _undefined_globals(_build_collect_script(fs_enabled))
        assert not offenders, (
            f"remote script (fs_enabled={fs_enabled}) references names "
            f"unavailable on EC2: {sorted(offenders)}"
        )


def test_remote_script_invokes_collect_and_emits_json() -> None:
    """Running the script exactly as SSM does must print one JSON object.

    A missing top-level ``collect()`` call makes this return an empty string.
    Runs the materialised (placeholder-substituted) script — the one that ships.
    """
    r = subprocess.run(
        [sys.executable, "-c", _build_collect_script(freeswitch_enabled=False)],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"remote script exited {r.returncode}: {r.stderr!r}"
    assert r.stdout.strip(), "remote script produced no output (collect() not called?)"
    data = json.loads(r.stdout)
    for key in ("host", "uptime_seconds", "load", "memory", "disk",
                "top_processes", "failed_units"):
        assert key in data, f"snapshot missing key {key!r}"
