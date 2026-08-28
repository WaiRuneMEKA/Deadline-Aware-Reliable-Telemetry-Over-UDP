"""DART v1 wire protocol.

The module contains no socket code.  It only defines the application-layer
message format, validation rules, and compact sensor payload encodings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
import json
import math
import struct
import time
from typing import Any, Iterable
import zlib


MAGIC = b"DART"
VERSION = 1
UDP_PORT = 9999
MAX_DATAGRAM_SIZE = 1200

# magic, version, type, delivery, flags, session, sensor, sequence,
# timestamp_ms, ttl_ms, payload_length, status_code, crc32
HEADER_STRUCT = struct.Struct("!4sBBBBIIIQIHHI")
HEADER_SIZE = HEADER_STRUCT.size
MAX_PAYLOAD_SIZE = MAX_DATAGRAM_SIZE - HEADER_SIZE


class MessageType(IntEnum):
    REGISTER_REQ = 1
    REGISTER_RES = 2
    DATA_BATCH = 3
    LATEST_UPDATE = 4
    CRITICAL_ALERT = 5
    ACK = 6
    CONFIG_REQ = 7
    CONFIG_RES = 8
    HEARTBEAT = 9
    HEARTBEAT_ACK = 10
    ERROR = 11


class DeliveryClass(IntEnum):
    CONTROL = 0
    BEST_EFFORT_BATCH = 1
    LATEST_ONLY = 2
    CRITICAL_RELIABLE = 3


class Flags(IntFlag):
    NONE = 0
    ACK_REQUIRED = 1 << 0
    RETRANSMISSION = 1 << 1
    SIMULATED = 1 << 2


class StatusCode(IntEnum):
    NONE = 0
    OK = 200
    REGISTERED = 201
    ACCEPTED = 202
    NO_CONTENT = 204
    MALFORMED = 400
    UNREGISTERED = 401
    EXPIRED = 408
    DUPLICATE = 409
    PAYLOAD_TOO_LARGE = 413
    INVALID_PAYLOAD = 422
    RATE_LIMITED = 429
    INTERNAL_ERROR = 500
    BUSY = 503


STATUS_PHRASES: dict[int, str] = {
    StatusCode.NONE: "NO STATUS",
    StatusCode.OK: "OK",
    StatusCode.REGISTERED: "REGISTERED",
    StatusCode.ACCEPTED: "ACCEPTED",
    StatusCode.NO_CONTENT: "NO CONTENT",
    StatusCode.MALFORMED: "MALFORMED",
    StatusCode.UNREGISTERED: "UNREGISTERED",
    StatusCode.EXPIRED: "EXPIRED",
    StatusCode.DUPLICATE: "DUPLICATE",
    StatusCode.PAYLOAD_TOO_LARGE: "PAYLOAD TOO LARGE",
    StatusCode.INVALID_PAYLOAD: "INVALID PAYLOAD",
    StatusCode.RATE_LIMITED: "RATE LIMITED",
    StatusCode.INTERNAL_ERROR: "INTERNAL ERROR",
    StatusCode.BUSY: "BUSY",
}


class MetricId(IntEnum):
    TEMPERATURE_C = 1
    HUMIDITY_PERCENT = 2
    SMOKE_PPM = 3
    POSITION_X = 4
    POSITION_Y = 5
    BATTERY_PERCENT = 6


METRIC_NAMES: dict[int, str] = {
    MetricId.TEMPERATURE_C: "temperature_c",
    MetricId.HUMIDITY_PERCENT: "humidity_percent",
    MetricId.SMOKE_PPM: "smoke_ppm",
    MetricId.POSITION_X: "position_x",
    MetricId.POSITION_Y: "position_y",
    MetricId.BATTERY_PERCENT: "battery_percent",
}


class ProtocolError(ValueError):
    """Base class for invalid DART messages."""


class TruncatedPacketError(ProtocolError):
    """The datagram is shorter than the declared DART message."""


class ChecksumError(ProtocolError):
    """The DART CRC32 did not match the received bytes."""


class UnsupportedVersionError(ProtocolError):
    """The packet uses a DART version this implementation cannot parse."""


class PayloadError(ProtocolError):
    """A message payload does not follow its declared encoding."""


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def status_phrase(code: int) -> str:
    return STATUS_PHRASES.get(code, "UNKNOWN STATUS")


@dataclass(frozen=True)
class DartPacket:
    msg_type: MessageType
    delivery: DeliveryClass
    flags: Flags = Flags.NONE
    session_id: int = 0
    sensor_id: int = 0
    sequence: int = 0
    timestamp_ms: int = field(default_factory=now_ms)
    ttl_ms: int = 5_000
    status_code: int = StatusCode.NONE
    payload: bytes = b""

    def encode(self) -> bytes:
        try:
            message_type = MessageType(self.msg_type)
            delivery = DeliveryClass(self.delivery)
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"unknown message/delivery value: {exc}") from exc
        flag_value = int(self.flags)
        known_flags = int(
            Flags.ACK_REQUIRED | Flags.RETRANSMISSION | Flags.SIMULATED
        )
        if flag_value < 0 or flag_value & ~known_flags:
            raise ProtocolError(f"unknown flag bits 0x{flag_value:x}")
        try:
            status_value = int(self.status_code)
        except (TypeError, ValueError) as exc:
            raise ProtocolError(f"invalid status_code {self.status_code!r}") from exc
        try:
            payload = bytes(self.payload)
        except (TypeError, ValueError) as exc:
            raise PayloadError("payload must be bytes-like") from exc
        if len(payload) > MAX_PAYLOAD_SIZE:
            raise PayloadError(
                f"payload is {len(payload)} bytes; maximum is {MAX_PAYLOAD_SIZE}"
            )
        for label, value, maximum in (
            ("session_id", self.session_id, 0xFFFFFFFF),
            ("sensor_id", self.sensor_id, 0xFFFFFFFF),
            ("sequence", self.sequence, 0xFFFFFFFF),
            ("timestamp_ms", self.timestamp_ms, 0xFFFFFFFFFFFFFFFF),
            ("ttl_ms", self.ttl_ms, 0xFFFFFFFF),
            ("status_code", status_value, 0xFFFF),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= maximum
            ):
                raise ProtocolError(f"{label} is outside its wire range: {value}")

        header_without_crc = HEADER_STRUCT.pack(
            MAGIC,
            VERSION,
            int(message_type),
            int(delivery),
            flag_value,
            self.session_id,
            self.sensor_id,
            self.sequence,
            self.timestamp_ms,
            self.ttl_ms,
            len(payload),
            status_value,
            0,
        )
        checksum = zlib.crc32(header_without_crc + payload) & 0xFFFFFFFF
        header = header_without_crc[:-4] + struct.pack("!I", checksum)
        return header + payload

    @classmethod
    def decode(cls, raw: bytes) -> "DartPacket":
        if len(raw) < HEADER_SIZE:
            raise TruncatedPacketError(
                f"datagram has {len(raw)} bytes; DART header needs {HEADER_SIZE}"
            )
        (
            magic,
            version,
            msg_type,
            delivery,
            flags,
            session_id,
            sensor_id,
            sequence,
            timestamp_ms,
            ttl_ms,
            payload_length,
            status_code,
            received_checksum,
        ) = HEADER_STRUCT.unpack_from(raw)

        if magic != MAGIC:
            raise ProtocolError(f"invalid magic {magic!r}")
        if version != VERSION:
            raise UnsupportedVersionError(
                f"unsupported DART version {version}; expected {VERSION}"
            )
        expected_length = HEADER_SIZE + payload_length
        if len(raw) != expected_length:
            raise TruncatedPacketError(
                f"datagram has {len(raw)} bytes; header declares {expected_length}"
            )
        if len(raw) > MAX_DATAGRAM_SIZE:
            raise PayloadError(
                f"datagram is {len(raw)} bytes; maximum is {MAX_DATAGRAM_SIZE}"
            )

        checksum_bytes = raw[: HEADER_SIZE - 4] + b"\x00\x00\x00\x00" + raw[HEADER_SIZE:]
        calculated_checksum = zlib.crc32(checksum_bytes) & 0xFFFFFFFF
        if received_checksum != calculated_checksum:
            raise ChecksumError(
                f"CRC32 mismatch: received 0x{received_checksum:08x}, "
                f"calculated 0x{calculated_checksum:08x}"
            )
        try:
            parsed_type = MessageType(msg_type)
            parsed_delivery = DeliveryClass(delivery)
        except ValueError as exc:
            raise ProtocolError(str(exc)) from exc
        known_flags = int(
            Flags.ACK_REQUIRED | Flags.RETRANSMISSION | Flags.SIMULATED
        )
        if flags & ~known_flags:
            raise ProtocolError(f"unknown flag bits 0x{flags:02x}")

        return cls(
            msg_type=parsed_type,
            delivery=parsed_delivery,
            flags=Flags(flags),
            session_id=session_id,
            sensor_id=sensor_id,
            sequence=sequence,
            timestamp_ms=timestamp_ms,
            ttl_ms=ttl_ms,
            status_code=status_code,
            payload=raw[HEADER_SIZE:],
        )

    def is_expired(self, at_ms: int | None = None) -> bool:
        if self.ttl_ms == 0:
            return False
        return (now_ms() if at_ms is None else at_ms) > self.timestamp_ms + self.ttl_ms

    @property
    def requires_ack(self) -> bool:
        return bool(self.flags & Flags.ACK_REQUIRED)

    @property
    def wire_size(self) -> int:
        return HEADER_SIZE + len(self.payload)


def encode_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PayloadError(f"value cannot be encoded as JSON: {exc}") from exc


def decode_json(payload: bytes) -> Any:
    try:
        decoded = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda value: _reject_json_constant(value),
        )
        _validate_json_numbers(decoded)
        return decoded
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PayloadError(f"invalid UTF-8 JSON payload: {exc}") from exc


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON numeric constant {value}")


def _validate_json_numbers(value: Any) -> None:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON number must be finite")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_numbers(item)
        return
    if isinstance(value, dict):
        for item in value.values():
            _validate_json_numbers(item)


@dataclass(frozen=True)
class Reading:
    metric_id: MetricId
    value: float
    age_ms: int = 0

    def __post_init__(self) -> None:
        try:
            metric = MetricId(self.metric_id)
        except (TypeError, ValueError) as exc:
            raise PayloadError(f"unknown metric id {self.metric_id}") from exc
        _require_float32(self.value, "reading value")
        if (
            isinstance(self.age_ms, bool)
            or not isinstance(self.age_ms, int)
            or not 0 <= self.age_ms <= 0xFFFF
        ):
            raise PayloadError("reading age_ms must be between 0 and 65535")
        object.__setattr__(self, "metric_id", metric)
        object.__setattr__(self, "value", float(self.value))


BATCH_COUNT_STRUCT = struct.Struct("!H")
READING_STRUCT = struct.Struct("!BfH")
LATEST_STRUCT = struct.Struct("!Bf")
MAX_BATCH_READINGS = (
    MAX_PAYLOAD_SIZE - BATCH_COUNT_STRUCT.size
) // READING_STRUCT.size


def encode_readings(readings: Iterable[Reading]) -> bytes:
    items = list(readings)
    if not items:
        raise PayloadError("DATA_BATCH must contain at least one reading")
    if len(items) > MAX_BATCH_READINGS:
        raise PayloadError(
            f"DATA_BATCH supports at most {MAX_BATCH_READINGS} readings"
        )
    encoded = bytearray(BATCH_COUNT_STRUCT.pack(len(items)))
    for reading in items:
        encoded.extend(
            READING_STRUCT.pack(
                int(reading.metric_id), float(reading.value), reading.age_ms
            )
        )
    return bytes(encoded)


def decode_readings(payload: bytes) -> list[Reading]:
    if len(payload) < BATCH_COUNT_STRUCT.size:
        raise PayloadError("DATA_BATCH payload is missing its reading count")
    (count,) = BATCH_COUNT_STRUCT.unpack_from(payload)
    expected = BATCH_COUNT_STRUCT.size + count * READING_STRUCT.size
    if count == 0 or len(payload) != expected:
        raise PayloadError(
            f"invalid DATA_BATCH length: count={count}, expected={expected}, "
            f"received={len(payload)}"
        )
    readings: list[Reading] = []
    offset = BATCH_COUNT_STRUCT.size
    for _ in range(count):
        metric_id, value, age_ms = READING_STRUCT.unpack_from(payload, offset)
        try:
            metric = MetricId(metric_id)
        except ValueError as exc:
            raise PayloadError(f"unknown metric id {metric_id}") from exc
        readings.append(Reading(metric, value, age_ms))
        offset += READING_STRUCT.size
    return readings


def encode_latest(metric_id: MetricId, value: float) -> bytes:
    try:
        metric = MetricId(metric_id)
    except (TypeError, ValueError) as exc:
        raise PayloadError(f"unknown metric id {metric_id}") from exc
    _require_float32(value, "latest value")
    return LATEST_STRUCT.pack(int(metric), float(value))


def decode_latest(payload: bytes) -> tuple[MetricId, float]:
    if len(payload) != LATEST_STRUCT.size:
        raise PayloadError(
            f"LATEST_UPDATE payload must be {LATEST_STRUCT.size} bytes"
        )
    metric_id, value = LATEST_STRUCT.unpack(payload)
    try:
        metric = MetricId(metric_id)
    except ValueError as exc:
        raise PayloadError(f"unknown metric id {metric_id}") from exc
    if not math.isfinite(value):
        raise PayloadError("latest value must be finite")
    return metric, value


def _require_float32(value: float, label: str) -> None:
    try:
        numeric = float(value)
        packed = struct.pack("!f", numeric)
        round_trip = struct.unpack("!f", packed)[0]
    except (TypeError, ValueError, OverflowError, struct.error) as exc:
        raise PayloadError(f"{label} must fit finite IEEE-754 float32") from exc
    if not math.isfinite(numeric) or not math.isfinite(round_trip):
        raise PayloadError(f"{label} must fit finite IEEE-754 float32")


def packet_summary(packet: DartPacket) -> str:
    flags = packet.flags.name or "NONE"
    return (
        f"{packet.msg_type.name} sensor={packet.sensor_id} "
        f"session={packet.session_id} seq={packet.sequence} "
        f"class={packet.delivery.name} flags={flags} "
        f"status={packet.status_code} {status_phrase(packet.status_code)} "
        f"bytes={packet.wire_size}"
    )
