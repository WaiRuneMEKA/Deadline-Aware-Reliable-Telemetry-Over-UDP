#!/usr/bin/env python3
"""Run repeatable loopback benchmarks for RAW, RELIABLE-ALL, and DART."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
from typing import Any

from dart.server import DartServer
from dart.simulator import Policy, run_simulation


POLICIES = (Policy.RAW, Policy.RELIABLE_ALL, Policy.DART)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DART policy benchmark")
    parser.add_argument("--sensors", type=int, default=3)
    parser.add_argument("--duration", type=float, default=2.5)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--alerts-per-sensor", type=int, default=3)
    parser.add_argument("--loss-rates", type=float, nargs="+", default=[0.0, 0.1, 0.2])
    parser.add_argument("--delay-ms", type=float, default=0.0)
    parser.add_argument("--jitter-ms", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=100)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output", default="results/benchmark.json")
    return parser


def run_case(
    policy: Policy,
    loss_rate: float,
    repeat: int,
    *,
    sensors: int,
    duration_s: float,
    delay_ms: float,
    jitter_ms: float,
    seed: int,
    alerts_per_sensor: int,
) -> dict[str, Any]:
    case_seed = seed + repeat * 1_000 + int(loss_rate * 10_000)
    server = DartServer(
        port=0,
        workers=max(4, sensors),
        ack_loss_rate=loss_rate,
        network_delay_ms=delay_ms,
        jitter_ms=jitter_ms,
        seed=case_seed + 50_000,
        allow_experimental_policies=True,
        quiet=True,
    ).start()
    try:
        simulation = run_simulation(
            server.address,
            sensors=sensors,
            duration_s=duration_s,
            policy=policy,
            loss_rate=loss_rate,
            delay_ms=delay_ms,
            jitter_ms=jitter_ms,
            alert_at_s=min(0.8, duration_s / 2),
            alert_all_sensors=True,
            alerts_per_sensor=alerts_per_sensor,
            seed=case_seed,
            quiet=True,
        )
        if simulation["registered_sensors"] != sensors:
            raise RuntimeError(
                "benchmark case is invalid because not all requested sensors "
                f"registered: {simulation['registered_sensors']}/{sensors}"
            )
        if not server.wait_until_idle(quiet_period_s=0.15, timeout_s=3.0):
            raise RuntimeError("benchmark server did not drain before its deadline")
    finally:
        server.stop()
    server_metrics = server.snapshot_metrics()
    totals = simulation["totals"]
    server_transmitter = server_metrics["server_transmitter"]
    server_sent_bytes = int(server_transmitter["sent_bytes"])
    server_attempted_bytes = int(server_transmitter["attempted_bytes"])
    total_sent_bytes = totals["sent_bytes"] + server_sent_bytes
    total_attempted_bytes = totals["attempted_bytes"] + server_attempted_bytes
    generated_readings = totals["readings_generated"]
    generated_latest = totals["latest_generated"]
    generated_alerts = totals["alerts_generated"]
    critical_accept_latency_samples = list(
        server_metrics["critical_latency_ms"]["samples"]
    )
    alert_results = [
        result
        for worker in simulation["workers"]
        for result in worker["alert_results"]
    ]
    confirmed_alerts: int | None = None
    ack_latency_samples: list[float] = []
    if policy != Policy.RAW:
        confirmed_alerts = sum(bool(result["success"]) for result in alert_results)
        ack_latency_samples = [
            float(result["latency_ms"])
            for result in alert_results
            if result["success"]
        ]

    session_by_sensor = {
        int(client["sensor_id"]): int(client["session_id"])
        for client in simulation["clients"]
    }
    expected_latest = {
        (session_by_sensor[int(worker["sensor_id"])], int(worker["sensor_id"])): float(
            worker["final_latest_value"]
        )
        for worker in simulation["workers"]
        if worker["final_latest_value"] is not None
    }
    matching_final_latest = 0
    for (session_id, sensor_id), expected_value in expected_latest.items():
        actual = server_metrics["latest_values"].get(
            f"session_{session_id}.sensor_{sensor_id}.POSITION_X"
        )
        if actual and math.isclose(
            float(actual["value"]), expected_value, rel_tol=1e-6, abs_tol=1e-4
        ):
            matching_final_latest += 1

    expected_alerts = sensors * alerts_per_sensor
    if generated_alerts != expected_alerts:
        raise RuntimeError(
            f"benchmark generated {generated_alerts} alerts; expected {expected_alerts}"
        )
    generated_values = generated_readings + generated_latest + generated_alerts
    result = {
        "policy": policy.value,
        "loss_rate": loss_rate,
        "repeat": repeat,
        "workload_fingerprint": simulation["workload_fingerprint"],
        "registered_sensors": simulation["registered_sensors"],
        "registration_failures": len(simulation["registration_failures"]),
        "readings_generated": generated_readings,
        "readings_received": server_metrics["readings_received"],
        "normal_delivery_rate": _ratio(
            server_metrics["readings_received"], generated_readings
        ),
        "latest_generated": generated_latest,
        "latest_received": server_metrics["latest_updates_accepted"],
        "latest_delivery_rate": _ratio(
            server_metrics["latest_updates_accepted"], generated_latest
        ),
        "latest_final_expected": len(expected_latest),
        "latest_final_matching": matching_final_latest,
        "latest_final_state_rate": _ratio(
            matching_final_latest, len(expected_latest)
        ),
        "alerts_generated": generated_alerts,
        "alerts_received": server_metrics["critical_alerts_received"],
        "critical_server_acceptance_rate": _ratio(
            server_metrics["critical_alerts_received"], generated_alerts
        ),
        "critical_server_accept_p95_ms": _percentile(
            critical_accept_latency_samples, 95
        ),
        "critical_server_accept_latency_samples_ms": (
            critical_accept_latency_samples
        ),
        "critical_confirmed": confirmed_alerts,
        "critical_confirmation_rate": (
            None
            if confirmed_alerts is None
            else _ratio(confirmed_alerts, generated_alerts)
        ),
        "critical_ack_p95_ms": _percentile(ack_latency_samples, 95),
        "critical_ack_latency_samples_ms": ack_latency_samples,
        "client_attempted_packets": totals["attempted_packets"],
        "client_sent_packets": totals["sent_packets"],
        "client_attempted_bytes": totals["attempted_bytes"],
        "client_sent_bytes": totals["sent_bytes"],
        "server_attempted_packets": server_transmitter["attempted_packets"],
        "server_sent_packets": server_transmitter["sent_packets"],
        "server_attempted_bytes": server_attempted_bytes,
        "server_sent_bytes": server_sent_bytes,
        "total_attempted_bytes": total_attempted_bytes,
        "total_sent_bytes": total_sent_bytes,
        "attempted_bytes_per_generated_value": round(
            total_attempted_bytes / max(1, generated_values), 3
        ),
        "sent_bytes_per_generated_value": round(
            total_sent_bytes / max(1, generated_values), 3
        ),
        "retransmissions": totals["retransmissions"],
        "duplicates_at_server": server_metrics["duplicate_packets"],
        "decode_errors": server_metrics["decode_errors"],
        "elapsed_ms": simulation["elapsed_ms"],
        "max_schedule_lateness_ms": max(
            float(worker["max_schedule_lateness_ms"])
            for worker in simulation["workers"]
        ),
    }
    return result


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)


def summarize(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for case in cases:
        groups.setdefault((case["policy"], case["loss_rate"]), []).append(case)
    rows: list[dict[str, Any]] = []
    mean_fields = (
        "client_attempted_bytes",
        "client_sent_bytes",
        "server_attempted_bytes",
        "server_sent_bytes",
        "total_attempted_bytes",
        "total_sent_bytes",
        "retransmissions",
        "duplicates_at_server",
        "elapsed_ms",
        "max_schedule_lateness_ms",
    )
    for (policy, loss_rate), group in sorted(groups.items()):
        row: dict[str, Any] = {
            "policy": policy,
            "loss_rate": loss_rate,
            "runs": len(group),
        }
        row["normal_delivery_rate"] = _ratio(
            sum(int(case["readings_received"]) for case in group),
            sum(int(case["readings_generated"]) for case in group),
        )
        row["latest_delivery_rate"] = _ratio(
            sum(int(case["latest_received"]) for case in group),
            sum(int(case["latest_generated"]) for case in group),
        )
        row["latest_final_state_rate"] = _ratio(
            sum(int(case["latest_final_matching"]) for case in group),
            sum(int(case["latest_final_expected"]) for case in group),
        )
        row["critical_server_acceptance_rate"] = _ratio(
            sum(int(case["alerts_received"]) for case in group),
            sum(int(case["alerts_generated"]) for case in group),
        )
        confirmation_values = [
            case for case in group if case["critical_confirmed"] is not None
        ]
        row["critical_confirmation_rate"] = (
            None
            if not confirmation_values
            else _ratio(
                sum(int(case["critical_confirmed"]) for case in confirmation_values),
                sum(int(case["alerts_generated"]) for case in confirmation_values),
            )
        )
        accept_latency_samples = [
            float(value)
            for case in group
            for value in case["critical_server_accept_latency_samples_ms"]
        ]
        ack_latency_samples = [
            float(value)
            for case in group
            for value in case["critical_ack_latency_samples_ms"]
        ]
        row["critical_server_accept_p95_ms"] = _percentile(
            accept_latency_samples, 95
        )
        row["critical_ack_p95_ms"] = _percentile(ack_latency_samples, 95)
        generated_values = sum(
            int(case["readings_generated"])
            + int(case["latest_generated"])
            + int(case["alerts_generated"])
            for case in group
        )
        row["attempted_bytes_per_generated_value"] = round(
            sum(int(case["total_attempted_bytes"]) for case in group)
            / max(1, generated_values),
            4,
        )
        row["sent_bytes_per_generated_value"] = round(
            sum(int(case["total_sent_bytes"]) for case in group)
            / max(1, generated_values),
            4,
        )
        row["critical_server_accept_sample_count"] = len(
            accept_latency_samples
        )
        row["critical_ack_sample_count"] = len(ack_latency_samples)
        for field in mean_fields:
            values = [float(case[field]) for case in group]
            row[field] = round(statistics.mean(values), 4)
        rows.append(row)
    return rows


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return round(ordered[lower] * (1 - fraction) + ordered[upper] * fraction, 3)


def validate_equal_workload(cases: list[dict[str, Any]]) -> None:
    """Fail loudly if a policy receives a different logical workload."""
    groups: dict[tuple[float, int], list[dict[str, Any]]] = {}
    for case in cases:
        groups.setdefault((case["loss_rate"], case["repeat"]), []).append(case)
    fields = (
        "registered_sensors",
        "readings_generated",
        "latest_generated",
        "alerts_generated",
        "workload_fingerprint",
    )
    for key, group in groups.items():
        signatures = {
            tuple(case[field] for field in fields) for case in group
        }
        if len(signatures) != 1:
            raise RuntimeError(
                f"benchmark workload differs across policies for {key}: "
                f"{sorted(signatures)}"
            )


def write_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(cases[0]))
        writer.writeheader()
        for case in cases:
            row = dict(case)
            for field in (
                "critical_server_accept_latency_samples_ms",
                "critical_ack_latency_samples_ms",
            ):
                row[field] = json.dumps(row[field], separators=(",", ":"))
            writer.writerow(row)


def print_table(summary: list[dict[str, Any]]) -> None:
    print(
        "\npolicy        loss  normal latest final  accepted confirmed "
        "rx-p95  ack-p95  attempted-B/value retries"
    )
    print("-" * 119)
    for row in summary:
        print(
            f"{row['policy']:<13} {row['loss_rate']:>4.0%}  "
            f"{row['normal_delivery_rate']:>6.1%}  "
            f"{row['latest_delivery_rate']:>6.1%}  "
            f"{row['latest_final_state_rate']:>5.1%}  "
            f"{row['critical_server_acceptance_rate']:>8.1%}  "
            f"{_format_rate(row['critical_confirmation_rate']):>9} "
            f"{_format_ms(row['critical_server_accept_p95_ms']):>8} "
            f"{_format_ms(row['critical_ack_p95_ms']):>8} "
            f"{row['attempted_bytes_per_generated_value']:>17.1f}  "
            f"{row['retransmissions']:>7.1f}"
        )


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _format_ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}ms"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.quick:
        args.sensors = 2
        args.duration = 1.5
        args.repeats = 1
        args.loss_rates = [0.0, 0.2]
    if args.sensors < 1:
        parser.error("--sensors must be at least 1")
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("--duration must be finite and positive")
    for loss_rate in args.loss_rates:
        if not 0.0 <= loss_rate <= 1.0:
            parser.error("--loss-rates values must be between 0 and 1")
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.alerts_per_sensor < 1:
        parser.error("--alerts-per-sensor must be at least 1")
    if (
        not math.isfinite(args.delay_ms)
        or not math.isfinite(args.jitter_ms)
        or args.delay_ms < 0
        or args.jitter_ms < 0
    ):
        parser.error("--delay-ms and --jitter-ms must be finite and non-negative")

    cases: list[dict[str, Any]] = []
    total = len(POLICIES) * len(args.loss_rates) * args.repeats
    number = 0
    for loss_rate in args.loss_rates:
        for policy in POLICIES:
            for repeat in range(1, args.repeats + 1):
                number += 1
                print(
                    f"[{number:02d}/{total:02d}] policy={policy.value:<12} "
                    f"loss={loss_rate:.0%} repeat={repeat}"
                )
                cases.append(
                    run_case(
                        policy,
                        loss_rate,
                        repeat,
                        sensors=args.sensors,
                        duration_s=args.duration,
                        delay_ms=args.delay_ms,
                        jitter_ms=args.jitter_ms,
                        seed=args.seed,
                        alerts_per_sensor=args.alerts_per_sensor,
                    )
                )

    validate_equal_workload(cases)
    summary = summarize(cases)
    report = {
        "project": "DART v1 policy benchmark",
        "generated_at": datetime.now().astimezone().isoformat(),
        "method": {
            "transport": "UDP over IPv4 loopback",
            "loss": "seeded pseudo-random application-level drop in both directions",
            "sensors": args.sensors,
            "duration_s": args.duration,
            "workload": (
                "fixed precomputed event schedule; counts, values, offsets, and "
                "SHA-256 workload fingerprint must match across policies"
            ),
            "alerts_per_sensor": args.alerts_per_sensor,
            "alerting_sensors": "all registered sensors",
            "repeats": args.repeats,
            "loss_rates": args.loss_rates,
            "delay_ms": args.delay_ms,
            "jitter_ms": args.jitter_ms,
            "latency_semantics": {
                "server_accept": (
                    "original packet timestamp to first server acceptance, "
                    "conditional on delivery, same-host wall clocks"
                ),
                "ack": (
                    "client protocol-call time to accepted ACK/response; n/a for raw"
                ),
                "schedule_lateness": (
                    "wall-clock backlog relative to the fixed logical schedule; "
                    "packet timestamps begin when the protocol call is made"
                ),
            },
            "byte_semantics": (
                "attempted and passed-to-OS DART application bytes in both "
                "directions; includes registration/control setup and excludes "
                "UDP/IP/link headers"
            ),
            "loss_pairing_caveat": (
                "policies use the same loss probability and seed, but retries/ACKs "
                "consume different PRNG draws, so individual drop events are not paired"
            ),
        },
        "cases": cases,
        "summary": summary,
    }
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    csv_target = target.with_suffix(".csv")
    write_csv(csv_target, cases)
    print_table(summary)
    print(f"\nJSON: {target.resolve()}\nCSV : {csv_target.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
