# DART video script — ประมาณ 14 นาที

รายวิชา **01418351 หลักการสื่อสารคอมพิวเตอร์และการประมวลผลบนคลาวด์**
(**Computer Communications and Cloud Computing Principles**)  
ผู้จัดทำ **เมตัส พานิช กายย์** รหัสนิสิต **6710405460**

สคริปต์นี้ตรงกับ `DART_Presentation.pptx` 15 slides และเผื่อเวลาเล็กน้อยเพื่อไม่ให้เกิน 15 นาที

## เตรียมก่อนอัด

1. เปิดกล้องให้เห็นผู้จัดทำอย่างน้อยช่วงเปิดเรื่องและสรุป
2. เปิด PowerPoint ในโหมด Presenter View เพื่ออ่าน speaker notes
3. เตรียม terminal ที่ repository root
4. ทดสอบ `python3 -B demo.py --quiet` ให้ได้ `PASS`
5. ติดตั้ง `wireshark/dart.lua` ตาม `wireshark/README.md`
6. Capture loopback interface และใช้ filter `dart`
7. ตั้งความละเอียดอัดจอให้อ่าน terminal และ Wireshark ได้

## 0:00-0:35 - Slide 1: DART v1

> สวัสดีครับ โปรเจกต์นี้ชื่อ DART หรือ Deadline-Aware Reliable Telemetry เป็น application-layer protocol บน UDP สำหรับข้อมูล sensor แนวคิดหลักคือ ข้อมูลทุกชิ้นไม่ควรเสียค่าใช้จ่ายด้านความน่าเชื่อถือเท่ากัน ข้อมูลปกติให้เบา ค่าล่าสุดให้สด และเหตุสำคัญให้ยืนยันครับ

ให้กล้องเห็นผู้จัดทำในช่วงนี้

## 0:35-1:20 - Slide 2: ปัญหา

> ในระบบเดียวมีข้อมูลสามแบบ อุณหภูมิถูกส่งถี่และยอมให้หายบางส่วนได้ ตำแหน่งเก่าหมดประโยชน์เมื่อมีค่าใหม่ และสัญญาณไฟไหม้ต้องยืนยันพร้อมป้องกันการทำผลข้างเคียงซ้ำภายใน duplicate-cache window ถ้า ACK ทุกอย่างจะช้าและเปลือง แต่ถ้าไม่ ACK เลย critical alert อาจหาย DART จึงแยก delivery policy ตามความหมายของข้อความครับ

## 1:20-2:05 - Slide 3: โปรเจกต์ทำอะไร

> Client เป็น virtual sensor ที่สร้างอุณหภูมิ ตำแหน่ง และเหตุไฟไหม้ด้วย software จากนั้นส่ง UDP datagram จริงไปยัง server จึงไม่ต้องใช้ hardware และไม่ต้องมี GUI จุดประสงค์ของ app คือพิสูจน์ว่า protocol ทำงานจริง รวมถึงหลาย logical connections, timeout, retry และ duplicate handling ครับ

## 2:05-2:55 - Slide 4: ทำไม UDP

> ผมเลือก UDP ไม่ใช่เพราะ UDP ดีกว่า TCP เสมอ แต่เพราะหนึ่ง datagram ตรงกับหนึ่ง DART message และ application สามารถเลือก ACK เฉพาะข้อความที่จำเป็น โดยเฉพาะ critical และ control ได้ ข้อแลกคือ DART ต้องสร้าง CRC, sequence, TTL, timer และ retry เอง และ prototype นี้ยังไม่มี congestion control แบบ production ครับ

## 2:55-3:50 - Slide 5: Delivery classes

> DART มีสาม class BEST-EFFORT BATCH รวม reading หลายค่าเพื่อลด bytes ต่อค่า LATEST ONLY เก็บเฉพาะ sequence ใหม่กว่าเพื่อรักษาความสด และ CRITICAL RELIABLE ใช้ ACK timeout และ retransmission เพื่อยืนยัน โดย server ป้องกันการทำ side effect ซ้ำภายใน duplicate-cache window นี่คือจุดเด่นหลักของ protocol ครับ

## 3:50-4:40 - Slide 6: Wire format

> ทุก packet มี fixed header 40 bytes ใช้ network byte order และจำกัด datagram 1,200 bytes Header แบ่งเป็น identity, session และ ordering, เวลาและผลลัพธ์ และ integrity มี CRC32 ครอบ header กับ payload ข้อมูลถี่ใช้ binary payload ส่วน control และ alert ใช้ compact JSON ครับ

## 4:40-5:25 - Slide 7: Request/response

> การลงทะเบียนใช้ REGISTER REQUEST และ REGISTER RESPONSE 201 REGISTERED เหตุสำคัญตอบ ACK 202 ACCEPTED, 409 DUPLICATE หรือ 408 EXPIRED ส่วน config และ heartbeat ตอบ 200 OK ทุก response echo session, sensor และ sequence และ log จะแสดงทั้ง status code กับ status phrase ตามโจทย์ครับ

## 5:25-6:25 - Slide 8: ACK ไม่มาถึงและ duplicate

> ตัวอย่างนี้ client ส่ง CRITICAL sequence 77 Server process ครั้งแรกและเตรียมตอบ ACK 202 แต่เดโมจงใจระงับ ACK แรกก่อน `sendto()` Client timeout แล้วส่ง sequence เดิมพร้อม RETRANSMISSION Server ใช้ identity จาก session, sensor, type และ sequence จึงรู้ว่าเป็น duplicate ไม่ทำ side effect ซ้ำภายใน cache 60 วินาที และตอบ 409 เพื่อให้ client ยืนยันสำเร็จ กลไกนี้ไม่ใช่ exactly-once guarantee ตลอดกาลครับ

## 6:25-7:05 - Slide 9: หลาย sensor และหลาย thread

> UDP destination port เดียว demultiplex เข้า receiver หนึ่งตัว จากนั้น server ส่งงานเข้า worker pool หลาย thread และแยก state ด้วย session กับ sensor Latest state ยังใส่ session ใน key จึงไม่ปะปนแม้สอง client เลือก sensor ID เดียวกัน Shared state ใช้ lock และ condition ป้องกัน concurrent duplicate ครับ

## 7:05-8:00 — Code walkthrough (ต่อจาก Slide 9)

เปิด code เพียงสามจุดและไม่เลื่อนทั้งไฟล์:

1. `dart/protocol.py`: ชี้ `HEADER_STRUCT`, `DartPacket.encode()` และ
   `decode()` ว่ากำหนด 40-byte header, network byte order, payload length และ
   CRC32 อย่างไร
2. `dart/client.py`: ชี้ `_send_reliable()` ว่าใช้ sequence เดิม รอ ACK ด้วย
   timeout และทำ bounded exponential-backoff retry อย่างไร
3. `dart/server.py`: ชี้ `_process_datagram()` สำหรับ strict validation และ
   `_claim_message()` / `_finish_message()` สำหรับ in-flight duplicate handling
   ที่ commit หลัง process สำเร็จก่อนตอบ ACK

> สามไฟล์นี้แยกหน้าที่ชัดเจนครับ `protocol.py` กำหนด syntax บน wire,
> `client.py` กำหนด timer และ retransmission rule และ `server.py` ตรวจ
> semantics, deadline และ duplicate identity ก่อนเปลี่ยน state จุดที่ผมภูมิใจคือ
> duplicate ที่เข้าพร้อมกันจะไม่ทำ side effect ซ้ำภายใน cache window และถ้า
> worker เจ้าของล้มเหลว ตัวที่รอสามารถรับช่วงได้ครับ

## 8:00-9:35 - Slide 10: Live demo

รัน:

```bash
python3 -B demo.py --sensors 5 --duration 6 --loss-rate 0.10 --seed 42
```

พูดระหว่างชี้ summary:

> เดโมลงทะเบียน virtual sensor ได้ 5 จาก 5 ตัว จงใจระงับ critical ACK แรกก่อน `sendto()` หนึ่งครั้ง Client retransmit และ server พบ duplicate แต่ alert เกิด side effect ครั้งเดียวในการทดลองและภายใน duplicate-cache window เกณฑ์ PASS ตรวจ confirmation, forced ACK suppression, retransmission, duplicate suppression และจำนวน side effect ครบ ไม่ได้ดูเพียงว่ามี packet เข้า server ครับ

ถ้า live demo มีปัญหา ให้เปิด `results/latest_demo.json` ที่เตรียมไว้และอธิบายเงื่อนไขเดียวกัน ห้ามกล่าวว่าเป็นผล live หากไม่ได้รันในคลิป

## 9:35-10:25 — Slide 10: Test variations

ชี้ให้เห็นว่าทดสอบมากกว่า happy path โดยไม่ต้องรันทุกคำสั่งยาวในคลิป:

```bash
# เพิ่ม packet/ACK loss และ delay
python3 -B demo.py --loss-rate 0.20 --ack-loss-rate 0.20 \
  --delay-ms 30 --jitter-ms 10 --seed 42

# ทดสอบ corruption และ CRC rejection
python3 -B -m unittest tests.test_protocol.DartPacketTests.test_crc_rejects_single_bit_payload_corruption -v

# ทดสอบ concurrent duplicate และ strict protocol validation
python3 -B -m unittest tests.test_server_client.ConcurrentDuplicateTests.test_concurrent_duplicate_waits_until_first_processing_succeeds -v
python3 -B -m unittest tests.test_protocol -v
```

> ผมเตรียมทั้ง loss, ACK loss, delay, corruption, malformed packet, expiry และ
> concurrent duplicate ไว้ใน demo กับ test suite ครับ ในคลิปผมรันกรณีหลัก
> แล้วชี้ผลของกรณีอื่นจาก test output เพื่อแสดงว่า protocol ถูกทดสอบทั้ง
> normal flow และ error flow โดยไม่อ้างว่าการจำลองนี้แทน Internet จริงทั้งหมดครับ

## 10:25-11:10 - Slide 11: Wireshark

เปิด Wireshark แล้วชี้ packet ตามลำดับ:

1. `REGISTER_REQ -> 201 REGISTERED`
2. `DATA_BATCH` และ field count/readings
3. `LATEST_UPDATE` และ sequence
4. `CRITICAL_ALERT` sequence เดิมสองครั้ง และ `ACK 409 DUPLICATE`

> Wireshark dissector อ่าน field ของ DART ได้โดยตรง จึงเห็น message type, flags, status และ payload บน wire หลักฐานนี้สำคัญกว่าหน้าตา app เพราะแสดงว่า protocol ทำตามกติกาที่ออกแบบจริงครับ

## 11:10-11:55 - Slide 12: วิธี benchmark

> การเปรียบเทียบใช้ RAW, RELIABLE-ALL และ DART โดย precompute event schedule เดียวกัน ให้ sensor เริ่มด้วย barrier และตรวจ SHA-256 workload fingerprint ให้ตรง วัด delivery, final latest state, critical acceptance, client confirmation, latency และ bytes แยกกัน รายงานเก็บ base seed, seed ของแต่ละ case, repeats และจำนวน latency samples ไว้ตรวจสอบย้อนหลัง Seed เป็น pseudo-random แต่ไม่รับประกัน packet-level drop เดียวกันทุก policy จึงใช้หลาย repeats ครับ

## 11:55-13:00 - Slide 13: ผลที่ 20% loss

> ในผลล่าสุดที่ 20 เปอร์เซ็นต์ loss ชุด 5 sensors คูณ 4 วินาที คูณ 5 repeats และ base seed 100 นั้น RAW ใช้ประมาณ 53.9 attempted bytes ต่อ generated value แต่ไม่มี client confirmation และ server รับ critical ได้ 78.7 เปอร์เซ็นต์ หรือ 59 จาก 75 เหตุการณ์ RELIABLE-ALL ยืนยันได้ 100 เปอร์เซ็นต์แต่ใช้ 97.2 bytes ต่อค่า ส่วน DART ยืนยันได้ 98.7 เปอร์เซ็นต์ หรือ 74 จาก 75 เหตุการณ์ และใช้ 67.3 bytes ต่อค่า ต่ำกว่า reliable ทุกข้อความ โดยยอมให้ normal และ latest บาง update หาย นี่เป็น trade-off ของ workload และ sample ชุดนี้บน loopback ไม่ใช่การชนะทุก metric หรือทุก network ครับ

## 13:00-13:45 - Slide 14: จุดเด่น ความแตกต่าง และข้อจำกัด

> จุดเด่นคือเลือก reliability ต่อ message class, มี latest-state semantics, retry พร้อม commit-safe duplicate handling, ตรวจ wire format ด้วย Wireshark และแยก acceptance จาก confirmation ใน benchmark TCP ให้ reliable ordered byte stream ทั้งสาย จึงเหมาะกว่าเมื่อทุก byte ต้องครบ ส่วน MQTT และ CoAP เป็นมาตรฐานที่สมบูรณ์กว่าและมี QoS หรือ confirmable-message mechanisms ของตนเอง DART ไม่ได้อ้างว่าแทนหรือเร็วกว่า protocol เหล่านั้น จุดที่ต่างในงานนี้คือผมออกแบบและวัด wire format เดียวที่รวม batch, latest-only, deadline และ critical retry เพื่อการศึกษา ข้อจำกัดคือยังไม่มี encryption, authentication, congestion control และ persistent state ครับ

## 13:45-14:15 - Slide 15: สรุป

> สรุปคือ DART ทำให้ reliability เป็นตัวเลือกของข้อความ ปกติให้เบา ล่าสุดให้สด และเหตุสำคัญให้ยืนยัน Source code, one-command demo, benchmark และ Wireshark dissector สามารถทดลองได้ทั้งหมด ขอบคุณครับ ยินดีตอบคำถามครับ

ให้กล้องเห็นผู้จัดทำอีกครั้งในช่วงสรุป

## หลังอัด

- ตรวจความยาวไม่เกิน 15 นาที
- ตรวจว่าเห็นผู้จัดทำในช่วงบรรยายอย่างชัดเจน
- ตรวจว่า terminal/Wireshark อ่านได้และไม่มีข้อมูลส่วนตัวติดจอ
- อัปโหลดวิดีโอไปยังบริการที่ผู้ตรวจเปิดได้ แล้วนำ URL จริงไปแทนสถานะ
  `ยังไม่ครบ` ในตาราง `Submission files` ของ `README.md`
- ทดลองเปิด video link ด้วยหน้าต่าง private/incognito จากนั้น commit และ push
  การแก้ไขขึ้น `main` ก่อนส่ง GitHub URL ให้อาจารย์
