# Generated results

- `latest_demo.json` is created by `python3 -B demo.py`.
- `benchmark.json` and `benchmark.csv` are created by `python3 -B benchmark.py`.
- `benchmark_quick.json` and `benchmark_quick.csv` are created by
  `python3 -B benchmark.py --quick` when no explicit `--output` is supplied.
  They are smoke-test artifacts and do not overwrite the full benchmark.

Every report records its workload and impairment settings. Keep those settings
next to any table or graph made from the results so comparisons remain fair.
The demo stores `seed`, `simulation_base_seed`, `server_seed`, `seed_scope`,
loss, ACK-loss, corruption, delay, jitter, sensor, duration, and workload
settings under `demo_configuration`.

## How to read the benchmark

- The standalone server is strict by default. `benchmark.py` explicitly enables
  experimental envelope acceptance so `raw` and `reliable-all` can alter ACK
  semantics; those rows are comparator policies, not DART-conforming modes.
- Each policy replays a precomputed event list. Its SHA-256 fingerprint covers
  sensor IDs, event counts, values, and offsets, and registered sensor workers
  use a common start barrier. A case aborts if a sensor worker fails,
  registration is incomplete, the alert count is wrong, policy fingerprints
  differ for the same loss/repeat, or the server cannot drain its workers and
  remain input-idle before the snapshot.
- The JSON `method` block records `base_seed` and `seed_derivation`; every case
  records `base_seed`, `case_seed`, `simulation_seed`, and `server_seed`. The
  CSV repeats those fields on every row so a selected case can be rerun without
  guessing its random streams.
- `critical_server_acceptance_rate` means the server processed an alert for the
  first time. `critical_confirmation_rate` means the client received an
  acceptable ACK/response; it is `null` for `raw`, which sends no critical ACK.
- `latest_delivery_rate` counts accepted latest datagrams, while
  `latest_final_state_rate` checks whether each sensor's final server value
  matches the final value in the logical workload.
- `attempted` bytes include packets discarded by the simulated impairment
  before `sendto()`. `sent` bytes are DART bytes passed to the OS UDP socket.
  Totals include client and server traffic, registration/control/ACK overhead,
  and exclude UDP/IP/link headers.
- Summary rates use pooled numerators/denominators, bytes-per-value use aggregate
  totals, and P95 values pool raw latency samples across repeats. No sample is
  represented by `null`, not zero.
- `max_schedule_lateness_ms` exposes wall-clock backlog relative to the fixed
  event schedule. Packet timestamps begin when each protocol call actually
  starts, so this value must be shown when synchronous ACK waits delay replay.

The same loss probability and seed do **not** create a packet-paired comparison:
ACKs and retries cause policies to consume different pseudo-random draws. Use
multiple repeats and limit claims to the recorded loopback method; do not claim
universal superiority over UDP, TCP, CoAP, or MQTT. This harness runs only
`raw`, `reliable-all`, and `dart` policies that reuse the DART wire format; it
does not execute TCP, CoAP, or MQTT implementations.
