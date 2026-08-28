"""Policy-aware acceptance-oracle tests for the one-command demo."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from demo import evaluate_report, main
from dart.simulator import Policy


class DemoAcceptanceOracleTests(unittest.TestCase):
    @staticmethod
    def report(*, policy=Policy.DART, forced_ack_drop=True):
        return {
            "demo_configuration": {
                "policy": policy.value,
                "drop_first_critical_ack": forced_ack_drop,
            },
            "simulation": {
                "policy": policy.value,
                "requested_sensors": 2,
                "registered_sensors": 2,
                "corrupt_rate": 0.0,
                "totals": {
                    "alerts_generated": 1,
                    "alerts_locally_successful": 1,
                    "retransmissions": 1,
                },
            },
            "server": {
                "readings_received": 10,
                "latest_values": {
                    "session_101.sensor_1.POSITION_X": {"value": 12.5}
                },
                "critical_alerts_received": 1,
                "decode_errors": 0,
                "acks_simulated_dropped": 1,
                "duplicate_packets": 1,
            },
        }

    def test_dart_forced_drop_passes_only_with_complete_retry_evidence(self):
        complete = evaluate_report(self.report())

        self.assertTrue(complete["passed"])
        for check in (
            "all_critical_alerts_confirmed",
            "critical_alerts_processed_once_within_demo_window",
            "forced_critical_ack_drop_observed",
            "critical_retransmission_observed",
            "duplicate_suppression_observed",
        ):
            self.assertTrue(complete["checks"][check], check)

        missing_evidence = (
            (
                "confirmation",
                ("simulation", "totals", "alerts_locally_successful"),
                "all_critical_alerts_confirmed",
            ),
            (
                "retransmission",
                ("simulation", "totals", "retransmissions"),
                "critical_retransmission_observed",
            ),
            (
                "duplicate suppression",
                ("server", "duplicate_packets"),
                "duplicate_suppression_observed",
            ),
        )
        for label, path, failed_check in missing_evidence:
            with self.subTest(missing=label):
                report = deepcopy(self.report())
                target = report
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = 0

                acceptance = evaluate_report(report)

                self.assertFalse(acceptance["passed"])
                self.assertFalse(acceptance["checks"][failed_check])

    def test_raw_without_forced_drop_uses_only_base_delivery_criteria(self):
        report = self.report(policy=Policy.RAW, forced_ack_drop=False)
        report["simulation"]["totals"].update(
            {
                "alerts_locally_successful": 0,
                "retransmissions": 0,
            }
        )
        report["server"].update(
            {"acks_simulated_dropped": 0, "duplicate_packets": 0}
        )

        acceptance = evaluate_report(report)

        self.assertTrue(acceptance["passed"])
        self.assertNotIn("all_critical_alerts_confirmed", acceptance["checks"])
        self.assertNotIn("critical_retransmission_observed", acceptance["checks"])
        self.assertNotIn("duplicate_suppression_observed", acceptance["checks"])

        missing_normal_data = deepcopy(report)
        missing_normal_data["server"]["readings_received"] = 0
        failed = evaluate_report(missing_normal_data)
        self.assertFalse(failed["passed"])
        self.assertFalse(failed["checks"]["server_received_normal_readings"])

    def test_dart_without_forced_drop_requires_confirmation_but_not_retry_artifacts(self):
        report = self.report(policy=Policy.DART, forced_ack_drop=False)
        report["simulation"]["totals"]["retransmissions"] = 0
        report["server"]["acks_simulated_dropped"] = 0
        report["server"]["duplicate_packets"] = 0

        acceptance = evaluate_report(report)

        self.assertTrue(acceptance["passed"])
        self.assertTrue(acceptance["checks"]["all_critical_alerts_confirmed"])
        self.assertTrue(
            acceptance["checks"]["critical_alerts_processed_once_within_demo_window"]
        )
        self.assertNotIn("forced_critical_ack_drop_observed", acceptance["checks"])
        self.assertNotIn("critical_retransmission_observed", acceptance["checks"])
        self.assertNotIn("duplicate_suppression_observed", acceptance["checks"])

        unconfirmed = deepcopy(report)
        unconfirmed["simulation"]["totals"]["alerts_locally_successful"] = 0
        failed = evaluate_report(unconfirmed)
        self.assertFalse(failed["passed"])
        self.assertFalse(failed["checks"]["all_critical_alerts_confirmed"])


class DemoReportProvenanceTests(unittest.TestCase):
    def test_main_records_seed_network_impairments_and_effective_configuration(self):
        simulation = {
            "policy": Policy.DART.value,
            "requested_sensors": 2,
            "registered_sensors": 2,
            "corrupt_rate": 0.03,
            "totals": {
                "alerts_generated": 1,
                "alerts_locally_successful": 1,
                "retransmissions": 0,
                "sent_packets": 5,
                "simulated_dropped_packets": 1,
            },
        }
        server_metrics = {
            "readings_received": 4,
            "latest_values": {
                "session_101.sensor_1.POSITION_X": {"value": 12.5}
            },
            "critical_alerts_received": 1,
            "decode_errors": 0,
            "acks_simulated_dropped": 0,
            "duplicate_packets": 0,
        }
        server = MagicMock()
        server.start.return_value = server
        server.address = ("127.0.0.1", 9999)
        server.wait_until_idle.return_value = True
        server.snapshot_metrics.return_value = server_metrics

        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "demo.json"
            with (
                patch("demo.DartServer", return_value=server) as server_factory,
                patch("demo.run_simulation", return_value=simulation) as simulation_run,
            ):
                status = main(
                    [
                        "--sensors",
                        "2",
                        "--duration",
                        "1.25",
                        "--loss-rate",
                        "0.2",
                        "--ack-loss-rate",
                        "0.15",
                        "--corrupt-rate",
                        "0.03",
                        "--delay-ms",
                        "5",
                        "--jitter-ms",
                        "2",
                        "--seed",
                        "777",
                        "--no-drop-first-ack",
                        "--output",
                        str(output),
                        "--quiet",
                    ]
                )
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(status, 0)
        configuration = report["demo_configuration"]
        self.assertEqual(configuration["seed"], 777)
        self.assertEqual(configuration["simulation_base_seed"], 777)
        self.assertEqual(configuration["server_seed"], 777)
        self.assertEqual(configuration["loss_rate"], 0.2)
        self.assertEqual(configuration["ack_loss_rate"], 0.15)
        self.assertEqual(configuration["corrupt_rate"], 0.03)
        self.assertEqual(configuration["delay_ms"], 5.0)
        self.assertEqual(configuration["jitter_ms"], 2.0)
        self.assertEqual(configuration["sensors"], 2)
        self.assertEqual(configuration["duration_s"], 1.25)
        self.assertEqual(configuration["sample_interval_s"], 0.2)
        self.assertEqual(configuration["batch_size"], 5)
        self.assertEqual(configuration["alert_at_s"], 0.625)
        self.assertEqual(configuration["alert_sensor_id"], 1)
        self.assertFalse(configuration["drop_first_critical_ack"])
        server_factory.assert_called_once()
        self.assertEqual(server_factory.call_args.kwargs["ack_loss_rate"], 0.15)
        self.assertEqual(server_factory.call_args.kwargs["seed"], 777)
        simulation_run.assert_called_once()
        self.assertEqual(simulation_run.call_args.kwargs["seed"], 777)
        self.assertEqual(simulation_run.call_args.kwargs["loss_rate"], 0.2)


if __name__ == "__main__":
    unittest.main()
