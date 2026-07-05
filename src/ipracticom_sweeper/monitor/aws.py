"""AWS-side health checks.

Uses boto3 if available and credentials are present; otherwise returns
'unavailable' status. Never crashes the sweeper on missing AWS setup.
"""

from __future__ import annotations

import os
from typing import Any

try:
    import boto3
    from botocore.exceptions import NoCredentialsError, ClientError
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False


def collect() -> dict[str, Any]:
    """Collect AWS-side metrics: instance status, CW quick stats.

    Returns dict with 'available': bool. If boto3 missing or no
    credentials, returns availability info but does not raise.
    """
    if not HAS_BOTO3:
        return {
            "available": False,
            "reason": "boto3 not installed",
        }

    region = os.getenv("AWS_REGION", "il-central-1")

    try:
        ec2 = boto3.client("ec2", region_name=region)
        cw = boto3.client("cloudwatch", region_name=region)
    except Exception as e:
        return {"available": False, "reason": f"client init failed: {e}"}

    instance_id = os.getenv("EC2_INSTANCE_ID") or _discover_instance_id()
    if not instance_id:
        return {
            "available": False,
            "reason": "instance_id not discoverable (not on EC2?)",
        }

    # Status check
    try:
        status_resp = ec2.describe_instance_status(InstanceIds=[instance_id])
        statuses = status_resp.get("InstanceStatuses", [])
        if not statuses:
            return {"available": False, "reason": "instance not found"}
        s = statuses[0]
        sys_status = s.get("SystemStatus", {}).get("Status", "unknown")
        inst_status = s.get("InstanceStatus", {}).get("Status", "unknown")
    except NoCredentialsError:
        return {"available": False, "reason": "no AWS credentials"}
    except ClientError as e:
        return {"available": False, "reason": f"AWS API error: {e}"}

    # CW metrics — CPU utilization last 5 min
    cw_data = _get_cw_metric(cw, instance_id)

    return {
        "available": True,
        "instance_id": instance_id,
        "region": region,
        "system_status": sys_status,
        "instance_status": inst_status,
        "cloudwatch": cw_data,
    }


def _get_cw_metric(cw, instance_id: str) -> dict[str, Any]:
    """Get last-5-min CPU utilization from CloudWatch."""
    try:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        resp = cw.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=now - timedelta(minutes=5),
            EndTime=now,
            Period=60,
            Statistics=["Average"],
        )
        datapoints = resp.get("Datapoints", [])
        if datapoints:
            avg = sum(d["Average"] for d in datapoints) / len(datapoints)
            return {"cpu_5min_avg": round(avg, 2), "datapoints": len(datapoints)}
        return {"cpu_5min_avg": None, "datapoints": 0}
    except Exception as e:
        return {"error": str(e)}


def _discover_instance_id() -> str | None:
    """Try to get instance ID from EC2 metadata."""
    try:
        import httpx

        token = httpx.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"},
            timeout=1.0,
        ).text
        return httpx.get(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": token},
            timeout=1.0,
        ).text
    except Exception:
        return None


def evaluate(values: dict[str, Any], rules: dict) -> str:
    """Apply rules; return 'ok' | 'warn' | 'crit'."""
    if not values.get("available"):
        # AWS unavailable is not an alert by itself — could be non-AWS box
        return "ok"

    # AWS says impaired = crit
    if values.get("system_status") in ("impaired", "failed"):
        return "crit"
    if values.get("instance_status") in ("impaired", "failed"):
        return "crit"

    cw = values.get("cloudwatch", {})
    cpu = cw.get("cpu_5min_avg")
    if cpu is not None:
        if cpu >= 95:
            return "crit"
        if cpu >= 80:
            return "warn"

    return "ok"