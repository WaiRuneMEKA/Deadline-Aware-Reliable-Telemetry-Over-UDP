# DART video script - ประมาณ 14 นาที

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

> สวัสดีครับ/ค่ะ โปรเจกต์นี้ชื่อ DART หรือ Deadline-Aware Reliable Telemetry เป็น application-layer protocol บน UDP สำหรับข้อมูล sensor แนวคิดหลักคือ ข้อมูลทุกชิ้นไม่ควรเสียค่าใช้จ่ายด้านความน่าเชื่อถือเท่ากัน ข้อมูลปกติให้เบา ค่าล่าสุดให้สด และเหตุสำคัญให้ยืนยันครับ/ค่ะ

ให้กล้องเห็นผู้จัดทำในช่วงนี้

## 0:35-1:20 - Slide 2: ปัญหา

> ในระบบเดียวมีข้อมูลสามแบบ อุณหภูมิถูกส่งถี่และยอมให้หายบางส่วนได้ ตำแหน่งเก่าหมดประโยชน์เมื่อมีค่าใหม่ และสัญญาณไฟไหม้ต้องยืนยันแต่ห้ามประมวลผลซ้ำ ถ้า ACK ทุกอย่างจะช้าและเปลือง แต่ถ้าไม่ ACK เลย critical alert อาจหาย DART จึงแยก delivery policy ตามความหมายของข้อความครับ/ค่ะ

## 1:20-2:05 - Slide 3: โปรเจกต์ทำอะไร

> Client เป็น virtual sensor ที่สร้างอุณหภูมิ ตำแหน่ง และเหตุไฟไหม้ด้วย software จากนั้นส่ง UDP datagram จริงไปยัง server จึงไม่ต้องใช้ hardware และไม่ต้องมี GUI จุดประสงค์ของ app คือพิสูจน์ว่า protocol ทำงานจริง รวมถึงหลาย logical connections, timeout, retry และ duplicate handling ครับ/ค่ะ

## 2:05-2:55 - Slide 4: ทำไม UDP

> ผม/ฉันเลือก UDP ไม่ใช่เพราะ UDP ดีกว่า TCP เสมอ แต่เพราะหนึ่ง datagram ตรงกับหนึ่ง DART message และ application สามารถเลือก ACK เฉพาะ critical กับ control ได้ ข้อแลกคือ DART ต้องสร้าง CRC, sequence, TTL, timer และ retry เอง และ prototype นี้ยังไม่มี congestion control แบบ production ครับ/ค่ะ

## 2:55-3:50 - Slide 5: Delivery classes

> DART มีสาม class BEST-EFFORT BATCH รวม reading หลายค่าเพื่อลด bytes ต่อค่า LATEST ONLY เก็บเฉพาะ sequence ใหม่กว่าเพื่อรักษาความสด และ CRITICAL RELIABLE ใช้ ACK timeout และ retransmission เพื่อยืนยัน แต่ server ต้องทำ side effect เพียงครั้งเดียว นี่คือจุดเด่นหลักของ protocol ครับ/ค่ะ

## 3:50-4:40 - Slide 6: Wire format

> ทุก packet มี fixed header 40 bytes ใช้ network byte order และจำกัด datagram 1,200 bytes Header แบ่งเป็น identity, session และ ordering, เวลาและผลลัพธ์ และ integrity มี CRC32 ครอบ header กับ payload ข้อมูลถี่ใช้ binary payload ส่วน control และ alert ใช้ compact JSON ครับ/ค่ะ

## 4:40-5:25 - Slide 7: Request/response

> การลงทะเบียนใช้ REGISTER REQUEST และ REGISTER RESPONSE 201 REGISTERED เหตุสำคัญตอบ ACK 202 ACCEPTED, 409 DUPLICATE หรือ 408 EXPIRED ส่วน config และ heartbeat ตอบ 200 OK ทุก response echo session, sensor และ sequence และ log จะแสดงทั้ง status code กับ status phrase ตามโจทย์ครับ/ค่ะ

## 5:25-6:25 - Slide 8: ACK หายและ duplicate

> ตัวอย่างนี้ client ส่ง CRITICAL sequence 77 Server process ครั้งแรกและตอบ ACK 202 แต่เดโมจงใจทำ ACK หาย Client timeout แล้วส่ง sequence เดิมพร้อม RETRANSMISSION Server ใช้ identity จาก session, sensor, type และ sequence จึงรู้ว่าเป็น duplicate ไม่ทำ side effect ซ้ำ และตอบ 409 เพื่อให้ client ยืนยันสำเร็จครับ/ค่ะ

## 6:25-7:05 - Slide 9: หลาย sensor และหลาย thread

> UDP destination port เดียว demultiplex เข้า receiver หนึ่งตัว จากนั้น server ส่งงานเข้า worker pool หลาย thread และแยก state ด้วย session กับ sensor Latest state ยังใส่ session ใน key จึงไม่ปะปนแม้สอง client เลือก sensor ID เดียวกัน Shared state ใช้ lock และ condition ป้องกัน concurrent duplicate ครับ/ค่ะ

## 7:05-9:05 - Slide 10: Live demo

รัน:

```bash
python3 -B demo.py --sensors 5 --duration 6 --loss-rate 0.10 --seed 42
```

พูดระหว่างชี้ summary:

> เดโมลงทะเบียน virtual sensor ได้ 5 จาก 5 ตัว จงใจทำ critical ACK หายหนึ่งครั้ง Client retransmit และ server พบ duplicate แต่ critical ถูก process เพียงครั้งเดียว เกณฑ์ PASS ตรวจ confirmation, exactly-once, forced ACK drop, retransmission และ duplicate suppression ครบ ไม่ได้ดูเพียงว่ามี packet เข้า server ครับ/ค่ะ

ถ้า live demo มีปัญหา ให้เปิด `results/latest_demo.json` ที่เตรียมไว้และอธิบายเงื่อนไขเดียวกัน ห้ามกล่าวว่าเป็นผล live หากไม่ได้รันในคลิป

## 9:05-10:05 - Slide 11: Wireshark

เปิด Wireshark แล้วชี้ packet ตามลำดับ:

1. `REGISTER_REQ -> 201 REGISTERED`
2. `DATA_BATCH` และ field count/readings
3. `LATEST_UPDATE` และ sequence
4. `CRITICAL_ALERT` sequence เดิมสองครั้ง และ `ACK 409 DUPLICATE`

> Wireshark dissector อ่าน field ของ DART ได้โดยตรง จึงเห็น message type, flags, status และ payload บน wire หลักฐานนี้สำคัญกว่าหน้าตา app เพราะแสดงว่า protocol ทำตามกติกาที่ออกแบบจริงครับ/ค่ะ

## 10:05-10:55 - Slide 12: วิธี benchmark

> การเปรียบเทียบใช้ RAW, RELIABLE-ALL และ DART โดย precompute event schedule เดียวกัน ให้ sensor เริ่มด้วย barrier และตรวจ SHA-256 workload fingerprint ให้ตรง วัด delivery, final latest state, critical acceptance, client confirmation, latency และ bytes แยกกัน Seed เป็น pseudo-random แต่ไม่รับประกัน packet-level drop เดียวกันทุก thread จึงใช้หลาย repeats ครับ/ค่ะ

## 10:55-12:05 - Slide 13: ผลที่ 20% loss

> ในผลล่าสุดที่ 20 เปอร์เซ็นต์ loss RAW ใช้ประมาณ 64.6 attempted bytes ต่อ generated value แต่ไม่มี client confirmation และ server รับ critical ได้ประมาณ 83 เปอร์เซ็นต์ RELIABLE-ALL ยืนยันได้ 100 เปอร์เซ็นต์แต่ใช้ 119.7 bytes ต่อค่า DART ยืนยัน critical ได้ 100 เปอร์เซ็นต์และใช้ 95.4 bytes ต่อค่า ต่ำกว่า reliable ทุกข้อความ โดยยอมให้ normal และ latest บาง update หาย นี่เป็น trade-off ภายใต้ method นี้ ไม่ใช่การชนะทุก metric ครับ/ค่ะ

## 12:05-13:05 - Slide 14: จุดเด่นและข้อจำกัด

> จุดเด่นคือเลือก reliability ต่อ message class, มี latest-state semantics, retry พร้อม commit-safe duplicate handling, ตรวจ wire format ด้วย Wireshark และแยก acceptance จาก confirmation ใน benchmark ข้อจำกัดคือยังไม่มี encryption, authentication, congestion control, persistent state และการทดสอบนี้เป็น loopback prototype เพื่อการศึกษา ไม่ใช่มาตรฐาน Internet ครับ/ค่ะ

## 13:05-13:35 - Slide 15: สรุป

> สรุปคือ DART ทำให้ reliability เป็นตัวเลือกของข้อความ ปกติให้เบา ล่าสุดให้สด และเหตุสำคัญให้ยืนยัน Source code, one-command demo, benchmark และ Wireshark dissector สามารถทดลองได้ทั้งหมด ขอบคุณครับ/ค่ะ ยินดีตอบคำถามครับ/ค่ะ

ให้กล้องเห็นผู้จัดทำอีกครั้งในช่วงสรุป

## หลังอัด

- ตรวจความยาวไม่เกิน 15 นาที
- ตรวจว่าเห็นผู้จัดทำในช่วงบรรยายอย่างชัดเจน
- ตรวจว่า terminal/Wireshark อ่านได้และไม่มีข้อมูลส่วนตัวติดจอ
- อัปโหลด PDF, source code และวิดีโอตามช่องทางที่รายวิชากำหนด
- ทดลองเปิด video link ด้วยหน้าต่าง private/incognito ก่อนส่ง
