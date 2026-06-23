# Writing Standard: truth_spec

**Status:** v1.0 (Kanban #970, 2026-06-22)
**Authored:** 2026-06-22 (Kanban #970)
**Intended destination:** `context/standards/writing/truth-spec.md`
**Sibling standard:** `context/standards/languages/thai-prose.md` (Category 11) — see §8.

---

## 1. Core idea: the fantasy↔reality spectrum

Writing is not a binary of "fiction" vs "non-fiction." Every piece sits somewhere on a continuous spectrum from pure invention to verifiable fact. Even the most fabulist novel contains elements that **must behave like the real world** — sensory physics, grammar that reads as natural, period-appropriate detail. Even a technical document may contain an _opinion_ section that must be labeled as such.

The `truth_spec` is the per-piece (or per-section) declaration that makes this spectrum explicit. It answers three questions for every downstream role (writer, editor, veracity-checker):

1. **What is allowed to be invented?** (`invented_layer`)
2. **What must be accurate?** (`must_be_real`)
3. **What is opinion/speculation, and how must it be framed?** (`speculative_labeled`)

It also expresses a rough `fantasy_ratio` that calibrates how deep the veracity-checker needs to dig.

---

## 2. The `truth_spec` block (YAML schema)

Place `truth_spec` as a sibling key to `output_budget` in any project or piece spec file.

```yaml
truth_spec:
  invented_layer:        # list of strings — elements that are fiction / invented
    - "<element>"
  must_be_real:          # list of strings — elements that must be plausible / accurate
    - "<element>"
  speculative_labeled:   # list of strings — opinions/hypotheses; MUST carry framing/disclaimer in the prose
    - "<element>"
  fantasy_ratio: 0.0     # float 0.0–1.0; rough % of invented content; guides veracity-checker depth
```

### Field semantics

| Field | Type | Meaning |
|---|---|---|
| `invented_layer` | string list | Characters, events, dialogue, world-building, plot — anything the author is free to invent. Verified only for **internal consistency** vs the project bible. |
| `must_be_real` | string list | Claims that must match real-world fact: geography, physics, period detail, named statistics, scientific mechanisms, sensory plausibility, technical processes. Each item gets ≥2 independent sources from the veracity-checker. |
| `speculative_labeled` | string list | Opinions, predictions, or hypotheses that appear in the prose. These are NOT required to be true, but MUST be framed with explicit disclaimers ("I believe…", "One interpretation is…", "As of [date], the evidence suggests…"). Veracity-checker confirms framing is intact — not that the claim is correct. |
| `fantasy_ratio` | float 0.0–1.0 | Rough fraction of the piece that is invented. 0.0 = fully factual (documentation); 1.0 = fully invented (pure surrealist fiction). Used to calibrate veracity-checker depth, not to grant permission to break physics. |

### Scope of application

`truth_spec` applies at multiple granularities:

- **Novel:** bible-level (whole world) + chapter-level + scene-level. Lower levels inherit parent and may tighten but not loosen.
- **Content article / newsletter:** per-piece. Newsletter with mixed sections may carry a per-section override.
- **Content social:** per-thread.
- **Documentation:** usually `fantasy_ratio: 0.0`; `must_be_real` covers all claims; `invented_layer` is typically empty.

---

## 3. Fantasy-ratio guidance matrix

These are reference baselines. Actual values are set by the content lead per piece.

| Content type | Baseline `fantasy_ratio` | Notes |
|---|---|---|
| Pure novel (drift-style, surrealist, speculative) | ~0.95 | High invention. Sensory physics and language naturalness remain in `must_be_real`. |
| Historical fiction | ~0.70 | Invented characters/plot; period geography, dress, customs, and named events are `must_be_real`. |
| Literary fiction (contemporary realism) | ~0.50 | Invented characters/events; setting, social detail, and professional procedures are `must_be_real`. |
| Memoir / personal essay | ~0.10 | Core events are real; characterization of self is subjective. `speculative_labeled` covers interpretation of events. |
| Opinion / think-piece | ~0.30 | Facts cited are `must_be_real`; central argument is `speculative_labeled`. |
| Marketing copy | ~0.25 | Brand voice and framing are invention; product claims, statistics, and pricing are `must_be_real`. |
| Social hot-take | ~0.20 | Framing/hook can be punchy/hyperbolic; underlying factual premise is `must_be_real`. |
| Newsletter (mixed format) | varies per section | Hard-news sections: ~0.05. Opinion sections: ~0.30. Roundup sections: per source. |
| Tech article (tutorial / explainer) | ~0.10 | Code samples must work; conceptual analogies may simplify. |
| News / journalism | ~0.02 | Near-zero invention; only structural choice (lead placement, quote order) is author-controlled. |
| Documentation | ~0.0 | Zero invention. All claims are `must_be_real`. No `invented_layer`. |

**Reading the matrix:** A high `fantasy_ratio` calibrates how much of the piece the veracity-checker skips (the invented portions). It does NOT grant permission to violate the items listed in `must_be_real`. See §5.1 (worked example 1) for why this distinction matters.

---

## 4. How roles use `truth_spec`

### 4.1 Content writer / novel writer

- Treat `invented_layer` items as unconstrained creative space.
- Treat `must_be_real` items as hard constraints — research them before writing, not after.
- Treat `speculative_labeled` items as requiring an explicit framing phrase in the prose.

### 4.2 Content editor / novel editor

- During structural pass: verify that `must_be_real` items are not treated as invented in the draft. Flag cases where a writer has drifted a `must_be_real` item into the invented layer without updating the spec.
- During line-edit: verify that every `speculative_labeled` item carries its framing phrase.

### 4.3 Veracity-checker

The `truth_spec` is the veracity-checker's primary work order:

| Source | Veracity-checker action |
|---|---|
| `must_be_real` items | Verify each against ≥2 independent sources. If any source contradicts the claim, raise a `DISAGREEMENT-FLAGGED` verdict with citations. If no source can confirm, raise `UNVERIFIABLE`. |
| `invented_layer` items | Do NOT fact-check against the external world. Verify internal consistency against the project bible, glossary, and prior chapters. |
| `speculative_labeled` items | Do NOT assess truth. Verify only that the framing/disclaimer phrase is present and unambiguous in the prose. |
| Perception-violating imagery (see §5) | Flag if a cue-less realism violation appears regardless of `fantasy_ratio`. |

**Depth calibration:** `fantasy_ratio` ≥ 0.70 → heavy focus on internal-consistency pass; lighter external-source pass. `fantasy_ratio` ≤ 0.15 → heavy external-source pass on nearly every claim.

---

## 5. Sub-principle: Subjective-perception imagery requires lead-in framing

### 5.1 Default: descriptions are objective

By default, prose descriptions are objective — they describe what the world looks like to a neutral observer. Readers hold an implicit contract: unless told otherwise, imagery must match real-world physics and normal perceptual expectations.

### 5.2 Exception: perception-altered states

Certain narrative states legitimately alter how the POV character perceives the world:

- Flow state / athletic peak concentration
- Tunnel vision under extreme stress
- Shock or dissociation after trauma
- Time-dilation / time-slow during a crisis
- Drug- or fever-altered perception
- Dream or hypnagogic states
- Deep grief or emotional overwhelm

In these states, the author is permitted to use realism-violating imagery (impossible color, distorted scale, unnatural slowing of time, sounds fading out, tunnel narrowing of field of view) **for dramatic effect**.

### 5.3 The cue-first rule

To use a realism-violating image, you must **earn it** with a cue line that establishes the perception shift **before** the violating image appears.

> **Wrong order:** [perception-violating image] → [cue explaining the shift]
> The reader encounters the image first, reads it as an error or a typo, stumbles, and loses trust.

> **Correct order:** [cue establishing altered state] → [realism-violating image]
> The reader enters the altered frame before the violation; the image lands as intended.

The cue does not need to be a full sentence. A clause, a fragment, or even a single word can establish the frame — but it must arrive **first**.

### 5.4 Representative Thai cue patterns

These examples are representative; the principle is **language-universal**. Equivalent constructions exist in every language.

| Cue pattern | Effect established |
|---|---|
| `ทุกอย่างช้าลง` / `เวลาเหมือนยืดออก` | Time-dilation; subsequent slow-motion imagery is earned. |
| `โลกหดแคบลงเหลือแค่ตรงหน้า` | Tunnel vision; peripheral-world imagery can collapse without it reading as error. |
| `เสียงรอบตัวหายไป` / `เสียงทุกอย่างเงียบลง` | Sound-fade; silence-as-dramatic-device is now established. |
| `ภาพตรงหน้าคมชัดผิดปกติ` | Hyper-sharpening; surreal clarity or unnatural detail is now earned. |
| `ในวินาทีนั้นเอง…` | Freeze-frame / singular-moment; slowing of narrative time is earned. |

### 5.5 Veracity-checker protocol for perception-violating imagery

When the veracity-checker encounters imagery that:
- Violates sensory physics (time slows, colors intensify beyond reality, sound disappears, scale distorts)
- AND the preceding context contains no cue establishing an altered perception state

The checker MUST raise a flag, regardless of `fantasy_ratio`, and offer:

- **Option A (realism fix):** Rewrite the image to match objective physics. No cue needed.
- **Option B (earn it):** Move a suitable cue line to immediately precede the violating image (or draft a new cue if none exists in the passage).

The checker does NOT choose between A and B — both options are presented to the writer/editor for decision.

---

## 6. Worked examples

### Example 1: High `fantasy_ratio` does not license broken sensory physics

**Source:** novel-drift, chapter 01 — the banana-smell-on-a-football-pitch incident.

**Context:** The scene is set on a football pitch at night. A surrealist/drift-style novel with a high `fantasy_ratio` (~0.95). The prose included a sensory detail involving the smell of bananas.

**The violation:** Smell is listed in the novel's `must_be_real` sensory physics (see the bible-level `truth_spec`). Even in a near-fully-invented narrative, sensory details ground the reader in the physical world. A football pitch at night does not smell of bananas under any ordinary condition. Because no altered-perception cue preceded this detail, the reader has no frame to interpret it as the character's distorted perception — it reads as an authorial error.

**Principle illustrated:**

> A high `fantasy_ratio` is NOT a blanket license to violate sensory physics. `fantasy_ratio` governs how much of the plot, character, and world is invented. Sensory details — smell, texture, temperature, the weight of objects — sit in `must_be_real` at the novel-bible level. They require either (a) real-world accuracy, or (b) an earned perception cue that shifts them from `must_be_real` into the character's subjective-perception frame.

**Correct `truth_spec` handling:**

```yaml
# novel-drift bible-level truth_spec
truth_spec:
  invented_layer:
    - characters, backstory, relationships
    - plot events and their outcomes
    - world topology (named places that do not exist)
    - dialogue
  must_be_real:
    - sensory physics (smell, texture, sound, temperature behave as in the real world
      unless a perception-altered state has been established)
    - named real locations (if used)
    - period-accurate detail when historical reference is made
  speculative_labeled: []
  fantasy_ratio: 0.95
```

Under this spec, the banana smell at a football pitch is a `must_be_real` violation. Fix paths:
- **Option A:** Replace with a smell that is plausible for a night football pitch (cut grass, damp earth, synthetic turf off-gassing, sweat).
- **Option B:** Precede the banana smell with a perception-altered-state cue (e.g., the character is feverish, in a dissociative episode, or experiencing a smell-memory intrusion — and the cue makes this clear before the smell arrives).

---

### Example 2: Perception-cue ordering violation (chapter 01, ~line 161)

**Source:** novel-drift, chapter 01, approximately line 161.

> **TODO(operator):** re-derive the exact original Thai text from `novel-drift/chapters/ch01`. The source text at this location was lost in the 2026-06-09 corruption event. Do NOT reconstruct from memory — the exact wording must come from the novel-drift repository directly.

**What is known:** A realism-violating perceptual image appeared at approximately line 161. The framing cue that was intended to earn it arrived at approximately line 163 — two lines after the violating image. This ordering reversed the cue-first rule: the reader encountered the violation before the frame.

**Representative demonstration (labeled as such — this is NOT the original novel-drift text):**

Assume the passage read something like (Thai, illustrative only):

> [Line 161 — representative]
> `เสียงฝูงชนกลายเป็นเพียงแสงสีขาวที่สั่นไหว` (The crowd-sound became only white light that rippled.)
>
> [Lines 162–163 — representative]
> `เขารู้สึกได้ว่าร่างกายหนักขึ้น หนักขึ้น ทุกอย่างช้าลงในหัว` (He felt his body grow heavier, heavier — everything slowed inside his head.)

The violating image at line 161 (sound converting to visual light — synesthesia under stress) requires the altered-state frame that arrives only at line 163.

**Option A — realism fix (remove the violation):**

> [Line 161 revised]
> `เสียงฝูงชนดังกึกก้อง ทับซ้อนกันจนแยกแยะไม่ออก` (The crowd-sound roared, layered and indistinguishable.)

No cue needed; the image is now within normal perceptual bounds.

**Option B — earn it (move the cue to a lead-in position):**

> [Line 160–161 revised — cue first, then violation]
> `ทุกอย่างช้าลงในหัว — เสียงฝูงชนกลายเป็นเพียงแสงสีขาวที่สั่นไหว`
> (Everything slowed inside his head — the crowd-sound became only white light that rippled.)

The cue (`ทุกอย่างช้าลงในหัว`) now precedes the violating image on the same line. The reader enters the altered frame before the synesthetic image lands.

---

## 7. Universal applicability: novel, content, documentation

The `truth_spec` framework is team-agnostic. It applies identically across:

| Team | Application |
|---|---|
| **novel** | Bible + chapter + scene levels. High `fantasy_ratio`; `must_be_real` anchors sensory/period/geographic detail. |
| **content** | Per-article, per-newsletter-section, per-social-thread. `speculative_labeled` is heavy in opinion/commentary pieces. |
| **documentation** | `fantasy_ratio: 0.0`; `invented_layer: []`; every claim is `must_be_real`. `speculative_labeled` used for roadmap notes ("as of [date], the design intent is…"). |

The veracity-checker role is the same agent regardless of team. The only difference is calibration depth (governed by `fantasy_ratio`) and which bible/reference document defines internal consistency.

---

## 8. Cross-reference: `languages/thai-prose.md` — Cat 11 sensory hallucination

**Sibling standard:**

`context/standards/languages/thai-prose.md`, Category 11 ("Sensory hallucination / unearned surreal image") links to this standard. The relationship is:

- **`languages/thai-prose.md` Cat 11** covers the Thai-language surface manifestation: specific Thai constructions that create an unearned surreal or synesthetic image, and how to recognize them in a proofreading pass.
- **This standard (truth-spec)** covers the structural rule: why the violation occurs (missing or misplaced perception-cue), what the cue must do, and what the veracity-checker does when it finds one.

A thai-proofreader agent that flags a Cat 11 construction MUST reference the `Option A / Option B` framework in §5.5 of this document in its output. The proofreader identifies the surface; this standard defines the remedy logic.

---

## 9. Quick-reference checklist (for writer and veracity-checker)

**Writer — before submitting a draft:**

- [ ] `truth_spec` block is present in the piece spec.
- [ ] Every `must_be_real` item was researched before writing (not post-hoc).
- [ ] Every `speculative_labeled` item carries a visible framing phrase in the prose.
- [ ] No perception-violating image appears without a preceding cue.

**Veracity-checker — during review:**

- [ ] Pull `truth_spec` from the piece spec before reading.
- [ ] For each `must_be_real` item: locate claims in prose → verify against ≥2 independent sources → flag `DISAGREEMENT-FLAGGED` or `UNVERIFIABLE` where needed.
- [ ] For each `invented_layer` item: verify internal consistency vs bible/glossary — do NOT check against external world.
- [ ] For each `speculative_labeled` item: confirm framing phrase is present and unambiguous.
- [ ] Scan for perception-violating imagery without lead-in cues → flag + offer Option A + Option B.

---

*Standard v1.0 — `context/standards/writing/truth-spec.md` (Kanban #970, 2026-06-22).*
