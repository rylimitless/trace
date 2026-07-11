# TRACE — Pitch & Submission Deliverables

Companion to the [product spec](docs/superpowers/specs/2026-07-11-trace-mvp-design.md) and [implementation design](docs/superpowers/specs/2026-07-11-trace-implementation-design.md). Covers the rubric's non-code deliverables.

---

## Project Name & 1-Sentence Tagline

**TRACE** — *A self-healing, dual-purpose supply-chain engine that turns fragmented Caribbean smallholder harvests into reliable resort supply, and never wastes a harvest that falls short.*

Alt tagline (shorter): *TRACE reroutes spoiled-in-transit produce automatically — so a farmer's harvest never goes to waste, and the truck never travels empty.*

---

## Pitch Deck (≤100 words)

Caribbean smallholders grow quality produce but lose it to the **gap between farm and buyer** — heat, delay, and handling spoil harvests in transit, not at the farm. TRACE proves this and **self-heals around it**: every batch is graded at the farm and again at handoff; if it decayed, a deterministic rules engine reroutes it automatically — Grade A → resort, downgraded → school feeding, waste → compost — and **the same truck hauls the rerouted load back on its returning leg**, a dual-purpose fleet. Farmers (Telegram, no app) get market access; buyers get reliability; nothing is wasted. Built for small-island infrastructure: low-bandwidth, degraded-mode-tolerant.

*(97 words.)*

### Rubric coverage
- **Scalability:** aggregation pools many smallholders into one virtual shipment; the rules engine generalizes to any crop/buyer/geo.
- **SIDS viability:** low-bandwidth (Telegram text-first, 2G), no app download, offline-degraded tolerant, uses existing phones.
- **Environmental impact:** circular — waste → compost on the returning leg (no extra trips); "we proved the loss, didn't hide it."
- **Specific problem solved:** transit spoilage in fragmented island supply chains; smallholder exclusion from resort supply.

---

## The "Twist" Demonstration

Two anomalies play in the live demo (and fallback video):

1. **Transit decay (the core twist):** a Grade A tomato batch ships, decays in the simulated transit gap, is **re-graded lower at handoff**, and the system **automatically reroutes** it to the school feeding program at the Grade B price — while **the returning leg of the same truck carries it there**. The farmer is told (Telegram): *"dropped to Grade B, sold at the Grade B price to the secondary market — $5.10, still sold, nothing wasted."* No human rerouted anything.

2. **Route disruption (chaos-handling):** a road/port washout closes the primary route to the composter while waste is en route. The system **recomputes the returning leg** to a fallback composter via an alternate route (`route_disruption`), re-payouts, and keeps flowing. Answers the curveball: *"what if a typhoon knocks out Island B?"*

The returning-leg fleet is **visibly dual-purpose** on the admin view — the same truck drops premium produce outbound and hauls rerouted/waste back. This is the dual-purpose, self-healing network the problem statement asks for.

---

## The AI Stack

| Tool | Role |
|---|---|
| **Claude Code (Claude)** | Spec + plan authoring, iterative design, rubric reasoning, the prompt-engineering workflow itself |
| **OpenRouter (vision LLM, temp 0)** | Produce grading — the USDA-anchored `grade(image, crop)` call |
| **Claude / GPT (text)** | Routing-justification text (turns a structured decision into the farmer's message + audit justification) |
| **FastAPI + PostgreSQL + Next.js + Tailwind/Mantine + Zustand** | Implementation stack |

The two LLM surfaces are tightly scoped (see spec §9): **grading** and **justification**. Neither moves money autonomously — the deterministic rules engine does.

---

## Ethics, Bias, Privacy & Safety

The rubric checks for this explicitly.

- **Grading bias:** a vision LLM may grade inconsistently across lighting conditions, crop varieties, or — importantly — the hands/skin visible holding produce in frame. **Mitigations:** (1) the USDA-anchored prompt makes the standard explicit, not the model's implicit preference; (2) temperature 0 for reproducibility; (3) every grade + reason is logged to the audit trail for review; (4) the model never decides money — only disambiguates quality; a deterministic engine reprices. **Residual risk:** temp 0 reduces but does not eliminate nondeterminism; we accept and disclose this.
- **Privacy:** farmers share only a Telegram handle, a photo of produce (with a coin), and a rough location. Contracts and buyer identities are hidden from farmers (§4a). No sensitive PII in the grading prompt. Photos are stored for the batch lifecycle; a retention policy is roadmap.
- **Safety / food grade:** grading is anchored to the **USDA Fresh Tomato standards (§51.1855–51.1859)** — a real, citable food-safety standard, not an invented rubric. This is the safety floor.
- **Economic fairness:** payouts are never silently zero — a composted batch produces an explicit, explained `$0` so losses are visible and accountable, not hidden.

---

## Known Failure Points (stress-test Q2: "Where is the failure point?")

Honest answer for the judges:
1. **LLM hallucinates a grade** — temp 0 mitigates but doesn't eliminate; the audit trail + the deterministic money layer contain the blast radius (a wrong grade affects one batch's routing, not the contract logic).
2. **No coin / poor photo** — the LLM may misjudge size; we don't gate on coin presence (accepted MVP limitation).
3. **No secondary buyer or composter capacity** — the batch goes to `LOST` (logged, feeds back as a demand signal). The system fails *safe and visibly*, not silently.
4. **Sustained outage** — degraded mode holds (§14a), but a long OpenRouter + Telegram outage together would queue, not lose, state.

---

## 24-Hour Roadmap (stress-test Q3)

If we had one more day, in priority order:
1. **Offline-first PWA capture** — queue photos locally, sync on reconnect (turns the §14a narrative into a real offline capability).
2. **A real shelf-life timer** replacing the `asyncio.sleep` demo clock.
3. **YOLOv8 grading** behind the same `grade()` interface (sharper blemish detection; same USDA anchor).
4. **LangGraph orchestration** replacing the plain state machine (same states/transitions).
5. **Hash-chained provenance ledger** replacing the `AuditEvent` log (tamper-evident audit).
6. **Planning Agent** — demand-led planting suggestions to farmers (closes the loop from the demand feed).

---

## Fallback Video (mandatory)

**Record a ≤2-minute screen capture** of the prototype running both anomalies end-to-end *before* judging. Per the rules: if the live demo doesn't work within 60 seconds, judges switch to this video. Keep it self-narrated (the demo must stand alone without a live presenter).
