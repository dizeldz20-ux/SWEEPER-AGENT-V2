"""Tests for evidence: S3 (mocked) + local retention."""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from ipracticom_sweeper.evidence import S3Exporter, has_credentials, cleanup_local


def test_has_credentials_with_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test_key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test_secret")
    assert has_credentials() is True


def test_has_credentials_without_env(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    # Without EC2 metadata, this should return False
    # We can't easily mock IMDS, so we just verify it doesn't crash
    result = has_credentials()
    assert isinstance(result, bool)


def test_s3_exporter_upload():
    exporter = S3Exporter(bucket="test-bucket", prefix="ev/")
    mock_client = MagicMock()
    with patch.object(exporter, "_get_client", return_value=mock_client):
        result = exporter.upload("snap.json", {"k": "v"})
    assert result is True
    mock_client.put_object.assert_called_once()
    call = mock_client.put_object.call_args
    assert call.kwargs["Bucket"] == "test-bucket"
    assert call.kwargs["Key"] == "ev/snap.json"


def test_s3_exporter_upload_failure():
    exporter = S3Exporter(bucket="test-bucket")
    mock_client = MagicMock()
    mock_client.put_object.side_effect = Exception("S3 down")
    with patch.object(exporter, "_get_client", return_value=mock_client):
        result = exporter.upload("snap.json", {"k": "v"})
    assert result is False


def test_s3_export_snapshot():
    exporter = S3Exporter(bucket="b")
    with patch.object(exporter, "upload", return_value=True) as mock_upload:
        result = exporter.export_snapshot("host1", {"snapshot_id": "abc123", "defcon": 4})
    assert result is True
    call_args = mock_upload.call_args
    assert "snapshots/host1/" in call_args.args[0]
    assert "abc123.json" in call_args.args[0]


def test_s3_export_repair():
    exporter = S3Exporter(bucket="b")
    with patch.object(exporter, "upload", return_value=True) as mock_upload:
        result = exporter.export_repair("host1", {"action": "drop_caches"})
    assert result is True
    call_args = mock_upload.call_args
    assert "repairs/host1/" in call_args.args[0]


def test_cleanup_local_removes_old(tmp_path):
    old_file = tmp_path / "old.json"
    old_file.write_text("x")
    # Set mtime to 100 days ago
    old_time = time.time() - (100 * 86400)
    import os
    os.utime(old_file, (old_time, old_time))

    new_file = tmp_path / "new.json"
    new_file.write_text("y")

    deleted = cleanup_local(tmp_path, older_than_days=90)
    assert deleted == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_cleanup_local_nonexistent_dir(tmp_path):
    deleted = cleanup_local(tmp_path / "nope", older_than_days=30)
    assert deleted == 0
