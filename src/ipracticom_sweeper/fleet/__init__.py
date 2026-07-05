"""Fleet aggregator: multi-host overview."""
from .aggregator import aggregate, FleetSummary, HostSummary, ssm_to_aggregator_format
from .aws_connector import AwsSsmConnector, HostSnapshot, SsmError
from .collector import (
    start_collector_loop,
    stop_collector_loop,
    collect_once,
    load_snapshot,
    load_all_snapshots,
    snapshots_dir,
    write_snapshot,
)

__all__ = [
    "aggregate",
    "FleetSummary",
    "HostSummary",
    "AwsSsmConnector",
    "HostSnapshot",
    "SsmError",
    "start_collector_loop",
    "stop_collector_loop",
    "collect_once",
    "load_snapshot",
    "load_all_snapshots",
    "snapshots_dir",
    "write_snapshot",
    "ssm_to_aggregator_format",
]
