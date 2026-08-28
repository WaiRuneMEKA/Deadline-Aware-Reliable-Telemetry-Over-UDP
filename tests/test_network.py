"""Tests for portable packet-loss and corruption simulation."""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from dart.network import ImpairedTransmitter


class RecordingSocket:
    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()

    def sendto(self, data, address):
        with self._lock:
            self.calls.append((bytes(data), address))
        return len(data)


class ImpairedTransmitterTests(unittest.TestCase):
    def test_clean_transmission_and_counters(self):
        sock = RecordingSocket()
        transmitter = ImpairedTransmitter(sock, seed=10)

        outcome = transmitter.sendto(b"DART-packet", ("127.0.0.1", 9999))

        self.assertTrue(outcome.sent)
        self.assertFalse(outcome.dropped)
        self.assertFalse(outcome.corrupted)
        self.assertEqual(outcome.wire_bytes, 11)
        self.assertEqual(sock.calls, [(b"DART-packet", ("127.0.0.1", 9999))])
        self.assertEqual(
            transmitter.stats.as_dict(),
            {
                "attempted_packets": 1,
                "sent_packets": 1,
                "dropped_packets": 0,
                "corrupted_packets": 0,
                "attempted_bytes": 11,
                "sent_bytes": 11,
            },
        )

    def test_total_loss_never_calls_socket_and_counts_attempt(self):
        events = []
        sock = RecordingSocket()
        transmitter = ImpairedTransmitter(
            sock, loss_rate=1.0, seed=20, on_event=events.append
        )

        first = transmitter.sendto(b"one", ("127.0.0.1", 9999))
        second = transmitter.sendto(b"12345", ("127.0.0.1", 9999))

        self.assertTrue(first.dropped)
        self.assertFalse(first.sent)
        self.assertTrue(second.dropped)
        self.assertEqual(sock.calls, [])
        self.assertEqual(len(events), 2)
        self.assertEqual(
            transmitter.stats.as_dict(),
            {
                "attempted_packets": 2,
                "sent_packets": 0,
                "dropped_packets": 2,
                "corrupted_packets": 0,
                "attempted_bytes": 8,
                "sent_bytes": 0,
            },
        )

    def test_total_corruption_changes_data_but_preserves_length(self):
        events = []
        sock = RecordingSocket()
        transmitter = ImpairedTransmitter(
            sock, corrupt_rate=1.0, seed=30, on_event=events.append
        )

        source = b"abcdefgh"
        outcome = transmitter.sendto(source, ("127.0.0.1", 1234))

        self.assertTrue(outcome.sent)
        self.assertTrue(outcome.corrupted)
        self.assertFalse(outcome.dropped)
        self.assertEqual(len(sock.calls), 1)
        self.assertNotEqual(sock.calls[0][0], source)
        self.assertEqual(len(sock.calls[0][0]), len(source))
        self.assertEqual(len(events), 1)
        self.assertEqual(transmitter.stats.attempted_packets, 1)
        self.assertEqual(transmitter.stats.sent_packets, 1)
        self.assertEqual(transmitter.stats.corrupted_packets, 1)
        self.assertEqual(transmitter.stats.attempted_bytes, len(source))
        self.assertEqual(transmitter.stats.sent_bytes, len(source))

    def test_empty_datagram_cannot_be_corrupted(self):
        sock = RecordingSocket()
        transmitter = ImpairedTransmitter(sock, corrupt_rate=1.0, seed=40)

        outcome = transmitter.sendto(b"", ("127.0.0.1", 9999))

        self.assertTrue(outcome.sent)
        self.assertFalse(outcome.corrupted)
        self.assertEqual(transmitter.stats.corrupted_packets, 0)
        self.assertEqual(sock.calls[0][0], b"")

    def test_seed_reproduces_outcomes_and_accounting(self):
        payloads = [bytes([index]) * (index + 1) for index in range(24)]

        def exercise():
            sock = RecordingSocket()
            transmitter = ImpairedTransmitter(
                sock,
                loss_rate=0.35,
                corrupt_rate=0.40,
                seed=2026,
            )
            outcomes = [
                transmitter.sendto(payload, ("127.0.0.1", 9999))
                for payload in payloads
            ]
            signature = [
                (outcome.sent, outcome.dropped, outcome.corrupted)
                for outcome in outcomes
            ]
            return signature, transmitter.stats.as_dict(), sock.calls

        signature_a, stats_a, calls_a = exercise()
        signature_b, stats_b, calls_b = exercise()

        self.assertEqual(signature_a, signature_b)
        self.assertEqual(stats_a, stats_b)
        self.assertEqual(calls_a, calls_b)
        self.assertEqual(stats_a["attempted_packets"], len(payloads))
        self.assertEqual(
            stats_a["sent_packets"] + stats_a["dropped_packets"],
            stats_a["attempted_packets"],
        )
        self.assertLessEqual(stats_a["corrupted_packets"], stats_a["sent_packets"])
        self.assertEqual(stats_a["sent_packets"], len(calls_a))
        self.assertEqual(
            stats_a["attempted_bytes"], sum(len(payload) for payload in payloads)
        )
        self.assertEqual(
            stats_a["sent_bytes"], sum(len(data) for data, _address in calls_a)
        )

    def test_invalid_configuration_is_rejected(self):
        sock = RecordingSocket()
        cases = (
            {"loss_rate": -0.01},
            {"loss_rate": 1.01},
            {"loss_rate": float("nan")},
            {"loss_rate": float("inf")},
            {"corrupt_rate": -0.01},
            {"corrupt_rate": 1.01},
            {"corrupt_rate": float("nan")},
            {"corrupt_rate": float("inf")},
            {"delay_ms": -1},
            {"delay_ms": float("nan")},
            {"delay_ms": float("inf")},
            {"delay_ms": float("-inf")},
            {"jitter_ms": -1},
            {"jitter_ms": float("nan")},
            {"jitter_ms": float("inf")},
            {"jitter_ms": float("-inf")},
        )
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    ImpairedTransmitter(sock, **kwargs)

    def test_two_delayed_transmissions_progress_in_parallel(self):
        delay_s = 0.15
        sock = RecordingSocket()
        transmitter = ImpairedTransmitter(sock, delay_ms=delay_s * 1000, seed=50)
        start_barrier = threading.Barrier(3)
        delay_barrier = threading.Barrier(2)
        real_sleep = time.sleep
        sleep_arguments = []
        outcomes = []
        errors = []

        def synchronized_sleep(seconds):
            sleep_arguments.append(seconds)
            # If the transmitter still holds its lock across delay, the second
            # sender cannot arrive here and this barrier deterministically breaks.
            delay_barrier.wait(timeout=1.0)
            real_sleep(seconds)

        def worker(payload):
            try:
                start_barrier.wait(timeout=1.0)
                outcomes.append(
                    transmitter.sendto(payload, ("127.0.0.1", 9999))
                )
            except BaseException as exc:  # asserted in the parent test thread
                errors.append(exc)

        with mock.patch("dart.network.time.sleep", side_effect=synchronized_sleep):
            threads = [
                threading.Thread(target=worker, args=(payload,))
                for payload in (b"first", b"second")
            ]
            for thread in threads:
                thread.start()
            started = time.monotonic()
            start_barrier.wait(timeout=1.0)
            for thread in threads:
                thread.join(timeout=2.0)
            elapsed = time.monotonic() - started

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(outcome.sent for outcome in outcomes))
        self.assertEqual(len(sleep_arguments), 2)
        self.assertTrue(
            all(abs(seconds - delay_s) < 1e-9 for seconds in sleep_arguments)
        )
        self.assertGreaterEqual(elapsed, delay_s * 0.8)
        self.assertLess(
            elapsed,
            delay_s * 1.75,
            f"two {delay_s:.2f}s delays serialized: elapsed={elapsed:.3f}s",
        )
        self.assertEqual(transmitter.stats.attempted_packets, 2)
        self.assertEqual(transmitter.stats.sent_packets, 2)
        self.assertEqual(len(sock.calls), 2)


if __name__ == "__main__":
    unittest.main()
