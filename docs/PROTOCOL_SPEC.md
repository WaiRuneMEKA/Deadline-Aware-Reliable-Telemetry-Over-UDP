# DART v1 Protocol Specification

**DART** ย่อมาจาก **Deadline-Aware Reliable Telemetry** เป็น application-layer protocol เชิงการศึกษาสำหรับส่งข้อมูล telemetry จาก sensor client ไปยัง server ผ่าน UDP แนวคิดหลักคือข้อมูลทุกชนิดไม่จำเป็นต้องใช้วิธีส่งแบบเดียวกัน:

- ข้อมูลปกติหลายค่ารวมส่งเป็นชุดเพื่อลด overhead
- ข้อมูลที่ค่าล่าสุดสำคัญกว่าค่าเก่าเก็บเฉพาะค่าล่าสุด
- เหตุการณ์วิกฤตต้องมี ACK และส่งซ้ำเมื่อ ACK ไม่กลับมา

DART v1 เป็นผลงานเพื่อการเรียนและการทดลอง ไม่ได้อ้างว่าเป็นแนวคิดใหม่ระดับสากลหรือเป็นมาตรฐานอินเทอร์เน็ต กลไกของมันนำหลักการจาก Chapter 1–3 โดยเฉพาะ packet loss, application protocol, UDP และ reliable data transfer มาประกอบเป็น protocol ขนาดเล็กที่สังเกตได้ด้วย Wireshark ดูความเชื่อมโยงกับบทเรียนได้ใน [COURSE_ALIGNMENT.md](COURSE_ALIGNMENT.md)

## 1. Scope และคำสำคัญ

เอกสารนี้กำหนด DART version 1 ได้แก่:

- สถาปัตยกรรม client-server
- wire format ของทุก datagram
- ชนิดข้อความ delivery class flags และ status code
- รูปแบบ payload
- ลำดับการลงทะเบียน ส่ง telemetry ส่ง alert และ retry
- การตรวจความถูกต้อง หมดอายุ และกำจัด duplicate

คำว่า **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT** และ **MAY** ในเอกสารนี้ใช้บอกระดับข้อบังคับของ DART v1 แม้ DART จะยังไม่ใช่ Internet standard

## 2. Architecture

```text
+------------------+      DART message / UDP       +------------------+
| Sensor Simulator | ----------------------------> |   DART Server    |
| (client process) | <---------------------------- | (server process) |
+------------------+    response / ACK / error     +------------------+
        socket                                         bound socket
           |                                                |
           +--------- UDP -> IP -> Link layers -------------+
```

- Sensor simulator เป็น **client process** เพราะเป็นฝ่ายเริ่มติดต่อและลงทะเบียน
- Server เป็น process ที่รอรับ datagram บน UDP port ค่าเริ่มต้น `9999`
- Hardware sensor ไม่ใช่ข้อบังคับ โปรแกรมจำลองสร้างข้อมูลแบบเดียวกับที่ hardware client จะส่งบน wire
- Client กำหนด `sensor_id` ของตนเอง ส่วน server ออก `session_id` หลังลงทะเบียน
- Server ใช้ UDP socket ที่ bind เพียงตัวเดียวรับหลาย client แล้ว DART demultiplex ต่อด้วย `session_id` และ `sensor_id`
- UDP ไม่สร้าง connection แต่ DART สร้าง **logical session ที่ application layer** หลังการลงทะเบียน

## 3. เหตุผลที่เลือก UDP

DART เลือก UDP เพราะ:

1. UDP รักษาขอบเขตของ message เป็นหนึ่ง datagram จึงเหมาะกับ frame ของ DART
2. ไม่มี connection handshake ก่อนส่งข้อมูลและ header ของ UDP มีขนาดเล็ก
3. Application เลือกความน่าเชื่อถือเป็นรายข้อความได้ ข้อมูลปกติไม่ต้องรอ ACK แต่ alert สำคัญเพิ่ม ACK/timer/retry เอง
4. Server socket เดียวรับ datagram จาก sensor หลายรายได้ และ `recvfrom()` บอก IP/port ของผู้ส่ง
5. ทำให้เห็นหลัก reliable data transfer จาก Chapter 3 ในโค้ดและ Wireshark ได้โดยตรง

การเลือก UDP **ไม่ได้แปลว่า DART รับประกันความเร็วหรือ latency** ทั้ง UDP และ TCP ไม่รับประกัน delay หรือ minimum throughput และ UDP อาจทำ datagram สูญหาย ซ้ำ หรือมาผิดลำดับได้ DART v1 เพิ่ม reliability เฉพาะข้อความที่กำหนดเท่านั้น

## 4. Datagram และ byte order

หนึ่ง UDP datagram บรรจุ DART message หนึ่งข้อความเสมอ:

```text
+-------------------------+-------------------------------+
| DART fixed header       | payload                       |
| 40 bytes                | payload_length bytes          |
+-------------------------+-------------------------------+
```

- จำนวนไบต์ทั้งหมด MUST เท่ากับ `40 + payload_length` พอดี ห้ามมี trailing bytes
- จำนวน datagram สูงสุดของ implementation นี้คือ `1200` ไบต์
- payload จึงยาวได้สูงสุด `1160` ไบต์
- integer หลายไบต์และ float ใช้ **network byte order (big-endian)**
- รูปแบบ Python `struct` ที่เป็น canonical คือ:

```python
HEADER_STRUCT = struct.Struct("!4sBBBBIIIQIHHI")
```

เครื่องหมาย `!` ระบุ network byte order และ format นี้ MUST มีขนาด 40 ไบต์

## 5. DART v1 fixed header

| Offset | Size | Field | Wire type | ความหมาย |
|---:|---:|---|---|---|
| 0 | 4 | `magic` | 4 bytes | ASCII `DART` (`44 41 52 54` hex) |
| 4 | 1 | `version` | uint8 | ต้องเป็น `1` |
| 5 | 1 | `message_type` | uint8 | ชนิดข้อความตามตารางข้อ 6 |
| 6 | 1 | `delivery_class` | uint8 | นโยบายส่งตามตารางข้อ 7 |
| 7 | 1 | `flags` | uint8 bitmask | OR ค่าจากตารางข้อ 8 |
| 8 | 4 | `session_id` | uint32 | logical session ที่ server ออกให้; ใช้ `0` ก่อนลงทะเบียน |
| 12 | 4 | `sensor_id` | uint32 | logical sensor ID ที่ client กำหนดและคงเดิมตลอด session |
| 16 | 4 | `sequence` | uint32 | หมายเลขข้อความของ sensor; ACK ต้อง echo หมายเลขที่รับ |
| 20 | 8 | `timestamp_ms` | uint64 | Unix epoch milliseconds ตอนสร้างข้อความ |
| 28 | 4 | `ttl_ms` | uint32 | อายุข้อความนับจาก `timestamp_ms`; `0` หมายถึงไม่หมดอายุ |
| 32 | 2 | `payload_length` | uint16 | จำนวน payload bytes ที่ตามหลัง header |
| 34 | 2 | `status_code` | uint16 | ผลของ response/ACK/error; request/data ปกติใช้ `0` |
| 36 | 4 | `checksum` | uint32 | CRC32 ของ header ที่ตั้งช่องนี้เป็นศูนย์ ตามด้วย payload |

### 5.1 Field validation

Receiver MUST ปฏิเสธ datagram เมื่อพบอย่างน้อยหนึ่งกรณีต่อไปนี้:

- datagram สั้นกว่า 40 ไบต์
- `magic` ไม่ใช่ `DART`
- `version` ไม่ใช่ 1
- `message_type` หรือ `delivery_class` ไม่รู้จัก
- `flags` มี bit นอกเหนือจาก `ACK_REQUIRED`, `RETRANSMISSION` และ `SIMULATED`
- ขนาด datagram ไม่ตรงกับ `40 + payload_length`
- datagram ใหญ่กว่า 1200 ไบต์
- checksum ไม่ตรง
- payload ไม่ตรงกับรูปแบบของ message type

### 5.2 Strict message envelope

หลัง decode wire format แล้ว receiver ยังต้องตรวจความสัมพันธ์ระหว่าง type, direction, delivery class, flags, status และ payload ด้วย Server ปัจจุบันใช้ **strict DART mode เป็นค่าเริ่มต้น** (`allow_experimental_policies=False`) และรับ client envelope ตามตารางนี้:

| Client message | Delivery ที่บังคับ | `ACK_REQUIRED` ใน strict mode | Status |
|---|---|---|---:|
| `REGISTER_REQ` | `CONTROL` | ต้องมี | 0 |
| `DATA_BATCH` | `BEST_EFFORT_BATCH` | ต้องไม่มี | 0 |
| `LATEST_UPDATE` | `LATEST_ONLY` | ต้องไม่มี | 0 |
| `CRITICAL_ALERT` | `CRITICAL_RELIABLE` | ต้องมี | 0 |
| `CONFIG_REQ` | `CONTROL` | ต้องมี | 0 |
| `HEARTBEAT` | `CONTROL` | ต้องมี | 0 |

- `RETRANSMISSION` ใช้ได้เมื่อมี `ACK_REQUIRED` เท่านั้น
- `REGISTER_REQ` ต้องใช้ `session_id = 0`
- Envelope ที่ decode identity ได้แต่ผิด rules ทำให้ server ตอบ `ERROR 400 MALFORMED`; datagram ที่เสียจน decode header/identity ไม่ได้จะถูก drop
- `REGISTER_RES`, `ACK`, `CONFIG_RES`, `HEARTBEAT_ACK` และ `ERROR` เป็น response-only type หากส่งจาก client จะถูก drop โดยไม่ตอบ ERROR เพื่อไม่สร้าง response loop
- Client ตรวจ response ในทิศกลับกัน: ต้องเป็น response type ที่รู้จัก, class `CONTROL`, ไม่มี request flags, มาจาก server address เดิม และมี sensor/session/sequence, status และ payload ตรงกับ response ที่กำลังรอ ข้อความที่ไม่ผ่านจะไม่ถูกยอมรับเป็นผลสำเร็จ

CLI server จะยอม comparator envelope ที่จงใจผิดจาก strict semantics ก็ต่อเมื่อระบุ `--allow-experimental-policies`: โหมดนี้ผ่อนเฉพาะ `raw` ที่ไม่ขอ ACK สำหรับ critical และ `reliable-all` ที่ขอ ACK สำหรับ batch/latest เท่านั้น `demo.py` เปิด override ให้อัตโนมัติเมื่อเลือก policy ที่ไม่ใช่ `dart`; `benchmark.py` เปิดไว้เพื่อรัน comparator ทั้งสาม ส่วนการรัน server แยกคู่กับ simulator `--policy dart` ไม่ต้องใช้ flag นี้

## 6. Message types

| Value | Name | Direction ปกติ | Payload | หน้าที่ |
|---:|---|---|---|---|
| 1 | `REGISTER_REQ` | client -> server | JSON | ขอสร้าง logical session และ sensor identity |
| 2 | `REGISTER_RES` | server -> client | JSON | ส่งผลการลงทะเบียน ID และ config |
| 3 | `DATA_BATCH` | client -> server | binary | ส่ง readings ปกติหลายค่าพร้อมกัน |
| 4 | `LATEST_UPDATE` | client -> server | binary | ส่งค่าล่าสุดของ metric ที่เปลี่ยนเร็ว |
| 5 | `CRITICAL_ALERT` | client -> server | JSON | ส่งเหตุการณ์สำคัญที่ต้อง ACK/retry |
| 6 | `ACK` | server -> client | empty | ยืนยันข้อความ ACK-required โดย echo `sequence` |
| 7 | `CONFIG_REQ` | client -> server | JSON | ขอหรือเสนอการตั้งค่า |
| 8 | `CONFIG_RES` | server -> client | JSON | ตอบการตั้งค่า |
| 9 | `HEARTBEAT` | client -> server | empty | ตรวจว่า server ตอบสนองหลังลงทะเบียน |
| 10 | `HEARTBEAT_ACK` | server -> client | empty | ตอบ heartbeat |
| 11 | `ERROR` | server -> client | JSON | รายงานข้อผิดพลาดที่ตอบกลับได้อย่างปลอดภัย |

Unknown message type MUST NOT ถูกตีความเป็นชนิดใกล้เคียง

## 7. Delivery classes

| Value | Name | Reliability policy |
|---:|---|---|
| 0 | `CONTROL` | ใช้กับ registration, response, ACK, config, heartbeat และ error |
| 1 | `BEST_EFFORT_BATCH` | ไม่มี ACK และไม่ retry; การสูญหายบาง batch ยอมรับได้ |
| 2 | `LATEST_ONLY` | ไม่มี ACK; receiver เก็บค่าที่ยังใหม่ที่สุดและละทิ้งค่าที่เก่ากว่า |
| 3 | `CRITICAL_RELIABLE` | ต้องตั้ง `ACK_REQUIRED`; sender ใช้ timer และ retry จน ACK, expiry หรือครบจำนวนครั้ง |

`delivery_class` บอก semantics ที่ application ต้องใช้ ไม่ได้เปลี่ยนคุณสมบัติพื้นฐานของ UDP

Simulator มีโหมด baseline เชิงทดลองสองแบบที่จงใจเปลี่ยน semantics ของ `ACK_REQUIRED` แต่ใช้ message format เดียวกันเพื่อเปรียบเทียบอย่างควบคุม:

- `raw` ปิด `ACK_REQUIRED` แม้ใน `CRITICAL_ALERT`
- `reliable-all` เปิด `ACK_REQUIRED` ให้ `DATA_BATCH` และ `LATEST_UPDATE` ด้วย

สองโหมดนี้ **ไม่ใช่ DART-conforming delivery policy** มีไว้เป็น comparator เท่านั้น เฉพาะ simulator `--policy dart` ที่ทำตามตาราง delivery classes ของ specification นี้

## 8. Flags

`flags` เป็น bitmask จึงรวมหลายค่าได้ด้วย bitwise OR

| Bit/value | Name | ความหมาย |
|---:|---|---|
| `0x01` | `ACK_REQUIRED` | ผู้ส่งต้องการ application-layer ACK |
| `0x02` | `RETRANSMISSION` | ข้อความนี้เป็นการส่งซ้ำ ไม่ใช่ attempt แรก |
| `0x04` | `SIMULATED` | ข้อมูลหรือเหตุการณ์ถูกสร้างจาก simulator |

ค่า `0` หมายถึงไม่มี flag การตั้ง `RETRANSMISSION` MUST คง `session_id`, `sensor_id`, `sequence` และเนื้อหาของข้อความเดิมไว้ เพื่อให้ receiver ตรวจ duplicate ได้

## 9. Status codes และ status phrases

| Code | Symbol | Phrase | ใช้เมื่อ |
|---:|---|---|---|
| 0 | `NONE` | `NO STATUS` | request/data ที่ยังไม่มีผลตอบกลับ |
| 200 | `OK` | `OK` | สำเร็จทั่วไป |
| 201 | `REGISTERED` | `REGISTERED` | ลงทะเบียนสำเร็จ |
| 202 | `ACCEPTED` | `ACCEPTED` | รับและประมวลผลข้อความ ACK-required ครั้งแรก |
| 204 | `NO_CONTENT` | `NO CONTENT` | สำเร็จแต่ไม่มี payload |
| 400 | `MALFORMED` | `MALFORMED` | header/message ไม่เป็นไปตาม syntax ที่ตอบกลับได้ |
| 401 | `UNREGISTERED` | `UNREGISTERED` | session/sensor ไม่ถูกต้องหรือยังไม่ลงทะเบียน |
| 408 | `EXPIRED` | `EXPIRED` | `timestamp_ms + ttl_ms` ผ่านไปแล้ว |
| 409 | `DUPLICATE` | `DUPLICATE` | เคยรับ message identity นี้แล้วและจะไม่ประมวลผลซ้ำ |
| 413 | `PAYLOAD_TOO_LARGE` | `PAYLOAD TOO LARGE` | ขนาดเกินขีดจำกัด |
| 422 | `INVALID_PAYLOAD` | `INVALID PAYLOAD` | header อ่านได้แต่ payload ผิด schema/range |
| 429 | `RATE_LIMITED` | `RATE LIMITED` | server จำกัดอัตราข้อความของ client |
| 500 | `INTERNAL_ERROR` | `INTERNAL ERROR` | server เกิดข้อผิดพลาดภายใน |
| 503 | `BUSY` | `BUSY` | server ยังรับงานใหม่ไม่ได้ชั่วคราว |

Status phrase มีไว้แสดงผลต่อมนุษย์ บน wire ส่งเฉพาะเลข `status_code` ผู้รับ MUST ตัดสินผลจากเลข ไม่ใช่ข้อความ phrase

Prototype ปัจจุบัน emit `OK`, `REGISTERED`, `ACCEPTED`, `MALFORMED`, `UNREGISTERED`, `EXPIRED`, `DUPLICATE`, `INVALID_PAYLOAD` และ `INTERNAL_ERROR` ตาม flow ที่เกิดจริง โดย `MALFORMED` ใช้เมื่อ message envelope ผิดกฎแต่ยังตอบกลับได้ ส่วน `NO_CONTENT`, `PAYLOAD_TOO_LARGE`, `RATE_LIMITED` และ `BUSY` ถูกสงวนไว้ใน v1 enum แต่ implementation ปัจจุบันยังไม่มี runtime path ปกติที่ส่ง code เหล่านั้น

## 10. Payload formats

### 10.1 กติกาทั่วไป

- Control และ alert payload ที่ระบุว่า JSON ใช้ compact UTF-8 JSON object
- Canonical encoder ของ implementation เรียง key และไม่ใส่ whitespace ที่ไม่จำเป็น
- JSON number ทุกตำแหน่งต้องเป็นค่าจำกัด: encoder/decoder ปฏิเสธ `NaN`, `Infinity`, `-Infinity` และค่าที่ parse แล้วล้นเป็น non-finite แต่ JSON number ไม่ได้ถูกบังคับให้มี precision/range แบบ float32
- Binary integer และ float ใช้ big-endian เฉพาะ value ใน `DATA_BATCH`/`LATEST_UPDATE` เป็น IEEE-754 binary32 และต้องมีค่าจำกัดที่ represent ได้ใน float32; ค่าที่ finite ใน Python แต่ล้น binary32 ก็ถูกปฏิเสธ
- Empty payload มี `payload_length = 0`

### 10.2 Metric IDs

| ID | Name | หน่วย/ความหมายตามปกติ |
|---:|---|---|
| 1 | `TEMPERATURE_C` | องศาเซลเซียส |
| 2 | `HUMIDITY_PERCENT` | เปอร์เซ็นต์ความชื้น |
| 3 | `SMOKE_PPM` | parts per million |
| 4 | `POSITION_X` | พิกัดแกน X ของระบบทดลอง |
| 5 | `POSITION_Y` | พิกัดแกน Y ของระบบทดลอง |
| 6 | `BATTERY_PERCENT` | เปอร์เซ็นต์แบตเตอรี่ |

Unknown metric ID ทำให้ payload ไม่ถูกต้องใน DART v1

### 10.3 `REGISTER_REQ`

Compact UTF-8 JSON:

```json
{"capabilities":["temperature_c","smoke_ppm","fire_alert"],"client_instance_id":"7f4a0d61e2754ae5b559ca1fce8bd17c","name":"sensor-01"}
```

- `name`: string ที่ใช้แสดงผล; server จำกัดไว้ 80 ตัวอักษร
- `capabilities`: array ของ string ที่อธิบายชนิดข้อมูลที่ client ส่งได้; prototype รับสูงสุด 32 ค่าและจำกัดค่าละ 40 ตัวอักษร
- `client_instance_id`: random 128-bit identity เขียนเป็น hexadecimal 32 ตัวอักษร สร้างครั้งเดียวต่อ `SensorClient` instance และต้องใช้ค่าเดิมใน registration retry ทุก attempt ค่าใหม่นี้ไม่ใช่ credential หรือ security token แต่ใช้แยก process ใหม่ออกจาก retry ของ process เดิม แม้ OS จะนำ source UDP port เดิมกลับมาใช้
- Header ใช้ `session_id = 0`, `sensor_id` ที่ client เลือก, class `CONTROL`
- Sender ใช้ reliable request/response retry pattern ตามข้อ 13

เพื่อให้ client รุ่นก่อนหน้าที่ยังไม่มี `client_instance_id` ใช้งานได้ Server prototype ยอมรับ field ที่หายไปและใช้ `(sensor_id, source address, REGISTER_REQ sequence, timestamp_ms)` เป็น compatibility identity ดังนั้น retry แบบเดิมที่ส่ง packet เดิมและใช้ sequence/timestamp เดิมยังได้ session เดิม แต่ REGISTER_REQ legacy message ใหม่จะได้ session ใหม่ Client ใหม่ SHOULD ส่ง `client_instance_id`; Server MUST ปฏิเสธ field ที่มีอยู่แต่ type/รูปแบบไม่ถูกต้องด้วย `422 INVALID_PAYLOAD`

### 10.4 `REGISTER_RES`

Compact UTF-8 JSON:

```json
{"config":{"ack_timeout_ms":250,"batch_size":5,"heartbeat_interval_ms":2000,"max_attempts":5,"max_datagram_size":1200},"server_time_ms":1760000000000,"session_id":1234}
```

- Header ใส่ `session_id` ที่ server ออกให้และ echo `sensor_id` ที่ client ส่งมา
- `status_code = 201 REGISTERED` เมื่อสำเร็จ
- `config` ใน implementation ปัจจุบันเป็นคำแนะนำ ไม่ใช่ congestion-control negotiation

### 10.5 `DATA_BATCH`

Binary payload:

```text
+----------------+-------------------------------------------+
| count: uint16  | reading[0] ... reading[count-1]           |
+----------------+-------------------------------------------+

reading (7 bytes each):
+------------------+----------------+----------------+
| metric_id: uint8 | value: float32 | age_ms: uint16 |
+------------------+----------------+----------------+
```

Canonical Python formats:

```python
count   = struct.Struct("!H")
reading = struct.Struct("!BfH")
```

- `count` MUST อยู่ระหว่าง 1 ถึง 165 ซึ่งเป็นจำนวนสูงสุดที่ยังทำให้ datagram ไม่เกิน 1200 ไบต์
- payload length MUST เท่ากับ `2 + count * 7`
- `age_ms` คืออายุของ reading เมื่อสร้าง batch ช่วยบอกว่าแต่ละค่าถูกพักรอรวม batch นานเท่าไร
- Header ใช้ class `BEST_EFFORT_BATCH`, ไม่ตั้ง `ACK_REQUIRED`, status `0`
- Receiver MAY สูญเสียทั้ง batch และ protocol จะไม่ส่งซ้ำ

### 10.6 `LATEST_UPDATE`

Binary payload 5 ไบต์:

```text
+------------------+----------------+
| metric_id: uint8 | value: float32 |
+------------------+----------------+
```

Canonical Python format คือ `!Bf` Header ใช้ class `LATEST_ONLY`, ไม่ตั้ง `ACK_REQUIRED`, status `0` Receiver เปรียบเทียบลำดับ/เวลาและต้องไม่ให้ค่าที่เก่ากว่าทับ state ที่ใหม่กว่า

### 10.7 `CRITICAL_ALERT`

Compact UTF-8 JSON:

```json
{"alert_type":"FIRE","message":"smoke threshold exceeded","severity":"critical","unit":"ppm","value":450.0}
```

- `alert_type`: non-empty string
- `severity`: string ระดับความรุนแรงที่ application ตกลงกัน เช่น `critical`
- `value`: number, string หรือ `null` ตามชนิด alert; `unit` เป็น string ของหน่วย
- `message`: คำอธิบายสำหรับมนุษย์
- Header ใช้ class `CRITICAL_RELIABLE` และตั้ง `ACK_REQUIRED`
- Attempt ที่สองเป็นต้นไปเพิ่ม `RETRANSMISSION`
- ค่าแนะนำของ `ttl_ms` คือ 5000 ms

Sender ของ prototype สร้างครบทั้งห้า field ส่วน server ปัจจุบันตรวจว่า payload เป็น JSON object และ `alert_type` เป็น non-empty string; การ validate type/range ของ `severity`, `value`, `unit` และ `message` ให้เข้มกว่านี้ยังเป็นงานต่อยอด

### 10.8 `ACK`

- Payload ว่าง
- Class `CONTROL`
- `sequence` MUST เท่ากับ sequence ของข้อความที่กำลังยืนยัน
- `session_id` และ `sensor_id` MUST ตรงกับข้อความนั้น
- `status_code = 202 ACCEPTED` เมื่อประมวลผลครั้งแรก
- `status_code = 409 DUPLICATE` เมื่อเคยประมวลผลแล้ว ทั้งสองกรณีทำให้ sender หยุด retry ได้

ACK ยืนยันว่า DART process ฝั่ง server รับและตรวจข้อความได้ ไม่ใช่หลักฐานทาง cryptography ว่าผู้ตอบเป็น server ที่ไว้ใจได้

### 10.9 `CONFIG_REQ` / `CONFIG_RES`

`CONFIG_REQ` เป็น compact JSON object ของ setting ที่ต้องการสอบถาม/เสนอ หรือ `{}` เมื่อขอ config ปัจจุบัน ตัวอย่าง:

```json
{"batch_size":5,"heartbeat_interval_ms":2000}
```

`CONFIG_RES` ส่ง compact JSON object พร้อม status `200 OK` หากสำเร็จ ค่า config ที่ prototype ส่งกลับคือ `heartbeat_interval_ms`, `batch_size`, `max_datagram_size`, `ack_timeout_ms` และ `max_attempts` Prototype ปัจจุบันตรวจเพียงว่า request เป็น JSON object แล้วส่ง recommended config กลับมา ยังไม่ apply setting จาก client

### 10.10 `HEARTBEAT` / `HEARTBEAT_ACK`

ทั้งคู่มี payload ว่างและ class `CONTROL` Client ส่ง `HEARTBEAT` หลังลงทะเบียน Server ตอบ `HEARTBEAT_ACK` โดย echo sequence และใช้ status `200 OK` ค่าแนะนำจาก registration คือช่วง 2000 ms Client prototype ใช้ TTL 3000 ms และ response ใช้ TTL 2000 ms

DART v1 prototype ยังไม่ลบ session ที่เงียบเกิน heartbeat interval ดังนั้น heartbeat แสดง liveness ใน demo แต่ยังไม่ใช่ lease

### 10.11 `ERROR`

Compact UTF-8 JSON:

```json
{"detail":"session_id is unknown","error":"UNREGISTERED"}
```

- `error`: machine-readable short name
- `detail`: ข้อความอธิบายสำหรับมนุษย์
- Header ใส่ status code 4xx/5xx ที่สอดคล้องกับสาเหตุ
- สำหรับ `500 INTERNAL_ERROR` Server ส่ง detail แบบทั่วไปและเก็บ exception จริงไว้ใน server log เพื่อไม่เปิดเผยรายละเอียด implementation ผ่าน network
- Server MUST NOT ตอบ ERROR ต่อ datagram ที่ไม่ทราบ source อย่างปลอดภัยหรือ malformed จนไม่สามารถสร้าง response identity ได้ เพื่อหลีกเลี่ยง amplification/response loop

## 11. Message identity และ sequence rules

- Client หลังลงทะเบียนใช้ uint32 sequence ที่เพิ่มขึ้นต่อ sensor
- Identity ที่ใช้กำจัด duplicate คือ `(session_id, sensor_id, message_type, sequence)`
- Retransmission MUST ใช้ sequence เดิม ห้ามสร้าง sequence ใหม่
- ACK echo sequence ของ message ต้นทางในช่อง `sequence`
- Server validate payload semantics ก่อน reserve identity; หาก dispatch ล้มเหลวหลัง reserve ต้องลบ identity นั้น เพื่อไม่ให้ malformed message ทำให้ corrected retry ถูกมองเป็น duplicate
- เมื่อ identity เดียวกันมาถึงพร้อมกันหลาย worker server ให้ copy แรกเป็น **in-flight owner** ส่วน copy อื่นรอ condition จน owner จบ จึงไม่มี worker ใดตอบ `409 DUPLICATE` ก่อนที่การประมวลผลครั้งแรกจะ commit สำเร็จ
- หาก owner สำเร็จ server ย้าย identity จาก in-flight set เข้า seen-cache แล้วปลุกผู้รอให้ตอบ duplicate; หาก owner ล้มเหลว server ลบเฉพาะ in-flight claim และเปิดให้ copy ที่รออยู่หนึ่งตัวรับช่วง process แทน
- Server เก็บ duplicate identity ใน retention cache ค่าเริ่มต้น 60 วินาทีและลบ entry เก่าแบบ opportunistic ก่อนตรวจข้อความใหม่
- Duplicate ของ `CRITICAL_ALERT` หรือ telemetry baseline ที่ตั้ง `ACK_REQUIRED` ต้องไม่ทำ side effect ซ้ำ และ server ส่ง `ACK 409 DUPLICATE` เพื่อให้ client หยุด retry
- Duplicate `CONFIG_REQ` และ `HEARTBEAT` ต้อง replay typed response (`CONFIG_RES` หรือ `HEARTBEAT_ACK`) เพราะ generic ACK ไม่ตรง response contract; operation ทั้งสองเป็น idempotent
- `REGISTER_REQ` ถูกจัดการแยกจาก seen-cache โดย registration identity `(sensor_id, source address, client_instance_id)` ทำให้ retry ของ client instance เดิมได้ `REGISTER_RES` ของ session เดิม แต่ process ใหม่ที่ใช้ source port และ sensor ID ซ้ำได้ session/sequence space ใหม่ สำหรับ legacy request ที่ไม่มี instance ID ใช้ REGISTER_REQ sequence และ timestamp เป็น fallback ตามข้อ 10.3
- การกด duplicate ภายใน cache ทำให้เกิดผลแบบ "process once within the cache window" เท่านั้น **ไม่ใช่ exactly-once guarantee ตลอดกาล**
- Client ข้ามค่า sequence `0` และ server เปรียบเทียบ latest sequence แบบ unsigned half-range เพื่อรองรับ wrap-around กรณีปกติ แต่ cache มีอายุจำกัดและ protocol ไม่มี maximum-packet-lifetime แบบพิสูจน์ได้ จึงไม่ควรอ้างว่ากำจัด ambiguity จาก packet เก่ามากได้ทุกกรณี

## 12. Timestamp, TTL และ expiry

ให้ `arrival_ms` เป็นเวลาที่ receiver ตรวจข้อความ:

```text
ถ้า ttl_ms == 0: ไม่ตรวจ expiry
ถ้า arrival_ms > timestamp_ms + ttl_ms: ข้อความหมดอายุ
```

- Expired telemetry MUST NOT เปลี่ยน state ล่าสุดของ server
- Server ตอบ status `408 EXPIRED` เมื่อสามารถตอบได้อย่างสมเหตุสมผล
- Sender MUST หยุด retry เมื่อเวลาปัจจุบันผ่าน deadline นี้ แม้ยังไม่ครบ max attempts
- ค่าที่ prototype ใช้: registration 10000 ms, config 5000 ms, heartbeat 3000 ms, batch 2000 ms, latest 1000 ms, critical 5000 ms
- TTL อาศัยนาฬิกาของ client และ server จึงไวต่อ clock skew ข้อจำกัดนี้ต้องกล่าวในการนำเสนอ

## 13. Timeout, retry และ duplicate behavior

### 13.1 Reliable request/response algorithm

ใช้กับ `REGISTER_REQ`, `CRITICAL_ALERT`, `CONFIG_REQ` และ `HEARTBEAT` โดยชนิด response ที่คาดหวังต่างกัน:

1. สร้าง message พร้อม sequence, timestamp และ TTL เพียงครั้งเดียว
2. ส่ง attempt แรกและเริ่ม timer ค่าเริ่มต้น 250 ms
3. ถ้าได้รับ response/ACK ที่ session, sensor และ sequence ตรงกัน ให้สำเร็จและยกเลิก timer
4. ถ้า timeout และข้อความยังไม่หมดอายุ ให้ส่ง datagram เดิมโดยเพิ่ม flag `RETRANSMISSION`
5. เพิ่ม timeout แบบ exponential `250, 500, 1000, ...` ms แต่ wait ต้องไม่เกินเวลาที่เหลือก่อน expiry
6. หยุดเมื่อได้รับผลตอบกลับ หมด TTL หรือครบ `max_attempts = 5` **รวม attempt แรก**

Backoff นี้ลด retry burst ใน demo แต่ **ไม่ใช่ congestion control** ไม่มีการวัด capacity, fairness, router queue หรือ competing flows

### 13.2 Critical alert receive behavior

```text
รับ CRITICAL_ALERT
  -> header/checksum/payload ไม่ผ่าน: drop หรือ ERROR ที่เหมาะสม
  -> session ไม่ถูกต้อง: ERROR 401
  -> หมดอายุ: ERROR/ACK 408 และห้าม process alert
  -> identity เดียวกันกำลัง in-flight: รอผล; ห้ามประกาศ duplicate ก่อน owner commit
  -> identity อยู่ใน duplicate cache: ห้าม process side effect ซ้ำภายใน cache window, ACK 409
  -> message ใหม่: claim, process/log หนึ่งครั้ง, commit cache, ACK 202
```

ถ้า ACK แรกหาย sender จะ timeout และ retransmit server จะเห็น duplicate และตอบ ACK 409 ส่งผลให้ client จบได้โดยไม่บันทึก alert ซ้ำ

### 13.3 Selective retry policy

DART ส่งซ้ำเฉพาะ message ที่ตั้ง `ACK_REQUIRED` และ timeout เท่านั้น ไม่ส่ง `DATA_BATCH` หรือ `LATEST_UPDATE` ที่อยู่ก่อนหรือหลังมันซ้ำ แนวคิดนี้คล้ายการหลีกเลี่ยงการส่งซ้ำที่ไม่จำเป็น แต่ DART v1 **ไม่ใช่ implementation เต็มของ Selective Repeat sliding-window protocol** เพราะไม่มี sender/receiver window และไม่มี per-packet pipeline สำหรับทุก message

## 14. State and sequence flows

### 14.1 Registration

```text
Client (UNREGISTERED)                         Server
       | REGISTER_REQ sid=0 sensor=K seq=N       |
       | instance=I                              |
       |---------------------------------------->|
       |                       validate/allocate |
       | REGISTER_RES status=201 sid=S sensor=K  |
       |<----------------------------------------|
Client stores S,K and enters REGISTERED
```

หาก response หาย client ส่ง REGISTER_REQ เดิมซ้ำตามข้อ 13 โดยใช้ sequence `N` และ `client_instance_id=I` เดิม Server SHOULD ทำ registration ให้ idempotent ต่อ client instance เดิมและตอบ session เดิม ไม่สร้าง sensor ใหม่ทุก retry เมื่อ client process ใหม่เริ่มทำงานต้องสร้าง `I` ใหม่เพื่อไม่สืบทอด latest-sequence state ของ process เก่าโดยบังเอิญ

### 14.2 Normal telemetry

```text
Client                                           Server
  | DATA_BATCH class=BEST_EFFORT_BATCH seq=10       |
  |------------------------------------------------>|
  | LATEST_UPDATE class=LATEST_ONLY seq=11           |
  |------------------------------------------------>|
  |                          no per-message ACK      |
```

Batch ที่หายจะไม่ retry ส่วน latest update เก่าที่มาช้ากว่าค่าใหม่ต้องไม่ย้อน state ของ server

### 14.3 Critical alert when packet is lost

```text
Client                                           Server
  | CRITICAL_ALERT seq=20 ACK_REQUIRED  ---X (lost)   |
  |                                                   |
  | [250 ms timeout]                                  |
  | CRITICAL_ALERT seq=20 ACK_REQUIRED|RETRANSMISSION |
  |-------------------------------------------------->|
  |                                first process + cache identity |
  | ACK seq=20 status=202                             |
  |<--------------------------------------------------|
```

### 14.4 Critical alert when ACK is lost

```text
Client                                           Server
  | CRITICAL_ALERT seq=21 ACK_REQUIRED               |
  |------------------------------------------------->| first process/cache identity
  | ACK seq=21 status=202 --------------------X       |
  | [timeout]                                         |
  | CRITICAL_ALERT seq=21 ACK_REQUIRED|RETRANSMISSION |
  |------------------------------------------------->| detect duplicate
  | ACK seq=21 status=409                             |
  |<-------------------------------------------------|
```

## 15. Checksum behavior

Sender คำนวณ checksum ดังนี้:

1. serialize header ทุก field โดยตั้ง `checksum = 0`
2. ต่อ payload bytes
3. คำนวณ IEEE CRC32 แบบที่ `zlib.crc32()` ใช้
4. จำกัดผลเป็น unsigned 32-bit และเขียนลง offset 36

Receiver ตั้ง checksum bytes กลับเป็นศูนย์ คำนวณใหม่จาก header+payload แล้วเปรียบเทียบกับค่าที่รับ ถ้าไม่ตรง MUST ปฏิเสธ packet

CRC32 นี้เพิ่มการตรวจ corruption ที่มองเห็นได้ในระดับ application แต่:

- แก้ error ไม่ได้ มีหน้าที่ detect เท่านั้น
- ไม่ใช่ MAC หรือ digital signature
- ป้องกันผู้โจมตีแก้ข้อมูลไม่ได้ เพราะผู้โจมตีคำนวณ CRC32 ใหม่ได้
- อยู่คนละชั้นกับ UDP checksum ซึ่ง OS/network stack จัดการให้

## 16. Recommended message combinations

| Message | Delivery | Required/normal flags | Normal status | Default TTL |
|---|---|---|---:|---:|
| `REGISTER_REQ` | `CONTROL` | `ACK_REQUIRED`, optional `SIMULATED` | 0 | 10000 ms |
| `REGISTER_RES` | `CONTROL` | none | 201 | 5000 ms |
| `DATA_BATCH` | `BEST_EFFORT_BATCH` | optional `SIMULATED` | 0 | 2000 ms |
| `LATEST_UPDATE` | `LATEST_ONLY` | optional `SIMULATED` | 0 | 1000 ms |
| `CRITICAL_ALERT` | `CRITICAL_RELIABLE` | `ACK_REQUIRED`, optional `SIMULATED`; retry adds `RETRANSMISSION` | 0 | 5000 ms |
| `ACK` | `CONTROL` | none | 202, 408 or 409 | `max(1000, original ttl_ms)` |
| `CONFIG_REQ` | `CONTROL` | `ACK_REQUIRED`, optional `SIMULATED` | 0 | 5000 ms |
| `CONFIG_RES` | `CONTROL` | none | 200 | 5000 ms |
| `HEARTBEAT` | `CONTROL` | `ACK_REQUIRED`, optional `SIMULATED` | 0 | 3000 ms |
| `HEARTBEAT_ACK` | `CONTROL` | none | 200 | 2000 ms |
| `ERROR` | `CONTROL` | none | 4xx/5xx | 5000 ms |

## 17. Measurement vocabulary

ผลการทดลองควรแยกคำต่อไปนี้:

- **attempted application wire bytes**: DART header + payload ที่ application พยายามส่ง รวม datagram ที่ impairment layer อาจ drop ก่อน `sendto()`
- **sent application wire bytes**: DART header + payload ที่ส่งเข้า UDP socket จริง ไม่รวม datagram ที่ถูก simulated drop และไม่รวม UDP/IP/link headers
- **server acceptance rate**: alert ที่ server ตรวจและ process ครั้งแรก หารด้วย alert ที่สร้าง ใช้เปรียบเทียบ `raw` ได้แต่ไม่ได้แปลว่า client รู้ผล
- **confirmation rate**: alert ที่ client ได้ ACK ที่ยอมรับได้ หารด้วย alert ที่สร้าง; `raw` ไม่มี ACK จึงรายงาน `null`/`n/a`
- **server-accept latency**: เวลาจาก timestamp ตอนเรียก protocol send จน server รับ/process ครั้งแรก วัดเฉพาะ alert ที่ถึงและอาศัยนาฬิกาเครื่องเดียวกันใน benchmark
- **ACK latency**: เวลาของ protocol call จาก attempt แรกจน client ได้ ACK; `raw` รายงาน `null`/`n/a`
- **final latest-state rate**: จำนวน sensor ที่ server ถือ `POSITION_X` เท่ากับค่าล่าสุดสุดท้ายใน logical workload หารด้วยจำนวน sensor ที่มี latest event; แยกจากอัตรา datagram latest ที่รับทั้งหมด
- **schedule lateness**: wall-clock backlog เทียบกับ fixed logical event schedule; เนื่องจาก reliable call เป็น synchronous policy ที่รอ ACK นานอาจส่ง event ถัดไปช้า แม้จำนวน event ที่เสนอเท่าเดิม และ packet timestamp จะเริ่มเมื่อ protocol call นั้นเริ่มจริง
- **retransmissions**: attempt หลังครั้งแรก
- **goodput**: payload ที่รับและใช้ได้ต่อเวลา ไม่รวม header/duplicate
- **data staleness**: อายุของ latest value ที่ server ถืออยู่
- **expiry count**: จำนวนข้อความที่มาถึงหลัง deadline

ใน `benchmark.py`, `total_attempted_bytes` รวม client+server DART bytes ที่พยายามส่งและรวม simulated drop ส่วน `total_sent_bytes` รวมเฉพาะ DART bytes ที่ผ่านเข้า UDP socket จริง ทั้งคู่รวม registration/response/ACK แต่ยังเป็น **application bytes** ไม่ใช่จำนวนไบต์บน Ethernet/Wi-Fi จริง

Benchmark สร้าง event schedule ล่วงหน้า ส่ง alert หลายครั้งจากทุก sensor และ fingerprint sensor ID พร้อม counts/values/offsets ของ event list ด้วย SHA-256 ก่อน replay Sensor workers ผ่าน barrier เดียวกันเพื่อประสานจุดเริ่ม จึงไม่ปล่อยให้ policy ที่รอ ACK นานลด offered workload; event ที่ช้าจะถูกส่งทันทีตามลำดับเดิมและรายงาน `max_schedule_lateness_ms` หาก worker ล้มเหลว, sensor ลงทะเบียนไม่ครบ, จำนวน alert ผิด หรือ fingerprint ต่างกันระหว่าง policy ใน loss/repeat เดียวกัน case จะล้มเหลว

เพื่อให้ตรวจ provenance และ rerun เงื่อนไขเดิมได้ JSON block `method` บันทึก `base_seed` กับ `seed_derivation` และทุก case บันทึก `case_seed`, `simulation_seed` และ `server_seed`; CSV ทำซ้ำ field เหล่านี้ในทุกแถว `demo.py` บันทึก `seed`, `simulation_base_seed`, `server_seed`, `seed_scope` พร้อม loss/ACK-loss/corruption/delay/jitter และ workload configuration ใน `demo_configuration` การรัน `benchmark.py --quick` ใช้เพียง smoke test และเมื่อไม่ได้ระบุ `--output` จะเขียน `results/benchmark_quick.json`/`.csv` แยกจากผลเต็ม

ก่อน `demo.py`/`benchmark.py` หยุด server และอ่าน snapshot จะเรียก `wait_until_idle(quiet_period_s=0.15, timeout_s=3.0)` ซึ่งรอทั้ง worker task เป็นศูนย์และไม่มี datagram ใหม่ตลอด quiet period; timeout ถือเป็น run ที่ใช้สรุปผลไม่ได้ วิธีนี้กันการอ่าน metrics/latest state ขณะที่งานยังค้าง แต่ไม่ได้เปลี่ยน protocol wire semantics

ค่า rate ใน `summary` ใช้ผลรวมนับตัวเศษหารผลรวมนับตัวส่วนข้าม repeats ไม่ใช่ค่าเฉลี่ยเปอร์เซ็นต์ต่อ run ค่า bytes/value ใช้ aggregate bytes หาร aggregate generated values และค่า P95 pool raw latency samples ข้าม repeats; เมื่อไม่มี sample ใช้ `null` ไม่ใช้ศูนย์

การเปรียบเทียบ MUST ใช้ workload, loss probability, duration และจำนวน sensor เดียวกัน แต่ seed เดียวกันไม่รับประกันว่า drop จะตกบน logical event เดียวกัน เพราะ ACK/retry ใช้ random draws เพิ่ม จึงควรใช้หลาย repeats และไม่ควรสรุปว่า DART "เร็วกว่า UDP/TCP ทุกกรณี" จากผลบน loopback Benchmark นี้เปรียบเทียบเฉพาะ experimental policies ที่ reuse DART wire format (`raw`, `reliable-all`, `dart`) ไม่ได้รัน TCP, MQTT หรือ CoAP implementation จึงใช้อ้าง performance เหนือ protocol เหล่านั้นไม่ได้

## 18. Limitations and non-goals

DART v1 มีข้อจำกัดที่ตั้งใจให้ชัดเจน:

- **ไม่มี congestion control** หรือ flow control จึงไม่ควรยิงปริมาณสูงบน public Internet
- **ไม่มี encryption, authentication, authorization หรือ replay protection**
- CRC32 ไม่ใช่ security integrity
- ไม่มี key exchange, TLS/DTLS หรือ certificate
- ไม่มี path-MTU discovery; ขีดจำกัด 1200 ไบต์ช่วยลดโอกาส fragmentation แต่ไม่รับประกันทุก path
- ไม่มี NAT traversal, service discovery, server failover หรือ persistent storage
- heartbeat ยังไม่ evict inactive session
- `client_instance_id` ใช้แยก lifecycle/idempotency เท่านั้น ไม่ authenticate เจ้าของ sensor และ session เก่ายังอยู่ใน memory จน server หยุด
- duplicate retention cache มี time bound 60 วินาทีแต่ไม่มี hard maximum entry count
- clock skew อาจทำให้ตัดสิน expiry ผิด
- duplicate suppression มีอายุจำกัด จึงไม่ใช่ exactly-once delivery แบบถาวร
- ไม่ได้ implement sliding window, general-purpose reliable byte stream หรือ ordered delivery
- exponential retry backoff ไม่ใช่ congestion control
- simulator impairment เป็นการ drop/corrupt/delay ใน application เพื่อให้ demo ทำซ้ำได้ ไม่ได้จำลอง router queue และ Internet ทั้งหมด
- protocol นี้เป็น educational prototype ไม่ใช่ RFC และไม่ได้อ้าง global novelty

สำหรับ production system ควรพิจารณา DTLS/QUIC, authentication, replay window, adaptive timer จาก RTT, rate control/congestion control, durable session state และ interoperability testing เพิ่มเติม

## 19. Course source

ข้อกำหนดนี้อิงแนวคิดจากเอกสารประกอบการเรียน Network บทที่ 1–3 โดยเฉพาะ:

- Chapter 1: `#sm1-perf` เรื่อง delay/loss/throughput และ `#sm1-layers` เรื่อง layers/encapsulation
- Chapter 2: `#sm2-principles` เรื่อง client-server, socket, protocol syntax/semantics/rules และ transport selection
- Chapter 3: `#sm3-mux`, `#sm3-rdt`, `#sm3-pipeline` เรื่อง UDP, mux/demux, checksum, sequence, ACK, timer และ selective retransmission

การ mapping ระหว่างแต่ละแนวคิดกับ feature ในโค้ดอยู่ใน [COURSE_ALIGNMENT.md](COURSE_ALIGNMENT.md)
