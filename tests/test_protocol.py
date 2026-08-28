"""Unit tests for the DART v1 wire format and payload codecs."""

from __future__ import annotations

import math
import unittest
import zlib

from dart.protocol import (
    BATCH_COUNT_STRUCT,
    HEADER_SIZE,
    HEADER_STRUCT,
    LATEST_STRUCT,
    MAGIC,
    MAX_DATAGRAM_SIZE,
    MAX_PAYLOAD_SIZE,
    READING_STRUCT,
    VERSION,
    ChecksumError,
    DartPacket,
    DeliveryClass,
    Flags,
    MessageType,
    MetricId,
    PayloadError,
    ProtocolError,
    Reading,
    StatusCode,
    TruncatedPacketError,
    UnsupportedVersionError,
    decode_json,
    decode_latest,
    decode_readings,
    encode_json,
    encode_latest,
    encode_readings,
    packet_summary,
    status_phrase,
)


def build_wire_packet(
    payload=b"",
    *,
    magic=MAGIC,
    version=VERSION,
    msg_type=MessageType.DATA_BATCH,
    delivery=DeliveryClass.BEST_EFFORT_BATCH,
    flags=Flags.NONE,
    payload_length=None,
):
    """Build a packet with a correct CRC, including intentionally invalid fields."""
    declared_length = len(payload) if payload_length is None else payload_length
    fields = [
        magic,
        version,
        int(msg_type),
        int(delivery),
        int(flags),
        11,
        22,
        33,
        1_700_000_000_000,
        5_000,
        declared_length,
        int(StatusCode.NONE),
        0,
    ]
    header_without_crc = HEADER_STRUCT.pack(*fields)
    checksum = zlib.crc32(header_without_crc + payload) & 0xFFFFFFFF
    fields[-1] = checksum
    return HEADER_STRUCT.pack(*fields) + payload


class DartPacketTests(unittest.TestCase):
    def test_round_trip_preserves_every_header_field(self):
        original = DartPacket(
            msg_type=MessageType.CRITICAL_ALERT,
            delivery=DeliveryClass.CRITICAL_RELIABLE,
            flags=Flags.ACK_REQUIRED | Flags.RETRANSMISSION | Flags.SIMULATED,
            session_id=0x12345678,
            sensor_id=42,
            sequence=0xFFFFFFFE,
            timestamp_ms=1_700_000_123_456,
            ttl_ms=9_876,
            status_code=StatusCode.ACCEPTED,
            payload=b'{"alert_type":"FIRE"}',
        )

        encoded = original.encode()
        decoded = DartPacket.decode(encoded)

        self.assertEqual(decoded, original)
        self.assertEqual(len(encoded), HEADER_SIZE + len(original.payload))
        self.assertEqual(decoded.wire_size, len(encoded))
        self.assertTrue(decoded.requires_ack)

    def test_all_message_and_delivery_enum_values_round_trip(self):
        delivery_values = list(DeliveryClass)
        for index, msg_type in enumerate(MessageType):
            with self.subTest(msg_type=msg_type.name):
                packet = DartPacket(
                    msg_type=msg_type,
                    delivery=delivery_values[index % len(delivery_values)],
                    sequence=index + 1,
                    timestamp_ms=1_700_000_000_000 + index,
                    payload=b"payload",
                )
                decoded = DartPacket.decode(packet.encode())
                self.assertEqual(decoded.msg_type, msg_type)
                self.assertEqual(decoded.delivery, packet.delivery)
                self.assertEqual(decoded.payload, b"payload")

    def test_crc_rejects_single_bit_payload_corruption(self):
        raw = bytearray(
            DartPacket(
                msg_type=MessageType.LATEST_UPDATE,
                delivery=DeliveryClass.LATEST_ONLY,
                payload=b"abcdef",
            ).encode()
        )
        raw[-1] ^= 0x01

        with self.assertRaises(ChecksumError):
            DartPacket.decode(bytes(raw))

    def test_truncated_header_is_rejected(self):
        with self.assertRaises(TruncatedPacketError):
            DartPacket.decode(b"DART")

    def test_truncated_payload_is_rejected_before_crc(self):
        raw = DartPacket(
            msg_type=MessageType.DATA_BATCH,
            delivery=DeliveryClass.BEST_EFFORT_BATCH,
            payload=b"12345",
        ).encode()
        with self.assertRaises(TruncatedPacketError):
            DartPacket.decode(raw[:-1])

    def test_extra_bytes_are_rejected_as_length_mismatch(self):
        raw = DartPacket(
            msg_type=MessageType.DATA_BATCH,
            delivery=DeliveryClass.BEST_EFFORT_BATCH,
            payload=b"12345",
        ).encode()
        with self.assertRaises(TruncatedPacketError):
            DartPacket.decode(raw + b"extra")

    def test_declared_payload_length_mismatch_is_rejected(self):
        raw = build_wire_packet(b"abc", payload_length=4)
        with self.assertRaises(TruncatedPacketError):
            DartPacket.decode(raw)

    def test_bad_magic_is_rejected(self):
        with self.assertRaises(ProtocolError):
            DartPacket.decode(build_wire_packet(magic=b"NOPE"))

    def test_unsupported_version_is_rejected(self):
        with self.assertRaises(UnsupportedVersionError):
            DartPacket.decode(build_wire_packet(version=VERSION + 1))

    def test_unknown_message_type_and_delivery_class_are_rejected(self):
        with self.subTest(field="message type"):
            with self.assertRaises(ProtocolError):
                DartPacket.decode(build_wire_packet(msg_type=255))
        with self.subTest(field="delivery class"):
            with self.assertRaises(ProtocolError):
                DartPacket.decode(build_wire_packet(delivery=255))

    def test_unknown_flag_bits_are_rejected_on_encode_and_decode(self):
        with self.assertRaises(ProtocolError):
            DartPacket(
                msg_type=MessageType.HEARTBEAT,
                delivery=DeliveryClass.CONTROL,
                flags=Flags(0x80),
            ).encode()
        with self.assertRaises(ProtocolError):
            DartPacket.decode(build_wire_packet(flags=0x80))

    def test_oversize_payload_is_rejected_on_encode_and_decode(self):
        packet = DartPacket(
            msg_type=MessageType.DATA_BATCH,
            delivery=DeliveryClass.BEST_EFFORT_BATCH,
            payload=b"x" * (MAX_PAYLOAD_SIZE + 1),
        )
        with self.assertRaises(PayloadError):
            packet.encode()

        oversized_wire = build_wire_packet(b"x" * (MAX_PAYLOAD_SIZE + 1))
        self.assertEqual(len(oversized_wire), MAX_DATAGRAM_SIZE + 1)
        with self.assertRaises(PayloadError):
            DartPacket.decode(oversized_wire)

    def test_out_of_range_integer_header_fields_are_rejected(self):
        cases = {
            "negative session": {"session_id": -1},
            "sensor overflow": {"sensor_id": 0x1_0000_0000},
            "sequence overflow": {"sequence": 0x1_0000_0000},
            "negative timestamp": {"timestamp_ms": -1},
            "ttl overflow": {"ttl_ms": 0x1_0000_0000},
            "status overflow": {"status_code": 0x1_0000},
        }
        for label, override in cases.items():
            with self.subTest(case=label):
                values = {
                    "msg_type": MessageType.ACK,
                    "delivery": DeliveryClass.CONTROL,
                }
                values.update(override)
                with self.assertRaises(ProtocolError):
                    DartPacket(**values).encode()

    def test_expiry_uses_strict_deadline_and_zero_ttl_never_expires(self):
        packet = DartPacket(
            msg_type=MessageType.HEARTBEAT,
            delivery=DeliveryClass.CONTROL,
            timestamp_ms=1_000,
            ttl_ms=500,
        )
        self.assertFalse(packet.is_expired(at_ms=1_500))
        self.assertTrue(packet.is_expired(at_ms=1_501))

        no_expiry = DartPacket(
            msg_type=MessageType.HEARTBEAT,
            delivery=DeliveryClass.CONTROL,
            timestamp_ms=1,
            ttl_ms=0,
        )
        self.assertFalse(no_expiry.is_expired(at_ms=2**63))

    def test_status_phrase_and_packet_summary_are_human_readable(self):
        packet = DartPacket(
            msg_type=MessageType.ACK,
            delivery=DeliveryClass.CONTROL,
            flags=Flags.NONE,
            sensor_id=7,
            sequence=9,
            status_code=StatusCode.ACCEPTED,
        )
        summary = packet_summary(packet)
        self.assertEqual(status_phrase(StatusCode.ACCEPTED), "ACCEPTED")
        self.assertEqual(status_phrase(999), "UNKNOWN STATUS")
        self.assertIn("ACK", summary)
        self.assertIn("sensor=7", summary)
        self.assertIn("status=202 ACCEPTED", summary)


class PayloadCodecTests(unittest.TestCase):
    def test_json_round_trip_is_compact_sorted_and_utf8(self):
        value = {"z": [1, True], "message": "ไฟไหม้", "a": 2}
        encoded = encode_json(value)
        self.assertNotIn(b" ", encoded)
        self.assertLess(encoded.index(b'"a"'), encoded.index(b'"z"'))
        self.assertEqual(decode_json(encoded), value)

    def test_json_rejects_unencodable_and_invalid_payloads(self):
        with self.assertRaises(PayloadError):
            encode_json({"not_json": {1, 2, 3}})
        for payload in (b"\xff", b"{not-json}"):
            with self.subTest(payload=payload):
                with self.assertRaises(PayloadError):
                    decode_json(payload)

    def test_json_rejects_nonfinite_and_overflowing_numbers(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(direction="encode", value=value):
                with self.assertRaises(PayloadError):
                    encode_json({"value": value})

        invalid_payloads = (
            b"NaN",
            b"Infinity",
            b"-Infinity",
            b'{"value":NaN}',
            b'{"value":Infinity}',
            b'{"value":-Infinity}',
            b'{"value":1e400}',
        )
        for payload in invalid_payloads:
            with self.subTest(direction="decode", payload=payload):
                with self.assertRaises(PayloadError):
                    decode_json(payload)

    def test_reading_batch_round_trip(self):
        readings = [
            Reading(MetricId.TEMPERATURE_C, 26.125, age_ms=0),
            Reading(MetricId.HUMIDITY_PERCENT, 57.5, age_ms=321),
            Reading(MetricId.SMOKE_PPM, 1.25, age_ms=65_535),
        ]
        encoded = encode_readings(readings)
        decoded = decode_readings(encoded)

        self.assertEqual(len(decoded), 3)
        for actual, expected in zip(decoded, readings):
            self.assertEqual(actual.metric_id, expected.metric_id)
            self.assertAlmostEqual(actual.value, expected.value, places=4)
            self.assertEqual(actual.age_ms, expected.age_ms)

    def test_batch_boundary_and_invalid_batch_payloads(self):
        maximum = (MAX_PAYLOAD_SIZE - BATCH_COUNT_STRUCT.size) // READING_STRUCT.size
        readings = [Reading(MetricId.TEMPERATURE_C, float(i)) for i in range(maximum)]
        self.assertLessEqual(len(encode_readings(readings)), MAX_PAYLOAD_SIZE)

        with self.assertRaises(PayloadError):
            encode_readings([])
        with self.assertRaises(PayloadError):
            encode_readings(readings + [Reading(MetricId.TEMPERATURE_C, 1.0)])
        with self.assertRaises(PayloadError):
            decode_readings(b"")
        with self.assertRaises(PayloadError):
            decode_readings(BATCH_COUNT_STRUCT.pack(0))
        with self.assertRaises(PayloadError):
            decode_readings(BATCH_COUNT_STRUCT.pack(1) + b"short")

        unknown_metric = BATCH_COUNT_STRUCT.pack(1) + READING_STRUCT.pack(255, 1.0, 0)
        with self.assertRaises(PayloadError):
            decode_readings(unknown_metric)

    def test_reading_rejects_nonfinite_value_and_bad_age(self):
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(PayloadError):
                    Reading(MetricId.TEMPERATURE_C, value)
        for age in (-1, 65_536):
            with self.subTest(age=age):
                with self.assertRaises(PayloadError):
                    Reading(MetricId.TEMPERATURE_C, 1.0, age_ms=age)

    def test_finite_values_that_overflow_float32_are_rejected(self):
        finite_but_too_large = 3.5e38
        self.assertTrue(math.isfinite(finite_but_too_large))
        with self.assertRaises(PayloadError):
            Reading(MetricId.TEMPERATURE_C, finite_but_too_large)
        with self.assertRaises(PayloadError):
            encode_latest(MetricId.POSITION_X, finite_but_too_large)

    def test_latest_round_trip_and_validation(self):
        payload = encode_latest(MetricId.POSITION_X, -12.75)
        metric, value = decode_latest(payload)
        self.assertEqual(metric, MetricId.POSITION_X)
        self.assertAlmostEqual(value, -12.75, places=4)

        for invalid in (math.nan, math.inf, -math.inf):
            with self.subTest(invalid=invalid):
                with self.assertRaises(PayloadError):
                    encode_latest(MetricId.POSITION_X, invalid)
        with self.assertRaises(PayloadError):
            decode_latest(b"short")
        with self.assertRaises(PayloadError):
            decode_latest(LATEST_STRUCT.pack(255, 1.0))
        with self.assertRaises(PayloadError):
            decode_latest(LATEST_STRUCT.pack(int(MetricId.POSITION_X), math.nan))


if __name__ == "__main__":
    unittest.main()
