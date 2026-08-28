"""Network impairment helpers used by the DART demo and benchmarks."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
import socket
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class TransmissionOutcome:
    sent: bool
    dropped: bool
    corrupted: bool
    wire_bytes: int


@dataclass
class ImpairmentStats:
    attempted_packets: int = 0
    sent_packets: int = 0
    dropped_packets: int = 0
    corrupted_packets: int = 0
    attempted_bytes: int = 0
    sent_bytes: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "attempted_packets": self.attempted_packets,
            "sent_packets": self.sent_packets,
            "dropped_packets": self.dropped_packets,
            "corrupted_packets": self.corrupted_packets,
            "attempted_bytes": self.attempted_bytes,
            "sent_bytes": self.sent_bytes,
        }


def validate_impairment(
    *,
    loss_rate: float,
    corrupt_rate: float,
    delay_ms: float,
    jitter_ms: float,
) -> None:
    """Validate impairment settings before callers allocate a socket."""
    for label, value in (("loss_rate", loss_rate), ("corrupt_rate", corrupt_rate)):
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{label} must be between 0 and 1")
    if (
        not math.isfinite(delay_ms)
        or not math.isfinite(jitter_ms)
        or delay_ms < 0
        or jitter_ms < 0
    ):
        raise ValueError("delay and jitter must be finite and non-negative")


class ImpairedTransmitter:
    """Wrap ``socket.sendto`` with seeded pseudo-random loss/corruption/delay.

    The seed makes the random stream repeatable, but concurrent callers may
    consume draws in a different order because thread scheduling is external
    to this wrapper.  Benchmarks therefore use repeats and report aggregate
    results instead of claiming an identical packet-level loss trace.

    These impairments are intentionally applied in the application so the demo
    remains portable on macOS, Linux, and Windows without privileged traffic
    control tools.
    """

    def __init__(
        self,
        sock: socket.socket,
        *,
        loss_rate: float = 0.0,
        corrupt_rate: float = 0.0,
        delay_ms: float = 0.0,
        jitter_ms: float = 0.0,
        seed: int | None = None,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        validate_impairment(
            loss_rate=loss_rate,
            corrupt_rate=corrupt_rate,
            delay_ms=delay_ms,
            jitter_ms=jitter_ms,
        )
        self.socket = sock
        self.loss_rate = loss_rate
        self.corrupt_rate = corrupt_rate
        self.delay_ms = delay_ms
        self.jitter_ms = jitter_ms
        self._random = random.Random(seed)
        self._lock = threading.Lock()
        self._on_event = on_event
        self.stats = ImpairmentStats()

    def sendto(self, data: bytes, address: tuple[str, int]) -> TransmissionOutcome:
        raw = bytes(data)
        event: str | None = None
        with self._lock:
            self.stats.attempted_packets += 1
            self.stats.attempted_bytes += len(raw)
            if self._random.random() < self.loss_rate:
                self.stats.dropped_packets += 1
                event = f"SIMULATED DROP bytes={len(raw)} target={address}"
                dropped = True
                corrupted = False
                delay = 0.0
            else:
                dropped = False
                corrupted = False
                if raw and self._random.random() < self.corrupt_rate:
                    mutable = bytearray(raw)
                    index = self._random.randrange(len(mutable))
                    mutable[index] ^= 1 << self._random.randrange(8)
                    raw = bytes(mutable)
                    corrupted = True
                    self.stats.corrupted_packets += 1
                    event = (
                        f"SIMULATED CORRUPTION offset={index} target={address}"
                    )

                delay = self.delay_ms
                if self.jitter_ms:
                    delay += self._random.uniform(-self.jitter_ms, self.jitter_ms)

        if event and self._on_event:
            self._on_event(event)
        if dropped:
            return TransmissionOutcome(False, True, False, len(raw))

        # Delay and socket I/O happen outside the RNG/stats lock.  A shared server
        # transmitter can therefore model concurrent propagation instead of
        # turning N independent 100 ms replies into an artificial N*100 ms queue.
        if delay > 0:
            time.sleep(delay / 1000.0)
        self.socket.sendto(raw, address)
        with self._lock:
            self.stats.sent_packets += 1
            self.stats.sent_bytes += len(raw)
        return TransmissionOutcome(True, False, corrupted, len(raw))
