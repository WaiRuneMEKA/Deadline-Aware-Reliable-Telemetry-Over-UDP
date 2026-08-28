"""Regression tests for fair workload generation and benchmark aggregation."""

from __future__ import annotations

import unittest

from benchmark import summarize, validate_equal_workload
from dart.simulator import _scheduled_offsets


class ScheduledOffsetsTests(unittest.TestCase):
    def test_offsets_are_strictly_before_duration_at_exact_boundary(self):
        offsets = _scheduled_offsets(duration_s=1.0, interval_s=0.25)

        self.assertEqual(offsets, [0.0, 0.25, 0.5, 0.75])
        self.assertNotIn(1.0, offsets)
        self.assertTrue(all(offset < 1.0 for offset in offsets))

    def test_count_is_deterministic_for_fractional_interval(self):
        first = _scheduled_offsets(duration_s=2.5, interval_s=0.2)
        second = _scheduled_offsets(duration_s=2.5, interval_s=0.2)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 13)
        self.assertAlmostEqual(first[0], 0.0)
        self.assertAlmostEqual(first[-1], 2.4)
        self.assertTrue(all(first[index] < first[index + 1] for index in range(12)))

    def test_zero_duration_and_sub_interval_duration_boundaries(self):
        self.assertEqual(_scheduled_offsets(duration_s=0.0, interval_s=0.2), [])
        self.assertEqual(_scheduled_offsets(duration_s=0.01, interval_s=1.0), [0.0])


class EqualWorkloadValidationTests(unittest.TestCase):
    @staticmethod
    def case(
        policy,
        loss_rate,
        repeat,
        *,
        sensors=3,
        readings=39,
        latest=15,
        alerts=9,
        fingerprint="workload-v1",
    ):
        return {
            "policy": policy,
            "loss_rate": loss_rate,
            "repeat": repeat,
            "registered_sensors": sensors,
            "readings_generated": readings,
            "latest_generated": latest,
            "alerts_generated": alerts,
            "workload_fingerprint": fingerprint,
        }

    def test_accepts_equal_signatures_with_different_result_groups(self):
        cases = []
        for loss_rate, repeat, signature in (
            (0.0, 1, (3, 39, 15, 9)),
            (0.2, 1, (3, 42, 18, 9)),
            (0.2, 2, (2, 28, 12, 6)),
        ):
            for policy in ("raw", "reliable-all", "dart"):
                cases.append(
                    self.case(
                        policy,
                        loss_rate,
                        repeat,
                        sensors=signature[0],
                        readings=signature[1],
                        latest=signature[2],
                        alerts=signature[3],
                        fingerprint=f"loss={loss_rate};repeat={repeat}",
                    )
                )

        self.assertIsNone(validate_equal_workload(cases))

    def test_rejects_mismatched_policy_workload_signature(self):
        cases = [
            self.case("raw", 0.2, 1),
            self.case("reliable-all", 0.2, 1),
            self.case("dart", 0.2, 1, alerts=8),
        ]

        with self.assertRaisesRegex(
            RuntimeError, r"workload differs across policies.*\(0\.2, 1\)"
        ):
            validate_equal_workload(cases)

    def test_rejects_mismatched_fingerprint_even_when_counts_match(self):
        cases = [
            self.case("raw", 0.2, 1, fingerprint="schedule-A"),
            self.case("reliable-all", 0.2, 1, fingerprint="schedule-A"),
            self.case("dart", 0.2, 1, fingerprint="schedule-B"),
        ]

        with self.assertRaisesRegex(RuntimeError, "workload differs across policies"):
            validate_equal_workload(cases)


class BenchmarkSummaryTests(unittest.TestCase):
    @staticmethod
    def case(
        *,
        readings_generated,
        readings_received,
        latest_generated,
        latest_received,
        latest_final_expected,
        latest_final_matching,
        alerts_generated,
        alerts_received,
        critical_confirmed,
        accept_latency_samples,
        ack_latency_samples,
        client_attempted_bytes,
        client_sent_bytes,
        server_attempted_bytes,
        server_sent_bytes,
        retries,
        duplicates,
        elapsed_ms,
        max_schedule_lateness_ms,
    ):
        total_attempted_bytes = client_attempted_bytes + server_attempted_bytes
        total_sent_bytes = client_sent_bytes + server_sent_bytes
        generated_values = readings_generated + latest_generated + alerts_generated
        return {
            "policy": "dart",
            "loss_rate": 0.2,
            "readings_generated": readings_generated,
            "readings_received": readings_received,
            "normal_delivery_rate": readings_received / readings_generated,
            "latest_generated": latest_generated,
            "latest_received": latest_received,
            "latest_delivery_rate": latest_received / latest_generated,
            "latest_final_expected": latest_final_expected,
            "latest_final_matching": latest_final_matching,
            "latest_final_state_rate": (
                latest_final_matching / latest_final_expected
            ),
            "alerts_generated": alerts_generated,
            "alerts_received": alerts_received,
            "critical_server_acceptance_rate": alerts_received / alerts_generated,
            "critical_server_accept_latency_samples_ms": accept_latency_samples,
            "critical_server_accept_p95_ms": None,
            "critical_confirmed": critical_confirmed,
            "critical_confirmation_rate": (
                None
                if critical_confirmed is None
                else critical_confirmed / alerts_generated
            ),
            "critical_ack_latency_samples_ms": ack_latency_samples,
            "critical_ack_p95_ms": None,
            "client_attempted_bytes": client_attempted_bytes,
            "client_sent_bytes": client_sent_bytes,
            "server_attempted_bytes": server_attempted_bytes,
            "server_sent_bytes": server_sent_bytes,
            "total_attempted_bytes": total_attempted_bytes,
            "total_sent_bytes": total_sent_bytes,
            "attempted_bytes_per_generated_value": (
                total_attempted_bytes / generated_values
            ),
            "sent_bytes_per_generated_value": total_sent_bytes / generated_values,
            "retransmissions": retries,
            "duplicates_at_server": duplicates,
            "elapsed_ms": elapsed_ms,
            "max_schedule_lateness_ms": max_schedule_lateness_ms,
        }

    def test_summary_uses_weighted_rates_pooled_p95_and_aggregate_byte_ratios(self):
        # The two runs intentionally have very different denominators. Averaging
        # their precomputed ratios would therefore produce demonstrably wrong values.
        cases = [
            self.case(
                readings_generated=1,
                readings_received=1,
                latest_generated=1,
                latest_received=1,
                latest_final_expected=1,
                latest_final_matching=1,
                alerts_generated=1,
                alerts_received=1,
                critical_confirmed=1,
                accept_latency_samples=[0.0],
                ack_latency_samples=[5.0],
                client_attempted_bytes=800,
                client_sent_bytes=650,
                server_attempted_bytes=200,
                server_sent_bytes=150,
                retries=0,
                duplicates=2,
                elapsed_ms=100,
                max_schedule_lateness_ms=2,
            ),
            self.case(
                readings_generated=9,
                readings_received=0,
                latest_generated=3,
                latest_received=0,
                latest_final_expected=3,
                latest_final_matching=0,
                alerts_generated=19,
                alerts_received=9,
                critical_confirmed=4,
                accept_latency_samples=[
                    10.0,
                    20.0,
                    30.0,
                    40.0,
                    50.0,
                    60.0,
                    70.0,
                    80.0,
                    90.0,
                ],
                ack_latency_samples=[15.0, 25.0, 35.0, 45.0],
                client_attempted_bytes=20,
                client_sent_bytes=10,
                server_attempted_bytes=80,
                server_sent_bytes=40,
                retries=10,
                duplicates=4,
                elapsed_ms=300,
                max_schedule_lateness_ms=6,
            ),
        ]

        rows = summarize(cases)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["policy"], "dart")
        self.assertEqual(row["loss_rate"], 0.2)
        self.assertEqual(row["runs"], 2)

        # Rates are ratios of total delivered to total generated, not mean ratios.
        self.assertEqual(row["normal_delivery_rate"], 0.1)  # 1 / 10
        self.assertEqual(row["latest_delivery_rate"], 0.25)  # 1 / 4
        self.assertEqual(row["latest_final_state_rate"], 0.25)  # 1 / 4
        self.assertEqual(row["critical_server_acceptance_rate"], 0.5)  # 10 / 20
        self.assertEqual(row["critical_confirmation_rate"], 0.25)  # 5 / 20
        self.assertNotEqual(row["normal_delivery_rate"], 0.5)

        # Both P95 values pool raw samples rather than averaging per-run P95s.
        self.assertEqual(row["critical_server_accept_sample_count"], 10)
        self.assertEqual(row["critical_server_accept_p95_ms"], 85.5)
        average_of_run_p95s = (0.0 + 86.0) / 2
        self.assertNotEqual(row["critical_server_accept_p95_ms"], average_of_run_p95s)
        self.assertEqual(row["critical_ack_sample_count"], 5)
        self.assertEqual(row["critical_ack_p95_ms"], 43.0)

        # Byte ratios use aggregate attempted/sent numerators over all 34 values.
        self.assertEqual(row["attempted_bytes_per_generated_value"], 32.3529)
        self.assertEqual(row["sent_bytes_per_generated_value"], 25.0)
        average_of_run_ratios = ((1000 / 3) + (100 / 31)) / 2
        self.assertNotAlmostEqual(
            row["attempted_bytes_per_generated_value"],
            average_of_run_ratios,
            places=4,
        )

        # Metrics that are explicitly reported as per-run means remain means.
        self.assertEqual(row["client_attempted_bytes"], 410.0)
        self.assertEqual(row["client_sent_bytes"], 330.0)
        self.assertEqual(row["server_attempted_bytes"], 140.0)
        self.assertEqual(row["server_sent_bytes"], 95.0)
        self.assertEqual(row["total_attempted_bytes"], 550.0)
        self.assertEqual(row["total_sent_bytes"], 425.0)
        self.assertEqual(row["retransmissions"], 5.0)
        self.assertEqual(row["duplicates_at_server"], 3.0)
        self.assertEqual(row["elapsed_ms"], 200.0)
        self.assertEqual(row["max_schedule_lateness_ms"], 4.0)

    def test_summary_uses_none_when_confirmation_or_latency_samples_do_not_exist(self):
        row = summarize(
            [
                self.case(
                    readings_generated=1,
                    readings_received=0,
                    latest_generated=1,
                    latest_received=0,
                    latest_final_expected=1,
                    latest_final_matching=0,
                    alerts_generated=1,
                    alerts_received=0,
                    critical_confirmed=None,
                    accept_latency_samples=[],
                    ack_latency_samples=[],
                    client_attempted_bytes=100,
                    client_sent_bytes=50,
                    server_attempted_bytes=0,
                    server_sent_bytes=0,
                    retries=0,
                    duplicates=0,
                    elapsed_ms=10,
                    max_schedule_lateness_ms=0,
                )
            ]
        )[0]

        self.assertIsNone(row["critical_confirmation_rate"])
        self.assertIsNone(row["critical_server_accept_p95_ms"])
        self.assertIsNone(row["critical_ack_p95_ms"])
        self.assertEqual(row["critical_server_accept_sample_count"], 0)
        self.assertEqual(row["critical_ack_sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
