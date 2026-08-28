"""DART sensor client with registration, ACK, timeout, and retransmission."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import logging
import math
import random
import secrets
import socket
import threading
import time
from typing import Any, Iterable

from .network import ImpairedTransmitter, validate_impairment
from .protocol import (
    ChecksumError,
    DartPacket,
    DeliveryClass,
    Flags,
    MAX_DATAGRAM_SIZE,
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
    now_ms,
    packet_summary,
)


LOGGER = logging.getLogger("dart.client")

CLIENT_INSTANCE_ID_BYTES = 16
CLIENT_INSTANCE_ID_HEX_LENGTH = CLIENT_INSTANCE_ID_BYTES * 2


def _normalize_client_instance_id(value: str) -> str:
    """Return the canonical 128-bit hexadecimal client-instance identity."""
    if not isinstance(value, str):
        raise ValueError("client_instance_id must be a hexadecimal string")
    normalized = value.lower()
    if (
        len(normalized) != CLIENT_INSTANCE_ID_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in normalized)
    ):
        raise ValueError(
            f"client_instance_id must contain exactly "
            f"{CLIENT_INSTANCE_ID_HEX_LENGTH} hexadecimal characters"
        )
    return normalized


@dataclass(frozen=True)
class DeliveryResult:
    success: bool
    sequence: int
    attempts: int
    latency_ms: float
    status_code: int
    response_type: MessageType | None = None
    detail: str = ""


@dataclass
class ClientMetrics:
    started_at_ms: int = field(default_factory=now_ms)
    messages_generated: Counter[str] = field(default_factory=Counter)
    responses_received: Counter[str] = field(default_factory=Counter)
    retransmissions: int = 0
    decode_errors: int = 0
    checksum_errors: int = 0
    reliable_successes: int = 0
    reliable_failures: int = 0
    critical_successes: int = 0
    critical_failures: int = 0
    reliable_latency_ms: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        latencies = sorted(self.reliable_latency_ms)
        return {
            "started_at_ms": self.started_at_ms,
            "messages_generated": dict(self.messages_generated),
            "responses_received": dict(self.responses_received),
            "retransmissions": self.retransmissions,
            "decode_errors": self.decode_errors,
            "checksum_errors": self.checksum_errors,
            "reliable_successes": self.reliable_successes,
            "reliable_failures": self.reliable_failures,
            "critical_successes": self.critical_successes,
            "critical_failures": self.critical_failures,
            "reliable_latency_ms": {
                "count": len(latencies),
                "median": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "max": max(latencies) if latencies else 0.0,
            },
        }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = (len(values) - 1) * percentile / 100.0
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    fraction = index - lower
    return round(values[lower] * (1 - fraction) + values[upper] * fraction, 3)


@dataclass
class _PendingResponse:
    event: threading.Event = field(default_factory=threading.Event)
    packet: DartPacket | None = None


class SensorClient:
    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        sensor_id: int,
        name: str | None = None,
        capabilities: Iterable[str] = ("temperature", "position", "alert"),
        bind_host: str = "127.0.0.1",
        loss_rate: float = 0.0,
        corrupt_rate: float = 0.0,
        network_delay_ms: float = 0.0,
        jitter_ms: float = 0.0,
        ack_timeout_s: float = 0.25,
        max_attempts: int = 5,
        client_instance_id: str | None = None,
        seed: int | None = None,
        quiet: bool = False,
    ) -> None:
        if not 0 <= sensor_id <= 0xFFFFFFFF:
            raise ValueError("sensor_id must fit in uint32")
        if not math.isfinite(ack_timeout_s) or ack_timeout_s <= 0:
            raise ValueError("ack_timeout_s must be finite and positive")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        validate_impairment(
            loss_rate=loss_rate,
            corrupt_rate=corrupt_rate,
            delay_ms=network_delay_ms,
            jitter_ms=jitter_ms,
        )
        resolved_client_instance_id = (
            secrets.token_hex(CLIENT_INSTANCE_ID_BYTES)
            if client_instance_id is None
            else _normalize_client_instance_id(client_instance_id)
        )
        self.server_address = (socket.gethostbyname(server_address[0]), server_address[1])
        self.sensor_id = sensor_id
        self.name = name or f"sensor-{sensor_id:03d}"
        self.capabilities = list(capabilities)
        self.ack_timeout_s = ack_timeout_s
        self.max_attempts = max_attempts
        self.client_instance_id = resolved_client_instance_id
        self.quiet = quiet
        self.session_id = 0
        self.server_config: dict[str, Any] = {}

        self._sequence_lock = threading.Lock()
        self._sequence = random.Random(seed).randrange(1, 0xFFFFFFFF)
        self._pending_lock = threading.Lock()
        self._pending: dict[int, _PendingResponse] = {}
        self._running = threading.Event()
        self._running.set()
        self.metrics = ClientMetrics()
        self._metrics_lock = threading.Lock()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((bind_host, 0))
            sock.settimeout(0.1)
            self._socket = sock
            self._transmitter = ImpairedTransmitter(
                sock,
                loss_rate=loss_rate,
                corrupt_rate=corrupt_rate,
                delay_ms=network_delay_ms,
                jitter_ms=jitter_ms,
                seed=seed,
                on_event=self._log_impairment,
            )
            self._receiver = threading.Thread(
                target=self._receive_loop,
                name=f"dart-client-{sensor_id}-receiver",
                daemon=True,
            )
            self._receiver.start()
        except Exception:
            self._running.clear()
            sock.close()
            raise

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._socket.getsockname()
        return str(host), int(port)

    def register(self) -> DeliveryResult:
        packet = DartPacket(
            msg_type=MessageType.REGISTER_REQ,
            delivery=DeliveryClass.CONTROL,
            flags=Flags.ACK_REQUIRED | Flags.SIMULATED,
            sensor_id=self.sensor_id,
            sequence=self._next_sequence(),
            ttl_ms=10_000,
            payload=encode_json(
                {
                    "name": self.name,
                    "capabilities": self.capabilities,
                    "client_instance_id": self.client_instance_id,
                }
            ),
        )
        result, response = self._send_reliable(
            packet, expected={MessageType.REGISTER_RES, MessageType.ERROR}
        )
        if result.success and response and response.msg_type == MessageType.REGISTER_RES:
            body = decode_json(response.payload)
            if isinstance(body, dict):
                self.session_id = int(body.get("session_id", response.session_id))
                raw_config = body.get("config", {})
                if isinstance(raw_config, dict):
                    self.server_config = raw_config
            else:
                self.session_id = response.session_id
            self._log(
                f"REGISTERED session={self.session_id} server={self.server_address}"
            )
        return result

    def send_batch(
        self,
        readings: Iterable[Reading],
        *,
        reliable: bool = False,
        ttl_ms: int = 2_000,
    ) -> DeliveryResult:
        self._require_registration()
        flags = Flags.SIMULATED | (Flags.ACK_REQUIRED if reliable else Flags.NONE)
        packet = DartPacket(
            msg_type=MessageType.DATA_BATCH,
            delivery=DeliveryClass.BEST_EFFORT_BATCH,
            flags=flags,
            session_id=self.session_id,
            sensor_id=self.sensor_id,
            sequence=self._next_sequence(),
            ttl_ms=ttl_ms,
            payload=encode_readings(readings),
        )
        return self._send_by_policy(packet, reliable)

    def send_latest(
        self,
        metric_id: MetricId,
        value: float,
        *,
        reliable: bool = False,
        ttl_ms: int = 1_000,
    ) -> DeliveryResult:
        self._require_registration()
        flags = Flags.SIMULATED | (Flags.ACK_REQUIRED if reliable else Flags.NONE)
        packet = DartPacket(
            msg_type=MessageType.LATEST_UPDATE,
            delivery=DeliveryClass.LATEST_ONLY,
            flags=flags,
            session_id=self.session_id,
            sensor_id=self.sensor_id,
            sequence=self._next_sequence(),
            ttl_ms=ttl_ms,
            payload=encode_latest(metric_id, value),
        )
        return self._send_by_policy(packet, reliable)

    def send_critical(
        self,
        *,
        alert_type: str,
        severity: str = "critical",
        value: float | int | str | None = None,
        unit: str = "",
        message: str = "",
        reliable: bool = True,
        ttl_ms: int = 5_000,
    ) -> DeliveryResult:
        self._require_registration()
        flags = Flags.SIMULATED | (Flags.ACK_REQUIRED if reliable else Flags.NONE)
        packet = DartPacket(
            msg_type=MessageType.CRITICAL_ALERT,
            delivery=DeliveryClass.CRITICAL_RELIABLE,
            flags=flags,
            session_id=self.session_id,
            sensor_id=self.sensor_id,
            sequence=self._next_sequence(),
            ttl_ms=ttl_ms,
            payload=encode_json(
                {
                    "alert_type": alert_type,
                    "severity": severity,
                    "value": value,
                    "unit": unit,
                    "message": message,
                }
            ),
        )
        result = self._send_by_policy(packet, reliable)
        with self._metrics_lock:
            if result.success:
                self.metrics.critical_successes += 1
            else:
                self.metrics.critical_failures += 1
        return result

    def heartbeat(self) -> DeliveryResult:
        self._require_registration()
        packet = DartPacket(
            msg_type=MessageType.HEARTBEAT,
            delivery=DeliveryClass.CONTROL,
            flags=Flags.ACK_REQUIRED | Flags.SIMULATED,
            session_id=self.session_id,
            sensor_id=self.sensor_id,
            sequence=self._next_sequence(),
            ttl_ms=3_000,
        )
        result, _ = self._send_reliable(
            packet, expected={MessageType.HEARTBEAT_ACK, MessageType.ERROR}
        )
        return result

    def request_config(self) -> tuple[DeliveryResult, dict[str, Any]]:
        self._require_registration()
        packet = DartPacket(
            msg_type=MessageType.CONFIG_REQ,
            delivery=DeliveryClass.CONTROL,
            flags=Flags.ACK_REQUIRED | Flags.SIMULATED,
            session_id=self.session_id,
            sensor_id=self.sensor_id,
            sequence=self._next_sequence(),
            ttl_ms=5_000,
            payload=encode_json({}),
        )
        result, response = self._send_reliable(
            packet, expected={MessageType.CONFIG_RES, MessageType.ERROR}
        )
        config: dict[str, Any] = {}
        if result.success and response and response.msg_type == MessageType.CONFIG_RES:
            decoded = decode_json(response.payload)
            if isinstance(decoded, dict):
                config = decoded
                self.server_config = config
        return result, config

    def snapshot_metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            result = self.metrics.as_dict()
        result.update(
            {
                "sensor_id": self.sensor_id,
                "name": self.name,
                "client_instance_id": self.client_instance_id,
                "session_id": self.session_id,
                "local_address": list(self.address),
                "transmitter": self._transmitter.stats.as_dict(),
            }
        )
        return result

    def close(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()
        self._socket.close()
        if self._receiver is not threading.current_thread():
            self._receiver.join(timeout=1.0)
        with self._pending_lock:
            for pending in self._pending.values():
                pending.event.set()
            self._pending.clear()

    def __enter__(self) -> "SensorClient":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()

    def _send_by_policy(self, packet: DartPacket, reliable: bool) -> DeliveryResult:
        if reliable:
            result, _ = self._send_reliable(
                packet, expected={MessageType.ACK, MessageType.ERROR}
            )
            return result
        return self._send_once(packet)

    def _send_once(self, packet: DartPacket) -> DeliveryResult:
        started = time.monotonic()
        with self._metrics_lock:
            self.metrics.messages_generated[packet.msg_type.name] += 1
        outcome = self._transmitter.sendto(packet.encode(), self.server_address)
        self._log(
            f"{'TX' if outcome.sent else 'DROP-TX'} {packet_summary(packet)} "
            f"to={self.server_address[0]}:{self.server_address[1]}"
        )
        return DeliveryResult(
            success=outcome.sent,
            sequence=packet.sequence,
            attempts=1,
            latency_ms=round((time.monotonic() - started) * 1000, 3),
            status_code=StatusCode.NONE,
            detail="sent without delivery acknowledgement"
            if outcome.sent
            else "simulated outbound drop",
        )

    def _send_reliable(
        self,
        packet: DartPacket,
        *,
        expected: set[MessageType],
    ) -> tuple[DeliveryResult, DartPacket | None]:
        pending = _PendingResponse()
        with self._pending_lock:
            if packet.sequence in self._pending:
                raise RuntimeError(f"sequence {packet.sequence} is already pending")
            self._pending[packet.sequence] = pending
        started = time.monotonic()
        response: DartPacket | None = None
        attempts = 0
        with self._metrics_lock:
            self.metrics.messages_generated[packet.msg_type.name] += 1
        try:
            for attempt in range(1, self.max_attempts + 1):
                attempts = attempt
                if packet.is_expired():
                    break
                flags = packet.flags
                if attempt > 1:
                    flags |= Flags.RETRANSMISSION
                    with self._metrics_lock:
                        self.metrics.retransmissions += 1
                outbound = replace(packet, flags=flags)
                outcome = self._transmitter.sendto(
                    outbound.encode(), self.server_address
                )
                self._log(
                    f"{'TX' if outcome.sent else 'DROP-TX'} {packet_summary(outbound)} "
                    f"attempt={attempt}/{self.max_attempts}"
                )

                remaining_ms = packet.timestamp_ms + packet.ttl_ms - now_ms()
                if packet.ttl_ms == 0:
                    remaining_s = self.ack_timeout_s * (2 ** (attempt - 1))
                else:
                    remaining_s = max(0.0, remaining_ms / 1000.0)
                wait_s = min(
                    self.ack_timeout_s * (2 ** (attempt - 1)), remaining_s
                )
                if wait_s <= 0:
                    break
                if pending.event.wait(wait_s):
                    response = pending.packet
                    if response is None:
                        break
                    if response.msg_type not in expected:
                        pending.event.clear()
                        continue
                    break
                self._warn(
                    f"TIMEOUT sensor={self.sensor_id} type={packet.msg_type.name} "
                    f"seq={packet.sequence} after={wait_s * 1000:.0f}ms"
                )
        finally:
            with self._pending_lock:
                self._pending.pop(packet.sequence, None)

        latency_ms = round((time.monotonic() - started) * 1000, 3)
        success_codes = {
            int(StatusCode.OK),
            int(StatusCode.REGISTERED),
            int(StatusCode.ACCEPTED),
            int(StatusCode.DUPLICATE),
        }
        success = bool(
            response
            and response.msg_type in expected
            and response.msg_type != MessageType.ERROR
            and int(response.status_code) in success_codes
        )
        status = int(response.status_code) if response else int(StatusCode.NONE)
        detail = "acknowledged" if success else "no acceptable response before expiry"
        if response and response.msg_type == MessageType.ERROR:
            try:
                detail = str(decode_json(response.payload).get("detail", "server error"))
            except Exception:
                detail = "server error"
        with self._metrics_lock:
            self.metrics.reliable_latency_ms.append(latency_ms)
            if success:
                self.metrics.reliable_successes += 1
            else:
                self.metrics.reliable_failures += 1
        result = DeliveryResult(
            success=success,
            sequence=packet.sequence,
            attempts=attempts,
            latency_ms=latency_ms,
            status_code=status,
            response_type=response.msg_type if response else None,
            detail=detail,
        )
        return result, response

    def _receive_loop(self) -> None:
        while self._running.is_set():
            try:
                raw, address = self._socket.recvfrom(MAX_DATAGRAM_SIZE + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            if (socket.gethostbyname(address[0]), address[1]) != self.server_address:
                self._warn(f"IGNORE datagram from unexpected peer {address}")
                continue
            try:
                packet = DartPacket.decode(raw)
            except ChecksumError as exc:
                with self._metrics_lock:
                    self.metrics.checksum_errors += 1
                    self.metrics.decode_errors += 1
                self._warn(f"DROP checksum_error: {exc}")
                continue
            except ProtocolError as exc:
                with self._metrics_lock:
                    self.metrics.decode_errors += 1
                self._warn(f"DROP malformed response: {exc}")
                continue
            try:
                self._validate_server_packet(packet)
            except ProtocolError as exc:
                with self._metrics_lock:
                    self.metrics.decode_errors += 1
                self._warn(f"DROP invalid response envelope/payload: {exc}")
                continue
            if packet.is_expired():
                self._warn(
                    f"DROP expired response type={packet.msg_type.name} "
                    f"seq={packet.sequence}"
                )
                continue
            if packet.sensor_id != self.sensor_id:
                self._warn(
                    f"IGNORE response for sensor={packet.sensor_id}; this sensor={self.sensor_id}"
                )
                continue
            if (
                self.session_id
                and packet.msg_type != MessageType.REGISTER_RES
                and packet.session_id != self.session_id
            ):
                self._warn(
                    f"IGNORE response for session={packet.session_id}; "
                    f"active session={self.session_id}"
                )
                continue
            with self._metrics_lock:
                self.metrics.responses_received[packet.msg_type.name] += 1
            self._log(f"RX {packet_summary(packet)}")
            with self._pending_lock:
                pending = self._pending.get(packet.sequence)
                if pending:
                    pending.packet = packet
                    pending.event.set()

    @staticmethod
    def _validate_server_packet(packet: DartPacket) -> None:
        response_types = {
            MessageType.REGISTER_RES,
            MessageType.ACK,
            MessageType.CONFIG_RES,
            MessageType.HEARTBEAT_ACK,
            MessageType.ERROR,
        }
        if packet.msg_type not in response_types:
            raise ProtocolError(
                f"client does not accept server message {packet.msg_type.name}"
            )
        if packet.delivery != DeliveryClass.CONTROL:
            raise ProtocolError("server responses must use CONTROL delivery")
        if packet.flags != Flags.NONE:
            raise ProtocolError("server responses must not set request flags")

        status = int(packet.status_code)
        if packet.msg_type == MessageType.REGISTER_RES:
            if status != int(StatusCode.REGISTERED) or not packet.session_id:
                raise ProtocolError("REGISTER_RES needs status 201 and a session_id")
            body = decode_json(packet.payload)
            if not isinstance(body, dict):
                raise PayloadError("REGISTER_RES payload must be a JSON object")
            body_session_id = body.get("session_id")
            if (
                type(body_session_id) is not int
                or not 1 <= body_session_id <= 0xFFFFFFFF
            ):
                raise PayloadError(
                    "REGISTER_RES body session_id must be a non-zero uint32"
                )
            if body_session_id != packet.session_id:
                raise PayloadError("REGISTER_RES body/header session_id mismatch")
            if "config" in body and not isinstance(body["config"], dict):
                raise PayloadError("REGISTER_RES config must be a JSON object")
            return
        if packet.msg_type == MessageType.ACK:
            if packet.payload:
                raise PayloadError("ACK payload must be empty")
            if status not in {
                int(StatusCode.ACCEPTED),
                int(StatusCode.DUPLICATE),
                int(StatusCode.EXPIRED),
            }:
                raise ProtocolError(f"invalid ACK status {status}")
            return
        if packet.msg_type == MessageType.CONFIG_RES:
            if status != int(StatusCode.OK):
                raise ProtocolError("CONFIG_RES status must be 200")
            if not isinstance(decode_json(packet.payload), dict):
                raise PayloadError("CONFIG_RES payload must be a JSON object")
            return
        if packet.msg_type == MessageType.HEARTBEAT_ACK:
            if status != int(StatusCode.OK) or packet.payload:
                raise ProtocolError("HEARTBEAT_ACK needs status 200 and empty payload")
            return
        if status < 400:
            raise ProtocolError("ERROR response status must be 4xx or 5xx")
        error = decode_json(packet.payload)
        if not isinstance(error, dict):
            raise PayloadError("ERROR payload must be a JSON object")

    def _next_sequence(self) -> int:
        with self._sequence_lock:
            sequence = self._sequence
            self._sequence = (self._sequence + 1) & 0xFFFFFFFF
            if self._sequence == 0:
                self._sequence = 1
            return sequence

    def _require_registration(self) -> None:
        if not self.session_id:
            raise RuntimeError("sensor is not registered; call register() first")

    def _log(self, message: str) -> None:
        if not self.quiet:
            LOGGER.info("sensor=%s | %s", self.sensor_id, message)

    def _warn(self, message: str) -> None:
        if not self.quiet:
            LOGGER.warning("sensor=%s | %s", self.sensor_id, message)

    def _log_impairment(self, message: str) -> None:
        self._warn(message)
