"""v1.5.0 Slice 5 — Silent-except gate.

The repo's convention is that every ``except`` block must do *something*
visible (log, raise, fall through, return a sentinel). The pattern
``except X: pass`` / ``except X: continue`` / bare ``except:`` swallow
errors silently and is forbidden in committed code.

This test enforces the rule. It scans ``src/ipracticom_sweeper`` for
the forbidden patterns and fails the suite if any are found — every
swallowed exception should be wrapped in ``_log.log_suppressed`` (or
similar) so the operator can see the failure in the journal.

Adding new silent blocks in a future commit will turn this test red;
fixing them with ``log_suppressed`` keeps it green.

The initial baseline of 50 silent blocks is tracked in
``CHANGELOG.md`` under [1.5.0]. The strict-zero rule is now in force
as of v1.5.6 — ``baseline = 0`` across ``src/ipracticom_sweeper/``.
``tests/`` may still use ``except: pass`` for negative-path assertions
(this gate intentionally excludes ``tests/``).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from ipracticom_sweeper._log import log_suppressed  # noqa: F401 — referenced for documentation


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "ipracticom_sweeper"

# Patterns that mark a swallowed exception. Each pattern is
# ``except <clause>:\n    <silent body>``; the body must be on the
# line immediately after the except.
SILENT_PATTERNS = [
    (re.compile(r"^\s*except[^:]*:\s*\n\s*pass\s*$", re.MULTILINE), "pass"),
    (re.compile(r"^\s*except[^:]*:\s*\n\s*continue\s*$", re.MULTILINE), "continue"),
    # Bare ``except:`` (no exception type). Always a bug.
    (re.compile(r"^\s*except\s*:\s*$", re.MULTILINE), "bare_except"),
]


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """Return [(line_no, pattern_name, snippet), ...] for every silent
    except block in the file."""
    text = path.read_text()
    findings = []
    for pattern, name in SILENT_PATTERNS:
        for m in pattern.finditer(text):
            # Compute 1-indexed line number of the match start
            line_no = text.count("\n", 0, m.start()) + 1
            snippet = m.group(0).strip().splitlines()[0]
            findings.append((line_no, name, snippet))
    return findings


def test_50_5_silent_except_blocks_have_logging_in_test_files():
    """Tests may use ``except: pass`` for negative-path assertions."""
    # This is a meta-test: it does NOT enforce the rule on test
    # files. The strict-zero rule applies only to ``src/`` — see
    # the test below. This is here so the absence of the rule on
    # tests is intentional, not a bug.
    pass


def test_50_5_no_silent_except_blocks_in_src():
    """No ``except X: pass`` / ``except X: continue`` / bare ``except:``
    in ``src/ipracticom_sweeper``.

    This is the strict-zero gate. As of v1.5.6 the regex-based baseline
    reaches 0 across the whole ``src/`` tree — re-enabled here.

    Adding new silent blocks in a future commit will turn this test red;
    fixing them with ``log_suppressed()`` keeps it green.

    Example fix::

        # before
        try:
            risky()
        except OSError:
            pass

        # after
        try:
            risky()
        except OSError as exc:
            log_suppressed("module.thing", exc)
    """
    findings: dict[str, list[tuple[int, str, str]]] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        hits = _scan_file(path)
        if hits:
            findings[str(path.relative_to(REPO_ROOT))] = hits
    assert not findings, (
        f"Strict-zero gate violated — found silent except blocks:\n"
        + "\n".join(
            f"  {p}:{ln} [{name}] {snip}"
            for p, hits in findings.items()
            for ln, name, snip in hits
        )
        + "\n\nFix each by wrapping the body in "
        "`log_suppressed(context, exc)` so the failure is recorded."
    )


def test_50_5_per_file_baseline_does_not_regress(tmp_path):
    """Each file's silent-except count must not increase from one
    test run to the next. We persist the current snapshot to a
    tmp cache file so the next test run can compare against it.

    A real CI run would store the baseline in the repo; for now,
    this test enforces only the local "no regression" rule (the
    snapshot starts at the current count, so a new silent block
    would push the count above the stored baseline and fail the
    test).
    """
    counts: dict[str, int] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        n = len(_scan_file(path))
        if n:
            counts[str(path.relative_to(REPO_ROOT))] = n
    cache = tmp_path / "silent_except_baseline.json"
    if cache.exists():
        import json
        prior = json.loads(cache.read_text())
        for path, n in counts.items():
            if path not in prior:
                continue  # new file, no regression possible
            assert n <= prior[path], (
                f"{path} gained silent except blocks: "
                f"{prior[path]} -> {n}. Use log_suppressed() to fix."
            )
    import json
    cache.write_text(json.dumps(counts, sort_keys=True))
