"""Concurrent UDP server for the DART protocol."""

from __future__ import annotations

from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import argparse
import json
import logging
import math
from pathlib import Path
import secrets
import signal
import socket
import threading
import time
from typing import Any

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
    StatusCode,
    decode_json,
    decode_latest,
    decode_readings,
    encode_json,
    now_ms,
    packet_summary,
)


LOGGER = logging.getLogger("dart.server")


class _EnvelopeError(ProtocolError):
    """A decodable packet violates DART message-envelope semantics."""


@dataclass
class SensorSession:
    session_id: int
    sensor_id: int
    client_instance_id: str | None
    name: str
    address: tuple[str, int]
    capabilities: list[str]
    registered_at_ms: int
    last_seen_ms: int


@dataclass
class ServerMetrics:
    started_at_ms: int = field(default_factory=now_ms)
    stopped_at_ms: int = 0
    packets_received: int = 0
    bytes_received: int = 0
    decode_errors: int = 0
    checksum_errors: int = 0
    expired_packets: int = 0
    unregistered_packets: int = 0
    duplicate_packets: int = 0
    stale_latest_discarded: int = 0
    data_batches_accepted: int = 0
    latest_updates_accepted: int = 0
    readings_received: int = 0
    critical_alerts_received: int = 0
    acks_sent: int = 0
    acks_simulated_dropped: int = 0
    errors_sent: int = 0
    messages_by_type: Counter[str] = field(default_factory=Counter)
    critical_latency_ms: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        latencies = sorted(self.critical_latency_ms)
        return {
            "started_at_ms": self.started_at_ms,
            "stopped_at_ms": self.stopped_at_ms,
            "duration_ms": max(
                0, (self.stopped_at_ms or now_ms()) - self.started_at_ms
            ),
            "packets_received": self.packets_received,
            "bytes_received": self.bytes_received,
            "decode_errors": self.decode_errors,
            "checksum_errors": self.checksum_errors,
            "expired_packets": self.expired_packets,
            "unregistered_packets": self.unregistered_packets,
            "duplicate_packets": self.duplicate_packets,
            "stale_latest_discarded": self.stale_latest_discarded,
            "data_batches_accepted": self.data_batches_accepted,
            "latest_updates_accepted": self.latest_updates_accepted,
            "readings_received": self.readings_received,
            "critical_alerts_received": self.critical_alerts_received,
            "acks_sent": self.acks_sent,
            "acks_simulated_dropped": self.acks_simulated_dropped,
            "errors_sent": self.errors_sent,
            "messages_by_type": dict(self.messages_by_type),
            "critical_latency_ms": {
                "count": len(latencies),
                "median": _percentile(latencies, 50),
                "p95": _percentile(latencies, 95),
                "max": max(latencies) if latencies else 0.0,
                "samples": [round(value, 3) for value in latencies],
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


def _sequence_is_newer(candidate: int, previous: int) -> bool:
    """Compare wrapping unsigned 32-bit sequence numbers."""
    distance = (candidate - previous) & 0xFFFFFFFF
    return 0 < distance < 0x80000000


class DartServer:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9999,
        *,
        workers: int = 8,
        ack_loss_rate: float = 0.0,
        ack_corrupt_rate: float = 0.0,
        network_delay_ms: float = 0.0,
        jitter_ms: float = 0.0,
        seed: int = 1,
        drop_first_critical_ack: bool = False,
        allow_experimental_policies: bool = False,
        duplicate_retention_s: float = 60.0,
        quiet: bool = False,
        metrics_file: str | Path | None = None,
    ) -> None:
        if workers < 1:
            raise ValueError("workers must be at least 1")
        if not 0 <= port <= 65535:
            raise ValueError("port must be between 0 and 65535")
        if (
            not math.isfinite(duplicate_retention_s)
            or duplicate_retention_s <= 0
        ):
            raise ValueError("duplicate_retention_s must be finite and positive")
        validate_impairment(
            loss_rate=ack_loss_rate,
            corrupt_rate=ack_corrupt_rate,
            delay_ms=network_delay_ms,
            jitter_ms=jitter_ms,
        )
        self.host = host
        self.port = port
        self.workers = workers
        self.ack_loss_rate = ack_loss_rate
        self.ack_corrupt_rate = ack_corrupt_rate
        self.network_delay_ms = network_delay_ms
        self.jitter_ms = jitter_ms
        self.seed = seed
        self.drop_first_critical_ack = drop_first_critical_ack
        self.allow_experimental_policies = allow_experimental_policies
        self.duplicate_retention_s = duplicate_retention_s
        self.quiet = quiet
        self.metrics_file = Path(metrics_file) if metrics_file else None

        self.metrics = ServerMetrics()
        self._metrics_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._sessions_by_id: dict[int, SensorSession] = {}
        self._registration_index: dict[
            tuple[int, tuple[str, int], str], int
        ] = {}
        self._latest_sequences: dict[tuple[int, int, MetricId], int] = {}
        self._latest_values: dict[
            tuple[int, int, MetricId], tuple[float, int]
        ] = {}
        self._seen: OrderedDict[tuple[int, int, int, int], float] = OrderedDict()
        self._inflight: set[tuple[int, int, int, int]] = set()
        self._duplicate_condition = threading.Condition(self._state_lock)
        self._dropped_first_ack_for: set[tuple[int, int]] = set()

        self._activity_condition = threading.Condition()
        self._active_tasks = 0
        self._last_datagram_at = time.monotonic()

        self._socket: socket.socket | None = None
        self._transmitter: ImpairedTransmitter | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._receiver_thread: threading.Thread | None = None
        self._running = threading.Event()

    @property
    def address(self) -> tuple[str, int]:
        if self._socket is None:
            return self.host, self.port
        address = self._socket.getsockname()
        return str(address[0]), int(address[1])

    def start(self) -> "DartServer":
        if self._running.is_set():
            return self
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        executor: ThreadPoolExecutor | None = None
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.settimeout(0.2)
            transmitter = ImpairedTransmitter(
                sock,
                loss_rate=self.ack_loss_rate,
                corrupt_rate=self.ack_corrupt_rate,
                delay_ms=self.network_delay_ms,
                jitter_ms=self.jitter_ms,
                seed=self.seed,
                on_event=self._log_impairment,
            )
            executor = ThreadPoolExecutor(
                max_workers=self.workers, thread_name_prefix="dart-worker"
            )
            self._socket = sock
            self.port = int(sock.getsockname()[1])
            self._transmitter = transmitter
            self._executor = executor
            with self._activity_condition:
                self._last_datagram_at = time.monotonic()
            self._running.set()
            receiver_thread = threading.Thread(
                target=self._receive_loop, name="dart-receiver", daemon=True
            )
            self._receiver_thread = receiver_thread
            receiver_thread.start()
        except Exception:
            self._running.clear()
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            sock.close()
            self._socket = None
            self._transmitter = None
            self._executor = None
            self._receiver_thread = None
            raise
        self._log(
            f"LISTEN udp://{self.address[0]}:{self.address[1]} workers={self.workers}"
        )
        return self

    def serve_forever(self) -> None:
        self.start()
        try:
            while self._running.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        if not self._running.is_set() and self._socket is None:
            return
        self._running.clear()
        with self._duplicate_condition:
            self._duplicate_condition.notify_all()
        with self._activity_condition:
            self._activity_condition.notify_all()
        sock = self._socket
        if self._receiver_thread and self._receiver_thread is not threading.current_thread():
            self._receiver_thread.join(timeout=1.0)
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
        self._socket = None
        if sock is not None:
            sock.close()
        with self._metrics_lock:
            self.metrics.stopped_at_ms = now_ms()
        if self.metrics_file:
            self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
            self.metrics_file.write_text(
                json.dumps(self.snapshot_metrics(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        self._log("STOPPED")

    def wait_until_idle(
        self, *, quiet_period_s: float = 0.1, timeout_s: float = 2.0
    ) -> bool:
        """Wait until all submitted datagrams finish and input stays quiet."""
        if quiet_period_s < 0 or timeout_s < 0:
            raise ValueError("quiet period and timeout cannot be negative")
        deadline = time.monotonic() + timeout_s
        with self._activity_condition:
            while True:
                current = time.monotonic()
                quiet_for = current - self._last_datagram_at
                if self._active_tasks == 0 and quiet_for >= quiet_period_s:
                    return True
                remaining = deadline - current
                if remaining <= 0:
                    return False
                quiet_remaining = max(0.0, quiet_period_s - quiet_for)
                self._activity_condition.wait(
                    timeout=min(remaining, max(0.01, quiet_remaining))
                )

    def snapshot_metrics(self) -> dict[str, Any]:
        with self._metrics_lock:
            result = self.metrics.as_dict()
        with self._state_lock:
            result["active_sessions"] = len(self._sessions_by_id)
            result["latest_values"] = {
                f"session_{session_id}.sensor_{sensor_id}.{metric.name}": {
                    "value": round(value, 4),
                    "timestamp_ms": timestamp,
                    "sequence": self._latest_sequences.get(
                        (session_id, sensor_id, metric)
                    ),
                }
                for (session_id, sensor_id, metric), (
                    value,
                    timestamp,
                ) in self._latest_values.items()
            }
        if self._transmitter:
            result["server_transmitter"] = self._transmitter.stats.as_dict()
        return result

    def _receive_loop(self) -> None:
        while self._running.is_set():
            sock = self._socket
            if sock is None:
                break
            try:
                raw, address = sock.recvfrom(MAX_DATAGRAM_SIZE + 1)
            except socket.timeout:
                continue
            except OSError:
                break
            with self._metrics_lock:
                self.metrics.packets_received += 1
                self.metrics.bytes_received += len(raw)
            with self._activity_condition:
                self._active_tasks += 1
                self._last_datagram_at = time.monotonic()
            executor = self._executor
            if executor:
                try:
                    executor.submit(self._process_datagram_task, raw, address)
                except RuntimeError:
                    with self._activity_condition:
                        self._active_tasks -= 1
                        self._activity_condition.notify_all()
                    break
            else:
                with self._activity_condition:
                    self._active_tasks -= 1
                    self._activity_condition.notify_all()

    def _process_datagram_task(
        self, raw: bytes, address: tuple[str, int]
    ) -> None:
        try:
            self._process_datagram(raw, address)
        finally:
            with self._activity_condition:
                self._active_tasks -= 1
                self._activity_condition.notify_all()

    def _process_datagram(self, raw: bytes, address: tuple[str, int]) -> None:
        try:
            packet = DartPacket.decode(raw)
        except ChecksumError as exc:
            with self._metrics_lock:
                self.metrics.checksum_errors += 1
                self.metrics.decode_errors += 1
            self._warn(f"DROP checksum_error from={address}: {exc}")
            return
        except ProtocolError as exc:
            with self._metrics_lock:
                self.metrics.decode_errors += 1
            self._warn(f"DROP malformed from={address}: {exc}")
            return

        with self._metrics_lock:
            self.metrics.messages_by_type[packet.msg_type.name] += 1
        self._log(f"RX {packet_summary(packet)} from={address[0]}:{address[1]}")

        client_message_types = {
            MessageType.REGISTER_REQ,
            MessageType.DATA_BATCH,
            MessageType.LATEST_UPDATE,
            MessageType.CRITICAL_ALERT,
            MessageType.CONFIG_REQ,
            MessageType.HEARTBEAT,
        }
        if packet.msg_type not in client_message_types:
            # Never answer a response-only packet with ERROR: doing so could form
            # an ERROR/ACK reflection loop with another faulty implementation.
            self._warn(
                f"DROP response-only type={packet.msg_type.name} from client {address}"
            )
            return

        try:
            self._validate_client_envelope(packet)
        except _EnvelopeError as exc:
            self._send_error(packet, address, StatusCode.MALFORMED, str(exc))
            return

        if packet.msg_type == MessageType.REGISTER_REQ:
            if packet.is_expired():
                with self._metrics_lock:
                    self.metrics.expired_packets += 1
                self._warn(
                    f"EXPIRED type={packet.msg_type.name} sensor={packet.sensor_id} "
                    f"seq={packet.sequence}"
                )
                self._send_expired(packet, address)
                return
            self._handle_register(packet, address)
            return
        if not self._valid_session(packet, address):
            with self._metrics_lock:
                self.metrics.unregistered_packets += 1
            self._send_error(packet, address, StatusCode.UNREGISTERED, "register first")
            return
        if packet.is_expired():
            with self._metrics_lock:
                self.metrics.expired_packets += 1
            self._warn(
                f"EXPIRED type={packet.msg_type.name} sensor={packet.sensor_id} "
                f"seq={packet.sequence}"
            )
            self._send_expired(packet, address)
            return

        # Validate the complete application payload before reserving its message
        # identity.  Otherwise a checksum-valid but malformed packet would poison
        # the duplicate cache and a corrected retry with the same sequence number
        # could never be processed.
        try:
            self._validate_client_payload(packet)
        except PayloadError as exc:
            self._send_error(packet, address, StatusCode.INVALID_PAYLOAD, str(exc))
            return

        claim = self._claim_message(packet)
        if claim == "expired":
            with self._metrics_lock:
                self.metrics.expired_packets += 1
            self._send_expired(packet, address)
            return
        if claim == "duplicate":
            with self._metrics_lock:
                self.metrics.duplicate_packets += 1
            self._warn(
                f"DUPLICATE type={packet.msg_type.name} sensor={packet.sensor_id} "
                f"seq={packet.sequence}"
            )
            # CONFIG_REQ and HEARTBEAT are idempotent request/response operations.
            # Replaying their typed response lets a client recover when the first
            # CONFIG_RES/HEARTBEAT_ACK was lost.  A generic ACK would not satisfy
            # the response contract for those requests.
            if packet.msg_type in {MessageType.CONFIG_REQ, MessageType.HEARTBEAT}:
                self._dispatch(packet, address)
            elif packet.requires_ack:
                self._send_ack(packet, address, StatusCode.DUPLICATE)
            return

        try:
            status = self._dispatch(packet, address)
        except PayloadError as exc:
            self._finish_message(packet, success=False)
            self._send_error(packet, address, StatusCode.INVALID_PAYLOAD, str(exc))
            return
        except Exception:  # keep an unexpected handler failure from killing the server
            self._finish_message(packet, success=False)
            LOGGER.exception("internal error while processing %s", packet.msg_type.name)
            self._send_error(
                packet,
                address,
                StatusCode.INTERNAL_ERROR,
                "internal server error",
            )
            return

        self._finish_message(packet, success=True)

        if packet.requires_ack and packet.msg_type not in {
            MessageType.CONFIG_REQ,
            MessageType.HEARTBEAT,
        }:
            self._send_ack(packet, address, status)

    def _validate_client_envelope(self, packet: DartPacket) -> None:
        expected_delivery = {
            MessageType.REGISTER_REQ: DeliveryClass.CONTROL,
            MessageType.DATA_BATCH: DeliveryClass.BEST_EFFORT_BATCH,
            MessageType.LATEST_UPDATE: DeliveryClass.LATEST_ONLY,
            MessageType.CRITICAL_ALERT: DeliveryClass.CRITICAL_RELIABLE,
            MessageType.CONFIG_REQ: DeliveryClass.CONTROL,
            MessageType.HEARTBEAT: DeliveryClass.CONTROL,
        }
        delivery = expected_delivery.get(packet.msg_type)
        if delivery is None:
            raise _EnvelopeError(
                f"server does not accept {packet.msg_type.name} from clients"
            )
        if packet.delivery != delivery:
            raise _EnvelopeError(
                f"{packet.msg_type.name} requires delivery class {delivery.name}"
            )
        if int(packet.status_code) != int(StatusCode.NONE):
            raise _EnvelopeError("client request/data status_code must be 0")
        known_flags = Flags.ACK_REQUIRED | Flags.RETRANSMISSION | Flags.SIMULATED
        unknown_flags = int(packet.flags) & ~int(known_flags)
        if unknown_flags:
            raise _EnvelopeError(f"unknown client flag bits 0x{unknown_flags:02x}")
        if packet.flags & Flags.RETRANSMISSION and not packet.requires_ack:
            raise _EnvelopeError("RETRANSMISSION requires ACK_REQUIRED")

        ack_required_types = {
            MessageType.REGISTER_REQ,
            MessageType.CRITICAL_ALERT,
            MessageType.CONFIG_REQ,
            MessageType.HEARTBEAT,
        }
        if (
            packet.msg_type in ack_required_types
            and not packet.requires_ack
            and not (
                self.allow_experimental_policies
                and packet.msg_type == MessageType.CRITICAL_ALERT
            )
        ):
            raise _EnvelopeError(f"{packet.msg_type.name} requires ACK_REQUIRED")

        if (
            packet.msg_type in {MessageType.DATA_BATCH, MessageType.LATEST_UPDATE}
            and packet.requires_ack
            and not self.allow_experimental_policies
        ):
            raise _EnvelopeError(
                f"{packet.msg_type.name} cannot request ACK in strict DART mode"
            )
        if packet.msg_type == MessageType.REGISTER_REQ and packet.session_id != 0:
            raise _EnvelopeError("REGISTER_REQ session_id must be 0")

    def _validate_client_payload(self, packet: DartPacket) -> None:
        """Validate message-specific payload semantics without changing state."""
        if packet.msg_type == MessageType.DATA_BATCH:
            decode_readings(packet.payload)
            return
        if packet.msg_type == MessageType.LATEST_UPDATE:
            decode_latest(packet.payload)
            return
        if packet.msg_type == MessageType.CRITICAL_ALERT:
            alert = decode_json(packet.payload)
            if (
                not isinstance(alert, dict)
                or not isinstance(alert.get("alert_type"), str)
                or not alert["alert_type"].strip()
            ):
                raise PayloadError(
                    "CRITICAL_ALERT needs a non-empty alert_type string"
                )
            return
        if packet.msg_type == MessageType.CONFIG_REQ:
            request = decode_json(packet.payload)
            if not isinstance(request, dict):
                raise PayloadError("CONFIG_REQ must contain a JSON object")
            return
        if packet.msg_type == MessageType.HEARTBEAT:
            if packet.payload:
                raise PayloadError("HEARTBEAT payload must be empty")
            return
        raise PayloadError(f"unsupported client message type {packet.msg_type.name}")

    def _dispatch(self, packet: DartPacket, address: tuple[str, int]) -> StatusCode:
        if packet.msg_type == MessageType.DATA_BATCH:
            readings = decode_readings(packet.payload)
            with self._metrics_lock:
                self.metrics.data_batches_accepted += 1
                self.metrics.readings_received += len(readings)
            values = ", ".join(
                f"{reading.metric_id.name}={reading.value:.2f}" for reading in readings
            )
            self._log(
                f"ACCEPT DATA_BATCH sensor={packet.sensor_id} count={len(readings)} "
                f"[{values}]"
            )
            return StatusCode.ACCEPTED

        if packet.msg_type == MessageType.LATEST_UPDATE:
            metric, value = decode_latest(packet.payload)
            key = (packet.session_id, packet.sensor_id, metric)
            with self._state_lock:
                previous = self._latest_sequences.get(key)
                if previous is not None and not _sequence_is_newer(
                    packet.sequence, previous
                ):
                    with self._metrics_lock:
                        self.metrics.stale_latest_discarded += 1
                    self._warn(
                        f"STALE latest sensor={packet.sensor_id} metric={metric.name} "
                        f"seq={packet.sequence} previous={previous}"
                    )
                    return StatusCode.DUPLICATE
                self._latest_sequences[key] = packet.sequence
                self._latest_values[key] = (value, packet.timestamp_ms)
            with self._metrics_lock:
                self.metrics.latest_updates_accepted += 1
            self._log(
                f"REPLACE latest sensor={packet.sensor_id} metric={metric.name} "
                f"value={value:.2f} seq={packet.sequence}"
            )
            return StatusCode.ACCEPTED

        if packet.msg_type == MessageType.CRITICAL_ALERT:
            alert = decode_json(packet.payload)
            if (
                not isinstance(alert, dict)
                or not isinstance(alert.get("alert_type"), str)
                or not alert["alert_type"].strip()
            ):
                raise PayloadError(
                    "CRITICAL_ALERT needs a non-empty alert_type string"
                )
            latency = max(0.0, float(now_ms() - packet.timestamp_ms))
            with self._metrics_lock:
                self.metrics.critical_alerts_received += 1
                self.metrics.critical_latency_ms.append(latency)
            self._log(
                "CRITICAL "
                f"sensor={packet.sensor_id} type={alert.get('alert_type')} "
                f"severity={alert.get('severity')} latency_ms={latency:.1f}"
            )
            return StatusCode.ACCEPTED

        if packet.msg_type == MessageType.CONFIG_REQ:
            if packet.payload:
                request = decode_json(packet.payload)
                if not isinstance(request, dict):
                    raise PayloadError("CONFIG_REQ must contain a JSON object")
            response = DartPacket(
                msg_type=MessageType.CONFIG_RES,
                delivery=DeliveryClass.CONTROL,
                session_id=packet.session_id,
                sensor_id=packet.sensor_id,
                sequence=packet.sequence,
                ttl_ms=5_000,
                status_code=StatusCode.OK,
                payload=encode_json(self._recommended_config()),
            )
            self._send(response, address)
            return StatusCode.OK

        if packet.msg_type == MessageType.HEARTBEAT:
            response = DartPacket(
                msg_type=MessageType.HEARTBEAT_ACK,
                delivery=DeliveryClass.CONTROL,
                session_id=packet.session_id,
                sensor_id=packet.sensor_id,
                sequence=packet.sequence,
                ttl_ms=2_000,
                status_code=StatusCode.OK,
            )
            self._send(response, address)
            return StatusCode.OK

        if packet.msg_type in {
            MessageType.ACK,
            MessageType.REGISTER_RES,
            MessageType.CONFIG_RES,
            MessageType.HEARTBEAT_ACK,
            MessageType.ERROR,
        }:
            raise PayloadError(f"server does not accept {packet.msg_type.name} from clients")
        raise PayloadError(f"unsupported message type {packet.msg_type.name}")

    def _handle_register(
        self, packet: DartPacket, address: tuple[str, int]
    ) -> None:
        if packet.is_expired():
            return
        try:
            registration = decode_json(packet.payload)
            if not isinstance(registration, dict):
                raise PayloadError("REGISTER_REQ payload must be a JSON object")
            raw_name = registration.get("name", f"sensor-{packet.sensor_id}")
            if not isinstance(raw_name, str):
                raise PayloadError("name must be a JSON string")
            name = raw_name[:80]
            raw_capabilities = registration.get("capabilities", [])
            if not isinstance(raw_capabilities, list):
                raise PayloadError("capabilities must be a JSON list")
            if any(not isinstance(value, str) for value in raw_capabilities):
                raise PayloadError("each capability must be a JSON string")
            capabilities = [value[:40] for value in raw_capabilities[:32]]

            # New DART v1 clients provide a stable random identity for the lifetime
            # of one SensorClient instance.  It distinguishes a retransmitted
            # registration from a fresh process that happens to reuse the same UDP
            # source port.  Legacy clients remain accepted: their registration
            # sequence and timestamp form a best-effort message identity instead.
            if "client_instance_id" in registration:
                raw_instance_id = registration["client_instance_id"]
                if not isinstance(raw_instance_id, str):
                    raise PayloadError(
                        "client_instance_id must be a hexadecimal string"
                    )
                client_instance_id = raw_instance_id.lower()
                if len(client_instance_id) != 32 or any(
                    character not in "0123456789abcdef"
                    for character in client_instance_id
                ):
                    raise PayloadError(
                        "client_instance_id must contain exactly 32 hexadecimal characters"
                    )
                registration_identity = f"instance:{client_instance_id}"
            else:
                client_instance_id = None
                registration_identity = (
                    f"legacy-message:{packet.sequence:08x}:"
                    f"{packet.timestamp_ms:016x}"
                )
        except PayloadError as exc:
            self._send_error(packet, address, StatusCode.INVALID_PAYLOAD, str(exc))
            return

        registration_key = (
            packet.sensor_id,
            address,
            registration_identity,
        )
        with self._state_lock:
            existing = self._registration_index.get(registration_key)
            if existing and existing in self._sessions_by_id:
                session = self._sessions_by_id[existing]
                session.last_seen_ms = now_ms()
            else:
                session_id = self._new_session_id()
                session = SensorSession(
                    session_id=session_id,
                    sensor_id=packet.sensor_id,
                    client_instance_id=client_instance_id,
                    name=name,
                    address=address,
                    capabilities=capabilities,
                    registered_at_ms=now_ms(),
                    last_seen_ms=now_ms(),
                )
                self._sessions_by_id[session_id] = session
                self._registration_index[registration_key] = session_id

        response = DartPacket(
            msg_type=MessageType.REGISTER_RES,
            delivery=DeliveryClass.CONTROL,
            session_id=session.session_id,
            sensor_id=packet.sensor_id,
            sequence=packet.sequence,
            ttl_ms=5_000,
            status_code=StatusCode.REGISTERED,
            payload=encode_json(
                {
                    "session_id": session.session_id,
                    "server_time_ms": now_ms(),
                    "config": self._recommended_config(),
                }
            ),
        )
        self._log(
            f"REGISTER sensor={packet.sensor_id} name={name!r} "
            f"session={session.session_id} instance="
            f"{session.client_instance_id or 'legacy'}"
        )
        self._send(response, address)

    def _valid_session(self, packet: DartPacket, address: tuple[str, int]) -> bool:
        with self._state_lock:
            session = self._sessions_by_id.get(packet.session_id)
            if not session:
                return False
            if session.sensor_id != packet.sensor_id or session.address != address:
                return False
            session.last_seen_ms = now_ms()
            return True

    def _claim_message(self, packet: DartPacket) -> str:
        """Atomically claim a valid identity, waiting for an in-flight copy.

        Returns ``new``, ``duplicate``, or ``expired``.  A duplicate is never
        reported until the first handler has committed successful processing.
        If that handler fails, one waiting copy takes over and processes it.
        """
        key = self._message_identity(packet)
        with self._duplicate_condition:
            while True:
                self._prune_seen_locked()
                if key in self._seen:
                    return "duplicate"
                if key not in self._inflight:
                    self._inflight.add(key)
                    return "new"
                if packet.is_expired():
                    return "expired"
                wait_s = 0.1
                if packet.ttl_ms:
                    remaining_s = (
                        packet.timestamp_ms + packet.ttl_ms - now_ms()
                    ) / 1000.0
                    if remaining_s <= 0:
                        return "expired"
                    wait_s = min(wait_s, remaining_s)
                self._duplicate_condition.wait(timeout=wait_s)

    def _finish_message(self, packet: DartPacket, *, success: bool) -> None:
        key = self._message_identity(packet)
        with self._duplicate_condition:
            self._inflight.discard(key)
            if success:
                self._seen[key] = time.monotonic()
                self._seen.move_to_end(key)
            self._duplicate_condition.notify_all()

    def _prune_seen_locked(self) -> None:
        cutoff = time.monotonic() - self.duplicate_retention_s
        while self._seen:
            _, timestamp = next(iter(self._seen.items()))
            if timestamp >= cutoff:
                return
            self._seen.popitem(last=False)

    def _is_duplicate(self, packet: DartPacket) -> bool:
        """Compatibility helper used by state tests; immediately commits a claim."""
        claim = self._claim_message(packet)
        if claim == "new":
            self._finish_message(packet, success=True)
            return False
        return claim == "duplicate"

    @staticmethod
    def _message_identity(packet: DartPacket) -> tuple[int, int, int, int]:
        return (
            packet.session_id,
            packet.sensor_id,
            int(packet.msg_type),
            packet.sequence,
        )

    def _new_session_id(self) -> int:
        while True:
            session_id = secrets.randbits(32)
            if session_id and session_id not in self._sessions_by_id:
                return session_id

    def _recommended_config(self) -> dict[str, int]:
        return {
            "heartbeat_interval_ms": 2_000,
            "batch_size": 5,
            "max_datagram_size": MAX_DATAGRAM_SIZE,
            "ack_timeout_ms": 250,
            "max_attempts": 5,
        }

    def _send_expired(
        self, original: DartPacket, address: tuple[str, int]
    ) -> None:
        if original.msg_type in {
            MessageType.REGISTER_REQ,
            MessageType.CONFIG_REQ,
            MessageType.HEARTBEAT,
        }:
            self._send_error(
                original,
                address,
                StatusCode.EXPIRED,
                "message deadline has passed",
            )
        elif original.requires_ack:
            self._send_ack(original, address, StatusCode.EXPIRED)

    def _send_ack(
        self,
        original: DartPacket,
        address: tuple[str, int],
        status: StatusCode,
    ) -> None:
        ack_owner = (original.session_id, original.sensor_id)
        if (
            self.drop_first_critical_ack
            and original.msg_type == MessageType.CRITICAL_ALERT
            and ack_owner not in self._dropped_first_ack_for
        ):
            with self._state_lock:
                if ack_owner not in self._dropped_first_ack_for:
                    self._dropped_first_ack_for.add(ack_owner)
                    with self._metrics_lock:
                        self.metrics.acks_simulated_dropped += 1
                    self._warn(
                        f"SIMULATED SUPPRESSION first critical ACK before sendto "
                        f"session={original.session_id} sensor={original.sensor_id} "
                        f"seq={original.sequence}"
                    )
                    return
        ack = DartPacket(
            msg_type=MessageType.ACK,
            delivery=DeliveryClass.CONTROL,
            session_id=original.session_id,
            sensor_id=original.sensor_id,
            sequence=original.sequence,
            ttl_ms=max(1_000, original.ttl_ms),
            status_code=status,
        )
        if self._send(ack, address):
            with self._metrics_lock:
                self.metrics.acks_sent += 1

    def _send_error(
        self,
        original: DartPacket,
        address: tuple[str, int],
        status: StatusCode,
        detail: str,
    ) -> None:
        error = DartPacket(
            msg_type=MessageType.ERROR,
            delivery=DeliveryClass.CONTROL,
            session_id=original.session_id,
            sensor_id=original.sensor_id,
            sequence=original.sequence,
            ttl_ms=5_000,
            status_code=status,
            payload=encode_json({"error": status.name, "detail": detail[:200]}),
        )
        if self._send(error, address):
            with self._metrics_lock:
                self.metrics.errors_sent += 1

    def _send(self, packet: DartPacket, address: tuple[str, int]) -> bool:
        transmitter = self._transmitter
        if transmitter is None:
            return False
        outcome = transmitter.sendto(packet.encode(), address)
        action = "TX" if outcome.sent else "DROP-TX"
        self._log(f"{action} {packet_summary(packet)} to={address[0]}:{address[1]}")
        return outcome.sent

    def _log(self, message: str) -> None:
        if not self.quiet:
            LOGGER.info(message)

    def _warn(self, message: str) -> None:
        if not self.quiet:
            LOGGER.warning(message)

    def _log_impairment(self, message: str) -> None:
        self._warn(message)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DART v1 UDP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--ack-loss-rate", type=float, default=0.0)
    parser.add_argument("--ack-corrupt-rate", type=float, default=0.0)
    parser.add_argument("--delay-ms", type=float, default=0.0)
    parser.add_argument("--jitter-ms", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--drop-first-critical-ack", action="store_true")
    parser.add_argument(
        "--allow-experimental-policies",
        action="store_true",
        help="accept non-conforming raw/reliable-all benchmark envelopes",
    )
    parser.add_argument("--metrics-file")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    for option, value in (
        ("--ack-loss-rate", args.ack_loss_rate),
        ("--ack-corrupt-rate", args.ack_corrupt_rate),
    ):
        if not 0.0 <= value <= 1.0:
            parser.error(f"{option} must be between 0 and 1")
    if (
        not math.isfinite(args.delay_ms)
        or not math.isfinite(args.jitter_ms)
        or args.delay_ms < 0
        or args.jitter_ms < 0
    ):
        parser.error("--delay-ms and --jitter-ms must be finite and non-negative")
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    server = DartServer(
        host=args.host,
        port=args.port,
        workers=args.workers,
        ack_loss_rate=args.ack_loss_rate,
        ack_corrupt_rate=args.ack_corrupt_rate,
        network_delay_ms=args.delay_ms,
        jitter_ms=args.jitter_ms,
        seed=args.seed,
        drop_first_critical_ack=args.drop_first_critical_ack,
        allow_experimental_policies=args.allow_experimental_policies,
        quiet=args.quiet,
        metrics_file=args.metrics_file,
    )

    def stop_server(_signum: int, _frame: Any) -> None:
        server.stop()

    signal.signal(signal.SIGTERM, stop_server)
    signal.signal(signal.SIGINT, stop_server)
    server.serve_forever()
    if not args.quiet:
        print(json.dumps(server.snapshot_metrics(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
