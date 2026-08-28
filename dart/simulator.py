"""Software sensor simulator for exercising the DART protocol."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
import argparse
import hashlib
import json
import logging
import math
from pathlib import Path
import random
import threading
import time
from typing import Any

from .client import DeliveryResult, SensorClient
from .protocol import MAX_BATCH_READINGS, MetricId, Reading


LOGGER = logging.getLogger("dart.simulator")


class Policy(str, Enum):
    RAW = "raw"
    RELIABLE_ALL = "reliable-all"
    DART = "dart"


@dataclass
class WorkerStats:
    sensor_id: int
    readings_generated: int = 0
    batches_generated: int = 0
    latest_generated: int = 0
    alerts_generated: int = 0
    alerts_locally_successful: int = 0
    reliable_failures: int = 0
    alert_results: list[DeliveryResult] = field(default_factory=list)
    scheduled_event_count: int = 0
    max_schedule_lateness_ms: float = 0.0
    final_latest_value: float | None = None
    workload_fingerprint: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sensor_id": self.sensor_id,
            "readings_generated": self.readings_generated,
            "batches_generated": self.batches_generated,
            "latest_generated": self.latest_generated,
            "alerts_generated": self.alerts_generated,
            "alerts_locally_successful": self.alerts_locally_successful,
            "reliable_failures": self.reliable_failures,
            "scheduled_event_count": self.scheduled_event_count,
            "max_schedule_lateness_ms": round(self.max_schedule_lateness_ms, 3),
            "final_latest_value": self.final_latest_value,
            "workload_fingerprint": self.workload_fingerprint,
            "alert_results": [
                {
                    "success": result.success,
                    "sequence": result.sequence,
                    "attempts": result.attempts,
                    "latency_ms": result.latency_ms,
                    "status_code": result.status_code,
                    "detail": result.detail,
                }
                for result in self.alert_results
            ],
        }


def parse_server(value: str) -> tuple[str, int]:
    host, separator, port = value.rpartition(":")
    if not separator or not host:
        raise argparse.ArgumentTypeError("server must be HOST:PORT")
    try:
        parsed_port = int(port)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("server port must be an integer") from exc
    if not 1 <= parsed_port <= 65535:
        raise argparse.ArgumentTypeError("server port must be between 1 and 65535")
    return host, parsed_port


def run_simulation(
    server_address: tuple[str, int],
    *,
    sensors: int = 5,
    duration_s: float = 8.0,
    sample_interval_s: float = 0.2,
    batch_size: int = 5,
    policy: Policy = Policy.DART,
    loss_rate: float = 0.0,
    corrupt_rate: float = 0.0,
    delay_ms: float = 0.0,
    jitter_ms: float = 0.0,
    alert_at_s: float = 2.0,
    alert_sensor_id: int = 1,
    alert_all_sensors: bool = False,
    alerts_per_sensor: int = 1,
    seed: int = 10,
    quiet: bool = False,
) -> dict[str, Any]:
    if sensors < 1:
        raise ValueError("sensors must be at least 1")
    if (
        not math.isfinite(duration_s)
        or not math.isfinite(sample_interval_s)
        or duration_s <= 0
        or sample_interval_s <= 0
    ):
        raise ValueError("duration and sample interval must be finite and positive")
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if batch_size > MAX_BATCH_READINGS:
        raise ValueError(
            f"batch_size must not exceed {MAX_BATCH_READINGS} readings"
        )
    if alerts_per_sensor < 1:
        raise ValueError("alerts_per_sensor must be at least 1")
    if not math.isfinite(alert_at_s) or alert_at_s < 0:
        raise ValueError("alert_at_s must be finite and non-negative")

    started = time.monotonic()
    clients: list[SensorClient] = []
    registration_failures: list[int] = []
    worker_stats: list[WorkerStats] = []
    client_snapshots: list[dict[str, Any]] = []
    try:
        for index in range(sensors):
            sensor_id = index + 1
            client = SensorClient(
                server_address,
                sensor_id=sensor_id,
                name=f"virtual-sensor-{sensor_id:03d}",
                loss_rate=loss_rate,
                corrupt_rate=corrupt_rate,
                network_delay_ms=delay_ms,
                jitter_ms=jitter_ms,
                seed=seed + sensor_id,
                quiet=quiet,
            )
            try:
                result = client.register()
            except Exception:
                client.close()
                raise
            if result.success:
                clients.append(client)
            else:
                registration_failures.append(sensor_id)
                client.close()
                if not quiet:
                    LOGGER.error(
                        "sensor=%s registration failed: %s",
                        sensor_id,
                        result.detail,
                    )

        if clients:
            start_barrier = threading.Barrier(len(clients) + 1)
            futures: list[tuple[int, Future[WorkerStats]]] = []
            with ThreadPoolExecutor(
                max_workers=len(clients), thread_name_prefix="virtual-sensor"
            ) as executor:
                for client in clients:
                    future = executor.submit(
                        _sensor_worker,
                        client,
                        start_barrier=start_barrier,
                        duration_s=duration_s,
                        sample_interval_s=sample_interval_s,
                        batch_size=batch_size,
                        policy=policy,
                        alert_at_s=alert_at_s,
                        should_alert=(
                            alert_all_sensors or client.sensor_id == alert_sensor_id
                        ),
                        alerts_per_sensor=alerts_per_sensor,
                        seed=seed + client.sensor_id * 100,
                        quiet=quiet,
                    )
                    futures.append((client.sensor_id, future))
                # Every worker reaches this same gate before any sensor starts
                # its logical schedule.  This keeps the cross-sensor workload
                # profile comparable even when executor threads start at
                # slightly different times.
                start_barrier.wait()

                worker_failures: list[tuple[int, Exception]] = []
                for sensor_id, future in futures:
                    try:
                        worker_stats.append(future.result())
                    except Exception as exc:
                        worker_failures.append((sensor_id, exc))
                if worker_failures:
                    details = "; ".join(
                        f"sensor {sensor_id}: {type(exc).__name__}: {exc}"
                        for sensor_id, exc in worker_failures
                    )
                    raise RuntimeError(
                        f"{len(worker_failures)} sensor worker(s) failed: {details}"
                    ) from worker_failures[0][1]

        client_snapshots = [client.snapshot_metrics() for client in clients]
    finally:
        for client in clients:
            client.close()
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)

    workers_result = sorted(
        (stats.as_dict() for stats in worker_stats),
        key=lambda item: item["sensor_id"],
    )
    combined_workload = hashlib.sha256(
        "|".join(
            f"{worker['sensor_id']}:{worker['workload_fingerprint']}"
            for worker in workers_result
        ).encode("ascii")
    ).hexdigest()
    result = {
        "policy": policy.value,
        "server": list(server_address),
        "requested_sensors": sensors,
        "registered_sensors": len(clients),
        "registration_failures": registration_failures,
        "duration_requested_s": duration_s,
        "elapsed_ms": elapsed_ms,
        "sample_interval_s": sample_interval_s,
        "batch_size": batch_size,
        "alert_all_sensors": alert_all_sensors,
        "alerts_per_sensor": alerts_per_sensor,
        "loss_rate": loss_rate,
        "corrupt_rate": corrupt_rate,
        "delay_ms": delay_ms,
        "jitter_ms": jitter_ms,
        "workload_fingerprint": combined_workload,
        "workers": workers_result,
        "clients": client_snapshots,
    }
    result["totals"] = _aggregate(result)
    return result


def _sensor_worker(
    client: SensorClient,
    *,
    start_barrier: threading.Barrier | None,
    duration_s: float,
    sample_interval_s: float,
    batch_size: int,
    policy: Policy,
    alert_at_s: float,
    should_alert: bool,
    alerts_per_sensor: int,
    seed: int,
    quiet: bool,
) -> WorkerStats:
    """Replay a fixed logical schedule, independent of ACK wait time.

    Reliable sends are deliberately synchronous so timeout/retry behavior stays
    visible.  The event list is generated first, however, so a slow policy cannot
    reduce the offered workload merely by spending wall-clock time waiting for
    ACKs.  Late events are sent immediately in their original logical order.
    """
    generator = random.Random(seed)
    stats = WorkerStats(client.sensor_id)
    pending_readings: list[tuple[Reading, float]] = []
    if start_barrier:
        start_barrier.wait()
    started = time.monotonic()
    reliable_normal = policy == Policy.RELIABLE_ALL
    reliable_alert = policy != Policy.RAW

    events: list[tuple[float, int, str, float]] = []
    for offset in _scheduled_offsets(duration_s, sample_interval_s):
        base = 25.0 + client.sensor_id * 0.15
        temperature = base + generator.uniform(-0.8, 0.8)
        events.append((offset, 0, "sample", temperature))
    for offset in _scheduled_offsets(duration_s, 0.5):
        position = client.sensor_id * 10.0 + offset * 1.5
        events.append((offset, 1, "latest", position))
    if should_alert and alert_at_s < duration_s:
        first_alert = max(0.0, alert_at_s)
        remaining = max(0.0, duration_s - first_alert)
        spacing = remaining / alerts_per_sensor
        for index in range(alerts_per_sensor):
            events.append((first_alert + index * spacing, 2, "alert", 92.4))
    events.sort()
    stats.scheduled_event_count = len(events)
    stats.workload_fingerprint = hashlib.sha256(
        json.dumps(
            [client.sensor_id, events],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).hexdigest()

    for scheduled_elapsed, _priority, event_type, event_value in events:
        _wait_until(started + scheduled_elapsed)
        lateness_ms = max(
            0.0, (time.monotonic() - (started + scheduled_elapsed)) * 1000.0
        )
        stats.max_schedule_lateness_ms = max(
            stats.max_schedule_lateness_ms, lateness_ms
        )
        if event_type == "sample":
            pending_readings.append(
                (
                    Reading(MetricId.TEMPERATURE_C, event_value, age_ms=0),
                    time.monotonic(),
                )
            )
            stats.readings_generated += 1
            if len(pending_readings) >= batch_size:
                result = client.send_batch(
                    _with_batch_ages(pending_readings),
                    reliable=reliable_normal,
                )
                stats.batches_generated += 1
                if reliable_normal and not result.success:
                    stats.reliable_failures += 1
                pending_readings = []
            continue

        if event_type == "latest":
            result = client.send_latest(
                MetricId.POSITION_X, event_value, reliable=reliable_normal
            )
            stats.final_latest_value = event_value
            stats.latest_generated += 1
            if reliable_normal and not result.success:
                stats.reliable_failures += 1
            continue

        if event_type == "alert":
            if not quiet:
                LOGGER.warning(
                    "sensor=%s generating FIRE alert under policy=%s",
                    client.sensor_id,
                    policy.value,
                )
            result = client.send_critical(
                alert_type="FIRE_DETECTED",
                severity="critical",
                value=event_value,
                unit="°C",
                message="Temperature exceeded the emergency threshold",
                reliable=reliable_alert,
            )
            stats.alerts_generated += 1
            stats.alert_results.append(result)
            if result.success:
                stats.alerts_locally_successful += 1
            elif reliable_alert:
                stats.reliable_failures += 1

    if pending_readings:
        result = client.send_batch(
            _with_batch_ages(pending_readings),
            reliable=reliable_normal,
        )
        stats.batches_generated += 1
        if reliable_normal and not result.success:
            stats.reliable_failures += 1
    return stats


def _scheduled_offsets(duration_s: float, interval_s: float) -> list[float]:
    """Return offsets 0, interval, ... strictly before the duration."""
    if (
        not math.isfinite(duration_s)
        or not math.isfinite(interval_s)
        or duration_s < 0
        or interval_s <= 0
    ):
        raise ValueError("duration must be finite/non-negative and interval positive")
    offsets: list[float] = []
    index = 0
    while True:
        offset = index * interval_s
        if offset >= duration_s:
            return offsets
        offsets.append(offset)
        index += 1


def _wait_until(deadline: float) -> None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.02))


def _with_batch_ages(
    readings: list[tuple[Reading, float]],
) -> list[Reading]:
    encoded_at = time.monotonic()
    return [
        Reading(
            reading.metric_id,
            reading.value,
            age_ms=min(0xFFFF, max(0, int((encoded_at - captured_at) * 1000))),
        )
        for reading, captured_at in readings
    ]


def _aggregate(result: dict[str, Any]) -> dict[str, int]:
    totals = {
        "readings_generated": 0,
        "batches_generated": 0,
        "latest_generated": 0,
        "alerts_generated": 0,
        "alerts_locally_successful": 0,
        "reliable_failures": 0,
        "attempted_packets": 0,
        "sent_packets": 0,
        "simulated_dropped_packets": 0,
        "attempted_bytes": 0,
        "sent_bytes": 0,
        "retransmissions": 0,
    }
    for worker in result["workers"]:
        for key in (
            "readings_generated",
            "batches_generated",
            "latest_generated",
            "alerts_generated",
            "alerts_locally_successful",
            "reliable_failures",
        ):
            totals[key] += int(worker[key])
    for client in result["clients"]:
        transmitter = client["transmitter"]
        totals["attempted_packets"] += int(transmitter["attempted_packets"])
        totals["sent_packets"] += int(transmitter["sent_packets"])
        totals["simulated_dropped_packets"] += int(transmitter["dropped_packets"])
        totals["attempted_bytes"] += int(transmitter["attempted_bytes"])
        totals["sent_bytes"] += int(transmitter["sent_bytes"])
        totals["retransmissions"] += int(client["retransmissions"])
    return totals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DART virtual sensor simulator")
    parser.add_argument("--server", type=parse_server, default=("127.0.0.1", 9999))
    parser.add_argument("--sensors", type=int, default=5)
    parser.add_argument("--duration", type=float, default=8.0)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--batch-size", type=int, default=5)
    parser.add_argument(
        "--policy", choices=[value.value for value in Policy], default=Policy.DART.value
    )
    parser.add_argument("--loss-rate", type=float, default=0.0)
    parser.add_argument("--corrupt-rate", type=float, default=0.0)
    parser.add_argument("--delay-ms", type=float, default=0.0)
    parser.add_argument("--jitter-ms", type=float, default=0.0)
    parser.add_argument("--alert-at", type=float, default=2.0)
    parser.add_argument("--alert-sensor", type=int, default=1)
    parser.add_argument(
        "--alert-all-sensors",
        action="store_true",
        help="generate alerts from every registered sensor",
    )
    parser.add_argument("--alerts-per-sensor", type=int, default=1)
    parser.add_argument("--seed", type=int, default=10)
    parser.add_argument("--metrics-file")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.sensors < 1:
        parser.error("--sensors must be at least 1")
    if (
        not math.isfinite(args.duration)
        or not math.isfinite(args.interval)
        or args.duration <= 0
        or args.interval <= 0
    ):
        parser.error("--duration and --interval must be finite and positive")
    if not 1 <= args.batch_size <= MAX_BATCH_READINGS:
        parser.error(f"--batch-size must be between 1 and {MAX_BATCH_READINGS}")
    if args.alerts_per_sensor < 1:
        parser.error("--alerts-per-sensor must be at least 1")
    if (
        not math.isfinite(args.alert_at)
        or args.alert_at < 0
        or args.alert_sensor < 1
    ):
        parser.error(
            "--alert-at must be finite/non-negative and --alert-sensor positive"
        )
    for option, value in (
        ("--loss-rate", args.loss_rate),
        ("--corrupt-rate", args.corrupt_rate),
    ):
        if not 0.0 <= value <= 1.0:
            parser.error(f"{option} must be between 0 and 1")
    if (
        not math.isfinite(args.delay_ms)
        or not math.isfinite(args.jitter_ms)
        or args.delay_ms < 0
        or args.jitter_ms < 0
    ):
        parser.error("--delay-ms and --jitter-ms must be finite and non-negative")
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    result = run_simulation(
        args.server,
        sensors=args.sensors,
        duration_s=args.duration,
        sample_interval_s=args.interval,
        batch_size=args.batch_size,
        policy=Policy(args.policy),
        loss_rate=args.loss_rate,
        corrupt_rate=args.corrupt_rate,
        delay_ms=args.delay_ms,
        jitter_ms=args.jitter_ms,
        alert_at_s=args.alert_at,
        alert_sensor_id=args.alert_sensor,
        alert_all_sensors=args.alert_all_sensors,
        alerts_per_sensor=args.alerts_per_sensor,
        seed=args.seed,
        quiet=args.quiet,
    )
    if args.metrics_file:
        target = Path(args.metrics_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not result["registration_failures"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
