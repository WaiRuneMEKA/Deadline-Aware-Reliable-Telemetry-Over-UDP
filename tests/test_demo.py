"""Policy-aware acceptance-oracle tests for the one-command demo."""

from __future__ import annotations

from copy import deepcopy
import unittest

from demo import evaluate_report
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
            "critical_alerts_processed_exactly_once",
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
            acceptance["checks"]["critical_alerts_processed_exactly_once"]
        )
        self.assertNotIn("forced_critical_ack_drop_observed", acceptance["checks"])
        self.assertNotIn("critical_retransmission_observed", acceptance["checks"])
        self.assertNotIn("duplicate_suppression_observed", acceptance["checks"])

        unconfirmed = deepcopy(report)
        unconfirmed["simulation"]["totals"]["alerts_locally_successful"] = 0
        failed = evaluate_report(unconfirmed)
        self.assertFalse(failed["passed"])
        self.assertFalse(failed["checks"]["all_critical_alerts_confirmed"])


if __name__ == "__main__":
    unittest.main()
