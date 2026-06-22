# มาตรฐานภาษาไทย — Thai Prose Language Standard

**Version:** 1.0 — Kanban #969, 2026-06-22
**Scope:** Language-level standard consumed by the novel team and content team.
**Humans-only write zone** (`context/standards/`). Propose changes via Kanban; do not auto-edit.

---

## วัตถุประสงค์

มาตรฐานนี้รวบรวมรูปแบบที่ทำให้ภาษาไทยอ่านแล้ว "เหมือนแปลมาจากภาษาอังกฤษ" แม้ผู้เขียนจะไม่ได้แปล — ปัญหาที่พบซ้ำใน AI-drafted Thai prose ทุกประเภท ตั้งแต่บทนิยาย บทความ Content Team สารบัญเลขาฯ ไปจนถึง marketing copy ใดก็ตามที่เขียนด้วย AI หรือผ่านกระบวนการที่ AI เกี่ยวข้อง กรอบ 17 หมวดนี้คือ checklist สำหรับ `thai-proofreader` ในขั้นตอน final language pass ก่อน Lead integrate — และเป็น reference สำหรับ writer/editor ที่ต้องการ self-audit ตั้งแต่ต้น

---

## หมวดที่ 1 — Nominalization translatese

**หลักการ:** "เป็นการ[verb/noun]ที่..." และ "การ[verb]ที่..." ที่ซ้อนกัน (stacked) คือสัญญาณของ English nominal structure ใต้น้ำ ภาษาไทยธรรมชาติใช้กริยาตรงๆ

| ❌ ต้นฉบับ | ✅ แก้ไข |
|---|---|
| "เป็นการแตะที่อยู่ในระหว่าง gesture กับไม่ได้ทำ" | "แตะแบบไม่ได้ตั้งใจจะแตะ" |

**Test:** นับ "เป็นการ..." ในบท — มากกว่า 5 ครั้ง = ต้อง re-pass หมวดนี้

---

## หมวดที่ 2 — English syntax shadow

**หลักการ:** โครงสร้าง "ใน[noun]" แบบ English literal calque, SVO inversion ที่ฟัง English, และ possessive chain ที่ยาวเกินธรรมชาติไทย

| ❌ ต้นฉบับ | ✅ แก้ไข |
|---|---|
| "ในเหงื่อบางๆ ที่ขมับ เขาได้กลิ่น..." | "กลิ่นเหงื่อบางๆ ที่ขมับ — เขาได้กลิ่น..." |
| "หกหมื่นเสียงที่กลายเป็นเสียงเดียวที่ไม่มีคำพูด" | "หกหมื่นเสียงรวมเป็นเสียงเดียว ไร้คำพูด" |

**Test:** อ่าน "ใน[noun]" แต่ละจุด — ถ้าสามารถ rephrase เป็น "noun + verb" ได้ = calque, flag

---

## หมวดที่ 3 — Awkward adverb placement

**หลักการ:** Adverb วางในตำแหน่ง English แทนที่ภาษาไทยธรรมชาติ หรือ stack qualifier ซ้อนกันโดยไม่มี rhythm

| ❌ ต้นฉบับ | ✅ แก้ไข |
|---|---|
| "เขาเดินเบามากและช้ามากผ่านประตู" | "เขาย่างเท้าเบาผ่านประตู" |

**Test:** อ่านออกเสียงในใจ — ถ้า adverb ทำให้จังหวะสะดุด = ตำแหน่งผิด

---

## หมวดที่ 4 — Non-native idiom / literal translation

**หลักการ:** สำนวน English แปลตรงทีละคำ (word-by-word) ซึ่งไม่มีใน Thai หรือ compound metaphor ที่ไม่มีที่มาในสำนวนไทย

| ❌ ต้นฉบับ | ✅ แก้ไข |
|---|---|
| "ม้วนตัวก้อนหิมะ" (snowball effect calque) | "ยิ่งขยายยิ่งใหญ่ขึ้น" / ใช้สำนวนไทยที่เทียบเคียงได้ |

**Test:** ถาม "สำนวนนี้มีคนไทยพูดจริงๆ ไหม?" ถ้าตอบไม่แน่ใจ → flag

---

## หมวดที่ 5 — Code-switching ที่ไม่ flow

**หลักการ:** คำ English ที่แทรกกลางประโยคไทยแล้วทำลาย rhythm — แต่ต้องแยกให้ออกระหว่าง calque (ผิด) กับ domain loan word ที่ผู้ปฏิบัติไทยพูดจริง (ถูก)

| ❌ ต้นฉบับ (ไม่ flow) | ✅ แก้ไข |
|---|---|
| "เขา implement ระบบแบบ fully automated" | "เขา implement ระบบโดยอัตโนมัติ" |
| ❌ บังคับแปล | ✅ loan คงไว้ถูกต้อง |
| "เขา deploy ระบบ" (นักพัฒนาพูดจริง) | คง "deploy" ไว้ — ถูกต้องตาม domain register |

**Test:** ถามว่า "ผู้ปฏิบัติงาน domain นี้ใช้คำ English นี้จริงหรือเปล่า?" ถ้าใช่ = loan word ที่ valid; ถ้าไม่ = translatese

---

## หมวดที่ 6 — Verb-noun mismatch in Thai

**หลักการ:** กริยาที่รับ object ผิด class ในภาษาไทย หรือใช้ causative ผิดบริบท

| ❌ ต้นฉบับ | ✅ แก้ไข |
|---|---|
| "ฟังเหมือนเขาไม่พอใจ" (when Thai would use รู้สึก/ดู) | "ดูเหมือนเขาไม่พอใจ" / "รู้สึกว่าเขาไม่พอใจ" |

**Test:** verb + object ออกเสียงในใจ แล้วถามว่า "native Thai speaker จะพูดประโยคนี้ไหม?"

---

## หมวดที่ 7 — Rhythm breaks

**หลักการ:** ประโยคหยุดผิดจังหวะ, conjunction อยู่ผิดตำแหน่งตาม English syntax, หรือใช้ em-dash/colon มากเกินจนไม่เป็น Thai prose

| ❌ ต้นฉบับ | ✅ แก้ไข |
|---|---|
| "เขาหยุด — แล้วก็มองออกไปนอกหน้าต่าง" (em-dash overuse) | "เขาหยุด มองออกไปนอกหน้าต่าง" |

**Test:** อ่านทั้งย่อหน้าออกเสียงในใจ ถ้าสะดุดมากกว่า 2 จุดต่อย่อหน้า = rhythm ผิด

---

## หมวดที่ 8 — Incomplete noun phrases (lonely classifier / ambiguous head noun)

**หมวดวิกฤต — ภาษาไทยต้องการ explicit complement ที่ภาษาอังกฤษปล่อยให้ standalone ได้**

| ❌ ต้นฉบับ | ✅ แก้ไข | เหตุผล |
|---|---|---|
| "เสียงของฝูง" | "เสียงฝูงคน" / "เสียงคนหมู่มาก" | ฝูงอะไร? ต้องบอก |
| "คงเพราะคู่นี้" | "คงเพราะคู่แข่งคืนนี้" | คู่อะไร? ต้องระบุ |

**กฎ:** ตรวจทุก collective noun (ฝูง/คู่/ทีม/กลุ่ม/พวก) ที่ standalone ถ้า English เขียน "the crowd" / "the pair" / "the herd" ได้โดยไม่บอกว่ากลุ่มของอะไร → Thai มักต้องการคำเสริม

---

## หมวดที่ 9 — Collocation errors (verb/adjective + noun mismatch)

**หมวดที่ยากที่สุด — ต้องใช้หูภาษาไทยที่ calibrated แล้ว Thai มี collocation strict; English literal translation ทำลาย pattern เหล่านี้**

| ❌ ต้นฉบับ | ✅ แก้ไข | เหตุผล |
|---|---|---|
| "น้ำที่กิน...ไม่ลึกพอ" | "น้ำที่ดื่มตอน warm-up ไม่พอ" | "ลึก" (deep) ไม่ collocate กับ "ดื่มน้ำ" ในภาษาไทย — calque จาก "drink deeply" |
| "ต้นแถว" (หมายถึง "head of the line") | "หัวแถว" | "ต้น" = ต้นไม้/ต้นกำเนิด (tree / origin classifier); "head of line" ในภาษาไทยคือ "หัวแถว" ไม่ใช่ "ต้นแถว" — calque จาก English "head of the line" |

**Collocation offenders ที่พบบ่อย:**
- English intensity adjectives ("ลึก", "หนัก", "เบา", "หลวม") + abstract noun ที่ไม่ collocate ในภาษาไทย
- Verbs of perception ("ฟัง", "รู้สึก", "เห็น", "ดู") + abstract object ที่ English-natural แต่ Thai-awkward
- "ของ" possessive overused ที่ภาษาไทยธรรมชาติใช้โครงสร้างอื่น

**Test:** อ่าน verb+object ออกเสียงในใจ ถาม: "native Thai speaker จะพูดประโยคนี้ใน casual prose ไหม หรือฟังเหมือนแปล?" ถ้า translation-feel → flag

---

## หมวดที่ 10 — Domain register mismatch

**Triggered when:** prose อยู่ใน domain เฉพาะทาง (กีฬา/การแพทย์/ทหาร/ศาล/การศึกษา/ศาสนา/food service/software/การเงิน ฯลฯ)
**Failure mode:** agent แปล English domain terms ทีละคำแทนที่จะใช้ register ที่ผู้ปฏิบัติ/แฟน/ผู้ติดตามไทยพูดจริง

**ตัวอย่างจาก football register:**

| ❌ ต้นฉบับ | ✅ Register ฟุตบอลจริง | เหตุผล |
|---|---|---|
| "สิบเอ็ดวิ่งเข้าไปเอาบอล" | "เบอร์สิบเอ็ดวิ่งเข้าไปเอาบอล" | หมายเลขเสื้อต้องมี "เบอร์" นำหน้าใน casual register |
| "หมายเลข 7" | "เบอร์ 7" | "หมายเลข" = ทางการ/ทะเบียน; "เบอร์" = casual |
| "ยิงลง" | "ยิงเข้า" / "ทำประตู" | "ยิงลง" = shoot down; goal = "ยิงเข้า" |
| "save save" | "ปัด" / "จับ" / "ตี" | Thai goalkeeper language |

**Domains อื่นๆ (หลักการทั่วไป):**
- **การแพทย์:** staff ใช้ register ที่ต่างจากตำรา
- **ทหาร:** ยศ/อุปกรณ์/การฝึก มีคำเฉพาะไทย
- **วัด/ศาสนา:** วัด/พระ/เณร/อุโบสถ — ห้ามแปลจาก English
- **โรงเรียน:** "ป.X" / "ม.X" — ไม่ใช่ "Grade X"
- **Software/tech:** นักพัฒนาไทยพูด "deploy", "merge", "PR" — partial English loan คือ register ที่ถูกต้อง
- **การเงิน:** ใช้ English loans + Thai compounds ผสม — ต้อง check register จริง

**กฎ:** เมื่อ prose เข้า domain ใดก็ตาม ถาม: "ผู้ปฏิบัติงานไทยใน domain นี้พูดว่าอะไร?" — ไม่ใช่ "ภาษาอังกฤษพูดว่าอะไร แล้วแปลเป็นไทย?"

---

## หมวดที่ 11 — Sensory hallucination / world-context drift

**Triggered when:** writer ประดิษฐ์ sensory detail ที่ละเมิดบริบทฉากจริง
**Failure mode:** model เลือก "evocative" sensory imagery จาก training data โดยไม่ตรวจว่า detail นั้นมีอยู่จริงในฉากนี้ — มักดึง rural Thai cliché (กลิ่นกล้วย/ข้าวหมัก/ควันหอม) แม้ฉากจะเป็น urban/professional/sterile

**ตัวอย่างจาก source:**

| ❌ ต้นฉบับ | ✅ แก้ไข | เหตุผล |
|---|---|---|
| "กลิ่นยางต้นกล้วยที่ติดมาจากการ tackle ใน warm-up" | "กลิ่นหญ้าที่ติดมือมาตอน tackle" | สนามฟุตบอลไม่มีต้นกล้วย |

**ตัวอย่างเพิ่มเติม (brief-specified):**
สนามฟุตบอลกลางคืนที่มีไฟสปอตไลต์ส่อง — ใต้แสงไฟ หญ้าอ่านเป็น **สีเขียวสดชัด** ไม่ใช่มืด writer ที่ต้องการเขียน noir/dark mood ต้องไม่อาศัยความมืดของสนามเป็น default — ฉากนั้นสว่าง หากต้องการ noir mood ต้องหา noir source อื่นในฉาก (เงา/จุดตาบอดของแสง/ความเปล่าเปลี่ยวของสถานที่ แม้มันจะสว่าง)

> Cross-reference: สำหรับ option "earn-the-noir" (การบรรยาย mood มืดโดยไม่บิดเบือน sensory fact) — ดู `writing/truth-spec.md` §5 (subjective-perception: cue-first rule)

**Test:** ถาม sensory detail แต่ละจุด: "สิ่งนี้มีอยู่จริงในฉากนี้ไหม?" ถ้าต้อง invent context ใหม่เพื่อให้ detail นั้น valid → ตัดหรือเปลี่ยน

---

## หมวดที่ 12 — Verb-noun agency order (ไวยากรณ์ไทยพื้นฐาน)

**Triggered when:** writer วาง noun ผิดด้านของ verb ทำให้สับสนว่าใครเป็นผู้กระทำและใครถูกกระทำ

**กฎพื้นฐาน:**
- **VERB + NOUN** = noun ถูกกระทำ (passive sense)
- **NOUN + VERB** = noun เป็นผู้กระทำ (active sense)

| ❌ ต้นฉบับ | ✅ แก้ไข | เหตุผล |
|---|---|---|
| "เฉี่ยวนิ้ว" | "นิ้วเฉี่ยว" | "เฉี่ยวนิ้ว" = "graze [the] finger" (นิ้วถูกเฉี่ยว); "นิ้วเฉี่ยว" = "finger grazes" (นิ้วเป็นผู้กระทำ) |
| "ตรงที่เขาเฉี่ยวนิ้วไว้" | "ตรงที่นิ้วเขาเฉี่ยวไว้" | ต้องการความหมาย "where his finger had grazed" |

**Offenders บ่อย:** ส่วนร่างกายในฐานะ agent (นิ้ว/มือ/ตา/เท้า/ขา/ปาก), สิ่งไม่มีชีวิตที่กระทำ (ลม/แสง/เสียง), อาวุธ/เครื่องมือ (มีด/ปืน/ดาบ) — เมื่อ noun เหล่านี้เป็นผู้กระทำ noun ต้องมาก่อน verb

**Test:** อ่าน verb+noun pair ออกเสียงในใจ ถาม: "noun ทำ หรือ noun ถูกทำ?" ถ้า "ทำ" → noun ต้องมาก่อน verb

---

## หมวดที่ 13 — Classifier mismatch (ลักษณนาม)

**Triggered when:** writer นำ classifier จาก feature หนึ่งมาใช้กับ entity อีก level หนึ่ง

**ตัวอย่างจาก source (สุนัข):**

| ❌ ต้นฉบับ | ✅ แก้ไข | เหตุผล |
|---|---|---|
| "เสียงคำรามมาจากคู่กลาง" | "เสียงคำรามมาจากตัวกลาง" | "คู่" ถูก carry over มาจากการนับ "คู่ตา" แต่พอ refer ถึงตัวสุนัขทั้งตัว ต้องใช้ "ตัว" |
| "คู่ขวา/คู่ซ้าย" (สุนัข) | "ตัวขวา/ตัวซ้าย" | สุนัข = "ตัว"; "คู่" = สำหรับสิ่งที่มาเป็นคู่โดยธรรมชาติ (ตา/ถุงมือ/รองเท้า) |

**Classifier reference:**

| Classifier | ใช้กับ |
|---|---|
| **ตัว** | สัตว์, ปลา, ตุ๊กตา, ตัวละคร, ตัวอักษร, เสื้อผ้าบางประเภท |
| **คน** | มนุษย์ |
| **คู่** | สิ่งที่มาเป็นคู่โดยธรรมชาติ (ตา/หู/รองเท้า/ถุงมือ/แฝด) |
| **เม็ด** | สิ่งเล็กกลม (เม็ดยา/เมล็ด/ลูกปัด) |
| **ดอก** | ดอกไม้, กุญแจ, ดอกไฟ |
| **ลูก** | ลูกบอล, ผลไม้บางชนิด, ลูกคลื่น |
| **คัน** | ยานพาหนะ, คันเบ็ด, ร่ม |
| **หลัง** | บ้าน, มุ้ง |
| **ใบ** | ใบไม้, กระดาษ, หมวก, กระเป๋า, แว่นตา |

**กฎ:** ทุกครั้งที่ reference เปลี่ยน granularity (ตา → สุนัข, ล้อ → รถ, กลีบดอก → ดอกไม้) — ต้องเปลี่ยน classifier ด้วย

---

## หมวดที่ 14 — Inanimate agency / improper personification (กรอบ 3 ระดับ)

**Triggered when:** writer ใส่ verb กับ inanimate subject โดยไม่ตรวจ agency tier ของ verb นั้นในภาษาไทย

**กรอบ 3 ระดับ:**

### Tier 1 — Process verbs (low agency, intrinsic physical action)
ใช้กับ inanimate subject ได้เสมอ

| Verb | ความหมาย | ตัวอย่าง canonical |
|---|---|---|
| ไหล | flow | น้ำไหล, เลือดไหล |
| พัด | blow | ลมพัด |
| ส่อง | shine | แสงส่อง, ไฟส่อง |
| ตก | fall | ฝนตก, ใบไม้ตก |
| ไหม้ | burn | ไฟไหม้ |
| ละลาย | melt | น้ำแข็งละลาย |
| หยด | drip | น้ำหยด, เลือดหยด |
| กลิ้ง | roll | หินกลิ้ง |
| ถล่ม | collapse | ดินถล่ม |

### Tier 2 — Metaphor-lexicalized verbs (established literary precedent)
ใช้ใน literary/narrative register ได้ อาจหนักเกินไปใน journalistic/neutral register

| Verb | ความหมาย | Inanimate-subject literary use |
|---|---|---|
| กลืน | swallow → engulf | ป่ากลืน, ความเงียบกลืน, ความมืดกลืน, เวลากลืน |
| ดูด | suck → absorb | โคลนดูด, น้ำวนดูด, หล่มดูด |
| กิน | eat → consume | เวลากิน, ความสงสัยกิน(ใจ) |
| คืบคลาน | creep → encompass | ความมืดคืบคลาน, เงาคืบคลาน, ความกลัวคืบคลาน |
| ครอบ | cover | ความมืดครอบ, เงาครอบ |
| ดา | rush down | น้ำดา, ฝนดา |
| พา | lead → carry along | ลมพา, น้ำพา, ความฝันพา |
| หอบ | carry away | ลมหอบ, น้ำหอบ |

### Tier 3 — Intent verbs (deliberate will required)
**ห้ามใช้กับ inanimate subject เด็ดขาด**

| Verb | ความหมาย | เหตุผลที่ผิดกับ inanimate |
|---|---|---|
| ปล่อย | release | ต้องการการตัดสินใจปล่อยโดยเจตนา |
| ตอบสนอง | respond | ต้องการ perception + การตอบกลับ |
| ตัดสิน(ใจ) | decide | การเลือกโดยสำนึก |
| บงการ | command | อำนาจเหนือผู้อื่น |
| ยอม | consent | การยอมรับโดยสำนึก |
| ขว้าง | throw | การเคลื่อนมือโดยเจตนา |
| เลือก | choose | การเลือกโดยสำนึก |
| สู้ | fight | เจตนาเป็นปฏิปักษ์ |
| ตั้งใจ | intend | โดยนิยามคือเจตนา |
| คิด | think | cognition |

**ตัวอย่าง Tier 3 violations + fixes:**

| ❌ ต้นฉบับ | ✅ แก้ไข |
|---|---|
| "โคลนปล่อยเขา" | "หลุดออกจากโคลน" (flip subject เป็น human) |
| "โคลนตอบสนองช้า" | "ดิ้นได้ช้า โคลนข้นเกินไป" (describe property ของโคลน) |
| "หินขว้างเข้ามา" | "หินถูกขว้างเข้ามา" / "เขาขว้างหิน" |

**Compound-noun exception:** บาง "inanimate + verb" combos เป็น compound noun ที่ established แล้วในภาษาไทย:
- **โคลนดูด / ทรายดูด / หล่มดูด** = quicksand (noun) — ใช้เป็น noun ได้, ไม่ต้อง tier check
- **น้ำเชี่ยว** = rapids (noun)
- **ลมหวน** = whirlwind (noun)

เมื่อใช้เป็น noun = ไม่ต้อง tier check; เมื่อ extend เป็น subject+verb → tier framework applies

**หลักการสำคัญ:** tier ของ verb กำหนดจาก Thai lexical history ไม่ใช่จาก English translation verb เดียวกันในภาษาอังกฤษอาจ map ไปคนละ tier ใน Thai

**Test สำหรับ verb ที่ไม่แน่ใจ:**
1. ค้นหา verb นั้นกับ inanimate subject ใน Thai literature/classical prose/contemporary writing
2. มี precedent ชัด (หลายคนเขียน ไม่ใช่แค่ครั้งเดียว) → Tier 2 OK ใน literary register
3. ไม่มี precedent ชัด → default Tier 3 (reject; recast)
4. สงสัย → flag ให้ Lead: "Verify Thai lexical precedent for verb X"

---

## หมวดที่ 15 — English noun-phrase calque สำหรับ ACTION

**Triggered when:** writer แปลง English noun phrase ("a deep breath", "a slow look") มาเป็น Thai noun+adjective ทั้งๆ ที่ภาษาไทยควรใช้ verb+adverb

**Pattern:**
- English: "[adjective] + [action-noun]"
- ❌ Thai calque: "[action-noun] + [adjective]"
- ✅ Thai natural: "[action-verb] + [adverb]"

| ❌ ต้นฉบับ | ✅ แก้ไข |
|---|---|
| "ลมหายใจยังไม่ลึก" | "เขายังหายใจได้ไม่ลึก" / "หายใจไม่ทั่วท้อง" |
| "การมองที่ช้า" | "มองช้า" |
| "การก้าวที่ยาว" | "ก้าวยาว" |
| "ความคิดที่เร็ว" | "คิดเร็ว" |

**เมื่อ noun+adjective เป็น Thai ที่ถูกต้อง:**
- "เสียงเขาดัง" — voice คือ quality/state ไม่ใช่ action
- "ลมหายใจสั้นและเร็ว" — short/fast describe breath เป็น thing ได้ — OK
- "เสียงต่ำ" — quality, OK

**Test:** noun ใน phrase แทน ACTION (หายใจ/มอง/คิด/เดิน) → suspect calque → recast เป็น verb+adverb; แทน QUALITY/STATE (เสียง/สี/ความสูง) → noun+adjective OK

---

## หมวดที่ 16 — Voice-register mismatch with speaker/POV spec (3-way structured check)

**Triggered when:** text ที่ attribute ให้ speaker/character (พูด/คิด/กระทำ) ไม่ match register ที่ spec ของ speaker นั้นกำหนดไว้

**Sub-checks เมื่อ speaker spec มี 3-way voice split + Output Budget:**

- **B1 — Speech (dialogue / quoted lines):** ความยาวตรง `output_budget.dialogue_per_turn`? Register ตรง B1 spec?
- **B2 — Thought (interior monologue):** Italics-block frequency ตรง `output_budget.thought_per_scene`? Register ตรง B2 spec?
- **B3 — Action (prose describing movement/gesture/posture):** ตรง B3 register? ไม่ elaborate เกินสำหรับ action เบาๆ?

**ตัวอย่าง:**

| ❌ ต้นฉบับ | ✅ แก้ไข | เหตุผล |
|---|---|---|
| บุญล้อม (spec: "พูดน้อย / สั้น 1-2 บรรทัด") พูด: "ที่นี่ไม่ใช่ที่ปลอดภัย เดินไปไกลกว่านี้ก่อน" | "ไม่ปลอดภัย" / "เดินต่อ" | ยาวเกินสำหรับคน spec ว่าพูดน้อย |

**กฎ:** ดู speaker spec ก่อน flag ทุกครั้ง cross-reference กับ sub-budget (B1/B2/B3) ที่ apply อย่า flag line ที่ match budget+register แม้จะ "feel underwritten" ตามมาตรฐาน prose ทั่วไป

**Pre-retrofit fallback:** ถ้า speaker spec ยังไม่มี 3-way split → ใช้ single-register check (dialogue length+register vs spec overall) และ flag ให้ Lead ว่า spec ควร retrofit

---

## หมวดที่ 17 — Descriptive granularity calibration

**Triggered when:** writer ใช้ precision มากเกินสำหรับ moment ที่ความประทับใจทั่วๆ ไปพอแล้ว หรือน้อยเกินสำหรับ moment ที่ต้องการ precision จริงๆ

| ❌ ต้นฉบับ | ✅ แก้ไข | เหตุผล |
|---|---|---|
| "มองรอบทั้งสามร้อยหกสิบองศา" (passing scan) | "มองไปรอบๆ" | over-precision สำหรับ transition beat |

**กฎ:** ใน narrative prose ให้ default = light description เพิ่ม precision เฉพาะเมื่อ:
- ผู้อ่านต้องการ exact image เพื่อติดตาม tactics/visualization ของฉาก
- POV ของตัวละครนั้น justify precision (นักกีฬาอ่าน position/ทหารนับภัยคุกคาม)
- Stakes ขึ้นอยู่กับ precision (3 คนศัตรู vs 5 คน = ผลต่าง)

**Test:** ถ้าเปลี่ยนเป็น loose Thai แล้วฉากยังทำงานได้ครบ → ใช้ loose; ถ้า loss of precision เสีย context → keep precise

---

## NOTE — หมวด 8 + 9 ต้องการ pass แยก

AI-drafted Thai prose ล้มเหลวหมวด 8 + 9 บ่อยที่สุด หมวด 1–7 เป็น structural มองเห็นได้ง่ายกว่า หมวด 8 + 9 ต้องการ deliberate ear-tuning อ่านทีละประโยค:

1. ทุก collective noun มี explicit complement ไหม?
2. ทุก verb+object pair — collocation นี้มีใน natural Thai ไหม?

ถ้าไม่แน่ใจข้อใดข้อหนึ่ง → flag ให้ native reviewer

---

## กรอบ 3 ระดับ verb agency — บทสรุป (Quick Reference)

| ระดับ | ประเภท | หลักการ | ตัวอย่างกริยา |
|---|---|---|---|
| **Tier 1** | Process verbs | ใช้กับ inanimate subject ได้เสมอ | ไหล / พัด / ส่อง / ตก / ไหม้ / ละลาย / กลิ้ง |
| **Tier 2** | Metaphor-lexicalized | OK ใน literary register — มี precedent ในวรรณกรรมไทย | กลืน / ดูด / กิน / คืบคลาน / พา / หอบ / ครอบ |
| **Tier 3** | Intent verbs | ห้ามใช้กับ inanimate subject — ต้องการเจตนา | ปล่อย / ตอบสนอง / ตัดสิน / ยอม / เลือก / คิด |

**กฎ:** tier ของ verb กำหนดจาก Thai lexical history ไม่ใช่จาก English translation — verb เดียวกันใน English อาจ map ไปคนละ tier ใน Thai

---

## วิธีใช้มาตรฐานนี้ใน pipeline

```
writer (drafts)
    ↓
editor (line/structural)
    ↓
thai-proofreader (final language pass — flag 17 categories, propose rewrites)
    ↓
Lead integrates → DONE
```

`thai-proofreader` = `.claude/agents/thai-proofreader.md` — spawn เป็น last language pass ก่อน Lead integrate ใช้ได้กับ novel chapter, content post, secretary doc, marketing copy — Thai prose ทุกประเภท

---

*Draft สร้างจาก: `Writing/shared/feedback_thai_translatese_patterns.md` + `.claude/agents/thai-proofreader.md` — Kanban #969, 2026-06-22*