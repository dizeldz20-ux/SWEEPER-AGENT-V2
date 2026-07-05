"""Real S3 upload integration test: boto3 talks to in-process mock S3.

This is a true integration test — no mocking of boto3, no mocking of
the exporter. Only the S3 server is mocked. The full request path is
exercised: boto3 builds the signed request, sends HTTP to the mock,
the mock stores the bytes, and the response is parsed back by boto3.
"""
import json
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ipracticom_sweeper.evidence import S3Exporter
from ipracticom_sweeper.evidence.signer import ManifestSigner

from .s3_mock import S3MockServer


def _make_exporter(s3_url: str, bucket: str):
    """Build a boto3 client pointed at the mock and wrap it in S3Exporter."""
    s3 = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test_key",
        aws_secret_access_key="test_secret",
        endpoint_url=s3_url,
        config=Config(signature_version="s3v4"),
    )
    exporter = S3Exporter(bucket=bucket, prefix="evidence/")
    exporter._client = s3  # inject the test client
    return exporter, s3


def test_real_put_object_round_trip():
    with S3MockServer() as s3:
        exporter, _ = _make_exporter(s3.url, "test-bucket")
        snapshot = {"defcon": 4, "host": "h1", "ts": 1700000000.0, "problems": []}
        result = exporter.upload("snapshots/h1/test.json", snapshot)
        assert result is True

        # Verify it really landed in the mock storage
        keys = s3.stored_keys("test-bucket")
        assert len(keys) == 1
        assert "snapshots/h1/test.json" in keys[0]

        # And the body is the JSON serialization of our snapshot
        body = s3.stored_body("test-bucket", keys[0])
        parsed = json.loads(body)
        assert parsed["defcon"] == 4
        assert parsed["host"] == "h1"


def test_real_export_snapshot_uses_correct_key():
    with S3MockServer() as s3:
        exporter, _ = _make_exporter(s3.url, "evidence-bucket")
        snapshot = {"snapshot_id": "snap-2024-001", "defcon": 2, "host": "web-01"}
        result = exporter.export_snapshot("web-01", snapshot)
        assert result is True

        keys = s3.stored_keys("evidence-bucket")
        assert len(keys) == 1
        # Format: snapshots/web-01/<date>/<id>.json
        assert keys[0].startswith("evidence/snapshots/web-01/")
        assert keys[0].endswith("snap-2024-001.json")


def test_real_export_repair_uses_correct_key():
    with S3MockServer() as s3:
        exporter, _ = _make_exporter(s3.url, "evidence-bucket")
        repair = {"action": "drop_caches", "snapshot_id": "snap-1", "ok": True}
        result = exporter.export_repair("web-01", repair)
        assert result is True

        keys = s3.stored_keys("evidence-bucket")
        assert len(keys) == 1
        assert keys[0].startswith("evidence/repairs/web-01/")


def test_real_signed_manifest_uploads_to_s3():
    """End-to-end: snapshot → manifest → sign → upload → verify on S3."""
    with S3MockServer() as s3:
        exporter, _ = _make_exporter(s3.url, "evidence-bucket")
        signer = ManifestSigner(host="web-01")

        # 1. produce snapshot
        snapshot = {
            "snapshot_id": "snap-A",
            "defcon": 4,
            "modules": {"cpu": "ok", "memory": "ok"},
        }
        # 2. sign it
        manifest = signer.sign("snap-A", snapshot)
        # 3. bundle manifest + snapshot
        bundle = {
            "manifest": manifest.to_dict(),
            "snapshot": snapshot,
        }
        # 4. upload bundle
        assert exporter.upload("bundles/snap-A.json", bundle) is True

        # 5. retrieve and verify the chain
        keys = s3.stored_keys("evidence-bucket")
        body = s3.stored_body("evidence-bucket", keys[0])
        parsed = json.loads(body)
        assert parsed["manifest"]["host"] == "web-01"
        assert parsed["manifest"]["snapshot_id"] == "snap-A"
        # Body sha should match what signer computed
        from ipracticom_sweeper.evidence.signer import hash_body
        assert parsed["manifest"]["body_sha256"] == hash_body(snapshot)


def test_real_failure_returns_false_does_not_raise():
    """If S3 is unreachable, exporter should return False, not raise."""
    # Use a port we know is closed
    s3 = S3MockServer()
    s3.start()
    s3.stop()  # immediately stop, so the port is dead

    exporter, _ = _make_exporter(s3.url, "test-bucket")
    result = exporter.upload("k.json", {"x": 1})
    assert result is False


def test_real_boto3_head_object_finds_uploaded_file():
    """After upload, boto3 can HEAD the same object back (proves boto3 flow)."""
    with S3MockServer() as s3:
        exporter, boto_client = _make_exporter(s3.url, "test-bucket")
        ok = exporter.upload("snapshots/h1/x.json", {"v": 1})
        assert ok

        # Now actually use boto3 to HEAD the object we uploaded
        head = boto_client.head_object(Bucket="test-bucket", Key="evidence/snapshots/h1/x.json")
        assert head["ContentLength"] > 0
        assert "ETag" in head


def test_real_missing_key_raises_client_error():
    """boto3 head_object on a missing key raises ClientError(404)."""
    with S3MockServer() as s3:
        _, boto_client = _make_exporter(s3.url, "test-bucket")
        try:
            boto_client.head_object(Bucket="test-bucket", Key="does-not-exist")
            assert False, "expected ClientError"
        except ClientError as e:
            assert e.response["Error"]["Code"] == "404"
