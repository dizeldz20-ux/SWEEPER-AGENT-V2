"""Evidence export to S3: snapshots + repairs with retention."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any

from ipracticom_sweeper.state import SQLiteStateStore


def has_credentials() -> bool:
    """True if AWS credentials are available (env or IAM role)."""
    import os
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return True
    # Check IMDS (EC2 metadata service) — best effort, don't fail
    try:
        import httpx
        r = httpx.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            timeout=0.5,
        )
        return r.status_code == 200
    except Exception:
        return False


class S3Exporter:
    def __init__(self, bucket: str, prefix: str = "evidence/"):
        self.bucket = bucket
        self.prefix = prefix
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3")
        return self._client

    def upload(self, key: str, data: dict[str, Any]) -> bool:
        """Upload JSON to s3://bucket/prefix/key. Returns True on success."""
        try:
            client = self._get_client()
            full_key = f"{self.prefix}{key}"
            client.put_object(
                Bucket=self.bucket,
                Key=full_key,
                Body=json.dumps(data, indent=2).encode(),
                ContentType="application/json",
            )
            return True
        except Exception as e:
            print(f"S3 upload failed: {e}")
            return False

    def export_snapshot(self, host: str, snapshot: dict[str, Any]) -> bool:
        date_str = time.strftime("%Y-%m-%d")
        snapshot_id = snapshot.get("snapshot_id", str(int(time.time() * 1000)))
        key = f"snapshots/{host}/{date_str}/{snapshot_id}.json"
        return self.upload(key, snapshot)

    def export_repair(self, host: str, repair: dict[str, Any]) -> bool:
        date_str = time.strftime("%Y-%m-%d")
        repair_id = str(int(time.time() * 1000))
        key = f"repairs/{host}/{date_str}/{repair_id}.json"
        return self.upload(key, repair)
