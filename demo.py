#!/usr/bin/env python3
"""Run a complete DART server + virtual-sensor demo in one command."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import math
from pathlib import Path

from dart.server import DartServer
from dart.simulator import Policy, run_simulation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="One-command DART demonstration")
    parser.add_argument("--sensors", type=int, default=5)
    parser.add_argument("--duration", type=float, default=6.0)
    parser.add_argument("--policy", choices=[p.value for p in Policy], default="dart")
    parser.add_argument("--loss-rate", type=float, default=0.10)
    parser.add_argument("--ack-loss-rate", type=float, default=0.0)
    parser.add_argument("--corrupt-rate", type=float, default=0.0)
    parser.add_argument("--delay-ms", type=float, default=0.0)
    parser.add_argument("--jitter-ms", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-drop-first-ack", action="store_true")
    parser.add_argument("--output", default="results/latest_demo.json")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.sensors < 1:
        parser.error("--sensors must be at least 1")
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("--duration must be finite and positive")
    for option, value in (
        ("--loss-rate", args.loss_rate),
        ("--ack-loss-rate", args.ack_loss_rate),
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
    if not args.quiet:
        print(
            "\nDART v1 — Deadline-Aware Reliable Telemetry\n"
            "Normal readings: batch | Fast-changing values: latest-only | "
            "Critical alerts: ACK + retry\n"
        )

    server_workers = max(4, args.sensors)
    sample_interval_s = 0.2
    batch_size = 5
    alert_at_s = min(2.0, args.duration / 2)
    alert_sensor_id = 1
    server = DartServer(
        port=0,
        workers=server_workers,
        ack_loss_rate=args.ack_loss_rate,
        ack_corrupt_rate=args.corrupt_rate,
        network_delay_ms=args.delay_ms,
        jitter_ms=args.jitter_ms,
        seed=args.seed,
        drop_first_critical_ack=not args.no_drop_first_ack,
        allow_experimental_policies=args.policy != Policy.DART.value,
        quiet=args.quiet,
    ).start()
    try:
        simulation = run_simulation(
            server.address,
            sensors=args.sensors,
            duration_s=args.duration,
            sample_interval_s=sample_interval_s,
            batch_size=batch_size,
            policy=Policy(args.policy),
            loss_rate=args.loss_rate,
            corrupt_rate=args.corrupt_rate,
            delay_ms=args.delay_ms,
            jitter_ms=args.jitter_ms,
            alert_at_s=alert_at_s,
            alert_sensor_id=alert_sensor_id,
            seed=args.seed,
            quiet=args.quiet,
        )
        if not server.wait_until_idle(quiet_period_s=0.15, timeout_s=3.0):
            raise RuntimeError("server did not become idle after the demo workload")
    finally:
        server.stop()

    report = {
        "project": "DART v1",
        "generated_at": datetime.now().astimezone().isoformat(),
        "demo_configuration": {
            "sensors": args.sensors,
            "duration_s": args.duration,
            "sample_interval_s": sample_interval_s,
            "batch_size": batch_size,
            "policy": args.policy,
            "loss_rate": args.loss_rate,
            "ack_loss_rate": args.ack_loss_rate,
            "corrupt_rate": args.corrupt_rate,
            "delay_ms": args.delay_ms,
            "jitter_ms": args.jitter_ms,
            "seed": args.seed,
            "simulation_base_seed": args.seed,
            "server_seed": args.seed,
            "seed_scope": (
                "the server uses server_seed directly; the simulator derives "
                "deterministic per-sensor transport and workload seeds from "
                "simulation_base_seed"
            ),
            "server_workers": server_workers,
            "alert_at_s": alert_at_s,
            "alert_sensor_id": alert_sensor_id,
            "drop_first_critical_ack": not args.no_drop_first_ack,
        },
        "simulation": simulation,
        "server": server.snapshot_metrics(),
    }
    report["acceptance"] = evaluate_report(report)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print_summary(report, target)
    return 0 if report["acceptance"]["passed"] else 1


def evaluate_report(report: dict) -> dict:
    simulation = report["simulation"]
    server = report["server"]
    configuration = report.get("demo_configuration", {})
    reliable_critical = simulation["policy"] != Policy.RAW.value
    forced_ack_drop = bool(configuration.get("drop_first_critical_ack", False))
    generated_alerts = simulation["totals"]["alerts_generated"]
    confirmed_alerts = simulation["totals"]["alerts_locally_successful"]
    checks = {
        "all_sensors_registered": simulation["registered_sensors"]
        == simulation["requested_sensors"],
        "server_received_normal_readings": server["readings_received"] > 0,
        "server_received_latest_values": bool(server["latest_values"]),
        "critical_alert_delivered": server["critical_alerts_received"] > 0,
        "wire_decode_clean": server["decode_errors"] == 0
        or simulation["corrupt_rate"] > 0,
    }
    if reliable_critical:
        checks.update(
            {
                "all_critical_alerts_confirmed": confirmed_alerts
                == generated_alerts,
                "critical_alerts_processed_once_within_demo_window": server[
                    "critical_alerts_received"
                ]
                == generated_alerts,
            }
        )
    if reliable_critical and forced_ack_drop and generated_alerts:
        checks.update(
            {
                "forced_critical_ack_drop_observed": server[
                    "acks_simulated_dropped"
                ]
                >= 1,
                "critical_retransmission_observed": simulation["totals"][
                    "retransmissions"
                ]
                >= 1,
                "duplicate_suppression_observed": server["duplicate_packets"]
                >= 1,
            }
        )
    return {"passed": all(checks.values()), "checks": checks}


def print_summary(report: dict, target: Path) -> None:
    sim = report["simulation"]
    server = report["server"]
    totals = sim["totals"]
    acceptance = report["acceptance"]
    print("\n=== DART DEMO SUMMARY ===")
    print(f"Policy                 : {sim['policy']}")
    print(
        f"Sensors registered     : {sim['registered_sensors']}/"
        f"{sim['requested_sensors']}"
    )
    print(f"Client packets sent    : {totals['sent_packets']}")
    print(f"Simulated client drops : {totals['simulated_dropped_packets']}")
    print(f"Retransmissions        : {totals['retransmissions']}")
    print(f"Normal readings at RX  : {server['readings_received']}")
    print(f"Critical alerts at RX  : {server['critical_alerts_received']}")
    print(f"Duplicate packets      : {server['duplicate_packets']}")
    print(f"ACKs suppressed pre-send: {server['acks_simulated_dropped']}")
    print(f"Result                  : {'PASS' if acceptance['passed'] else 'FAIL'}")
    for name, passed in acceptance["checks"].items():
        print(f"  [{'x' if passed else ' '}] {name}")
    print(f"Full JSON report        : {target.resolve()}")


if __name__ == "__main__":
    raise SystemExit(main())
