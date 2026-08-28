# DART Presentation & Demo Guide (10–15 นาที)

เอกสารประกอบ Project 1: Socket Programming รายวิชา **01418351 — หลักการสื่อสารคอมพิวเตอร์และการประมวลผลบนคลาวด์ (Computer Communications and Cloud Computing Principles)** โดย **เมตัส พานิช กายย์ (6710405460)**

เป้าหมายของการนำเสนอคือทำให้ **protocol เป็นพระเอก** แอป sensor simulator เป็นเพียงเครื่องมือสร้าง traffic เพื่อพิสูจน์ว่า message format และ delivery rules ทำงานจริง

## ประโยคเปิดและแก่นของงาน

เปิดด้วยคำถาม:

> “ข้อมูลทุกชิ้นสำคัญเท่ากันหรือไม่ ถ้าค่าอุณหภูมิธรรมดาหายหนึ่งค่ากับสัญญาณไฟไหม้หายหนึ่งครั้ง ผลกระทบเหมือนกันไหม?”

ตามด้วยคำอธิบายหนึ่งประโยค:

> “DART เป็น application-layer protocol บน UDP ที่รวมข้อมูลปกติเป็น batch เก็บเฉพาะค่าล่าสุดสำหรับข้อมูลที่เปลี่ยนเร็ว และใช้ ACK กับ bounded retransmission เฉพาะเหตุการณ์วิกฤต”

สิ่งที่ควรให้ผู้ชมจำได้เมื่อจบ:

1. DART แบ่ง delivery policy ตามคุณค่าของข้อมูล
2. เป็น protocol จริง มี wire format, message types, semantics และ rules
3. ACK/timeout/bounded retry/duplicate suppression เพิ่มโอกาสส่ง critical alert สำเร็จเมื่อเกิด packet/ACK loss โดยยังถูกจำกัดด้วย attempts และ TTL
4. ผลลัพธ์วัดได้และตรวจ packet จริงด้วย Wireshark

## เตรียมก่อนอัดวิดีโอ

### 1. Pre-flight checklist

- เช็กสถานะงานส่งจาก [ASSIGNMENT_CHECKLIST.md](ASSIGNMENT_CHECKLIST.md) โดยเฉพาะ PDF, source code และวิดีโอไม่เกิน 15 นาที
- รันคำสั่งทั้งหมดจาก repository root หลัง clone
- ใช้ Python 3 ตามที่ระบุใน README หลัก
- รัน test suite ให้ผ่านก่อนอัดด้วย `python3 -B -m unittest discover -s tests -v`
- ปิดโปรแกรมที่ใช้ UDP port `9999`
- เตรียม terminal อย่างน้อย 2 หน้าต่าง: server และ simulator
- เปิด Wireshark และเลือก `lo0` บน macOS หรือ loopback interface ของระบบ
- ติดตั้ง `wireshark/dart.lua` ตาม [Wireshark guide](../wireshark/README.md)
- ใช้ capture filter `udp port 9999` และ display filter `dart`
- ขยาย font ของ terminal และ Wireshark ให้อ่านได้ในวิดีโอ
- ซ่อน notification และข้อมูลส่วนตัวก่อนเริ่ม record
- เตรียมผล benchmark/กราฟไว้ล่วงหน้า ไม่รันการทดลองยาวระหว่างอัด

Smoke test แบบคำสั่งเดียวก่อนจัดหน้าต่าง live demo:

```bash
python3 -B demo.py --sensors 5 --duration 6 --loss-rate 0.10 --seed 42
```

คำสั่งนี้เปิด server บน ephemeral port และเขียนรายงานที่ `results/latest_demo.json` จึงเหมาะกับตรวจระบบรวดเร็ว สำหรับ Wireshark live demo ให้ใช้ server port `9999` ตามคำสั่งในหัวข้อถัดไปเพื่อให้ dissector จับอัตโนมัติ

### 2. คำสั่งเดโมหลัก

Terminal A — controlled forced-ACK-suppression setup เพื่อให้เห็น retry แน่นอน (ไม่ใช่การจำลองว่า network ทิ้ง ACK ที่ส่งออกไปแล้ว):

```bash
python3 -B -m dart.server \
  --host 127.0.0.1 \
  --port 9999 \
  --workers 8 \
  --drop-first-critical-ack
```

Terminal B — sensor simulator 5 ตัว:

```bash
python3 -B -m dart.simulator \
  --server 127.0.0.1:9999 \
  --sensors 5 \
  --duration 8 \
  --policy dart \
  --alert-at 2 \
  --alert-sensor 1 \
  --seed 10
```

ลำดับที่ควรเห็นใน log:

```text
REGISTER_REQ -> REGISTER_RES
DATA_BATCH / LATEST_UPDATE
CRITICAL_ALERT seq=N ACK_REQUIRED
server processes alert and suppresses first ACK before sendto()
client timeout
CRITICAL_ALERT seq=N ACK_REQUIRED|RETRANSMISSION
server detects duplicate and sends ACK seq=N status=409
client reports success without server processing alert twice
```

แม้ option จะชื่อ `--drop-first-critical-ack` แต่กลไกนี้คือการบังคับ **ไม่ส่ง ACK แรกจาก application ก่อน `sendto()`** เพื่อสร้างเส้นทาง retry แบบ deterministic ไม่ใช่หลักฐานว่าเครือข่ายจริงทิ้ง ACK ที่ถูกส่งแล้ว ส่วนการทดลอง performance ใช้ `--loss-rate` กับ `--seed` เพื่อจำลองผลของ loss ที่ application boundary และเปรียบเทียบอย่างควบคุม

Server คำสั่งนี้ทำงานใน strict DART mode ตามค่าเริ่มต้น ถ้าจะรัน simulator แยกด้วย `--policy raw` หรือ `--policy reliable-all` ต้องเพิ่ม `--allow-experimental-policies` ให้ server อย่างชัดเจน เพราะสอง policy นั้นจงใจเปลี่ยน ACK envelope เพื่อเป็น comparator เท่านั้น (`demo.py` และ `benchmark.py` จัดการ override นี้ให้อัตโนมัติ)

### 3. Wireshark filters พร้อมใช้

```text
# ทั้ง protocol
dart

# Critical alert และ ACK เท่านั้น
dart.msg_type == 5 || dart.msg_type == 6

# Retransmission
dart.flags.retransmission == 1

# ACK-required messages
dart.flags.ack_required == 1

# Status ที่บอกว่า receiver เคยเห็น message แล้ว
dart.status_code == 409
```

ก่อนเริ่มนำเสนอ แนะนำเพิ่ม `Sensor ID`, `Sequence`, `Message Type`, `Flags` และ `Status Code` เป็นคอลัมน์ใน Wireshark จะเห็นว่า alert ครั้งแรกกับ retry ใช้ sequence เดียวกันทันที

หมายเหตุ: ACK ที่ server บังคับไม่ส่งก่อน `sendto()` จะไม่ปรากฏใน Wireshark เพราะไม่เคยออกจาก process ให้ใช้ server log ยืนยัน forced suppression แล้วใช้ Wireshark แสดง alert เดิมและ retry ที่ส่งจริง

### 4. เตรียม benchmark ก่อนอัด

ตรวจ pipeline แบบสั้น:

```bash
python3 -B benchmark.py --quick
```

`--quick` ใช้ตรวจว่า pipeline รันครบเท่านั้น ไม่ใช่ผล performance สำหรับสรุปหรือใส่สไลด์

สร้างผลที่น่าเชื่อถือกว่าสำหรับสไลด์ด้วยหลาย repeat:

```bash
python3 -B benchmark.py \
  --sensors 5 \
  --duration 6 \
  --repeats 5 \
  --alerts-per-sensor 3 \
  --loss-rates 0 0.05 0.10 0.20 \
  --seed 100 \
  --output results/benchmark.json
```

สคริปต์รัน `raw`, `reliable-all` และ `dart` ด้วย fixed event schedule เดียวกันจาก sensor ทุกตัว โดยปล่อย sensor workers จาก start barrier เดียวกันและให้ fingerprint ครอบคลุม sensor ID, counts, values และ offsets; event ที่ ACK wait ทำให้ช้าจะยังถูก replay ตามลำดับและรายงาน `max_schedule_lateness_ms` ก่อนเก็บ snapshot โปรแกรมรอให้ worker server เป็นศูนย์และ input quiet ผ่าน `wait_until_idle()` แล้ว abort หาก sensor worker ล้มเหลว, drain ไม่ทัน, ลงทะเบียนไม่ครบ, จำนวน alert ผิด หรือ workload fingerprint ต่างกัน

ค่า `summary` ใช้ผลรวมตัวเศษ/ตัวส่วนสำหรับ rate และ pool latency sample ข้าม repeats สำหรับ P95 ไม่ใช่ค่าเฉลี่ยเปอร์เซ็นต์/P95 ราย run ให้เก็บ block `method` ไว้บนสไลด์ด้วย `total_attempted_bytes` รวม DART bytes ทั้งสองทิศทางที่พยายามส่งรวม simulated drop ส่วน `total_sent_bytes` นับเฉพาะที่ผ่านเข้า UDP socket ทั้งคู่ไม่รวม UDP/IP/link headers Seed และ loss probability เดียวกันไม่ได้ทำให้ drop เป็นคู่ตรง logical event เพราะ ACK/retry ของแต่ละ policy ใช้ PRNG draw ไม่เท่ากัน จึงต้องใช้หลาย repeats และไม่เรียกผลนี้ว่า paired packet trace

ผล benchmark เป็น controlled loopback evidence ไม่ใช่ production-network benchmark: simulated loss เกิดก่อน `sendto()`, latency เป็น conditional on samples ที่รับ/ยืนยันได้ และจำนวน repeats ไม่ได้ตั้งขึ้นเพื่ออนุมานเชิงสถิติ ทุกสไลด์ที่อ้างตัวเลขจึงต้องแสดง workload, loss, seed, repeats/sample counts และขอบเขต byte counters พร้อมกัน

## Flow นำเสนอ 12 นาที

เวลาสามารถขยายเป็น 15 นาทีด้วยผลทดลองเพิ่ม แต่ไม่ควรเกินเวลาที่กำหนด

### 0:00–1:00 — ปัญหาและ hook

แสดง slide โรงงาน/ห้องเซนเซอร์แบบง่าย แล้วพูด:

> “สมมติ sensor 20 ตัวส่งอุณหภูมิ ตำแหน่ง และสัญญาณไฟไหม้พร้อมกัน หากเราบังคับให้ทุกค่าต้องยืนยันหมดจะเสีย traffic แต่หากไม่ยืนยันอะไรเลย alert อาจหาย DART จึงให้แต่ละ message เลือก delivery policy ตามความสำคัญและอายุของข้อมูล”

ยังไม่ต้องพูด code หรือ GUI จุดนี้ต้องทำให้ผู้ชมเข้าใจปัญหาก่อน

### 1:00–2:00 — Architecture และตำแหน่งของ protocol

แสดงภาพ:

```text
Virtual sensors -> DART -> UDP -> IP -> Server
```

ชี้สามเรื่อง:

- เป็น client-server; simulator เป็น client และ server รอ UDP port 9999
- DART อยู่ application layer และรันบน end systems ไม่ได้แก้ router
- ใช้ software simulator แทน hardware ได้ เพราะสิ่งที่กำลังทดสอบคือ bytes และ rules บน network

เชื่อม Chapter 1 เรื่อง layers และ Chapter 2 เรื่อง client-server/socket แบบสั้น ๆ

### 2:00–3:30 — Protocol design

แสดง header 40 ไบต์จาก [PROTOCOL_SPEC.md](PROTOCOL_SPEC.md) โดยเน้นเฉพาะ field ที่เล่าเรื่องได้:

- `message_type`: ตอนนี้คือ batch, latest หรือ critical
- `delivery_class`: ต้องดูแลแบบใด
- `flags`: ACK required / retransmission / simulated
- `sensor_id` + `sequence`: ระบุผู้ส่งและจับ duplicate
- `timestamp_ms` + `ttl_ms`: ข้อมูลมีอายุแค่ไหน
- `payload_length` + `checksum`: framing และ error detection
- `status_code`: ผลลัพธ์ที่ receiver ตอบ

พูดให้ชัดว่า syntax คือโครงสร้าง field, semantics คือความหมาย และ rules คือเงื่อนไขส่ง/ตอบ นี่ตรงกับ Chapter 2 โดยตรง

### 3:30–4:45 — สาม delivery classes

ใช้ตารางเดียว:

| ข้อมูล | DART policy | เมื่อหาย |
|---|---|---|
| อุณหภูมิปกติ | `BEST_EFFORT_BATCH` | ไม่ retry |
| ตำแหน่ง | `LATEST_ONLY` | รอค่าล่าสุด ไม่กู้ค่าเก่า |
| ไฟไหม้ | `CRITICAL_RELIABLE` | ACK + timer + retry |

ประโยคสำคัญ:

> “DART ไม่พยายามทำให้ UDP reliable ทั้งหมด แต่เพิ่ม reliability เฉพาะ message ที่ยังมีคุณค่าพอให้ส่งซ้ำ”

### 4:45–6:15 — Reliable transfer flow

แสดง sequence diagram กรณี ACK หาย:

```text
Client                 Server
  | ALERT seq=50 -------->| process once
  |<----- ACK 202 ---X    | ACK lost
  | timeout               |
  | ALERT seq=50 RETRY -->| duplicate, do not process twice
  |<----- ACK 409 --------|
```

โยงกับ Chapter 3:

- CRC32 ตรวจ corruption
- sequence จับคู่ ACK/duplicate
- timer ป้องกันรอตลอดไป
- retransmission กู้จาก loss
- duplicate cache ป้องกัน side effect ซ้ำ

บอกอย่างแม่นยำว่าเป็น selective retry policy ไม่ใช่ Selective Repeat sliding window แบบเต็ม

ใช้เวลาประมาณ 30–45 วินาทีชี้ code เพียงสามจุด ไม่เลื่อนทั้งไฟล์:

- `dart/protocol.py`: `HEADER_STRUCT`, `DartPacket.encode()` และ `decode()` แสดง syntax/checksum
- `dart/client.py`: `_send_reliable()` แสดง timer, exponential backoff และ sequence เดิมตอน retry
- `dart/server.py`: `_process_datagram()` แสดง strict envelope/expiry/session/payload validation และ `_claim_message()` / `_finish_message()` แสดง in-flight duplicate ที่ commit หลัง process สำเร็จก่อนส่ง ACK

ปิด code walkthrough ด้วยการบอกว่า `dart/network.py` เป็น impairment wrapper สำหรับทำ loss/corruption/delay แบบมี seed ไม่ใช่ network simulator เต็มรูปแบบ

### 6:15–9:00 — Live demo

1. เริ่ม Wireshark capture ด้วย `udp port 9999`
2. เปิด server ให้เห็น `LISTEN ... workers=8`
3. รัน simulator 5 sensors
4. ชี้ `REGISTER_REQ` / `REGISTER_RES`
5. ชี้ว่า `DATA_BATCH` หนึ่ง packet มี reading หลายค่า
6. ชี้ `LATEST_UPDATE` และ log `REPLACE latest`
7. รอ `FIRE_DETECTED`
8. ชี้ server log ว่าบังคับไม่ส่ง ACK แรกก่อน `sendto()`
9. ชี้ client `TIMEOUT` และ packet retry ที่มี sequence เดิม + flag `RETRANSMISSION`
10. ชี้ `ACK 409 DUPLICATE` และยืนยันว่า server log มี `CRITICAL` เพียงครั้งเดียว

ห้ามใช้เวลาชี้ทุก field เลือกเฉพาะ field ที่พิสูจน์ claim

### 9:00–10:30 — ผลทดลองและความแตกต่าง

เปรียบเทียบ policy ภายใต้ sensor count, duration, fixed event schedule, loss probability/seed และ delay เดียวกัน แต่พูดกำกับว่า individual drop event ไม่ได้จับคู่กันข้าม policy เพราะ retry/ACK ใช้ PRNG draw เพิ่ม:

| Baseline | Normal telemetry | Critical alert |
|---|---|---|
| `raw` | ส่งครั้งเดียว | ส่งครั้งเดียว ไม่มี ACK |
| `reliable-all` | ACK/retry ทุก message | ACK/retry |
| `dart` | batch/latest ไม่ ACK | ACK/retry เฉพาะ critical |

`raw` และ `reliable-all` เป็น experimental comparator modes ที่จงใจเปลี่ยน `ACK_REQUIRED` semantics จึงไม่ใช่ DART-conforming policy; แถว `dart` เท่านั้นที่ทำตาม protocol specification ใช้สอง baseline เพื่อแสดง trade-off ไม่ใช่เสนอเป็น protocol เพิ่มอีกสองตัว

แสดง metrics ต่อไปนี้จากผลจริง:

- critical server acceptance rate คือ server process ครั้งแรก ส่วน client confirmation rate คือ client ได้ ACK ที่ยอมรับได้ (ฝั่ง `raw` เป็น n/a เพราะไม่มี ACK)
- P95 server-accept latency และ ACK latency โดยบอกว่าเป็น conditional-on-delivery/loopback
- attempted bytes รวม simulated drop ส่วน sent bytes คือ application bytes ที่ผ่านเข้า OS socket ทั้งสองค่ารวม client+server และไม่รวม UDP/IP/link header
- retransmission count
- server duplicate count
- latest accepted rate และ final latest-state correctness ซึ่งตรวจว่าค่าสุดท้ายใน server ตรงกับ final latest ของแต่ละ sensor
- max schedule lateness เพื่อเปิดเผย backlog จาก synchronous ACK wait ไม่ซ่อน workload ที่มาช้า

พูดผลเป็นตัวเลขจากไฟล์ที่ทดลองจริง เช่น “ในการทดลอง loss X% และ seed Y, DART ได้ ...” ห้ามพูดผลที่คาดเดาเป็นผลจริง และห้ามสรุปว่าเร็วกว่าทุก protocol

### 10:30–11:30 — จุดเด่นและสิ่งที่ต่าง

พูดแบบระมัดระวัง:

> “แนวคิด ACK, retry, batching และ latest-only มีอยู่ในระบบอื่นแล้ว จุดเด่นของงานนี้ไม่ใช่การอ้างว่าเราเป็นคนแรก แต่คือการออกแบบ wire protocol ขนาดเล็กที่รวม deadline และ delivery policy ต่างกันใน session เดียว แล้วพิสูจน์ trade-off ด้วย packet capture และ metrics ที่ทำซ้ำได้”

สิ่งที่ภูมิใจและควรชู:

- exact binary header และ custom Wireshark dissector
- forced-ACK-suppression demo ที่ process critical alert เพียงครั้งเดียวและทำให้ retry เกิดแบบ deterministic
- concurrent UDP server + worker pool
- seeded pseudo-random impairment ที่ควบคุมเงื่อนไขได้; ACK/retry ทำให้จำนวน PRNG draw ต่างกันจึงไม่ใช่ paired drop trace ข้าม policy และต้องใช้หลาย repeats
- baseline `raw`, `reliable-all`, `dart` ที่เงื่อนไขเดียวกัน

### 11:30–12:00 — Limitations และสรุป

พูดข้อจำกัดอย่างตรงไปตรงมา:

- ไม่มี authentication, encryption, authorization หรือ replay protection; ใช้เฉพาะ loopback/controlled lab และห้ามเปิดรับ traffic จาก shared/untrusted network
- CRC32 ไม่ใช่ encryption
- ไม่มี congestion control
- TTL ขึ้นกับ clock ของสองฝั่ง
- เป็น educational prototype ไม่ใช่ production/RFC

ปิดด้วย:

> “DART แสดงว่าความน่าเชื่อถือไม่จำเป็นต้องเป็น all-or-nothing เราสามารถออกแบบ protocol ให้ใช้ bandwidth ตามคุณค่าของข้อมูล และตรวจสอบการตัดสินใจนั้นได้จริงในทุก packet”

## ถ้าต้องขยายเป็น 15 นาที

ใช้เวลาเพิ่มอย่างมีคุณค่า:

- 1 นาที: เปิด payload ของ `DATA_BATCH` ใน Wireshark ให้เห็น count และ records
- 1 นาที: สาธิต corruption แล้วชี้ CRC mismatch/decoder drop
- 1 นาที: แสดงผล concurrency เมื่อ sensor หลายตัว register/send พร้อมกัน

อย่าใช้เวลาเพิ่มกับหน้าตาแอป เพราะไม่ใช่แกนของคะแนน protocol

## Demo สำรองเมื่อ Wireshark มีปัญหา

เตรียม `.pcapng` ที่บันทึกจาก pre-flight ไว้ก่อนวันอัด หาก live capture ไม่ขึ้น:

1. เปิด capture ที่เตรียมไว้
2. ใช้ filter `dart.msg_type == 5 || dart.msg_type == 6`
3. แสดง sequence/flags/status ตาม flow เดิม
4. รัน server/simulator live ต่อเพื่อให้เห็น log ว่าโปรแกรมยังทำงาน

ถ้า Lua plugin ไม่โหลด ให้ใช้ UDP packet bytes + server/client logs เป็น fallback แต่ควรแก้ plugin ก่อนอัดจริง เพราะ dissector เป็นหลักฐานที่เข้าใจง่ายกว่า

## คำถามที่ TA อาจถาม

### “ทำไมไม่ใช้ TCP?”

คำตอบ:

> “ข้อมูลปกติและ latest value ไม่ต้องการ retransmit ทุกชิ้น ส่วน critical alert ต้องการ ACK DART จึงเลือก UDP เพื่อกำหนด reliability ต่อ message ใน application เอง เป้าหมายคือศึกษา trade-off ไม่ได้บอกว่า UDP ดีกว่า TCP ทุกกรณีครับ”

### “รับประกันว่า alert ถึงไหม?”

คำตอบ:

> “ไม่รับประกัน 100% ครับ ระบบ retry ได้สูงสุด 5 attempts และไม่เกิน TTL ถ้า server ล่มหรือ loss ต่อเนื่องก็ล้มเหลว แต่เราวัด delivery rate และแสดง failure ได้ตรงไปตรงมา”

### “ACK หายแล้ว server จะบันทึก alert ซ้ำหรือไม่?”

คำตอบ:

> “Client retry ด้วย sequence เดิมครับ ถ้า copy ซ้ำเข้าพร้อมกัน worker หลังจะรอ in-flight owner ก่อน Server จะ commit identity เข้า cache 60 วินาทีเมื่อ process สำเร็จเท่านั้น แล้วจึงตอบ ACK 409 DUPLICATE โดยไม่ทำ side effect ซ้ำ ถ้า owner ล้มเหลว copy ที่รอรับช่วงได้ กลไกนี้ยังเป็น process-once เฉพาะใน cache window ไม่ใช่ exactly-once ตลอดกาลครับ”

### “ต่างจาก MQTT/CoAP อย่างไร?”

คำตอบ:

> “MQTT/CoAP เป็น protocol มาตรฐานและสมบูรณ์กว่ามาก DART เป็น educational protocol ที่โฟกัสการรวม batch, latest-only, deadline และ critical retry ใน wire format ที่เรา implement/measure เอง เราไม่ได้อ้างว่าเป็นแนวคิดใหม่ของโลกครับ”

### “CRC32 ทำไมต้องมี ทั้งที่ UDP มี checksum?”

คำตอบ:

> “UDP checksum อยู่ transport layer และ OS จัดการ DART CRC32 เป็น application-level validation ที่ทำให้เรา serialize/ตรวจ message เองและสาธิต corruption ได้ชัด แต่ไม่ใช่ security และแก้ error ไม่ได้ครับ”

### “Server รองรับหลาย sensor อย่างไร?”

คำตอบ:

> “UDP destination port เดียว demux เข้า socket เดียว จากนั้น DART ใช้ session_id/sensor_id แยก state และ receiver thread ส่งงานเข้า worker pool หลาย worker ครับ”

### “นี่คือ Selective Repeat หรือไม่?”

คำตอบ:

> “ไม่ใช่ SR sliding window แบบเต็มครับ เรานำหลัก selective retransmission มาใช้ คือ retry เฉพาะ ACK-required message ที่ timeout โดยไม่ resend batch รอบข้าง ไม่มี window หรือ out-of-order buffer แบบ SR เต็ม”

## สิ่งที่ห้ามพลาดในวิดีโอ

- ต้องเห็นผู้จัดทำตามเงื่อนไขงานอย่างน้อยช่วงหนึ่ง
- พูดชื่อและวัตถุประสงค์ protocol ก่อน code
- แสดง wire format และ normal/error flow
- เดโมหลายกรณี ไม่ใช่เฉพาะ happy path
- แสดง packet จริงหรือ capture ที่บันทึกจากโปรแกรมจริง
- รายงานผลวัดพร้อมเงื่อนไขและ seed
- แยก “ผลทดลองจริง” ออกจาก “สมมติฐาน”
- กล่าว limitation เพื่อไม่ให้ claim เกินหลักฐาน

## ไฟล์อ้างอิงระหว่างเตรียมสไลด์

- [PROTOCOL_SPEC.md](PROTOCOL_SPEC.md) — wire format และ protocol rules
- [COURSE_ALIGNMENT.md](COURSE_ALIGNMENT.md) — เชื่อม implementation กับ Chapter 1–3
- [ASSIGNMENT_CHECKLIST.md](ASSIGNMENT_CHECKLIST.md) — เทียบข้อกำหนดในโจทย์ PDF กับหลักฐานและงานที่ยังต้องส่ง
- [Wireshark README](../wireshark/README.md) — ติดตั้ง dissector และ filters
- เอกสารประกอบรายวิชา Chapters 1–3 — แหล่งเนื้อหาบทเรียน (ไม่รวมใน public repository)
