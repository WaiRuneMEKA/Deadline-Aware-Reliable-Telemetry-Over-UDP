"""Server state-machine and localhost integration tests for DART."""

from __future__ import annotations

from dataclasses import replace
import threading
import unittest
from unittest import mock

from dart.client import ClientMetrics, SensorClient, _PendingResponse
from dart.protocol import (
    DartPacket,
    DeliveryClass,
    Flags,
    MessageType,
    MetricId,
    PayloadError,
    ProtocolError,
    Reading,
    StatusCode,
    decode_json,
    encode_json,
    encode_latest,
    encode_readings,
)
from dart.server import DartServer, _sequence_is_newer


class SequenceAndServerStateTests(unittest.TestCase):
    def test_wrapping_uint32_sequence_comparison(self):
        self.assertTrue(_sequence_is_newer(11, 10))
        self.assertFalse(_sequence_is_newer(10, 10))
        self.assertFalse(_sequence_is_newer(9, 10))
        self.assertTrue(_sequence_is_newer(1, 0xFFFFFFFF))
        self.assertFalse(_sequence_is_newer(0xFFFFFFFF, 1))
        self.assertFalse(_sequence_is_newer(0x80000000, 0))

    def test_client_sequence_skips_zero_after_wrap(self):
        client = SensorClient.__new__(SensorClient)
        client._sequence_lock = threading.Lock()
        client._sequence = 0xFFFFFFFF

        self.assertEqual(client._next_sequence(), 0xFFFFFFFF)
        self.assertEqual(client._next_sequence(), 1)
        self.assertEqual(client._next_sequence(), 2)

    def test_duplicate_identity_includes_session_sensor_type_and_sequence(self):
        server = DartServer(port=0, quiet=True)
        packet = DartPacket(
            msg_type=MessageType.DATA_BATCH,
            delivery=DeliveryClass.BEST_EFFORT_BATCH,
            session_id=100,
            sensor_id=7,
            sequence=55,
        )

        self.assertFalse(server._is_duplicate(packet))
        self.assertTrue(server._is_duplicate(packet))
        self.assertFalse(server._is_duplicate(replace(packet, sequence=56)))
        self.assertFalse(server._is_duplicate(replace(packet, sensor_id=8)))
        self.assertFalse(server._is_duplicate(replace(packet, session_id=101)))
        self.assertFalse(
            server._is_duplicate(
                replace(
                    packet,
                    msg_type=MessageType.LATEST_UPDATE,
                    delivery=DeliveryClass.LATEST_ONLY,
                )
            )
        )

    def test_duplicate_hit_preserves_timestamp_order_for_retention_pruning(self):
        server = DartServer(port=0, quiet=True, duplicate_retention_s=10.0)
        old_packet = DartPacket(
            msg_type=MessageType.DATA_BATCH,
            delivery=DeliveryClass.BEST_EFFORT_BATCH,
            session_id=100,
            sensor_id=7,
            sequence=1,
        )
        fresh_packet = replace(old_packet, sequence=2)
        old_key = server._message_identity(old_packet)
        fresh_key = server._message_identity(fresh_packet)
        server._seen[old_key] = 10.0
        server._seen[fresh_key] = 14.0

        # At t=15 neither entry is stale. A duplicate lookup must not turn the
        # timestamp-ordered cache into access/LRU order by moving old_key to end.
        with mock.patch("dart.server.time.monotonic", return_value=15.0):
            self.assertEqual(server._claim_message(old_packet), "duplicate")
        self.assertEqual(list(server._seen), [old_key, fresh_key])

        # At t=23 only old_key is outside the 10-second retention window. If the
        # duplicate lookup reordered it behind fresh_key, pruning would stop early.
        with mock.patch("dart.server.time.monotonic", return_value=23.0):
            with server._duplicate_condition:
                server._prune_seen_locked()
        self.assertNotIn(old_key, server._seen)
        self.assertIn(fresh_key, server._seen)

    def test_latest_only_replaces_newer_and_discards_stale_sequence(self):
        server = DartServer(port=0, quiet=True)

        def latest(sequence, value):
            return DartPacket(
                msg_type=MessageType.LATEST_UPDATE,
                delivery=DeliveryClass.LATEST_ONLY,
                session_id=100,
                sensor_id=3,
                sequence=sequence,
                timestamp_ms=1_700_000_000_000 + sequence,
                payload=encode_latest(MetricId.POSITION_X, value),
            )

        first_status = server._dispatch(latest(100, 10.0), ("127.0.0.1", 10000))
        stale_status = server._dispatch(latest(99, -1.0), ("127.0.0.1", 10000))
        newer_status = server._dispatch(latest(101, 20.0), ("127.0.0.1", 10000))

        self.assertEqual(first_status, StatusCode.ACCEPTED)
        self.assertEqual(stale_status, StatusCode.DUPLICATE)
        self.assertEqual(newer_status, StatusCode.ACCEPTED)
        metrics = server.snapshot_metrics()
        self.assertEqual(metrics["latest_updates_accepted"], 2)
        self.assertEqual(metrics["stale_latest_discarded"], 1)
        self.assertEqual(
            metrics["latest_values"]["session_100.sensor_3.POSITION_X"]["value"],
            20.0,
        )

    def test_latest_sequence_spaces_are_independent_across_sessions(self):
        server = DartServer(port=0, quiet=True)
        sensor_id = 3
        session_a = 0xA0A0A0A0
        session_b = 0xB0B0B0B0
        # These deliberately distant starting values model the independent,
        # randomized sequence spaces of two registrations using the same sensor ID.
        # If latest state were keyed only by sensor/metric, B's first update would
        # be considered stale relative to A's sequence.
        sequence_a = 0x60000000
        sequence_b = 0x10000000

        def latest(session_id, sequence, value):
            return DartPacket(
                msg_type=MessageType.LATEST_UPDATE,
                delivery=DeliveryClass.LATEST_ONLY,
                session_id=session_id,
                sensor_id=sensor_id,
                sequence=sequence,
                timestamp_ms=1_700_000_000_000 + sequence,
                payload=encode_latest(MetricId.POSITION_X, value),
            )

        statuses = [
            server._dispatch(
                latest(session_a, sequence_a, 10.0), ("127.0.0.1", 10000)
            ),
            server._dispatch(
                latest(session_b, sequence_b, 20.0), ("127.0.0.1", 10001)
            ),
            server._dispatch(
                latest(session_a, sequence_a + 1, 11.0), ("127.0.0.1", 10000)
            ),
            server._dispatch(
                latest(session_b, sequence_b + 1, 21.0), ("127.0.0.1", 10001)
            ),
        ]

        self.assertEqual(statuses, [StatusCode.ACCEPTED] * 4)
        metrics = server.snapshot_metrics()
        key_a = f"session_{session_a}.sensor_{sensor_id}.POSITION_X"
        key_b = f"session_{session_b}.sensor_{sensor_id}.POSITION_X"
        self.assertEqual(metrics["latest_values"][key_a]["value"], 11.0)
        self.assertEqual(metrics["latest_values"][key_a]["sequence"], sequence_a + 1)
        self.assertEqual(metrics["latest_values"][key_b]["value"], 21.0)
        self.assertEqual(metrics["latest_values"][key_b]["sequence"], sequence_b + 1)
        self.assertEqual(metrics["latest_updates_accepted"], 4)
        self.assertEqual(metrics["stale_latest_discarded"], 0)

    def test_server_marks_expired_packet_and_returns_expired_ack(self):
        server = DartServer(port=0, quiet=True)
        packet = DartPacket(
            msg_type=MessageType.CRITICAL_ALERT,
            delivery=DeliveryClass.CRITICAL_RELIABLE,
            flags=Flags.ACK_REQUIRED,
            session_id=10,
            sensor_id=20,
            sequence=30,
            timestamp_ms=1,
            ttl_ms=1,
            payload=encode_json({"alert_type": "FIRE_DETECTED"}),
        )
        address = ("127.0.0.1", 12345)

        with mock.patch.object(
            server, "_valid_session", return_value=True
        ), mock.patch.object(server, "_send_ack") as send_ack:
            server._process_datagram(packet.encode(), address)

        send_ack.assert_called_once_with(packet, address, StatusCode.EXPIRED)
        metrics = server.snapshot_metrics()
        self.assertEqual(metrics["expired_packets"], 1)
        self.assertEqual(metrics["unregistered_packets"], 0)

    def test_unregistered_status_takes_precedence_over_expiry(self):
        server = DartServer(port=0, quiet=True)
        packet = DartPacket(
            msg_type=MessageType.CRITICAL_ALERT,
            delivery=DeliveryClass.CRITICAL_RELIABLE,
            flags=Flags.ACK_REQUIRED,
            session_id=999,
            sensor_id=20,
            sequence=31,
            timestamp_ms=1,
            ttl_ms=1,
            payload=encode_json({"alert_type": "FIRE_DETECTED"}),
        )
        responses = []

        def capture_send(response, _destination):
            responses.append(response)
            return True

        with mock.patch.object(server, "_send", side_effect=capture_send):
            server._process_datagram(packet.encode(), ("127.0.0.1", 12345))

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].msg_type, MessageType.ERROR)
        self.assertEqual(responses[0].status_code, StatusCode.UNREGISTERED)
        metrics = server.snapshot_metrics()
        self.assertEqual(metrics["unregistered_packets"], 1)
        self.assertEqual(metrics["expired_packets"], 0)

    def test_server_counts_checksum_and_general_decode_errors(self):
        server = DartServer(port=0, quiet=True)
        valid = bytearray(
            DartPacket(
                msg_type=MessageType.HEARTBEAT,
                delivery=DeliveryClass.CONTROL,
                payload=b"crc",
            ).encode()
        )
        valid[-1] ^= 1

        server._process_datagram(bytes(valid), ("127.0.0.1", 12345))
        server._process_datagram(b"not-a-DART-packet", ("127.0.0.1", 12345))

        metrics = server.snapshot_metrics()
        self.assertEqual(metrics["checksum_errors"], 1)
        self.assertEqual(metrics["decode_errors"], 2)

    def test_invalid_payload_does_not_poison_duplicate_identity(self):
        """A corrected retry must not be rejected because validation failed first."""
        server = DartServer(
            port=0, quiet=True, allow_experimental_policies=True
        )
        address = ("127.0.0.1", 12345)
        identity = {
            "msg_type": MessageType.DATA_BATCH,
            "delivery": DeliveryClass.BEST_EFFORT_BATCH,
            "flags": Flags.ACK_REQUIRED,
            "session_id": 100,
            "sensor_id": 7,
            "sequence": 55,
            "timestamp_ms": 1_700_000_000_000,
            "ttl_ms": 0,
        }
        invalid = DartPacket(payload=b"not-a-reading-batch", **identity)
        corrected = DartPacket(
            payload=encode_readings(
                [Reading(MetricId.TEMPERATURE_C, 26.5, age_ms=0)]
            ),
            **identity,
        )
        responses = []

        def capture_send(packet, destination):
            responses.append((packet, destination))
            return True

        with mock.patch.object(server, "_valid_session", return_value=True), mock.patch.object(
            server, "_send", side_effect=capture_send
        ):
            # Both calls enter through wire decoding, so both datagrams have valid CRCs.
            server._process_datagram(invalid.encode(), address)
            server._process_datagram(corrected.encode(), address)

        self.assertEqual(len(responses), 2)
        first_response, first_destination = responses[0]
        second_response, second_destination = responses[1]
        self.assertEqual(first_destination, address)
        self.assertEqual(first_response.msg_type, MessageType.ERROR)
        self.assertEqual(first_response.status_code, StatusCode.INVALID_PAYLOAD)
        self.assertEqual(second_destination, address)
        self.assertEqual(second_response.msg_type, MessageType.ACK)
        self.assertEqual(second_response.status_code, StatusCode.ACCEPTED)
        self.assertEqual(second_response.sequence, corrected.sequence)

        metrics = server.snapshot_metrics()
        self.assertEqual(metrics["data_batches_accepted"], 1)
        self.assertEqual(metrics["readings_received"], 1)
        self.assertEqual(metrics["duplicate_packets"], 0)


class RegistrationIdentityTests(unittest.TestCase):
    _MISSING = object()

    @classmethod
    def registration_packet(
        cls,
        *,
        sequence: int,
        client_instance_id=_MISSING,
        sensor_id: int = 7,
    ) -> DartPacket:
        body = {"name": "restartable-sensor", "capabilities": ["position"]}
        if client_instance_id is not cls._MISSING:
            body["client_instance_id"] = client_instance_id
        return DartPacket(
            msg_type=MessageType.REGISTER_REQ,
            delivery=DeliveryClass.CONTROL,
            flags=Flags.ACK_REQUIRED,
            sensor_id=sensor_id,
            sequence=sequence,
            ttl_ms=0,
            payload=encode_json(body),
        )

    @staticmethod
    def deliver_registration(server, packet, address):
        responses = []

        def capture_send(response, destination):
            responses.append((response, destination))
            return True

        with mock.patch.object(server, "_send", side_effect=capture_send):
            server._handle_register(packet, address)
        return responses

    def test_same_client_instance_reuses_session_for_registration_retry(self):
        server = DartServer(port=0, quiet=True)
        address = ("127.0.0.1", 12345)
        instance_id = "ab" * 16
        first = self.registration_packet(
            sequence=100, client_instance_id=instance_id.upper()
        )
        retry = self.registration_packet(
            sequence=100, client_instance_id=instance_id
        )

        first_response = self.deliver_registration(server, first, address)[0][0]
        retry_response = self.deliver_registration(server, retry, address)[0][0]

        self.assertEqual(first_response.msg_type, MessageType.REGISTER_RES)
        self.assertEqual(retry_response.msg_type, MessageType.REGISTER_RES)
        self.assertEqual(first_response.session_id, retry_response.session_id)
        self.assertEqual(server.snapshot_metrics()["active_sessions"], 1)

    def test_new_instance_on_reused_address_gets_independent_latest_state(self):
        server = DartServer(port=0, quiet=True)
        address = ("127.0.0.1", 12345)
        old_registration = self.registration_packet(
            sequence=100,
            client_instance_id="11" * 16,
        )
        # The new process deliberately begins at the same registration sequence
        # and source address.  The instance nonce must still distinguish it.
        new_registration = self.registration_packet(
            sequence=100,
            client_instance_id="22" * 16,
        )
        old_session = self.deliver_registration(
            server, old_registration, address
        )[0][0].session_id
        new_session = self.deliver_registration(
            server, new_registration, address
        )[0][0].session_id

        self.assertNotEqual(old_session, new_session)
        old_latest = DartPacket(
            msg_type=MessageType.LATEST_UPDATE,
            delivery=DeliveryClass.LATEST_ONLY,
            session_id=old_session,
            sensor_id=7,
            sequence=0x60000000,
            ttl_ms=0,
            payload=encode_latest(MetricId.POSITION_X, 1.0),
        )
        new_latest = replace(
            old_latest,
            session_id=new_session,
            sequence=0x10000000,
            payload=encode_latest(MetricId.POSITION_X, 2.0),
        )

        self.assertEqual(
            server._dispatch(old_latest, address), StatusCode.ACCEPTED
        )
        self.assertEqual(
            server._dispatch(new_latest, address), StatusCode.ACCEPTED
        )
        metrics = server.snapshot_metrics()
        self.assertEqual(metrics["active_sessions"], 2)
        self.assertEqual(
            metrics[
                "latest_values"
            ][f"session_{new_session}.sensor_7.POSITION_X"]["value"],
            2.0,
        )

    def test_legacy_registration_uses_message_scoped_compatibility_key(self):
        server = DartServer(port=0, quiet=True)
        address = ("127.0.0.1", 12345)
        first = self.registration_packet(sequence=100)
        retry = replace(first)
        fresh_request = replace(first, timestamp_ms=first.timestamp_ms + 1)

        first_session = self.deliver_registration(server, first, address)[0][0].session_id
        retry_session = self.deliver_registration(server, retry, address)[0][0].session_id
        fresh_session = self.deliver_registration(
            server, fresh_request, address
        )[0][0].session_id

        self.assertEqual(first_session, retry_session)
        self.assertNotEqual(first_session, fresh_session)

    def test_invalid_client_instance_id_is_rejected(self):
        server = DartServer(port=0, quiet=True)
        address = ("127.0.0.1", 12345)
        packet = self.registration_packet(
            sequence=100,
            client_instance_id="not-a-128-bit-hex-value",
        )

        responses = self.deliver_registration(server, packet, address)

        self.assertEqual(len(responses), 1)
        response, destination = responses[0]
        self.assertEqual(destination, address)
        self.assertEqual(response.msg_type, MessageType.ERROR)
        self.assertEqual(response.status_code, StatusCode.INVALID_PAYLOAD)
        self.assertIn("client_instance_id", decode_json(response.payload)["detail"])
        self.assertEqual(server.snapshot_metrics()["active_sessions"], 0)

    def test_client_register_payload_keeps_instance_id_stable(self):
        client = SensorClient.__new__(SensorClient)
        client.sensor_id = 7
        client.name = "registration-payload-test"
        client.capabilities = ["position"]
        client.client_instance_id = "cd" * 16
        client._sequence_lock = threading.Lock()
        client._sequence = 10
        unsuccessful = mock.Mock(success=False)

        with mock.patch.object(
            client,
            "_send_reliable",
            return_value=(unsuccessful, None),
        ) as send_reliable:
            client.register()
            client.register()

        packets = [call.args[0] for call in send_reliable.call_args_list]
        self.assertEqual([packet.sequence for packet in packets], [10, 11])
        self.assertEqual(
            [decode_json(packet.payload)["client_instance_id"] for packet in packets],
            [client.client_instance_id, client.client_instance_id],
        )


class ServerHardeningTests(unittest.TestCase):
    def test_internal_error_response_does_not_disclose_exception_text(self):
        server = DartServer(port=0, quiet=True)
        address = ("127.0.0.1", 12345)
        packet = DartPacket(
            msg_type=MessageType.CRITICAL_ALERT,
            delivery=DeliveryClass.CRITICAL_RELIABLE,
            flags=Flags.ACK_REQUIRED,
            session_id=100,
            sensor_id=7,
            sequence=55,
            ttl_ms=0,
            payload=encode_json({"alert_type": "FIRE_DETECTED"}),
        )
        responses = []

        def capture_send(response, destination):
            responses.append((response, destination))
            return True

        with mock.patch.object(
            server, "_valid_session", return_value=True
        ), mock.patch.object(
            server, "_dispatch", side_effect=RuntimeError("SECRET implementation path")
        ), mock.patch.object(
            server, "_send", side_effect=capture_send
        ), mock.patch(
            "dart.server.LOGGER.exception"
        ) as log_exception:
            server._process_datagram(packet.encode(), address)

        log_exception.assert_called_once()
        self.assertEqual(len(responses), 1)
        response, destination = responses[0]
        self.assertEqual(destination, address)
        self.assertEqual(response.status_code, StatusCode.INTERNAL_ERROR)
        error = decode_json(response.payload)
        self.assertEqual(error["detail"], "internal server error")
        self.assertNotIn("SECRET", response.payload.decode("utf-8"))

    def test_forced_first_critical_ack_drop_is_scoped_per_session(self):
        server = DartServer(port=0, quiet=True, drop_first_critical_ack=True)
        address = ("127.0.0.1", 12345)

        def critical(session_id, sequence):
            return DartPacket(
                msg_type=MessageType.CRITICAL_ALERT,
                delivery=DeliveryClass.CRITICAL_RELIABLE,
                flags=Flags.ACK_REQUIRED,
                session_id=session_id,
                sensor_id=7,
                sequence=sequence,
                ttl_ms=0,
                payload=encode_json({"alert_type": "FIRE_DETECTED"}),
            )

        with mock.patch.object(server, "_send", return_value=True) as send:
            first_session = critical(100, 1)
            second_session = critical(200, 1)
            server._send_ack(first_session, address, StatusCode.ACCEPTED)
            server._send_ack(first_session, address, StatusCode.DUPLICATE)
            server._send_ack(second_session, address, StatusCode.ACCEPTED)
            server._send_ack(second_session, address, StatusCode.DUPLICATE)

        self.assertEqual(send.call_count, 2)
        metrics = server.snapshot_metrics()
        self.assertEqual(metrics["acks_simulated_dropped"], 2)
        self.assertEqual(metrics["acks_sent"], 2)


class EnvelopeValidationTests(unittest.TestCase):
    def test_server_silently_drops_response_only_client_packets(self):
        packets = [
            DartPacket(
                msg_type=MessageType.ACK,
                delivery=DeliveryClass.CONTROL,
                sensor_id=2,
                sequence=1,
                status_code=StatusCode.ACCEPTED,
            ),
            DartPacket(
                msg_type=MessageType.ERROR,
                delivery=DeliveryClass.CONTROL,
                sensor_id=2,
                sequence=2,
                status_code=StatusCode.MALFORMED,
                payload=encode_json({"error": "MALFORMED"}),
            ),
        ]

        for packet in packets:
            with self.subTest(msg_type=packet.msg_type.name):
                server = DartServer(port=0, quiet=True)
                with mock.patch.object(server, "_send_error") as send_error, mock.patch.object(
                    server, "_send"
                ) as send, mock.patch.object(server, "_valid_session") as valid_session:
                    server._process_datagram(
                        packet.encode(), ("127.0.0.1", 12345)
                    )

                send_error.assert_not_called()
                send.assert_not_called()
                valid_session.assert_not_called()
                metrics = server.snapshot_metrics()
                self.assertEqual(metrics["errors_sent"], 0)
                self.assertEqual(metrics["unregistered_packets"], 0)
                self.assertEqual(metrics["messages_by_type"][packet.msg_type.name], 1)

    def test_strict_server_rejects_wrong_delivery_flags_and_status(self):
        reading_payload = encode_readings(
            [Reading(MetricId.TEMPERATURE_C, 26.5)]
        )
        cases = {
            "wrong delivery": DartPacket(
                msg_type=MessageType.DATA_BATCH,
                delivery=DeliveryClass.CONTROL,
                session_id=1,
                sensor_id=2,
                sequence=10,
                ttl_ms=0,
                payload=reading_payload,
            ),
            "retransmission without ack-required": DartPacket(
                msg_type=MessageType.HEARTBEAT,
                delivery=DeliveryClass.CONTROL,
                flags=Flags.RETRANSMISSION,
                session_id=1,
                sensor_id=2,
                sequence=11,
                ttl_ms=0,
            ),
            "request carrying response status": DartPacket(
                msg_type=MessageType.DATA_BATCH,
                delivery=DeliveryClass.BEST_EFFORT_BATCH,
                session_id=1,
                sensor_id=2,
                sequence=12,
                ttl_ms=0,
                status_code=StatusCode.ACCEPTED,
                payload=reading_payload,
            ),
        }

        for label, packet in cases.items():
            with self.subTest(case=label):
                server = DartServer(port=0, quiet=True)
                responses = []

                def capture_send(response, _destination):
                    responses.append(response)
                    return True

                with mock.patch.object(server, "_send", side_effect=capture_send):
                    server._process_datagram(
                        packet.encode(), ("127.0.0.1", 12345)
                    )

                self.assertEqual(len(responses), 1)
                self.assertEqual(responses[0].msg_type, MessageType.ERROR)
                self.assertEqual(responses[0].status_code, StatusCode.MALFORMED)
                self.assertEqual(server.snapshot_metrics()["errors_sent"], 1)

    def test_strict_policy_flags_are_rejected_but_experimental_override_accepts(self):
        address = ("127.0.0.1", 12345)
        batch = DartPacket(
            msg_type=MessageType.DATA_BATCH,
            delivery=DeliveryClass.BEST_EFFORT_BATCH,
            flags=Flags.ACK_REQUIRED,
            session_id=1,
            sensor_id=2,
            sequence=20,
            ttl_ms=0,
            payload=encode_readings([Reading(MetricId.TEMPERATURE_C, 26.5)]),
        )
        raw_critical = DartPacket(
            msg_type=MessageType.CRITICAL_ALERT,
            delivery=DeliveryClass.CRITICAL_RELIABLE,
            flags=Flags.NONE,
            session_id=1,
            sensor_id=2,
            sequence=21,
            ttl_ms=0,
            payload=encode_json({"alert_type": "FIRE_DETECTED"}),
        )

        for packet in (batch, raw_critical):
            with self.subTest(strict_rejects=packet.msg_type.name):
                strict = DartServer(port=0, quiet=True)
                responses = []

                def strict_send(response, _destination):
                    responses.append(response)
                    return True

                with mock.patch.object(strict, "_send", side_effect=strict_send):
                    strict._process_datagram(packet.encode(), address)
                self.assertEqual(len(responses), 1)
                self.assertEqual(responses[0].msg_type, MessageType.ERROR)
                self.assertEqual(responses[0].status_code, StatusCode.MALFORMED)

        experimental = DartServer(
            port=0, quiet=True, allow_experimental_policies=True
        )
        responses = []

        def experimental_send(response, _destination):
            responses.append(response)
            return True

        with mock.patch.object(
            experimental, "_valid_session", return_value=True
        ), mock.patch.object(
            experimental, "_send", side_effect=experimental_send
        ):
            experimental._process_datagram(batch.encode(), address)
            experimental._process_datagram(raw_critical.encode(), address)

        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0].msg_type, MessageType.ACK)
        self.assertEqual(responses[0].status_code, StatusCode.ACCEPTED)
        metrics = experimental.snapshot_metrics()
        self.assertEqual(metrics["data_batches_accepted"], 1)
        self.assertEqual(metrics["critical_alerts_received"], 1)
        self.assertEqual(metrics["errors_sent"], 0)


class ServerPacketValidationTests(unittest.TestCase):
    @staticmethod
    def register_response(body_session_id, *, header_session_id=123, sequence=1):
        return DartPacket(
            msg_type=MessageType.REGISTER_RES,
            delivery=DeliveryClass.CONTROL,
            session_id=header_session_id,
            sensor_id=7,
            sequence=sequence,
            status_code=StatusCode.REGISTERED,
            payload=encode_json(
                {"session_id": body_session_id, "config": {}}
            ),
        )

    def test_accepts_each_valid_server_response_type(self):
        responses = [
            DartPacket(
                msg_type=MessageType.REGISTER_RES,
                delivery=DeliveryClass.CONTROL,
                session_id=123,
                sensor_id=7,
                sequence=1,
                status_code=StatusCode.REGISTERED,
                payload=encode_json({"session_id": 123, "config": {}}),
            ),
            DartPacket(
                msg_type=MessageType.ACK,
                delivery=DeliveryClass.CONTROL,
                session_id=123,
                sensor_id=7,
                sequence=2,
                status_code=StatusCode.ACCEPTED,
            ),
            DartPacket(
                msg_type=MessageType.CONFIG_RES,
                delivery=DeliveryClass.CONTROL,
                session_id=123,
                sensor_id=7,
                sequence=3,
                status_code=StatusCode.OK,
                payload=encode_json({"batch_size": 5}),
            ),
            DartPacket(
                msg_type=MessageType.HEARTBEAT_ACK,
                delivery=DeliveryClass.CONTROL,
                session_id=123,
                sensor_id=7,
                sequence=4,
                status_code=StatusCode.OK,
            ),
            DartPacket(
                msg_type=MessageType.ERROR,
                delivery=DeliveryClass.CONTROL,
                session_id=123,
                sensor_id=7,
                sequence=5,
                status_code=StatusCode.UNREGISTERED,
                payload=encode_json({"error": "UNREGISTERED"}),
            ),
        ]

        for response in responses:
            with self.subTest(msg_type=response.msg_type.name):
                self.assertIsNone(SensorClient._validate_server_packet(response))

        for status in (
            StatusCode.ACCEPTED,
            StatusCode.DUPLICATE,
            StatusCode.EXPIRED,
        ):
            with self.subTest(ack_status=status):
                ack = replace(responses[1], status_code=status)
                self.assertIsNone(SensorClient._validate_server_packet(ack))

    def test_rejects_invalid_server_response_envelopes_and_payloads(self):
        valid_ack = DartPacket(
            msg_type=MessageType.ACK,
            delivery=DeliveryClass.CONTROL,
            session_id=123,
            sensor_id=7,
            sequence=10,
            status_code=StatusCode.ACCEPTED,
        )
        cases = {
            "request message type": DartPacket(
                msg_type=MessageType.HEARTBEAT,
                delivery=DeliveryClass.CONTROL,
                status_code=StatusCode.OK,
            ),
            "wrong delivery": replace(
                valid_ack, delivery=DeliveryClass.CRITICAL_RELIABLE
            ),
            "response flags": replace(valid_ack, flags=Flags.ACK_REQUIRED),
            "wrong ACK status": replace(valid_ack, status_code=StatusCode.OK),
            "ACK payload": replace(valid_ack, payload=b"not-empty"),
            "CONFIG_RES non-object payload": DartPacket(
                msg_type=MessageType.CONFIG_RES,
                delivery=DeliveryClass.CONTROL,
                status_code=StatusCode.OK,
                payload=encode_json([]),
            ),
            "HEARTBEAT_ACK payload": DartPacket(
                msg_type=MessageType.HEARTBEAT_ACK,
                delivery=DeliveryClass.CONTROL,
                status_code=StatusCode.OK,
                payload=b"unexpected",
            ),
            "ERROR success status": DartPacket(
                msg_type=MessageType.ERROR,
                delivery=DeliveryClass.CONTROL,
                status_code=StatusCode.OK,
                payload=encode_json({"error": "wrong-status"}),
            ),
            "ERROR non-object payload": DartPacket(
                msg_type=MessageType.ERROR,
                delivery=DeliveryClass.CONTROL,
                status_code=StatusCode.MALFORMED,
                payload=encode_json([]),
            ),
            "REGISTER_RES wrong status": DartPacket(
                msg_type=MessageType.REGISTER_RES,
                delivery=DeliveryClass.CONTROL,
                session_id=123,
                status_code=StatusCode.OK,
                payload=encode_json({"session_id": 123}),
            ),
            "REGISTER_RES non-object config": DartPacket(
                msg_type=MessageType.REGISTER_RES,
                delivery=DeliveryClass.CONTROL,
                session_id=123,
                status_code=StatusCode.REGISTERED,
                payload=encode_json({"session_id": 123, "config": []}),
            ),
            "REGISTER_RES body/header session mismatch": DartPacket(
                msg_type=MessageType.REGISTER_RES,
                delivery=DeliveryClass.CONTROL,
                session_id=123,
                status_code=StatusCode.REGISTERED,
                payload=encode_json({"session_id": 999, "config": {}}),
            ),
        }

        for label, response in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ProtocolError):
                    SensorClient._validate_server_packet(response)

    def test_register_response_requires_exact_non_bool_integer_session_id(self):
        valid = self.register_response(123, header_session_id=123)
        self.assertIsNone(SensorClient._validate_server_packet(valid))

        invalid_values = (
            ("null", None, 123),
            ("object", {}, 123),
            ("boolean", True, 1),
            ("numeric string", "123", 123),
            ("integral float", 123.0, 123),
        )
        for label, body_session_id, header_session_id in invalid_values:
            with self.subTest(case=label):
                response = self.register_response(
                    body_session_id,
                    header_session_id=header_session_id,
                )
                with self.assertRaises(ProtocolError):
                    SensorClient._validate_server_packet(response)

    def test_receiver_rejects_register_session_id_type_confusion(self):
        invalid_values = (
            (None, 123),
            ({}, 123),
            (True, 1),
            ("123", 123),
            (123.0, 123),
        )
        responses = [
            self.register_response(
                body_session_id,
                header_session_id=header_session_id,
                sequence=100 + index,
            )
            for index, (body_session_id, header_session_id) in enumerate(
                invalid_values
            )
        ]
        running = threading.Event()
        running.set()

        class ScriptedSocket:
            def __init__(self):
                self.responses = list(responses)

            def recvfrom(self, _size):
                if self.responses:
                    response = self.responses.pop(0)
                    return response.encode(), ("127.0.0.1", 9999)
                running.clear()
                raise OSError("scripted receiver shutdown")

        client = SensorClient.__new__(SensorClient)
        client.server_address = ("127.0.0.1", 9999)
        client.sensor_id = 7
        client.session_id = 0
        client.quiet = True
        client._socket = ScriptedSocket()
        client._running = running
        client.metrics = ClientMetrics()
        client._metrics_lock = threading.Lock()
        client._pending_lock = threading.Lock()
        pending = {
            response.sequence: _PendingResponse() for response in responses
        }
        client._pending = pending

        client._receive_loop()

        self.assertEqual(client.metrics.decode_errors, len(responses))
        self.assertEqual(dict(client.metrics.responses_received), {})
        for item in pending.values():
            self.assertIsNone(item.packet)
            self.assertFalse(item.event.is_set())

    def test_receiver_ignores_expired_valid_response(self):
        expired = DartPacket(
            msg_type=MessageType.ACK,
            delivery=DeliveryClass.CONTROL,
            session_id=123,
            sensor_id=7,
            sequence=55,
            timestamp_ms=1,
            ttl_ms=1,
            status_code=StatusCode.ACCEPTED,
        )
        running = threading.Event()
        running.set()

        class OnePacketSocket:
            def __init__(self):
                self.delivered = False

            def recvfrom(self, _size):
                if not self.delivered:
                    self.delivered = True
                    return expired.encode(), ("127.0.0.1", 9999)
                running.clear()
                raise OSError("scripted receiver shutdown")

        client = SensorClient.__new__(SensorClient)
        client.server_address = ("127.0.0.1", 9999)
        client.sensor_id = 7
        client.session_id = 123
        client.quiet = True
        client._socket = OnePacketSocket()
        client._running = running
        client.metrics = ClientMetrics()
        client._metrics_lock = threading.Lock()
        client._pending_lock = threading.Lock()
        pending = _PendingResponse()
        client._pending = {55: pending}

        client._receive_loop()

        self.assertIsNone(pending.packet)
        self.assertFalse(pending.event.is_set())
        self.assertEqual(dict(client.metrics.responses_received), {})
        self.assertEqual(client.metrics.decode_errors, 0)


class ConcurrentDuplicateTests(unittest.TestCase):
    @staticmethod
    def packet():
        return DartPacket(
            msg_type=MessageType.CRITICAL_ALERT,
            delivery=DeliveryClass.CRITICAL_RELIABLE,
            flags=Flags.ACK_REQUIRED,
            session_id=100,
            sensor_id=7,
            sequence=55,
            ttl_ms=0,
            payload=encode_json({"alert_type": "FIRE_DETECTED"}),
        )

    @staticmethod
    def start_worker(target, errors):
        def guarded_target():
            try:
                target()
            except BaseException as exc:  # surfaced by the test thread below
                errors.append(exc)

        thread = threading.Thread(target=guarded_target)
        thread.start()
        return thread

    def test_concurrent_duplicate_waits_until_first_processing_succeeds(self):
        server = DartServer(port=0, quiet=True)
        packet = self.packet()
        raw = packet.encode()
        address = ("127.0.0.1", 12345)
        first_dispatch_entered = threading.Event()
        second_claim_entered = threading.Event()
        release_first = threading.Event()
        processing_succeeded = threading.Event()
        claim_lock = threading.Lock()
        response_lock = threading.Lock()
        errors = []
        responses = []
        claim_calls = [0]
        original_claim = server._claim_message

        def observed_claim(value):
            with claim_lock:
                claim_calls[0] += 1
                if claim_calls[0] == 2:
                    second_claim_entered.set()
            return original_claim(value)

        def controlled_dispatch(_packet, _address):
            first_dispatch_entered.set()
            if not release_first.wait(timeout=2.0):
                raise AssertionError("test did not release first dispatch")
            processing_succeeded.set()
            return StatusCode.ACCEPTED

        def capture_send(response, _destination):
            with response_lock:
                responses.append((response, processing_succeeded.is_set()))
            return True

        with mock.patch.object(
            server, "_valid_session", return_value=True
        ), mock.patch.object(
            server, "_claim_message", side_effect=observed_claim
        ), mock.patch.object(
            server, "_dispatch", side_effect=controlled_dispatch
        ), mock.patch.object(server, "_send", side_effect=capture_send):
            first = self.start_worker(
                lambda: server._process_datagram(raw, address), errors
            )
            self.assertTrue(first_dispatch_entered.wait(timeout=1.0))
            second = self.start_worker(
                lambda: server._process_datagram(raw, address), errors
            )
            self.assertTrue(second_claim_entered.wait(timeout=1.0))
            with response_lock:
                self.assertFalse(
                    any(
                        response.status_code == StatusCode.DUPLICATE
                        for response, _completed in responses
                    )
                )
            release_first.set()
            first.join(timeout=2.0)
            second.join(timeout=2.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        with response_lock:
            statuses = [response.status_code for response, _completed in responses]
            duplicate_states = [
                completed
                for response, completed in responses
                if response.status_code == StatusCode.DUPLICATE
            ]
        self.assertCountEqual(statuses, [StatusCode.ACCEPTED, StatusCode.DUPLICATE])
        self.assertEqual(duplicate_states, [True])
        self.assertEqual(server.snapshot_metrics()["duplicate_packets"], 1)

    def test_concurrent_duplicate_takes_over_when_first_processing_fails(self):
        server = DartServer(port=0, quiet=True)
        packet = self.packet()
        raw = packet.encode()
        address = ("127.0.0.1", 12345)
        first_dispatch_entered = threading.Event()
        second_claim_entered = threading.Event()
        release_first = threading.Event()
        claim_lock = threading.Lock()
        dispatch_lock = threading.Lock()
        response_lock = threading.Lock()
        errors = []
        responses = []
        claim_calls = [0]
        dispatch_calls = [0]
        original_claim = server._claim_message

        def observed_claim(value):
            with claim_lock:
                claim_calls[0] += 1
                if claim_calls[0] == 2:
                    second_claim_entered.set()
            return original_claim(value)

        def controlled_dispatch(_packet, _address):
            with dispatch_lock:
                dispatch_calls[0] += 1
                call_number = dispatch_calls[0]
            if call_number == 1:
                first_dispatch_entered.set()
                if not release_first.wait(timeout=2.0):
                    raise AssertionError("test did not release first dispatch")
                raise PayloadError("simulated first-handler failure")
            return StatusCode.ACCEPTED

        def capture_send(response, _destination):
            with response_lock:
                responses.append(response)
            return True

        with mock.patch.object(
            server, "_valid_session", return_value=True
        ), mock.patch.object(
            server, "_claim_message", side_effect=observed_claim
        ), mock.patch.object(
            server, "_dispatch", side_effect=controlled_dispatch
        ), mock.patch.object(server, "_send", side_effect=capture_send):
            first = self.start_worker(
                lambda: server._process_datagram(raw, address), errors
            )
            self.assertTrue(first_dispatch_entered.wait(timeout=1.0))
            second = self.start_worker(
                lambda: server._process_datagram(raw, address), errors
            )
            self.assertTrue(second_claim_entered.wait(timeout=1.0))
            with response_lock:
                self.assertFalse(
                    any(
                        response.status_code == StatusCode.DUPLICATE
                        for response in responses
                    )
                )
            release_first.set()
            first.join(timeout=2.0)
            second.join(timeout=2.0)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        with response_lock:
            response_pairs = [
                (response.msg_type, response.status_code) for response in responses
            ]
        self.assertCountEqual(
            response_pairs,
            [
                (MessageType.ERROR, StatusCode.INVALID_PAYLOAD),
                (MessageType.ACK, StatusCode.ACCEPTED),
            ],
        )
        self.assertNotIn(
            (MessageType.ACK, StatusCode.DUPLICATE), response_pairs
        )
        self.assertEqual(dispatch_calls[0], 2)
        self.assertEqual(server.snapshot_metrics()["duplicate_packets"], 0)


class LocalhostEndToEndTests(unittest.TestCase):
    def test_registration_batch_latest_and_critical_retransmission(self):
        server = DartServer(
            host="127.0.0.1",
            port=0,
            workers=4,
            seed=900,
            drop_first_critical_ack=True,
            allow_experimental_policies=True,
            quiet=True,
        ).start()
        client = None
        try:
            client = SensorClient(
                server.address,
                sensor_id=17,
                name="integration-sensor",
                ack_timeout_s=0.10,
                max_attempts=5,
                seed=901,
                quiet=True,
            )

            registration = client.register()
            self.assertTrue(registration.success, registration.detail)
            self.assertEqual(registration.response_type, MessageType.REGISTER_RES)
            self.assertEqual(registration.status_code, StatusCode.REGISTERED)
            self.assertNotEqual(client.session_id, 0)
            self.assertEqual(client.server_config["max_datagram_size"], 1200)

            batch = client.send_batch(
                [
                    Reading(MetricId.TEMPERATURE_C, 26.5),
                    Reading(MetricId.HUMIDITY_PERCENT, 54.25, age_ms=10),
                ],
                reliable=True,
            )
            self.assertTrue(batch.success, batch.detail)
            self.assertEqual(batch.response_type, MessageType.ACK)
            self.assertEqual(batch.status_code, StatusCode.ACCEPTED)

            latest = client.send_latest(
                MetricId.POSITION_X,
                123.5,
                reliable=True,
            )
            self.assertTrue(latest.success, latest.detail)
            self.assertEqual(latest.status_code, StatusCode.ACCEPTED)

            critical = client.send_critical(
                alert_type="FIRE_DETECTED",
                severity="critical",
                value=92.4,
                unit="C",
                message="integration test alert",
                reliable=True,
            )
            self.assertTrue(critical.success, critical.detail)
            self.assertGreaterEqual(critical.attempts, 2)
            self.assertEqual(critical.response_type, MessageType.ACK)
            self.assertEqual(critical.status_code, StatusCode.DUPLICATE)

            server_metrics = server.snapshot_metrics()
            client_metrics = client.snapshot_metrics()
            self.assertEqual(server_metrics["active_sessions"], 1)
            self.assertEqual(server_metrics["data_batches_accepted"], 1)
            self.assertEqual(server_metrics["readings_received"], 2)
            self.assertEqual(server_metrics["latest_updates_accepted"], 1)
            self.assertEqual(
                server_metrics["latest_values"][
                    f"session_{client.session_id}.sensor_17.POSITION_X"
                ]["value"],
                123.5,
            )
            self.assertEqual(server_metrics["critical_alerts_received"], 1)
            self.assertEqual(server_metrics["acks_simulated_dropped"], 1)
            self.assertGreaterEqual(server_metrics["duplicate_packets"], 1)
            self.assertGreaterEqual(client_metrics["retransmissions"], 1)
            self.assertEqual(client_metrics["critical_successes"], 1)
            self.assertEqual(client_metrics["critical_failures"], 0)
        finally:
            if client is not None:
                client.close()
            server.stop()


if __name__ == "__main__":
    unittest.main()
