# DART v1 — Deadline-Aware Reliable Telemetry

DART is an educational application-layer protocol over UDP. It demonstrates
one idea: **not every sensor message needs the same delivery policy**.

## Course and author

- **Course:** 01418351 — หลักการสื่อสารคอมพิวเตอร์และการประมวลผลบนคลาวด์
  (Computer Communications and Cloud Computing Principles)
- **Author:** เมตัส พานิช กายย์
- **Student ID:** 6710405460

## Delivery policies

- Normal readings are packed into compact `DATA_BATCH` datagrams.
- Fast-changing state uses `LATEST_UPDATE`; stale values may be discarded.
- Emergency events use `CRITICAL_ALERT` with ACK, timeout, duplicate detection,
  and bounded retransmission until an acceptable response, the attempt limit,
  or the message deadline is reached.

The project uses software sensor simulators. No sensor hardware is required.
Its purpose is to demonstrate the protocol, not to build a production IoT app.

## What is complete

- DART v1 binary wire header with version, message type, delivery class, flags,
  session/sensor IDs, sequence, timestamp, TTL, status code, payload length,
  and CRC32.
- Concurrent UDP server for many sensor clients through one bound socket.
- ACK-based registration and critical delivery with bounded
  exponential-backoff retries.
- Best-effort batching, latest-only replacement, expiry, malformed-packet
  handling, strict client/server envelopes, finite JSON/float32 validation, and
  in-flight duplicate commit before idempotent replay.
- Per-process 128-bit client instance identities make registration retries
  idempotent without confusing a restarted client with an old request.
- Portable loss, corruption, delay, and jitter simulation.
- One-command demo and a three-policy benchmark (`raw`, `reliable-all`, `dart`).
- Wireshark Lua dissector with CRC and payload decoding.
- 86 standard-library unit and localhost integration tests.
- Protocol specification, course alignment, assignment checklist, and
  presentation/demo guide.
- Project hand-in artifacts: the protocol report at `docs.pdf`, plus a
  PowerPoint deck with speaker notes and a timed Thai video script under
  `submission/`.

## Submission package

- [`docs.pdf`](docs.pdf)
  is the 16-page protocol-design report grounded in Chapters 1-3.
- [`submission/DART_Presentation.pptx`](submission/DART_Presentation.pptx)
  is a 15-slide presentation with presenter notes and per-slide source blocks.
- [`submission/VIDEO_SCRIPT.md`](submission/VIDEO_SCRIPT.md) is the timed
  recording script and live-demo checklist.

Before submitting, verify the author/course information in every artifact,
record the required video with the presenter visible for part of the narration,
and verify all uploaded links. The GitHub repository itself is the canonical
source tree; GitHub can also generate a source archive for a tagged revision.

## Requirements

- Python 3.10 or newer
- Wireshark is optional
- No third-party Python packages

## Fastest way to try it

From this directory:

```bash
python3 -B demo.py
```

The demo starts a server on a temporary localhost UDP port, registers five
virtual sensors, injects 10% outbound loss, and deterministically suppresses
the first critical ACK before `sendto()` so the bounded retry path can be
observed without processing the same alert twice. This forced ACK suppression
is a demo control, not a claim that a transmitted ACK was lost in the network.
A complete report is written to `results/latest_demo.json`.

Useful variations:

```bash
# Short and quiet smoke test
python3 -B demo.py --sensors 3 --duration 3 --quiet

# More loss and network delay
python3 -B demo.py --loss-rate 0.20 --ack-loss-rate 0.20 \
  --delay-ms 30 --jitter-ms 10

# Compare behavior when critical alerts are not reliable
python3 -B demo.py --policy raw --no-drop-first-ack
```

## Run server and sensors separately

Terminal 1:

```bash
python3 -B -m dart.server --host 127.0.0.1 --port 9999 \
  --drop-first-critical-ack
```

Terminal 2:

```bash
python3 -B -m dart.simulator --server 127.0.0.1:9999 \
  --sensors 10 --duration 10 --policy dart --loss-rate 0.10
```

This form is best for recording a presentation because server and sensor logs
are visible independently while Wireshark captures UDP port 9999.

The standalone server is strict by default. A manual `raw` or `reliable-all`
simulator run requires `--allow-experimental-policies` on the server because
those comparator modes intentionally alter DART's `ACK_REQUIRED` rules.
`demo.py` enables the override when a non-DART policy is selected, while
`benchmark.py` enables it for its three-policy harness. A normal
`--policy dart` client/server pair needs no override.

## Compare the three policies

Quick validation:

```bash
python3 -B benchmark.py --quick
```

Presentation-quality run with repeated measurements:

```bash
python3 -B benchmark.py --sensors 5 --duration 4 --repeats 5 \
  --loss-rates 0 0.05 0.10 0.20
```

The benchmark writes JSON and CSV under `results/`. Every policy replays the
same precomputed event list (counts, values, offsets, and sensor IDs are
verified by a SHA-256 fingerprint), then waits for the server worker pool and
input to become idle before taking its snapshot. It separates first server
acceptance from client ACK confirmation, reports receiver/ACK latency, final
latest-state correctness, schedule lateness, attempted bytes including
simulated drops, passed-to-OS application bytes, retransmissions, and
duplicates. The same seed does not pair individual drops across policies
because ACKs/retries consume different PRNG draws. Results are scoped to the
declared loopback workload with loss injected at the application boundary
before `sendto()`. They exclude UDP/IP/link headers, are not packet-paired
across policies, and do not claim that DART is universally faster than TCP,
CoAP, or MQTT. Treat `--quick` as a pipeline smoke test; use multiple repeats
and report the workload, seed, sample count, and conditional latency sample
counts with any performance result. The committed measurements are controlled
lab evidence, not a production-network or statistically universal benchmark.

## Run tests

```bash
python3 -B -m unittest discover -s tests -v
```

## Inspect with Wireshark

Follow [`wireshark/README.md`](wireshark/README.md), install `dart.lua`, then
capture the loopback interface with this display filter:

```text
dart
```

Useful filters include:

```text
dart.msg_type == 5
dart.flags.ack_required == 1
dart.flags.retransmission == 1
dart.sensor_id == 1
```

## Course grounding (Chapters 1–3)

- **Chapter 1:** packet loss, delay, throughput, header overhead, layering, and
  measurement under a controlled workload.
- **Chapter 2:** client-server architecture, socket programming, UDP service
  choice, and protocol message types/syntax/semantics/rules.
- **Chapter 3:** UDP demultiplexing, checksum, sequence numbers, ACK, timers,
  duplicate detection, retransmission, and application-level reliability.

See [`docs/COURSE_ALIGNMENT.md`](docs/COURSE_ALIGNMENT.md) for the detailed map.

For the exact Project 1 hand-in requirements and the file that proves each
item, see [`docs/ASSIGNMENT_CHECKLIST.md`](docs/ASSIGNMENT_CHECKLIST.md).

## Project layout

```text
repository-root/
├── dart/
│   ├── protocol.py       # Wire format and payload codecs
│   ├── network.py        # Portable impairment simulator
│   ├── server.py         # Concurrent UDP receiver
│   ├── client.py         # Sensor session + reliability logic
│   └── simulator.py      # Multiple virtual sensors
├── demo.py               # One-command end-to-end demonstration
├── benchmark.py          # RAW vs RELIABLE-ALL vs DART
├── tests/                # Unit and integration tests
├── wireshark/dart.lua    # DART protocol dissector
├── docs/                 # Specification, requirement map, and demo material
├── results/              # Generated JSON/CSV evidence
├── docs.pdf              # 16-page protocol-design report
└── submission/           # PowerPoint deck and recording script
```

## Important limitations

DART v1 is deliberately scoped for a networking course:

- No authentication, encryption, authorization, or replay protection. The
  default CLI binds to loopback; do not expose it to a shared or untrusted
  network.
- No congestion-control algorithm; use only in a controlled lab/demo network.
- Sessions live in server memory and are not persisted.
- Sensor clocks are assumed to be reasonably synchronized for TTL/latency.
- The benchmark's loss is injected at the application boundary for portability.
- It is an educational design, not a standards-compatible replacement for
  CoAP or MQTT.

Read [`docs/PROTOCOL_SPEC.md`](docs/PROTOCOL_SPEC.md) before changing the wire
format.

## Copyright and license

Copyright © 2026 เมตัส พานิช กายย์. All rights reserved.

This repository is public for course review and demonstration, but it does not
include an open-source license. Public availability does not place the work in
the public domain or grant permission to copy, modify, or redistribute it.
