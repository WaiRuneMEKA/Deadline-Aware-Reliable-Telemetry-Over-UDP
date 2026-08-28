"""Regression tests for socket/executor cleanup during initialization failures."""

from __future__ import annotations

import unittest
from unittest import mock

from dart.client import SensorClient
from dart.server import DartServer


class FakeSocket:
    def __init__(self, *, bind_error=None):
        self.bind_error = bind_error
        self.close_calls = 0
        self.bound_address = None
        self.timeout = None

    def setsockopt(self, *_args):
        return None

    def bind(self, address):
        if self.bind_error is not None:
            raise self.bind_error
        self.bound_address = address

    def settimeout(self, timeout):
        self.timeout = timeout

    def getsockname(self):
        return ("127.0.0.1", 54321)

    def close(self):
        self.close_calls += 1


class FakeExecutor:
    def __init__(self):
        self.shutdown_calls = []

    def shutdown(self, *, wait=True, cancel_futures=False):
        self.shutdown_calls.append((wait, cancel_futures))


class FailingThread:
    def __init__(self, error):
        self.error = error
        self.start_calls = 0

    def start(self):
        self.start_calls += 1
        raise self.error


class ImpairmentPreallocationTests(unittest.TestCase):
    def test_client_rejects_invalid_impairment_before_dns_or_socket_allocation(self):
        cases = (
            {"loss_rate": -0.01},
            {"loss_rate": 1.01},
            {"loss_rate": float("nan")},
            {"corrupt_rate": -0.01},
            {"corrupt_rate": 1.01},
            {"corrupt_rate": float("inf")},
            {"network_delay_ms": -1},
            {"network_delay_ms": float("nan")},
            {"network_delay_ms": float("inf")},
            {"jitter_ms": -1},
            {"jitter_ms": float("nan")},
            {"jitter_ms": float("-inf")},
        )
        with mock.patch("dart.client.socket.socket") as socket_constructor, mock.patch(
            "dart.client.socket.gethostbyname"
        ) as resolver:
            for kwargs in cases:
                with self.subTest(kwargs=kwargs):
                    socket_constructor.reset_mock()
                    resolver.reset_mock()
                    with self.assertRaises(ValueError):
                        SensorClient(
                            ("must-not-resolve.invalid", 9999),
                            sensor_id=1,
                            **kwargs,
                        )
                    resolver.assert_not_called()
                    socket_constructor.assert_not_called()

    def test_client_rejects_nonfinite_ack_timeout_before_socket_allocation(self):
        with mock.patch("dart.client.socket.socket") as socket_constructor, mock.patch(
            "dart.client.socket.gethostbyname"
        ) as resolver:
            for timeout in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(timeout=timeout):
                    with self.assertRaises(ValueError):
                        SensorClient(
                            ("must-not-resolve.invalid", 9999),
                            sensor_id=1,
                            ack_timeout_s=timeout,
                        )
            resolver.assert_not_called()
            socket_constructor.assert_not_called()

    def test_client_rejects_invalid_instance_id_before_dns_or_socket_allocation(self):
        invalid_values = (
            "",
            "ab" * 15,
            "ab" * 17,
            "z" * 32,
            123,
        )
        with mock.patch("dart.client.socket.socket") as socket_constructor, mock.patch(
            "dart.client.socket.gethostbyname"
        ) as resolver:
            for instance_id in invalid_values:
                with self.subTest(instance_id=instance_id):
                    with self.assertRaises(ValueError):
                        SensorClient(
                            ("must-not-resolve.invalid", 9999),
                            sensor_id=1,
                            client_instance_id=instance_id,
                        )
            resolver.assert_not_called()
            socket_constructor.assert_not_called()

    def test_server_rejects_invalid_impairment_without_socket_allocation(self):
        cases = (
            {"ack_loss_rate": -0.01},
            {"ack_loss_rate": 1.01},
            {"ack_loss_rate": float("nan")},
            {"ack_corrupt_rate": -0.01},
            {"ack_corrupt_rate": 1.01},
            {"ack_corrupt_rate": float("inf")},
            {"network_delay_ms": -1},
            {"network_delay_ms": float("nan")},
            {"network_delay_ms": float("inf")},
            {"jitter_ms": -1},
            {"jitter_ms": float("nan")},
            {"jitter_ms": float("-inf")},
        )
        with mock.patch("dart.server.socket.socket") as socket_constructor:
            for kwargs in cases:
                with self.subTest(kwargs=kwargs):
                    socket_constructor.reset_mock()
                    with self.assertRaises(ValueError):
                        DartServer(port=0, quiet=True, **kwargs)
                    socket_constructor.assert_not_called()

    def test_server_rejects_nonfinite_duplicate_retention_without_socket_allocation(self):
        with mock.patch("dart.server.socket.socket") as socket_constructor:
            for retention in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(retention=retention):
                    with self.assertRaises(ValueError):
                        DartServer(
                            port=0,
                            quiet=True,
                            duplicate_retention_s=retention,
                        )
            socket_constructor.assert_not_called()


class SensorClientInitializationCleanupTests(unittest.TestCase):
    @staticmethod
    def construct(client, **kwargs):
        SensorClient.__init__(
            client,
            ("127.0.0.1", 9999),
            sensor_id=7,
            quiet=True,
            **kwargs,
        )

    def test_bind_failure_closes_socket_and_clears_running_state(self):
        sock = FakeSocket(bind_error=PermissionError("bind failed"))
        client = SensorClient.__new__(SensorClient)

        with mock.patch("dart.client.socket.socket", return_value=sock):
            with self.assertRaisesRegex(PermissionError, "bind failed"):
                self.construct(client)

        self.assertEqual(sock.close_calls, 1)
        self.assertFalse(client._running.is_set())

    def test_transmitter_setup_failure_closes_bound_socket(self):
        sock = FakeSocket()
        client = SensorClient.__new__(SensorClient)

        with mock.patch("dart.client.socket.socket", return_value=sock), mock.patch(
            "dart.client.ImpairedTransmitter",
            side_effect=RuntimeError("transmitter setup failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "transmitter setup failed"):
                self.construct(client)

        self.assertEqual(sock.close_calls, 1)
        self.assertEqual(sock.bound_address, ("127.0.0.1", 0))
        self.assertFalse(client._running.is_set())

    def test_receiver_thread_start_failure_closes_socket(self):
        sock = FakeSocket()
        receiver = FailingThread(RuntimeError("thread start failed"))
        client = SensorClient.__new__(SensorClient)

        with mock.patch("dart.client.socket.socket", return_value=sock), mock.patch(
            "dart.client.ImpairedTransmitter", return_value=object()
        ), mock.patch("dart.client.threading.Thread", return_value=receiver):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                self.construct(client)

        self.assertEqual(receiver.start_calls, 1)
        self.assertEqual(sock.close_calls, 1)
        self.assertFalse(client._running.is_set())


class DartServerStartCleanupTests(unittest.TestCase):
    def assert_server_reset(self, server):
        self.assertFalse(server._running.is_set())
        self.assertIsNone(server._socket)
        self.assertIsNone(server._transmitter)
        self.assertIsNone(server._executor)
        self.assertIsNone(server._receiver_thread)

    def test_bind_failure_closes_socket_and_resets_server_fields(self):
        server = DartServer(port=0, quiet=True)
        sock = FakeSocket(bind_error=PermissionError("bind failed"))

        with mock.patch("dart.server.socket.socket", return_value=sock):
            with self.assertRaisesRegex(PermissionError, "bind failed"):
                server.start()

        self.assertEqual(sock.close_calls, 1)
        self.assert_server_reset(server)

    def test_transmitter_setup_failure_closes_socket_and_resets_server_fields(self):
        server = DartServer(port=0, quiet=True)
        sock = FakeSocket()

        with mock.patch("dart.server.socket.socket", return_value=sock), mock.patch(
            "dart.server.ImpairedTransmitter",
            side_effect=RuntimeError("transmitter setup failed"),
        ), mock.patch("dart.server.ThreadPoolExecutor") as executor_constructor:
            with self.assertRaisesRegex(RuntimeError, "transmitter setup failed"):
                server.start()

        executor_constructor.assert_not_called()
        self.assertEqual(sock.close_calls, 1)
        self.assert_server_reset(server)

    def test_thread_start_failure_closes_socket_shuts_executor_and_resets_fields(self):
        server = DartServer(port=0, quiet=True)
        sock = FakeSocket()
        transmitter = object()
        executor = FakeExecutor()
        receiver = FailingThread(RuntimeError("thread start failed"))

        with mock.patch("dart.server.socket.socket", return_value=sock), mock.patch(
            "dart.server.ImpairedTransmitter", return_value=transmitter
        ), mock.patch(
            "dart.server.ThreadPoolExecutor", return_value=executor
        ), mock.patch("dart.server.threading.Thread", return_value=receiver):
            with self.assertRaisesRegex(RuntimeError, "thread start failed"):
                server.start()

        self.assertEqual(receiver.start_calls, 1)
        self.assertEqual(executor.shutdown_calls, [(True, True)])
        self.assertEqual(sock.close_calls, 1)
        self.assert_server_reset(server)


if __name__ == "__main__":
    unittest.main()
