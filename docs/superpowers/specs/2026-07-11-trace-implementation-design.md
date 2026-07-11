# Project TRACE — Implementation Design

**Status:** Approved for plan generation
**Date:** 2026-07-11
**Builds on:** [TRACE MVP Design](./2026-07-11-trace-mvp-design.md) (the product/system spec — read that first)
**Build horizon:** One day to a demoable MVP, built by a small team in parallel.

This document defines **how** the MVP is built: repo structure, the core scaffold that lands first, the four parallel slices, the interface contracts between them, the frontend architecture, and the branch/consolidation strategy. It is the source of truth for the five implementation plans derived from it (one core-scaffold plan + four slice plans).

---

## 1. Architectural decisions (locked)

| Decision | Resolution |
|---|---|
| Backend topology | **Monolith in Docker** — one FastAPI app, plain modules, *not* microservices (per the product spec). Containerized for clean local dev and deploy. |
| Containerization | `docker-compose` with two services: `app` (FastAPI) + `db` (Postgres). One command boots a clean, seeded system. |
| Frontend framework | **Next.js** (App Router). Separate deploy (Vercel or equivalent), consumes the FastAPI REST + SSE API. |
| Frontend styling | **Tailwind CSS** with the **Hand-Drawn design system** (see §6) for all visible components. |
| Component library | **Mantine**, used **only for behavior primitives** (Modal, Select/Combobox, DateInput, Notifications). Never for visible look — Approach B from the design discussion. |
| State management | **Zustand** for client state. SSE/REST data flows through Zustand stores. |
| Messaging | **Telegram Bot API** (not WhatsApp). Conversation channel only; photos go through the `/capture` web link. |
| Repo strategy | **One repo, branches, no worktrees.** Core scaffold on `main` first; slices branch off `main`. |
| Team split | **One person owns the core scaffold** (the shared trunk). Then **four slices** worked in parallel — Frontend/UI, Intake/Comms, Grading/Decay, Orchestration. |

---

## 2. Repository structure

```
trace/
  compose.yaml                 # app + db (Ryheeme's placeholder lives here, to be fleshed out)
  .env.example                 # OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, DATABASE_URL, etc.
  backend/
    Dockerfile
    pyproject.toml             # FastAPI, SQLAlchemy, psycopg, httpx, Pillow, alembic, pytest
    app/
      main.py                  # FastAPI app, CORS, router mounting, /health
      config.py                # env loading (pydantic-settings)
      db.py                    # SQLAlchemy engine + sessionmaker + get_db dependency
      models.py                # ALL tables (spec §6) — owned by core, day one
      statemachine.py          # Batch lifecycle: states, transition table, guards — core
      events.py                # SSE pub/sub bus + AuditEvent writer — core
      seed.py                  # deterministic seed — core
      routers/                 # REST route modules (skeletons land in core, filled by slices)
        batches.py             #   batch state, transitions (orchestration fills)
        capture.py             #   photo upload endpoint (intake fills)
        contracts.py           #   contract + HITL confirm (orchestration fills)
        payouts.py             #   payout reads (orchestration fills)
        admin.py               #   audit timeline, SSE stream (core stubs, all fill)
      services/                # the slice logic, imported by routers
        grading.py             #   grade() + simulate_decay()  (grading slice)
        routing.py             #   decide_route() + payout math  (orchestration slice)
        messaging.py           #   Telegram send/receive        (intake slice)
        aggregation.py         #   pooling + contract matching  (orchestration slice)
      tests/
  frontend/
    package.json               # next, react, @mantine/core, zustand, tailwindcss
    tailwind.config.ts         # Hand-Drawn tokens (wobbly radii, hard shadows, fonts)
    app/                       # Next.js App Router
      layout.tsx               # MantineProvider + Tailwind + fonts (Kalam, Patrick Hand)
      capture/[token]/page.tsx # the one photo-upload surface
      farmer/                  # read-only batch/payout view
      premium-buyer/           # contract confirm + disputes
      secondary-buyer/         # incoming reroute offers
      composter/               # waste pickups
      admin/                   # pitch-hero cascade view (SSE)
    components/handdrawn/      # Button, Card, Input, Badge, Tape, Thumbtack — design system
    stores/                    # Zustand stores (batches, contracts, sse)
    lib/                       # api client, sse client, mock-api (for unblocked dev)
  docs/superpowers/specs/      # this doc + the product spec
```

**Naming convention:** slice branches are `slice/<name>` (`slice/frontend`, `slice/intake`, `slice/grading`, `slice/orchestration`). Feature work within a slice can use further sub-branches at the owner's discretion.

---

## 3. Phase 0 — Core scaffold (lands on `main` first, owned by one person)

The shared trunk. Every slice codes against this. It must land on `main` before slices branch, so the first slice merge isn't a schema conflict.

**Deliverables:**
1. **`compose.yaml`** — `app` (FastAPI, hot-reload in dev) + `db` (Postgres 16), healthchecks, a `seed` one-shot. `docker-compose up` boots clean.
2. **`models.py`** — all nine tables from product spec §6, fully: `Farmer`, `Buyer`, `Contract`, `Batch`, `VirtualShipment` (+ the `VirtualShipmentBatch` link table holding `%` contribution), `Route`, `RoutingDecision`, `Payout`, `AuditEvent`. Use SQLAlchemy 2.0 declarative. Include the two grade fields on `Batch` (`farm_grade`, `handoff_grade`, `final_grade`) and both reason fields.
3. **`statemachine.py`** — the `Batch` lifecycle from product spec §7: all 13 states, the named guarded transitions, raises on illegal moves, and on every successful transition (a) appends an `AuditEvent`, (b) publishes to the SSE bus. **Slices never mutate `batch.status` directly — they call `batch.transition(DEST, **ctx)`.** This invariant is what keeps merges clean.
4. **`events.py`** — an in-process SSE pub/sub (a simple `asyncio` broker; no external broker for the MVP) plus the `AuditEvent` writer. `publish(event_type, payload)` → all connected SSE clients receive it.
5. **`seed.py`** — deterministic seed (product spec §13): 6 tomato farmers across a small geography, 1 resort premium contract (200 kg Grade A by 4 pm), 1 school-feeding secondary buyer, 1 composter on the returning leg, and **one batch parked at `GRADED_FARM`** ready to flow. Idempotent (`seed.py --reset` drops + recreates + inserts).
6. **REST skeletons** — route modules with handlers returning HTTP 501 Not Implemented, so the frontend can wire real routes from minute one. Includes the `GET /admin/stream` SSE endpoint stub.
7. **`/health`** + **`config.py`** (pydantic-settings env loading) + **`.env.example`** listing every key slices will need (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `LLM_JUSTIFICATION_MODEL`, etc.).

**Definition of done for Phase 0:** `docker-compose up` → `seed.py --reset` → `GET /health` returns 200 → `GET /batches` returns the seeded batch at `GRADED_FARM` → `GET /admin/stream` holds an SSE connection. No slice logic yet — just the spine.

---

## 4. Phase 1 — the four slices (parallel off `main`)

Each slice branches off `main` after Phase 0 lands, codes against the contracts in §5, and merges back independently. Each delivers something that runs.

### Slice A — Frontend & UI (teammate-owned)

**Scope:** all six routes, the Hand-Drawn design system, REST + SSE consumption.
**Tech:** Next.js App Router, Tailwind + Hand-Drawn tokens, Mantine for behavior primitives only, Zustand for state.
**Key property:** builds against a **mock API** (`lib/mock-api`) from minute one, so it is never blocked on backend slices. The mock returns canned batches/contracts and a fake SSE stream that plays the cascade. Swapping to the real API is a config flip once endpoints land.
**Deliverables:** `/capture/[token]` (camera upload, coin-in-frame, no login), `/farmer` (read-only batches + payouts), `/premium-buyer` (contract confirm + disputes), `/secondary-buyer` (incoming reroute offers), `/composter` (waste pickups), `/admin` (the pitch-hero live cascade via SSE + provenance timeline).
**Hand-off:** consumes `GET /batches`, `GET /contracts`, `POST /contracts/{id}/confirm`, `GET /admin/stream` (SSE), `POST /capture` — all defined in §5.

### Slice B — Intake & Comms

**Scope:** the farmer's front door and the outbound message channel.
**Tech:** Telegram Bot API (webhook), FastAPI.
**Deliverables:**
- Telegram webhook handler: receives intent ("harvest 10kg tomatoes"), resolves/creates the `Farmer` (by `telegram_chat_id`), creates a `Batch` at `HARVESTED`, replies with the one-tap `/capture/{token}` link.
- `POST /capture/{token}` upload endpoint: accepts the photo, stores it, and advances `harvested→graded_farm` (then triggers grading via Slice C's `grade()`).
- `services/messaging.py` → `send_message(chat_id, text)`: the single outbound channel. The grade, the reroute reason, the payout — all sent to the farmer through this.
**Hand-off:** imports `grade(image, crop)` from Slice C; calls `batch.transition()` from core.

### Slice C — Grading & Decay

**Scope:** the pure grading function and the decay simulator.
**Tech:** OpenRouter (vision model, temp 0), PIL.
**Deliverables:**
- `services/grading.py` → `grade(image_bytes, crop) -> {grade, reason}`: one OpenRouter call, the fixed USDA-anchored prompt (product spec §8.2), structured JSON out, temp 0. Retry-once on failure/malformed-JSON then raise.
- `simulate_decay(image_bytes) -> image_bytes`: PIL degradation (darkening/browning of the produce region, soft-spot artifacts) per product spec §8.3. **No second photo upload** — the handoff pass runs `grade(simulate_decay(original), crop)`.
- Golden-image fixture tests: fresh → A, blemished → B, decayed → Waste; and a test that `simulate_decay` moves a known-A image to B/Waste.
**Hand-off:** the cleanest slice — exports `grade()` and `simulate_decay()`; depends on nothing but the image and the crop config. Knows nothing of Telegram or routing.

### Slice D — Orchestration (Routing & Payout)

**Scope:** aggregation, contract matching, the self-healing cascade, all money math.
**Tech:** plain Python rules engine + one LLM call (justification).
**Deliverables:**
- `services/aggregation.py`: pool `GRADED_FARM` batches by crop + grade + geo into a `VirtualShipment` against a `Contract`; compute each batch's `%` contribution.
- Contract matching + the HITL confirm transition (`pooled→contracted` blocked until buyer confirms).
- `contracted→shipped` (assign `Route`) → `shipped→graded_handoff` (calls Slice C's `grade(simulate_decay(original))`). **"Spoilage clock" is MVP-simple:** there is no real wall-clock timer — the seeded decay-triggered batch is marked at seed time to decay on its handoff pass, and a short `asyncio.sleep` (a few seconds, for demo pacing) gates the `shipped→graded_handoff` transition. A real shelf-life timer is roadmap.
- `services/routing.py` → `decide_route(batch, handoff_grade, contract, buyers) -> RoutingDecision`: the deterministic rules engine (product spec §10), including returning-leg preference via straight-line lat/lng. Drives `graded_handoff→{delivered | rerouted | composted}` and the `rerouted→delivered_secondary` transition.
- Payout math (product spec §11): farmer payout at delivered grade × destination price/kg; recompute `%` and contract fulfillment on reroute; zero-amount payout on compost; buyer-side short/refund logic. Drives `→paid` transitions.
- Routing-justification LLM call: turns `{reason_code, from, to, facts}` into the logged justification + the farmer message text (passed to Slice B's `send_message`).
**Hand-off:** imports `grade()`/`simulate_decay()` from Slice C and `send_message()` from Slice B; calls `batch.transition()` from core.

---

## 5. Interface contracts (the seams that make parallel work possible)

These are agreed signatures. A slice codes against the signature, not against another slice's half-finished implementation.

```python
# Slice C exports (Grading)
grade(image_bytes: bytes, crop: str) -> dict
    # -> {"grade": "A"|"B"|"WASTE", "reason": "one short sentence"}
simulate_decay(image_bytes: bytes) -> bytes
    # -> PIL-degraded image (for the handoff re-grade)

# Slice D exports (Orchestration)
decide_route(batch, handoff_grade, contract, buyers) -> RoutingDecision
    # deterministic; sets the transition + writes RoutingDecision + triggers payout
compute_payout(batch, destination, price_per_kg) -> Payout
    # creates/updates the Payout row; recompute on reroute

# Slice B exports (Intake)
send_message(chat_id: str, text: str) -> None
    # the single outbound Telegram channel
POST /capture/{token}   -> accepts photo, advances harvested->graded_farm, triggers grade()
POST /telegram/webhook  -> receives inbound messages

# Core exports (Phase 0)
Batch.transition(dest: State, **ctx) -> None   # the ONLY way state changes
publish(event_type: str, payload: dict) -> None  # SSE bus
GET /admin/stream        -> text/event-stream (SSE)
GET /batches, GET /contracts, POST /contracts/{id}/confirm, GET /payouts  # REST
```

**The single rule that keeps merges clean:** `batch.status` is mutated **only** inside `Batch.transition()`, in core. Every slice calls `transition()`. No slice writes `batch.status = "..."` directly. This guarantees the four slice branches never conflict on the most-contended field.

---

## 6. Frontend architecture — Hand-Drawn + Mantine (Approach B)

**Design system:** the Hand-Drawn spec supplied by the team (wobbly multi-value `border-radius`, hard-offset no-blur shadows, Kalam/Patrick Hand fonts, paper-grain texture, tape/thumbtack decorations, playful rotation, limited palette: warm paper `#fdfbf7`, soft pencil black `#2d2d2d`, correction red `#ff4d4d`, ballpoint blue `#2d5da1`, post-it yellow `#fff9c4`).

**Token centralization:** all Hand-Drawn tokens live in `tailwind.config.ts` (`wobbly`, `wobblyMd` border-radius helpers; `.shadow-hard`, `.shadow-hard-lg`; paper-grain background utility; font families) and a small `theme.tokens.ts`. No inline one-off values scattered across components — the design system role prompt's centralization requirement.

**Mantine boundary (firm):** Mantine is imported **only** for `Modal`, `Select`/`Combobox`, `DateInput`, and `Notifications`. These are behavior-heavy primitives where Mantine's a11y/positioning earns its keep. Everything the user *sees as a shape* — buttons, cards, inputs, badges, the capture flow, the dashboards — is a hand-built `components/handdrawn/*` component in Tailwind, themed to the design system. Mantine components used are restyled via their `classNames`/`styles` API to match Hand-Drawn (wobbly radius, hard shadow) so they don't leak the clinical look.

**State:** Zustand stores hold batch/contract/payout data received from REST and updated live by the SSE client. The `/admin` cascade view subscribes to the SSE store and re-renders as batches transition.

**Mock API:** `lib/mock-api` + a fake SSE stream replaying the cascade let the frontend ship before any backend slice lands. The swap to real endpoints is an environment toggle, not a rewrite.

**Accessibility & responsiveness:** the design system's mobile-first rules apply (touch targets ≥ 48px, decorative elements `hidden md:block`, wobbly borders preserved at all sizes). Mantine primitives bring their own a11y; hand-built components honor focus states (blue ring per the design system).

---

## 7. Branch & consolidation strategy

- **`main`** — protected. Phase 0 core scaffold lands here first. Slices merge here via PR.
- **`slice/frontend`, `slice/intake`, `slice/grading`, `slice/orchestration`** — each branches off `main` *after* Phase 0 lands. Owners may use sub-branches within their slice.
- **Merge discipline:**
  - Each slice merges `main` into its branch frequently (core may evolve slightly as slices surface needs).
  - Slices merge to `main` one at a time, in dependency order where it exists: **C (Grading) → B (Intake) and D (Orchestration) can land once C is in → A (Frontend) swaps off mock last.** In practice the function-contract seams mean any order works; this is just the smoothest.
  - The `batch.transition()` invariant (§5) means slice branches almost never conflict on logic — only on router/service file additions, which merge trivially.
- **Integration smoke test:** once all four are in, run `seed.py --reset` + the end-to-end cascade test (product spec §15): one seeded batch flows `graded_farm → pooled → contracted → shipped → graded_handoff(decay) → rerouted → delivered_secondary → paid`, the farmer gets the Telegram reason, the buyer's fulfillment recomputes, the admin view shows it live.

---

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Core scaffold slips, blocking all four slices | Phase 0 is tightly scoped (§3) to land in a morning; slices prep (read spec, stub tests against the §5 signatures) while it lands. Frontend is never blocked — mock API. |
| Slice branches conflict on `batch.status` | The `transition()` invariant (§5) — enforced by review. |
| Mantine leaks its clinical look into Hand-Drawn UI | The firm Mantine boundary (§6) — only behavior primitives, restyled. |
| Live demo flakiness from LLM grading | Temp 0 + golden-image fixtures + the mock-API path means the frontend demo can run fully offline if needed. |
| Consolidation merge hell | Function-contract seams + frequent `main` merges + one-at-a-time PRs in dependency order (§7). |

---

## 9. What this spec produces (next step)

Five implementation plans, generated via the `writing-plans` skill:
1. **Core scaffold plan** (Phase 0) — the owner's plan.
2. **Slice A — Frontend & UI plan.**
3. **Slice B — Intake & Comms plan.**
4. **Slice C — Grading & Decay plan.**
5. **Slice D — Orchestration plan.**

Each plan references the product spec (§sections) and this doc (contracts, files) and is independently executable by its owner.
