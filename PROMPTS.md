# TRACE — The Golden Prompt & Prompt Log

The rubric asks (stress-test Q1): *"Show us your prompt log — was the prompting iterative and logical?"* and for a **"Golden Prompt"** deliverable. Both live here.

---

## The Golden Prompt — USDA produce grading

This is the most important prompt engineered for TRACE. It is **fixed** (one string for every image) and runs at **temperature 0** for reproducibility. Because the prompt is fixed and deterministic, **the prompt is the grading standard** — anchored to the real USDA *United States Standards for Grades of Fresh Tomatoes* (§51.1855–51.1859), so the grades are defensible, not invented.

```
You are a produce quality grader following the USDA United States
Standards for Grades of Fresh Tomatoes (§51.1855–1859). A coin is in
the frame as a size reference. Grade this batch of tomatoes by the
USDA definitions, using visible SIZE (vs the coin), MATURITY
(color/ripeness), and DEFECTS (cuts, bruising, growth cracks,
soft/wrinkled spots, decay, mold):

- A     = U.S. No. 1 — fairly uniform ripe color, ~free from damage
- B     = U.S. No. 2 — tolerable defects, free from serious damage
- WASTE = below No. 2 — decay / severe damage / unsellable

Reply ONLY: {"grade":"A"|"B"|"WASTE",
              "reason":"one sentence citing the USDA deciding factor"}
```

### Why this prompt is engineered, not lucky
- **Anchored to a real standard** (§51.1855–1859) — the A↔B boundary is USDA's "damage vs serious damage" distinction, a real boundary.
- **Forces structure** — JSON-only output makes the grade machine-parseable and the reason auditable.
- **Names the coin** — gives the model a scale reference so size isn't hallucinated.
- **Temp 0 + fixed string** — the same photo yields the same grade. Money-moving reproducibility.
- **The reason field is the audit trail** — every grade is explainable in USDA terms.

### The routing-justification prompt (second LLM surface)
Structured-in, text-out. The rules engine decides the destination; the LLM only explains it:

```
You write clear, kind farmer messages. Given a routing decision as JSON:
{reason_code, crop, farm_grade, handoff_grade, market_category,
 payout_was, payout_now}

Write ONE message to the farmer. Rules:
- Use only grade + outcome + market_category (e.g. "secondary market").
- NEVER name a specific buyer, contract, or destination.
- If money changed, state both the old and new amount.
- Reassuring tone; under 40 words.
```

---

## The Prompt Log — where to find it

The prompt engineering for TRACE was **iterative and logical**, visible in the Claude Code session that produced the specs:

1. **Spec drafting** → the grading approach evolved: OpenCV heuristic → tiered OpenCV+LLM → pure vision-LLM (the team's choice). Each step reasoned and recorded in the spec history.
2. **Rubric-driven refinement** → the USDA anchor was added to make grades defensible; temp 0 + fixed string added for reproducibility; the honesty note ("honest *detection* of simulated spoilage") corrected an overclaim.
3. **Conflict review** → a pre-plan cross-slice check forced explicit seams (`grade()`, `decide_route()`, `market_category`) so the prompts have stable, agreed inputs.

**To submit the prompt log:** export this Claude Code session transcript (the conversation that built TRACE's specs + plans) as the prompt-history artifact for the Technical Spot-Check. The git history of `docs/superpowers/specs/` is the written record of the iterative reasoning.

```
# to export the prompt log
# Claude Code: File → Export (or) copy the session transcript to a PDF
# place in /docs or link from the README
```

---

## Judge's checklist (from the rubric)

- [x] **Prompt log visible** — this session's transcript + the spec git history.
- [x] **Ethics mentioned** — bias (USDA anchor, temp 0, audit), privacy (minimal farmer data, hidden contracts), safety (USDA food-grade standard). See [PITCH.md](PITCH.md).
- [x] **Real logic flow** — deterministic state machine + rules engine; the LLM never moves money. See the specs.
