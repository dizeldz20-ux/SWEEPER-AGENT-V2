"""Tests for AIDE file integrity monitor."""
from __future__ import annotations
from ipracticom_sweeper.monitor.aide_check import (
    parse_aide_output,
    AideReport,
)


SAMPLE_AIDE_OK = """\
AIDE, version 0.18.4

### All files match AIDE database. Looks okay!
"""

SAMPLE_AIDE_CHANGES = """\
AIDE, version 0.18.4

### Files added to the database:
+ /etc/nginx/sites-available/mysite.conf
+ /root/.ssh/authorized_keys

### Files removed from the database:
- /tmp/old.log

### Files changed:
f ... .b... /etc/passwd
f ... .b... /etc/shadow
"""


def test_parse_aide_output_no_changes():
    """No changes = clean status."""
    report = parse_aide_output(SAMPLE_AIDE_OK)
    assert report.added == 0
    assert report.removed == 0
    assert report.changed == 0
    assert report.changed_files == []
    assert report.parse_error is None


def test_parse_aide_output_counts_changes():
    """Changes are counted and listed."""
    report = parse_aide_output(SAMPLE_AIDE_CHANGES)
    assert report.added == 2
    assert report.removed == 1
    assert report.changed == 2
    assert "/etc/passwd" in report.changed_files
    assert "/etc/shadow" in report.changed_files
    assert "/etc/nginx/sites-available/mysite.conf" in report.added_files


def test_parse_aide_handles_empty():
    """Empty output = zeros."""
    report = parse_aide_output("")
    assert report.added == 0
    assert report.removed == 0
    assert report.changed == 0


def test_parse_aide_tracks_added_files():
    """Added files list is populated correctly."""
    report = parse_aide_output(SAMPLE_AIDE_CHANGES)
    assert "/root/.ssh/authorized_keys" in report.added_files
    assert len(report.added_files) == 2


def test_aide_report_to_dict():
    """to_dict() returns serializable dict."""
    report = parse_aide_output(SAMPLE_AIDE_CHANGES)
    d = report.to_dict()
    assert "added" in d
    assert "removed" in d
    assert "changed" in d
    assert "added_files" in d
    assert "changed_files" in d
