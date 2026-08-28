"""Focused lifecycle and input-boundary tests for the sensor simulator."""

from __future__ import annotations

from contextlib import redirect_stderr
import io
from types import SimpleNamespace
import unittest
from unittest import mock

from dart.protocol import MAX_BATCH_READINGS, MetricId, Reading
from dart.simulator import (
    Policy,
    _scheduled_offsets,
    _with_batch_ages,
    main,
    run_simulation,
)


class SimulationLifecycleTests(unittest.TestCase):
    def test_scheduled_offsets_rejects_nonfinite_inputs(self):
        cases = (
            (float("nan"), 0.1),
            (float("inf"), 0.1),
            (float("-inf"), 0.1),
            (1.0, float("nan")),
            (1.0, float("inf")),
            (1.0, float("-inf")),
        )
        for duration_s, interval_s in cases:
            with self.subTest(duration_s=duration_s, interval_s=interval_s):
                with self.assertRaises(ValueError):
                    _scheduled_offsets(duration_s, interval_s)

    def test_run_simulation_rejects_nonfinite_schedule_before_client_creation(self):
        cases = (
            {"duration_s": float("nan")},
            {"duration_s": float("inf")},
            {"duration_s": float("-inf")},
            {"sample_interval_s": float("nan")},
            {"sample_interval_s": float("inf")},
            {"sample_interval_s": float("-inf")},
        )
        with mock.patch("dart.simulator.SensorClient") as client_constructor:
            for kwargs in cases:
                with self.subTest(kwargs=kwargs):
                    client_constructor.reset_mock()
                    schedule = {
                        "duration_s": 1.0,
                        "sample_interval_s": 0.1,
                    }
                    schedule.update(kwargs)
                    with self.assertRaises(ValueError):
                        run_simulation(
                            ("127.0.0.1", 9999),
                            sensors=1,
                            quiet=True,
                            **schedule,
                        )
                    client_constructor.assert_not_called()

    def test_cli_rejects_representative_nonfinite_values(self):
        cases = (
            ["--duration", "nan", "--quiet"],
            ["--interval", "inf", "--quiet"],
            ["--delay-ms", "-inf", "--quiet"],
        )
        for argv in cases:
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main(argv)
                self.assertEqual(raised.exception.code, 2)

    def test_batch_ages_use_actual_monotonic_capture_timestamps(self):
        captured = [
            (Reading(MetricId.TEMPERATURE_C, 20.0), 99.5),
            (Reading(MetricId.HUMIDITY_PERCENT, 50.0), 99.875),
            (Reading(MetricId.SMOKE_PPM, 1.0), 100.25),
            (Reading(MetricId.BATTERY_PERCENT, 90.0), 0.0),
        ]

        with mock.patch("dart.simulator.time.monotonic", return_value=100.0):
            aged = _with_batch_ages(captured)

        self.assertEqual([reading.age_ms for reading in aged], [500, 125, 0, 65_535])
        self.assertEqual(
            [reading.metric_id for reading in aged],
            [reading.metric_id for reading, _captured_at in captured],
        )
        self.assertEqual(
            [reading.value for reading in aged],
            [reading.value for reading, _captured_at in captured],
        )

    def test_worker_exception_propagates_and_every_registered_client_closes(self):
        instances = []

        class FailingSensorClient:
            def __init__(self, _server_address, *, sensor_id, **_kwargs):
                self.sensor_id = sensor_id
                self.close_calls = 0
                instances.append(self)

            def register(self):
                return SimpleNamespace(success=True, detail="registered")

            def send_batch(self, _readings, *, reliable=False, **_kwargs):
                raise RuntimeError(f"worker boom from sensor {self.sensor_id}")

            def close(self):
                self.close_calls += 1

            def snapshot_metrics(self):
                raise AssertionError("failed simulation must not snapshot clients")

        with mock.patch("dart.simulator.SensorClient", FailingSensorClient):
            with self.assertRaisesRegex(
                RuntimeError, r"3 sensor worker\(s\) failed:.*worker boom"
            ):
                run_simulation(
                    ("127.0.0.1", 9999),
                    sensors=3,
                    duration_s=0.01,
                    sample_interval_s=0.01,
                    batch_size=1,
                    policy=Policy.DART,
                    alert_at_s=2.0,
                    quiet=True,
                )

        self.assertEqual(len(instances), 3)
        self.assertEqual([client.sensor_id for client in instances], [1, 2, 3])
        self.assertTrue(all(client.close_calls == 1 for client in instances))

    def test_batch_size_above_wire_protocol_max_is_rejected_before_client_creation(self):
        with mock.patch("dart.simulator.SensorClient") as client_constructor:
            with self.assertRaisesRegex(
                ValueError, rf"batch_size must not exceed {MAX_BATCH_READINGS}"
            ):
                run_simulation(
                    ("127.0.0.1", 9999),
                    sensors=1,
                    duration_s=0.01,
                    sample_interval_s=0.01,
                    batch_size=MAX_BATCH_READINGS + 1,
                    quiet=True,
                )

        client_constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
