# Project 1 Requirement Checklist

รายวิชา **01418351 — หลักการสื่อสารคอมพิวเตอร์และการประมวลผลบนคลาวด์ (Computer Communications and Cloud Computing Principles)**  
ผู้จัดทำ **เมตัส พานิช กายย์ — 6710405460**

เอกสารนี้เทียบข้อกำหนดจากไฟล์ **Project 1: Socket Programming** กับหลักฐานใน DART โดยแยกสิ่งที่มีใน repository แล้วออกจากงานส่งที่ผู้จัดทำต้องทำเอง

สถานะ: **พร้อม** = มีเนื้อหา/โปรแกรมรองรับแล้ว, **ต้องจัดส่ง** = ต้องจัดรูปแบบ อัด หรือส่งด้วยบัญชีของผู้จัดทำ

## ข้อกำหนดในโจทย์ PDF

| ข้อกำหนด | หลักฐานใน DART | สถานะ/สิ่งที่ต้องทำ |
|---|---|---|
| เสนอ network application และวัตถุประสงค์ | [README](../README.md) และ [PROTOCOL_SPEC](PROTOCOL_SPEC.md) §2: virtual sensor telemetry ไปยัง server | **พร้อม** |
| อธิบายว่าใช้ทำอะไรและมี characteristics อย่างไร | [PROTOCOL_SPEC](PROTOCOL_SPEC.md) §7: batch, latest-only, critical reliable, TTL | **พร้อม** |
| เลือก Transport service model: UDP หรือ TCP พร้อมเหตุผล | [PROTOCOL_SPEC](PROTOCOL_SPEC.md) §3 และ [COURSE_ALIGNMENT](COURSE_ALIGNMENT.md) §2.5 | **พร้อม** |
| ออกแบบและตั้งชื่อ Application-Layer Protocol | DART v1: Deadline-Aware Reliable Telemetry; exact 40-byte wire header ใน [PROTOCOL_SPEC](PROTOCOL_SPEC.md) §4-5 | **พร้อม** |
| กำหนด request/response ระหว่าง Client และ Server | Message types 1-11, payload, status และ flows ใน [PROTOCOL_SPEC](PROTOCOL_SPEC.md) §6-16 | **พร้อม** |
| ส่ง PDF ที่ตอบข้อ 1 โดยละเอียด | [docs.pdf](../docs.pdf) 16 หน้า ครอบคลุมปัญหา transport wire format flow code test benchmark และข้อจำกัด | **พร้อม:** ตรวจข้อมูลวิชา/ผู้จัดทำและไฟล์รอบสุดท้ายก่อนอัปโหลด |
| เขียน client/server ที่ใช้ protocol ที่ออกแบบ | `dart/client.py`, `dart/server.py`, `dart/protocol.py`, `dart/simulator.py` | **พร้อม** |
| พิมพ์ messages, status code และ status phrase ที่ส่ง/รับ | `packet_summary()` และ client/server logs; เดโม ACK `202 ACCEPTED`, `409 DUPLICATE`, ERROR flows | **พร้อม** |
| ส่ง source code | public repository นี้รวม implementation, automated tests, README, docs, Wireshark และ measured results; ใช้ GitHub URL, tagged revision หรือ archive ที่ GitHub สร้างจาก commit ที่ส่ง | **พร้อม:** อัปโหลดตามช่องทางวิชาและบันทึก commit/tag ที่ใช้ส่ง |
| นำเสนอ protocol ด้วย PPT แล้วอธิบาย code | [DART_Presentation.pptx](../submission/DART_Presentation.pptx) 15 slides พร้อม speaker notes และ [VIDEO_SCRIPT](../submission/VIDEO_SCRIPT.md) มี code walkthrough 55 วินาทีที่ชี้ `protocol.py`, `client.py` และ `server.py` | **พร้อม:** ตรวจข้อมูลผู้จัดทำและบรรยายเอง |
| Demo และทดสอบหลายรูปแบบ | `demo.py`, `benchmark.py`, `tests/`, loss/ACK-loss/delay/corruption/expiry/malformed/strict-envelope/concurrent-duplicate cases และ Wireshark; [VIDEO_SCRIPT](../submission/VIDEO_SCRIPT.md) จัดช่วง test variations แยกจาก happy path | **พร้อมสำหรับอัด** |
| วิดีโอมีภาพผู้จัดทำช่วงบรรยายและยาวประมาณไม่เกิน 15 นาที | [DEMO_GUIDE](DEMO_GUIDE.md) กำหนด flow 10-15 นาทีและเตือนเรื่องภาพผู้จัดทำ | **ต้องจัดส่ง:** อัด ตัด ตรวจเวลา และอัปโหลดวิดีโอ |

## ประเด็นเพิ่มจาก TA

| คำแนะนำ TA | สิ่งที่ใช้ตอบ/สาธิต |
|---|---|
| ให้ protocol เป็นหลัก ไม่ให้คะแนนความสวยของ app | simulator ไม่มี GUI และทำหน้าที่สร้าง traffic เพื่อพิสูจน์ wire rules |
| บอกจุดเด่น ต่างจากสิ่งที่มี และนำไปใช้อะไร | [DEMO_GUIDE](DEMO_GUIDE.md) ช่วง 10:30 และ limitations; เทียบขอบเขตกับ TCP/MQTT/CoAP โดยไม่อ้าง global novelty หรือ performance ที่ไม่ได้วัด |
| ถ้าอ้าง performance ต้องบอกว่าดีกว่าอย่างไรและวัดอย่างไร | `benchmark.py`, [results/README](../results/README.md), fixed workload fingerprint, acceptance/confirmation, bytes, P95, final latest state; ระบุ controlled loopback/application-loss scope พร้อม `base_seed`, case/simulation/server seeds, repeats และ sample counts; `--quick` เขียนไฟล์แยกไม่ทับผลที่ใช้นำเสนอ |
| ชู feature ที่ภูมิใจ เช่นหลาย connection/thread | UDP socket เดียว + worker pool หลาย worker และ in-flight duplicate commit ใน `dart/server.py` |
| ใช้ Wireshark อธิบาย protocol | `wireshark/dart.lua`, [Wireshark guide](../wireshark/README.md) และ filters ใน [DEMO_GUIDE](DEMO_GUIDE.md) |

## Final hand-in gate

ก่อนกดส่งต้องมีครบสามชิ้นตาม PDF: **protocol design PDF**, **source code**, และ **วิดีโอประมาณไม่เกิน 15 นาทีที่เห็นผู้จัดทำช่วงบรรยาย** PDF, PPTX, source, demo และผลวัดพร้อมแล้ว งานที่ผู้จัดทำยังต้องทำเองคืออัดวิดีโอโดยให้เห็นผู้จัดทำบางส่วน, อัปโหลด, บันทึก commit/tag ที่ใช้ส่ง และตรวจว่า link เปิดได้จากบัญชีอื่น
