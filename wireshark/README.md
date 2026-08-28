# DART Wireshark dissector

ไฟล์ `dart.lua` ทำให้ Wireshark อ่านแพ็กเก็ต **DART v1** บน UDP port `9999` ได้เป็น field แทนการแสดงเพียงข้อมูลไบต์ดิบ เหมาะสำหรับใช้พิสูจน์ในวิดีโอว่า protocol ทำงานตาม specification จริง

## ความสามารถ

- แยก header ขนาด 40 ไบต์แบบ big-endian ครบทุก field
- แสดงชื่อของ message type, delivery mode, status code และ flags
- ตรวจ magic (`DART`), version (`1`) และความยาว payload
- คำนวณและตรวจ IEEE/zlib CRC-32 โดยถือว่า checksum bytes ที่ offset 36–39 เป็นศูนย์
- แยก `DATA_BATCH` เป็น metric records และแยก `LATEST_UPDATE` เป็น metric ID กับค่า float
- แสดง payload ชนิดอื่นเป็น UTF-8 และส่ง JSON ให้ JSON dissector ของ Wireshark เมื่อใช้งานได้
- แจ้ง malformed packet และ checksum mismatch ผ่าน Expert Information

## ติดตั้ง

1. เปิด Wireshark แล้วไปที่ **Wireshark > About Wireshark > Folders**
2. ดูตำแหน่ง **Personal Lua Plugins** แล้วสร้างโฟลเดอร์นั้นหากยังไม่มี
3. คัดลอก `dart.lua` ไปไว้ในโฟลเดอร์ดังกล่าว
4. ปิด–เปิด Wireshark ใหม่ หรือเลือก **Analyze > Reload Lua Plugins**

ตัวอย่างสำหรับ Wireshark รุ่นใหม่บน macOS (ตรวจตำแหน่งจริงจากเมนู Folders ก่อน):

```bash
mkdir -p "$HOME/.local/lib/wireshark/plugins"
cp wireshark/dart.lua "$HOME/.local/lib/wireshark/plugins/dart.lua"
```

ตรวจว่า plugin โหลดสำเร็จได้ที่ **Help > About Wireshark > Plugins** แล้วค้นคำว่า `dart.lua` หากมีข้อผิดพลาด Lua จะปรากฏในหน้าต่างแจ้งเตือนตอนเปิด Wireshark

## จับแพ็กเก็ตสำหรับ Demo

ถ้ารัน client และ server บนเครื่องเดียวกัน ให้เลือก interface `lo0` บน macOS (`Loopback` บนระบบอื่น) แล้วใช้ capture filter:

```text
udp port 9999
```

จากนั้นเริ่ม server และ sensor simulator เมื่อแพ็กเก็ตเข้ามา คอลัมน์ Protocol ควรแสดง `DART` และสามารถกางต้นไม้ **DART v1 Telemetry Protocol** เพื่อดู header, payload และ checksum ได้

> Capture filter และ display filter ใช้ภาษาไม่เหมือนกัน: ใส่ `udp port 9999` ก่อนเริ่มจับ แต่ใส่ `dart` ในช่องกรองเหนือรายการแพ็กเก็ตหลังเริ่มจับแล้ว

## Display filters ที่ใช้ในการนำเสนอ

```text
# แสดงเฉพาะ DART
dart

# Critical alert
dart.msg_type == 5

# ACK
dart.msg_type == 6

# ข้อความที่ใช้ critical-reliable delivery
dart.delivery == 3

# ข้อความที่กำหนดให้ต้องตอบ ACK
dart.flags.ack_required == 1

# แสดงการ retransmit หลัง ACK หายหรือ timeout
dart.flags.retransmission == 1

# ข้อมูลที่สร้างจาก sensor simulator
dart.flags.simulated == 1

# Sensor และ sequence ที่ต้องการติดตาม
dart.sensor_id == 7 && dart.sequence == 305

# Response ที่เป็น error
dart.status_code >= 400

# แพ็กเก็ตที่ CRC-32 ไม่ถูกต้อง
dart.checksum.valid == 0

# Metric ID ที่ต้องการดูจาก DATA_BATCH/LATEST_UPDATE
dart.metric_id == 1
```

สามารถใช้ชื่อ field เป็นคอลัมน์ได้โดยคลิกขวาที่ค่า เช่น `Sequence` หรือ `Sensor ID` แล้วเลือก **Apply as Column** วิธีนี้ช่วยให้ชี้ให้เห็น alert เดิมกับ retransmission ที่มี sequence เดียวกันได้ง่าย

## Header ที่ dissector คาดหวัง

| Offset | Size | Field | Wireshark field |
|---:|---:|---|---|
| 0 | 4 | Magic `DART` | `dart.magic` |
| 4 | 1 | Version | `dart.version` |
| 5 | 1 | Message type | `dart.msg_type` |
| 6 | 1 | Delivery mode | `dart.delivery` |
| 7 | 1 | Flags | `dart.flags.*` |
| 8 | 4 | Session ID | `dart.session_id` |
| 12 | 4 | Sensor ID | `dart.sensor_id` |
| 16 | 4 | Sequence | `dart.sequence` |
| 20 | 8 | Unix timestamp (ms) | `dart.timestamp_ms` |
| 28 | 4 | TTL (ms) | `dart.ttl_ms` |
| 32 | 2 | Payload length | `dart.payload_length` |
| 34 | 2 | Status code | `dart.status_code` |
| 36 | 4 | CRC-32 | `dart.checksum` |

### Message types

| Value | Name |
|---:|---|
| 1 | `REGISTER_REQ` |
| 2 | `REGISTER_RES` |
| 3 | `DATA_BATCH` |
| 4 | `LATEST_UPDATE` |
| 5 | `CRITICAL_ALERT` |
| 6 | `ACK` |
| 7 | `CONFIG_REQ` |
| 8 | `CONFIG_RES` |
| 9 | `HEARTBEAT` |
| 10 | `HEARTBEAT_ACK` |
| 11 | `ERROR` |

Delivery mode คือ `0 CONTROL`, `1 BEST_EFFORT_BATCH`, `2 LATEST_ONLY` และ `3 CRITICAL_RELIABLE` ส่วน flags ที่นิยามใน v1 คือ `0x01 ACK_REQUIRED`, `0x02 RETRANSMISSION` และ `0x04 SIMULATED`

## Payload แบบ binary

`DATA_BATCH` เริ่มด้วย `count:uint16` และตามด้วย record ขนาด 7 ไบต์จำนวน `count` รายการ:

```text
metric_id:uint8 | value:float32 | age_ms:uint16
```

`LATEST_UPDATE` มีขนาด 5 ไบต์:

```text
metric_id:uint8 | value:float32
```

Metric ID ที่กำหนดใน v1 คือ `1 TEMPERATURE_C`, `2 HUMIDITY_PERCENT`, `3 SMOKE_PPM`, `4 POSITION_X`, `5 POSITION_Y` และ `6 BATTERY_PERCENT`

ทุกจำนวนเป็น big-endian เช่นเดียวกับ header หากจำนวน record หรือความยาวไม่ตรงกัน Wireshark จะแสดง malformed payload ใน Expert Information

## ตรวจไฟล์ capture ด้วย `tshark`

หลังติดตั้ง plugin แล้ว สามารถตรวจจาก command line ได้:

```bash
tshark -r dart-demo.pcapng -Y dart -V
tshark -r dart-demo.pcapng -Y 'dart.flags.retransmission == 1'
```

หาก Wireshark ไม่แสดง DART ให้ตรวจสามจุดก่อน: เลือก interface ที่ถูกต้อง, โปรแกรมใช้ UDP port `9999`, และ `dart.lua` ปรากฏในรายการ Plugins
