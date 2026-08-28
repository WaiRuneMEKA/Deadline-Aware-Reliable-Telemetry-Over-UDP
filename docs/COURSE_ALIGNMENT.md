# DART กับเนื้อหา Network Chapter 1–3

เอกสารนี้เป็นส่วนหนึ่งของ Project 1: Socket Programming รายวิชา **01418351 — หลักการสื่อสารคอมพิวเตอร์และการประมวลผลบนคลาวด์ (Computer Communications and Cloud Computing Principles)** จัดทำโดย **เมตัส พานิช กายย์ (6710405460)**

เอกสารนี้อธิบายว่า DART ไม่ได้เป็นเพียงแอปจำลอง sensor แต่เป็นการนำแนวคิดจากเอกสารประกอบรายวิชา Chapters 1–3 มาสร้าง **application-layer protocol ที่รันจริงบน UDP socket** เอกสารประกอบรายวิชาไม่ได้รวมอยู่ใน public repository นี้ จึงอ้างอิงด้วยชื่อบทและหัวข้อแทนลิงก์ไปยังไฟล์ในเครื่องผู้จัดทำ

## ภาพรวมจากบทเรียนสู่ implementation

| บทเรียน | แนวคิด | สิ่งที่ DART ทำจริง | หลักฐานที่สังเกต/วัดได้ |
|---|---|---|---|
| Ch.1 | Layers และ encapsulation | สร้าง DART message ที่ application layer แล้วส่งเป็น UDP segment | Wireshark เห็น DART -> UDP -> IP -> link frame |
| Ch.1 | Packet loss | ตัวจำลองทำ seeded pseudo-random loss; delivery class ตอบสนองต่อ loss ต่างกัน | log drop, retry, ACK และ delivery rate |
| Ch.1 | Delay | timestamp, TTL, ACK timer และ fixed-schedule lateness | P95 alert/ACK latency, expiry และ schedule lateness |
| Ch.1 | Throughput | batch หลาย reading ต่อ header และนับ payload/wire bytes | bytes, packets, goodput และ retransmission overhead |
| Ch.2 | Client-server | simulator เริ่ม registration; server รอที่ well-known UDP port | `REGISTER_REQ` / `REGISTER_RES` flow |
| Ch.2 | Protocol definition | นิยาม message types, syntax, semantics, strict envelope และ rules ครบ | fixed 40-byte header, payload schema, state flows, malformed response |
| Ch.2 | Socket programming | ใช้ `AF_INET`, `SOCK_DGRAM`, `sendto()`, `recvfrom()` | client/server CLI และ packet capture |
| Ch.2 | Transport selection | เลือก UDP แล้วเพิ่ม reliability เฉพาะ critical message | batch/latest ไม่มี ACK แต่ alert มี ACK/retry |
| Ch.3 | UDP และ checksum | message boundary หนึ่ง datagram; CRC32 ที่ application layer | corruption ถูก decoder ปฏิเสธ |
| Ch.3 | Mux/demux | UDP port demux เข้า server socket; DART IDs demux ต่อใน app | หลาย sensor ใช้ port/socket เดียว |
| Ch.3 | Reliable transfer | sequence, checksum, ACK, timer, bounded retransmission, duplicate detection | จำลอง packet loss หรือบังคับไม่ส่ง ACK แล้วสังเกต retry ภายใน attempts/TTL |
| Ch.3 | Selective retransmission | retry เฉพาะ ACK-required message ที่ไม่ได้รับ response ก่อน timeout | batch ไม่ถูกส่งซ้ำ; critical seq เดิมถูกส่งซ้ำ |

## Chapter 1 — Introduction

แหล่งอ้างอิงในบทเรียน:

- Chapter 1: Performance — delay, loss และ throughput
- Chapter 1: Protocol layers และ encapsulation

### 1.1 Protocol layers และ encapsulation

Chapter 1 อธิบาย Internet stack 5 ชั้นและหลัก encapsulation หน่วยข้อมูลของ application คือ message จากนั้น transport เพิ่ม header เป็น segment, network ห่อเป็น datagram และ link ห่อเป็น frame

ใน DART เส้นทางข้อมูลคือ:

```text
DART header + payload          application message
        |
        v
UDP header + DART message      UDP segment/datagram
        |
        v
IP header + UDP + DART         IP datagram
        |
        v
Link header/trailer + ...      Ethernet/Wi-Fi frame
```

การแบ่งชั้นเห็นได้ใน implementation ดังนี้:

- `dart/protocol.py` รู้จักเฉพาะ syntax/validation ของ DART และไม่มี socket code
- `dart/network.py` รับ serialized bytes แล้วส่งผ่าน UDP socket พร้อม impairment สำหรับการทดลอง
- OS เป็นผู้สร้าง UDP/IP headers และจัดการ link layer
- `wireshark/dart.lua` แยก DART header ที่อยู่ใน UDP payload ออกมาให้ดูได้

จุดสำคัญสำหรับการนำเสนอ: CRC32 ของ DART อยู่ application layer ส่วน UDP checksum อยู่ transport layer การมีทั้งสองช่องไม่ได้ทำให้สอง layer เป็น layer เดียวกัน

### 1.2 Delay

บทเรียนแยก nodal delay เป็น processing, queueing, transmission และ propagation delay:

```text
d_nodal = d_proc + d_queue + d_trans + d_prop
d_trans = L / R
```

DART เชื่อมโยงกับแนวคิดนี้อย่างเป็นรูปธรรม:

- `timestamp_ms` บันทึกเวลาที่ client สร้าง message
- `ttl_ms` ทำให้ message ที่ช้าเกินคุณค่าของข้อมูลถูกละทิ้ง
- reliable sender วัดเวลาจากส่ง critical alert จนได้ ACK เป็น observed application RTT
- server เก็บ latency ของ critical alert และสรุป median/P95/max
- impairment layer เพิ่ม delay/jitter แบบควบคุมได้เพื่อดูผลต่อ timeout และ expiry
- `DATA_BATCH` ลดจำนวน header และ packet แต่ต้องแลกกับ **application batching delay** ระหว่างรอให้ครบ batch
- simulator สร้าง event schedule ก่อนเริ่มส่ง ประสาน sensor workers ด้วย start barrier และบันทึก `max_schedule_lateness_ms` เพื่อเปิดเผย backlog ที่เกิดจาก synchronous ACK wait; timestamp ของ packet เริ่มเมื่อ protocol call ของ event นั้นเริ่มจริง
- ก่อนอ่านผล `demo.py`/`benchmark.py` รอ worker เป็นศูนย์และ input quiet ผ่าน `wait_until_idle()` เพื่อไม่เก็บ snapshot ขณะ server ยัง process datagram ค้างอยู่

ผลที่วัดบนเครื่องเดียวรวม processing/scheduling/application delay เป็นหลัก ไม่สามารถอ้างว่าแทน propagation หรือ router queue ของ Internet จริงได้

### 1.3 Packet loss

Chapter 1 อธิบายว่าเมื่อ buffer เต็ม packet อาจถูก drop และการกู้คืนอาจทำที่ node ต้นทาง end system หรือไม่กู้เลย DART เลือกวิธีตามคุณค่าข้อมูล:

| DART class | เมื่อ datagram หาย | เหตุผล |
|---|---|---|
| `BEST_EFFORT_BATCH` | ไม่ส่งซ้ำใน conforming DART mode | reading ปกติยอมเสียบางชุดได้ |
| `LATEST_ONLY` | ไม่ส่งค่ารุ่นเก่าซ้ำ; รอค่าล่าสุดใน conforming DART mode | ค่าใหม่มีประโยชน์กว่าค่าเก่า |
| `CRITICAL_RELIABLE` | timeout แล้วส่ง seq เดิมซ้ำใน conforming DART mode | alert ต้องมีโอกาสไปถึงสูงกว่า telemetry ปกติ |

`ImpairedTransmitter` ใน `dart/network.py` ใช้ seed กำหนด pseudo-random stream สำหรับ loss/corruption แต่เมื่อหลาย thread ส่งพร้อมกัน packet ใดจะได้ random draw ใดอาจต่างตาม thread scheduling จึงไม่รับประกัน packet-level trace ที่เหมือนกันทุกรอบ และควรใช้หลาย repeats การ drop นี้เกิดใน application ก่อน `sendto()` เพื่อ portability จึงเป็น **แบบจำลองผลของ loss** ไม่ใช่การจำลองสาเหตุ buffer overflow ของ router

### 1.4 Throughput, goodput และ overhead

Chapter 1 นิยาม throughput เป็นอัตราที่ bit ถูกส่งถึงจริงและถูกจำกัดด้วย bottleneck link DART ไม่สามารถเปลี่ยน physical bottleneck ได้ แต่เปลี่ยนจำนวน bytes/packets ที่ application สร้าง:

- หนึ่ง DART message มี fixed overhead 40 bytes ก่อน UDP/IP/link headers
- `DATA_BATCH` จ่าย fixed header ครั้งเดียวให้หลาย reading
- ACK/retransmission เพิ่ม reliability แต่เพิ่ม bytes on wire
- Duplicate ที่ receiver ไม่ถูกนับเป็น useful application data

ดังนั้น benchmark ควรรายงานอย่างน้อย:

```text
application-wire throughput = DART bytes ที่ส่งจริง / เวลา
goodput                    = useful payload ที่รับและใช้ได้ / เวลา
server acceptance rate     = alerts ที่ server process / alerts ที่สร้าง
confirmation rate          = alerts ที่ client ได้ ACK / alerts ที่สร้าง
```

Benchmark แยก `total_attempted_bytes` (รวม datagram ที่ impairment จำลองว่าหาย) ออกจาก `total_sent_bytes` (DART bytes ที่ผ่านเข้า UDP socket จริง) ทั้งสองค่ารวม client+server และไม่รวม UDP/IP/link headers ค่า overhead ของ registration/control รวมอยู่ด้วยและบันทึกไว้ใน `method`

รายงานยังบันทึก provenance สำหรับ rerun: `method.base_seed` กับ `seed_derivation` อธิบายจุดเริ่มและสูตร ส่วนทุก case มี `case_seed`, `simulation_seed` และ `server_seed`; CSV ทำซ้ำค่าเหล่านี้ในทุกแถว Demo report เก็บ `seed`, `simulation_base_seed`, `server_seed`, `seed_scope` และ impairment/workload configuration ใน `demo_configuration` การรัน `benchmark.py --quick` โดยไม่ระบุ output เขียน `results/benchmark_quick.json`/`.csv` แยกจากผลเต็ม เพื่อไม่ให้ smoke test ทับหลักฐานที่ใช้ในสไลด์

ทุก policy replay ตาราง event ที่สร้างล่วงหน้า และตรวจทั้งจำนวน ค่า offset และ SHA-256 workload fingerprint ให้ตรงกัน Seed และ loss probability เท่ากัน แต่ไม่ใช่ paired drop trace ราย packet เพราะ ACK/retry ของแต่ละ policy ใช้จำนวน random draw ต่างกัน คำกล่าวที่ตรวจสอบได้จึงเป็น “ภายใต้ fixed workload และการทดลองตาม method นี้ DART มี trade-off เท่าใด” ไม่ใช่ “DART เร็วกว่า TCP หรือ UDP ทุกกรณี” เพราะ DART เองก็วิ่งบน UDP และ bottleneck ของแต่ละ path ต่างกัน Benchmark รันเฉพาะ `raw`, `reliable-all` และ `dart` ที่ reuse DART wire format ไม่ได้รัน TCP/MQTT/CoAP จึงไม่มีหลักฐาน performance ข้าม protocol เหล่านั้น

ผลนี้เป็น controlled loopback experiment: loss ถูกจำลองใน application ก่อน `sendto()`, latency ที่รายงานเป็น conditional on available acceptance/ACK samples, และ byte counters ไม่รวม UDP/IP/link headers จำนวน repeats ที่บันทึกเป็นหลักฐานเชิงสาธิต ไม่ใช่การออกแบบตัวอย่างเพื่อสรุปเชิงสถิติกับ production network จึงต้องแสดง workload, seed, repeats และ sample counts ทุกครั้งที่อ้างตัวเลข

สำหรับ `LATEST_ONLY` ต้องดูทั้ง `latest_delivery_rate` และ `latest_final_state_rate`: ค่าแรกนับ update ที่รับทั้งหมด ส่วนค่าหลังตรวจว่า state สุดท้ายของแต่ละ sensor ตรงกับ final latest ใน logical workload จึงไม่ใช้จำนวน packet เพียงค่าเดียวแทนความถูกต้องของ semantics

## Chapter 2 — Application Layer

แหล่งอ้างอิงในบทเรียน:

- Chapter 2: หลักการของ network application
- Chapter 2: Socket programming — UDP และ TCP

### 2.1 Complexity at the edge

Chapter 2 อธิบายว่า network application รันบน end systems โดยไม่ต้องแก้ router/switch DART ทำตามแนวคิดนี้ตรง ๆ:

- client simulator และ server เป็นโปรแกรมที่ปลายทาง
- ทั้งสองใช้บริการ UDP ของ OS
- loss simulation, classification, ACK และ retry อยู่ที่ application edge
- ไม่ต้องติดตั้งโค้ด DART ใน network core

นี่เป็นเหตุผลว่าทำไมเดโมด้วย sensor simulator จึงแสดง protocol ได้ครบแม้ไม่มี hardware sensor

### 2.2 Client-server architecture

ในระดับ process client คือฝ่ายเริ่มติดต่อ ส่วน server คือฝ่ายรอถูกติดต่อ DART กำหนดบทบาทดังนี้:

- client ส่ง `REGISTER_REQ` ก่อน
- server bind ที่ host/port ที่รู้ล่วงหน้าและตอบ `REGISTER_RES`
- server ออก `session_id`; client ใช้ session เดิมใน telemetry ถัดไป
- server เก็บ session, latest value และ duplicate cache
- server มี receiver thread รับ datagram และ `ThreadPoolExecutor` ประมวลผลหลายงานพร้อมกัน

นี่เป็น centralized client-server prototype ไม่ใช่ P2P และ server ยังเป็น single point of failure

### 2.3 Process, socket และ addressing

Chapter 2 เปรียบ socket เป็นประตูระหว่าง application กับ transport และอธิบายว่า IP address อย่างเดียวไม่พอ ต้องมี port เพื่อระบุ process

DART มี addressing สองระดับ:

1. OS/UDP ใช้ destination IP + UDP port เพื่อส่งถึง server process
2. DART ใช้ `session_id` + `sensor_id` เพื่อระบุ logical client ภายใน process นั้น

Server ใช้ `recvfrom()` เพื่อได้ทั้ง datagram และ source `(IP, port)` แล้วตรวจว่าตรงกับ session ที่ลงทะเบียนไว้ การตรวจนี้ช่วยรักษา state ของ demo แต่ไม่ใช่ authentication เพราะ source address ปลอมแปลงหรือ session ID รั่วได้

### 2.4 Application-layer protocol: types, syntax, semantics, rules

Chapter 2 ระบุว่า application protocol ต้องกำหนดอย่างน้อย:

- **message types** — request/response มีชนิดใดบ้าง
- **syntax** — field และขอบเขต message เป็นอย่างไร
- **semantics** — ค่าของ field หมายถึงอะไร
- **rules** — process ส่ง/ตอบเมื่อใดและอย่างไร

DART มีครบทั้งสี่ส่วน:

| สิ่งที่บทเรียนกำหนด | DART concrete implementation |
|---|---|
| Message types | enum 1–11 ตั้งแต่ `REGISTER_REQ` ถึง `ERROR` |
| Syntax | fixed big-endian 40-byte header + binary/JSON payload schema |
| Semantics | delivery class, flag, TTL, status และ metric ID มีความหมายตายตัว |
| Rules | strict type/delivery/flag/status envelope; ต้อง register ก่อน; alert ต้อง ACK; timeout ใช้ seq เดิม retry; duplicate ห้าม process side effect ซ้ำภายใน cache window |

รายละเอียด wire-level ทั้งหมดอยู่ใน [PROTOCOL_SPEC.md](PROTOCOL_SPEC.md)

Server CLI ใช้ strict DART semantics เป็นค่าเริ่มต้นและตอบ `400 MALFORMED` เมื่อ envelope ที่ decode ได้ผิดกฎ `--allow-experimental-policies` มีไว้ผ่อนเฉพาะ ACK semantics ของ `raw`/`reliable-all` comparator; ไม่ได้ปิด validation อื่น Client ก็ validate response direction, class, flags, status, payload และ identity ก่อนนับว่าสำเร็จ ส่วน JSON ต้องมีเลข finite และ binary telemetry ต้อง fit finite IEEE-754 float32

### 2.5 Transport-service requirements และการเลือก UDP

Chapter 2 ให้พิจารณา data integrity, timing, throughput และ security ก่อนเลือก transport DART วิเคราะห์ตามแต่ละมิติดังนี้:

| มิติ | ความต้องการ DART | การตัดสินใจ |
|---|---|---|
| Data integrity/delivery | normal telemetry ทน loss ได้; critical alert ต้องดูแลมากกว่า | UDP + CRC/ACK/retry เฉพาะ critical |
| Timing | latest value/alert มีอายุจำกัด | timestamp + TTL; ห้ามอ้างว่า UDP รับประกันเวลา |
| Throughput | sensor payload เล็กแต่ส่งถี่ | batch เพื่อลด packet/header overhead |
| Security | prototype ยังไม่มี authentication, encryption, authorization หรือ replay protection | จำกัดการใช้ไว้ที่ loopback/controlled lab; CRC32 ไม่ใช่ security |

UDP เหมาะกับเป้าหมายการเรียนเพราะเปิดให้ implement reliability policy ใน application แต่ไม่ได้แปลว่า UDP ดีกว่า TCP สำหรับทุกระบบ หาก requirement เปลี่ยนเป็นส่งไฟล์ครบทุก byte ตามลำดับ TCP จะเหมาะกว่าและง่ายกว่า

MQTT และ CoAP เป็น application-layer standards ที่กว้างกว่า DART: MQTT ใช้ broker-based publish/subscribe พร้อม QoS ส่วน CoAP ใช้ REST-style request/response พร้อม confirmable/non-confirmable messages DART ไม่ได้เสนอแทนสองมาตรฐานนี้ แต่เป็น educational custom protocol ที่รวม batch, latest-only, deadline และ critical retry เพื่อให้นิสิต implement wire format และวัด trade-off ด้วยตนเอง

CLI bind ที่ `127.0.0.1` เป็นค่าเริ่มต้นเพื่อให้เดโมอยู่ในเครื่องเดียว หากเปลี่ยนไป bind interface อื่น ผู้ใช้ต้องรับผิดชอบ network boundary เอง DART v1 ไม่ควรเปิดรับ traffic จาก shared หรือ untrusted network และ status/session checks ใน prototype ไม่ใช่ระบบยืนยันตัวตน

### 2.6 UDP socket API

รูปแบบ code ตรงกับ socket programming ในบทเรียน:

```python
# server concept
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((host, port))
raw, client_address = sock.recvfrom(max_size)
sock.sendto(response, client_address)

# client concept
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(packet, (server_host, server_port))
raw, server_address = sock.recvfrom(max_size)
```

UDP ไม่ต้องมี welcoming socket + connection socket แบบ TCP Server ใช้ socket เดียวรับทุก source และ DART code เป็นผู้จัด logical session เอง

## Chapter 3 — Transport Layer

แหล่งอ้างอิงในบทเรียน:

- Chapter 3: Transport service, multiplexing/demultiplexing และ UDP
- Chapter 3: Reliable Data Transfer — rdt1.0 ถึง rdt3.0
- Chapter 3: Go-Back-N และ Selective Repeat

### 3.1 Logical process-to-process communication

Chapter 3 อธิบายว่า transport layer ให้ logical communication ระหว่าง process โดยอาศัย best-effort network layer DART เป็น message ที่ application ส่งผ่าน logical UDP process-to-process channel นี้ หาก IP/UDP ส่งไม่ถึง DART sender จะรู้ได้เฉพาะกรณีที่รอ response/ACK แล้ว timeout

### 3.2 Multiplexing และ demultiplexing

บทเรียนแยกสองขั้น:

- sender transport **multiplex** ข้อมูลจากหลาย socket ลง segment
- receiver transport **demultiplex** UDP ตาม destination port ไปยัง socket

ใน DART มี demux ต่อกันสองชั้น:

```text
UDP destination port 9999
          |
          v
one DART server socket
          |
          +--> session_id / sensor_id A
          +--> session_id / sensor_id B
          +--> session_id / sensor_id C
```

ดังนั้น sensor หลาย client ที่มี source IP/port ต่างกันส่งมาที่ destination port เดียวกันจะเข้า server UDP socket เดียว แล้ว application header แยก logical sensor ต่อ นี่เป็นตัวอย่าง concrete ของ layering ไม่ใช่การแทนที่ UDP demux

### 3.3 UDP properties

Chapter 3 ระบุคุณสมบัติ UDP ว่า connectionless, ไม่มี handshake/state ที่ transport, header 8 bytes, best-effort และไม่มี flow/congestion control DART สอดคล้องดังนี้:

- ทุก message เป็น datagram อิสระและต้องพก identity/addressing ของตัวเอง
- application สร้าง session state เหนือ UDP ไม่ได้ทำให้ UDP กลายเป็น connection-oriented
- datagram อาจหาย ซ้ำ หรือ reorder ได้
- server ใช้ one bound socket รับหลาย client
- DART ไม่ได้เพิ่ม congestion control จึงจำกัด datagram และใช้ workload ที่ควบคุมใน demo

### 3.4 Error detection

UDP มี checksum ของ transport layer อยู่แล้ว DART เพิ่ม CRC32 ใน application header เพื่อ:

- ตรวจว่า bytes ที่ parser เห็นตรงกับ DART message ที่ sender serialize
- สาธิต corruption handling โดย impairment layer พลิก bit
- แสดง checksum field ใน custom Wireshark dissector

`DartPacket.encode()` คำนวณ CRC32 โดยทำช่อง checksum เป็นศูนย์ ส่วน `DartPacket.decode()` คำนวณใหม่และ throw `ChecksumError` เมื่อไม่ตรง Server นับ `checksum_errors` และ drop datagram

เหมือน checksum ในบทเรียน CRC32 **ตรวจได้แต่แก้ไม่ได้** และไม่ใช่ authentication

### 3.5 Reliable data transfer building blocks

Chapter 3 ไล่วิวัฒนาการ rdt และสรุปว่าช่องทางที่ทำ error/loss ต้องมี checksum, sequence, ACK, timer และ retransmission DART นำกลไกเหล่านี้มาใช้กับ critical class:

| RDT building block | DART field/behavior | ปัญหาที่แก้ |
|---|---|---|
| Checksum | `checksum` CRC32 | ตรวจ corruption ก่อน parse/process |
| Sequence number | `sequence` uint32 | จับคู่ ACK และแยก duplicate |
| ACK | `ACK`, status 202/409 | บอก sender ว่า server รับ/process แล้ว |
| Timer | configurable 250 ms initial timeout | ป้องกัน sender รอตลอดไปเมื่อ data/ACK หาย |
| Retransmission | seq เดิม + `RETRANSMISSION` | กู้จาก packet loss หรือ ACK loss |
| Duplicate detection | in-flight claim แล้ว commit เข้า seen-cache 60 วินาที | ป้องกัน side effect ซ้ำจาก retry ที่มาพร้อมกันหรือมาทีหลัง |

ลำดับเหตุผลตรงกับบทเรียน:

```text
corruption -> checksum
packet/ACK loss -> timer
timeout -> retransmit
retransmit อาจสร้าง duplicate -> sequence + duplicate cache
receiver ยืนยันผล -> ACK
```

### 3.6 ACK loss และ premature timeout

ถ้า server process alert แล้ว ACK หาย client ไม่รู้ว่า alert หรือ ACK หาย จึงส่ง alert seq เดิมซ้ำ Server ใช้ cache รู้ว่าเคย process แล้วและตอบ `409 DUPLICATE` Client ถือ ACK นี้เป็น terminal success ได้ ถ้า copy เดิมกับ retry ถูก worker คนละตัวประมวลผลพร้อมกัน copy หลังจะรอ in-flight owner และจะถูกเรียก duplicate ก็ต่อเมื่อ owner commit สำเร็จ; หาก owner ล้มเหลว copy ที่รอสามารถรับช่วงได้ นี่แสดงแนวคิดเดียวกับ rdt3.0: sender ไม่ต้องรู้สาเหตุที่ timeout เพราะ action คือ retransmit และ sequence number ช่วยรับมือ duplicate

ถ้า timeout สั้นกว่า RTT ก็อาจเกิด premature retransmission แม้ packet ไม่หาย DART เก็บจำนวน duplicate/retry ทำให้เห็นต้นทุนนี้ แต่ timer ปัจจุบันยังไม่ estimate RTT แบบ TCP

### 3.7 Selective retransmission — เหมือนและต่างจาก SR

DART ไม่ทำ Go-Back-N: critical seq 20 timeout จะส่งซ้ำเฉพาะ seq 20 ไม่ส่ง batch/latest รอบข้างใหม่ทั้งหมด ทำให้ลดการส่งซ้ำที่ไม่มีประโยชน์

อย่างไรก็ตามต้องอธิบายอย่างแม่นยำว่า DART v1 **ไม่ใช่ Selective Repeat protocol แบบเต็ม** เพราะ:

- ไม่มี sliding sender/receiver window
- ไม่มี buffer out-of-order เพื่อส่งขึ้น application ตามลำดับ
- ไม่ได้มี timer แยกให้ packet ทุกตัวใน pipeline
- reliability ใช้เฉพาะ message class ที่เลือก

คำที่เหมาะกับ DART คือ **selective retry policy inspired by reliable-transfer principles** ไม่ใช่ “เรา implement SR ครบแล้ว”

Simulator ยังมี `raw` และ `reliable-all` เป็น experimental baselines ซึ่งจงใจปิด ACK ของ critical หรือเปิด ACK ของ normal/latest ตามลำดับ ทั้งสอง reuse wire format เพื่อการเปรียบเทียบแต่ไม่ใช่ DART-conforming delivery policy; `--policy dart` เท่านั้นที่แสดง protocol semantics ตาม specification

### 3.8 Concurrency กับ transport demux

Server มี receiver thread เรียก `recvfrom()` จาก socket เดียว แล้วส่งงาน decode/dispatch ไป worker pool ค่าเริ่มต้น 8 workers สิ่งนี้ทำให้หลาย datagram ถูกประมวลผลพร้อมกัน แต่ต้องแยกให้ออกว่า:

- UDP demultiplexing ไป socket ทำโดย OS จาก destination port
- DART demultiplexing session/sensor ทำใน application
- Thread pool เป็น implementation concurrency ไม่ใช่คุณสมบัติ reliability ของ UDP
- shared session/latest/metrics state ต้องใช้ lock เพื่อป้องกัน race
- benchmark ต้องรอ active worker เป็นศูนย์และ input quiet ก่อน snapshot เพื่อไม่ให้ concurrency ทำให้ตัวเลขหายจากปลาย run

Feature นี้เหมาะกับการนำเสนอ code เพราะชี้ให้เห็นว่าหนึ่ง UDP socket รองรับหลาย client และ application จัด concurrency เอง

### 3.9 Congestion control ที่ยังไม่มี

Chapter 3 อธิบาย congestion ว่า sender รวมกันส่งเร็วเกิน capacity ทำให้ queue, delay, loss และ retransmission เพิ่ม DART v1 มี exponential retry backoff แต่ **ยังไม่ใช่ congestion control**; `429 RATE_LIMITED` เป็นเพียง status ที่สงวนไว้และ implementation ปัจจุบันยังไม่มี rate limiter หรือ runtime path ที่ส่ง code นี้:

- ไม่วัด bottleneck bandwidth หรือ queue delay
- ไม่มี congestion window
- ไม่รับประกัน fairness กับ flow อื่น
- ไม่มี ECN/AIMD/slow start

ดังนั้น demo ควรใช้ loopback/LAN และอัตราที่ควบคุม ไม่ควรใช้ DART v1 ยิง traffic ปริมาณสูงบนเครือข่ายสาธารณะ

## สิ่งที่ควรชี้ใน Wireshark

1. `dart.magic = DART` และ `version = 1`
2. message type เปลี่ยนจาก `REGISTER_REQ` เป็น `REGISTER_RES`
3. `DATA_BATCH` มีหนึ่ง header แต่หลาย reading ใน payload
4. `CRITICAL_ALERT` มี `ACK_REQUIRED`
5. retry ใช้ sequence เดิมและเพิ่ม `RETRANSMISSION`
6. `ACK` echo sequence เดิมพร้อม status `202` หรือ `409`
7. DART อยู่ภายใน UDP และ UDP อยู่ภายใน IP ยืนยันเรื่อง encapsulation

หมายเหตุ: datagram ที่ impairment layer “drop ก่อน `sendto()`” จะไม่ปรากฏใน capture เพราะมันไม่เคยออกจาก process ให้ใช้ application log คู่กับ Wireshark ส่วน retry/ACK ที่ส่งจริงจะปรากฏ

## ข้อสรุปที่ใช้ในการนำเสนอ

ประโยคที่แม่นยำ:

> DART เป็น educational application-layer protocol บน UDP ที่เลือก delivery policy ตามคุณค่าของ telemetry และนำ checksum, sequence, ACK, timer และ duplicate suppression มาเพิ่มความน่าเชื่อถือเฉพาะ critical message

ประโยคที่ไม่ควรกล่าว:

- “DART รับประกันว่า alert ถึง 100%” — retry ยังหมด TTL/attempts และ server อาจล่ม
- “UDP เร็วกว่า TCP เสมอ” — ขึ้นกับ workload/path และ UDP ไม่รับประกันเวลา
- “CRC32 ทำให้ข้อมูลปลอดภัย” — CRC32 ไม่ authenticate หรือ encrypt
- “DART เป็น protocol แนวคิดใหม่ของโลก” — เป็นงานเชิงการศึกษาที่ประกอบแนวคิดที่มีอยู่
- “DART implement Selective Repeat ครบ” — ทำเพียง selective retry ต่อ delivery class
