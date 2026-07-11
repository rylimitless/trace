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
      auth.py                  # session middleware, bcrypt, role dependencies — core
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
        grading.py             #   grade() + simulate_decay()           (grading slice, C)
        routing.py             #   decide_route() + payout math         (orchestration slice, D)
        scheduler.py           #   background task: gates shipped→graded_handoff ('spoilage clock')  (D)
        handoff.py             #   the handoff re-grade step (pulls photo, decay+grade, fires decide_route)  (D)
        messaging.py           #   Telegram send/receive + send_farmer_update()  (intake slice, B)
        aggregation.py         #   pooling + contract matching + demand-feed derivation  (D)
      photos/                  # photo storage (files or object store) — written by B's capture endpoint,
                               #   read via get_batch_photo(batch) seam (used by D's handoff step)
      tests/
  frontend/
    package.json               # next, react, @mantine/core, zustand, tailwindcss
    tailwind.config.ts         # Hand-Drawn tokens (wobbly radii, hard shadows, fonts)
    app/                       # Next.js App Router
      layout.tsx               # MantineProvider + Tailwind + fonts (Kalam, Patrick Hand)
      capture/[token]/page.tsx # the one photo-upload surface (farmer reaches via Telegram link)
      admin/                   # operator pitch-hero cascade view (SSE + provenance)
      premium-buyer/           # primary customer: own contract confirm + disputes
      secondary-buyer/         # secondary customer: incoming reroute offers
      composter/               # waste pickups
      # (no /farmer route — farmers are Telegram-only; see product spec §4a)
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
2. **`models.py`** — all tables from product spec §6, fully: `User` (id, email, bcrypt hash, role, optional buyer_id), `Farmer`, `Buyer`, `Contract`, `Batch` (incl. `capture_token` + `capture_token_expires_at`), `VirtualShipment` (+ the `VirtualShipmentBatch` link table holding `%` contribution), `Route`, `RoutingDecision`, `Payout`, `AuditEvent`. Use SQLAlchemy 2.0 declarative. Include the two grade fields on `Batch` (`farm_grade`, `handoff_grade`, `final_grade`) and both reason fields.
3. **`auth.py`** — bcrypt password hashing, signed httpOnly **session-cookie** middleware, and the role dependencies: `require_admin`, `require_buyer(type=...)`, `require_composter`, plus `current_user()`. **DB-level scoping helpers** so a premium buyer's queries filter to their own `buyer_id` (this is what enforces product-spec §4a — the visibility rule must hold at the DB, not just the UI). Also: `POST /auth/login`, `POST /auth/logout`, and a `generate_capture_token(batch)` helper (random urlsafe token + DB row + expiry) used by Slice B when a batch is created.
4. **`statemachine.py`** — the `Batch` lifecycle from product spec §7: all 13 states, the named guarded transitions, raises on illegal moves, and on every successful transition (a) appends an `AuditEvent`, (b) publishes to the SSE bus. **Slices never mutate `batch.status` directly — they call `batch.transition(DEST, **ctx)`.** This invariant is what keeps merges clean.
5. **`events.py`** — an in-process SSE pub/sub (a simple `asyncio` broker; no external broker for the MVP) plus the `AuditEvent` writer. `publish(event_type, payload)` → all connected SSE clients receive it.
6. **`seed.py`** — deterministic seed (product spec §13): 6 tomato farmers across a small geography, 1 resort premium contract (200 kg Grade A by 4 pm), 1 school-feeding secondary buyer, 1 composter on the returning leg, **the seed-time `User` accounts** (1 admin + buyer accounts, bcrypt-hashed), and **one batch parked at `GRADED_FARM`** ready to flow. Idempotent (`seed.py --reset` drops + recreates + inserts).
7. **REST skeletons** — route modules with handlers returning HTTP 501 Not Implemented, so the frontend can wire real routes from minute one. Includes the `GET /admin/stream` SSE endpoint stub. Routes are gated by the role dependencies from `auth.py` (item 3).
8. **`/health`** + **`config.py`** (pydantic-settings env loading) + **`.env.example`** listing every key slices will need (`OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `TELEGRAM_BOT_TOKEN`, `DATABASE_URL`, `LLM_JUSTIFICATION_MODEL`, `SESSION_SECRET`, etc.).

**Definition of done for Phase 0:** `docker-compose up` → `seed.py --reset` → `GET /health` returns 200 → `GET /batches` returns the seeded batch at `GRADED_FARM` → `GET /admin/stream` holds an SSE connection. No slice logic yet — just the spine.

---

## 4. Phase 1 — the four slices (parallel off `main`)

Each slice branches off `main` after Phase 0 lands, codes against the contracts in §5, and merges back independently. Each delivers something that runs.

### Slice A — Frontend & UI (teammate-owned)

**Scope:** five web surfaces for **admins and customers only** (farmers are Telegram-only — no `/farmer` route): `/capture/[token]`, `/admin`, `/premium-buyer`, `/secondary-buyer`, `/composter`. The Hand-Drawn design system, REST + SSE consumption.
**Tech:** Next.js App Router, Tailwind + Hand-Drawn tokens, Mantine for behavior primitives only, Zustand for state.
**Key property:** builds against a **mock API** (`lib/mock-api`) from minute one, so it is never blocked on backend slices. The mock returns canned batches/contracts and a fake SSE stream that plays the cascade. Swapping to the real API is a config flip once endpoints land.
**Deliverables:** `/capture/[token]` (camera upload, coin-in-frame, no login — reached by farmers via the Telegram link), `/admin` (the pitch-hero live cascade via SSE + provenance timeline), `/premium-buyer` (their **own contract**, grade+produce framed, with HITL confirm + disputes), `/secondary-buyer` (incoming reroute offers, no contract), `/composter` (waste pickups).
**Visibility rule (product spec §4a):** the frontend **never** shows contracts or buyer identities to farmers — but there is no farmer web view anyway. The premium-buyer view shows *that buyer's own* contract only; secondary/composter see offers/pickups, not contracts. Only `/admin` sees all contracts.
**Hand-off:** consumes `GET /batches`, `GET /contracts` (admin) + `GET /contracts/mine` (premium buyer), `POST /contracts/{id}/confirm`, `GET /admin/stream` (SSE), `POST /capture` — defined in §5.

### Slice B — Intake & Comms

**Scope:** the farmer's front door and the outbound message channel.
**Tech:** Telegram Bot API (webhook), FastAPI.
**Deliverables:**
- Telegram webhook handler: receives intent ("harvest 10kg tomatoes"), resolves/creates the `Farmer` (by `telegram_chat_id`), creates a `Batch` at `HARVESTED`, replies with the one-tap `/capture/{token}` link.
- `POST /capture/{token}` upload endpoint: accepts the photo, **stores it under `photos/`** (this storage + the `get_batch_photo(batch)` read seam is owned by B), then advances `harvested→graded_farm`. The **farm grade** is computed inline by calling Slice C's `grade()` and written to `farm_grade` + `grade_reason_farm`. (The more complex **handoff** re-grade is owned by Slice D, not B — see Slice D.)
- `services/messaging.py` → `send_message(chat_id, text)` and `send_farmer_update(chat_id, payout_or_event)`: the single outbound channel. The grade, the reroute reason, the payout — all sent to the farmer through this. **Visibility rule (product spec §4a):** all farmer-facing messages are **grade + outcome + buyer-type-category** framed (e.g. "sold at the Grade B price to the secondary market"), never naming a specific buyer, contract, or destination. The category is read directly from the payout/view model produced by Slice D — B does **not** map destination→category.
- **Demand-feed messaging:** on intent (and on request, e.g. "what's needed?"), the bot messages the farmer the **anonymized demand feed** — crop + grade + rough quantity + urgency, derived by Slice D from open contracts (no buyer/price/contract-id). This is the *only* demand signal a farmer gets (there is no farmer web UI).
**Hand-off:** imports `grade(image, crop)` from Slice C; calls `batch.transition()` from core; **exports `get_batch_photo(batch) -> bytes`** (the photo-storage read seam Slice D's handoff step depends on).

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
- `services/aggregation.py`: pool `GRADED_FARM` batches by crop + grade + geo into a `VirtualShipment` against a `Contract`; compute each batch's `%` contribution. Also derives the **anonymized demand feed** (crop + grade + rough qty + urgency) from open contracts — with no buyer/price/contract-id — for Slice B to message to farmers and for `GET /demand` (used by the admin view).
- Contract matching + the HITL confirm transition (`pooled→contracted` blocked until buyer confirms). `contracted→shipped` (assign `Route`).
- **`services/scheduler.py` — the "spoilage clock" home:** a background task that, after `contracted→shipped`, waits a few seconds (`asyncio.sleep`, for demo pacing — there is no real wall-clock timer in the MVP; the seeded decay-triggered batch is marked at seed time to decay) then triggers the handoff step. A real shelf-life timer is roadmap.
- **`services/handoff.py` — Slice D owns the entire handoff re-grade step:** pulls the stored photo via Slice B's `get_batch_photo(batch)` seam, runs Slice C's `simulate_decay` + `grade`, writes `handoff_grade` + `grade_reason_handoff` and advances `shipped→graded_handoff`, then calls `decide_route`. This is the three-slice interaction (B photo → C decay+grade → D routing) and it has one owner: D.
- `services/routing.py` → `decide_route(batch, handoff_grade, contract, buyers) -> RoutingDecision`: the deterministic rules engine (product spec §10), including returning-leg preference via straight-line lat/lng. Drives `graded_handoff→{delivered | rerouted | composted}` and the `rerouted→delivered_secondary` transition.
- Payout math (product spec §11): farmer payout at delivered grade × destination price/kg; recompute `%` and contract fulfillment on reroute; zero-amount payout on compost; buyer-side short/refund logic. Drives `→paid` transitions. **Payout rows / view models carry a `market_category`** (`premium_market | secondary_market | composted`) — the buyer-type category Slice B reads for farmer messages, so the visibility rule holds at the data layer and B never maps a destination.
- Routing-justification LLM call: turns `{reason_code, from, to, facts}` into the logged justification + the farmer message text (passed to Slice B's `send_message`).
- **Endpoints owned by D** (the routing/payout data they expose): `GET /offers` (secondary buyer's incoming reroute offers), `GET /pickups` (composter's waste pickups), `POST /batches/{id}/dispute` (premium buyer flags a delivered batch → `DISPUTED`).
**Hand-off:** imports `grade()`/`simulate_decay()` from Slice C, `get_batch_photo()` + `send_message()` from Slice B; calls `batch.transition()` from core.

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
    # creates/updates the Payout row; recompute on reroute.
    # INTERNAL only — destination never reaches farmers. The Payout/View
    # also carries market_category (below), which is what B reads.
run_handoff(batch) -> None
    # D's owned handoff step: get_batch_photo -> simulate_decay -> grade
    # -> write handoff_grade -> shipped->graded_handoff -> decide_route

# Slice B exports (Intake)
send_message(chat_id: str, text: str) -> None
    # the single outbound Telegram channel
send_farmer_update(chat_id, event) -> None
    # formats a farmer-facing update using ONLY grade + outcome + market_category
get_batch_photo(batch) -> bytes
    # photo-storage read seam; D's handoff step uses this to fetch the farm photo

# Core exports (Phase 0)
Batch.transition(dest: State, **ctx) -> None   # the ONLY way state changes
publish(event_type: str, payload: dict) -> None  # SSE bus
generate_capture_token(batch) -> token          # for farmer upload links (no login)
# Auth (role-gated REST):
POST /auth/login {email,password} -> sets signed session cookie
GET  /admin/stream          -> text/event-stream (SSE)          [require_admin]
GET  /batches               -> all batches                       [require_admin]
GET  /contracts             -> all contracts                     [require_admin]
GET  /contracts/mine        -> WHERE buyer_id = current_user     [require_buyer(premium)]
POST /contracts/{id}/confirm                                    [require_buyer(premium), owns contract]
POST /batches/{id}/dispute  -> delivered->disputed               [require_buyer(premium), owns batch]  (D)
GET  /offers                -> incoming reroute offers           [require_buyer(secondary)]  (D)
GET  /pickups               -> waste pickups                     [require_composter)]  (D)
GET  /payouts               -> payouts (carry market_category)   [require_admin]
POST /capture/{token}       -> upload photo (token-gated, no login)  (B)
POST /telegram/webhook      -> receives inbound messages         (B)
GET  /demand                -> anonymized demand feed [{crop,grade,qty_band,urgency}]  [require_admin]
                              (the same feed is messaged to farmers by Slice B)
```

**Payout / view-model field (owned by D):** `market_category: "premium_market" | "secondary_market" | "composted"` — the buyer-type category Slice B reads for farmer messages. This is how the visibility rule (product spec §4a) holds at the data layer: the destination is internal, the category is what crosses into farmer-facing code.

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
