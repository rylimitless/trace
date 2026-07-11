"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/handdrawn/Button";
import { Card } from "@/components/handdrawn/Card";
import { Input } from "@/components/handdrawn/Input";
import { Badge } from "@/components/handdrawn/Badge";
import { Tape } from "@/components/handdrawn/Tape";
import { getBatch, type Batch } from "@/lib/api";

const TELEGRAM_BOT_URL =
  process.env.NEXT_PUBLIC_TELEGRAM_BOT_URL ?? "https://t.me/trace_bot";

const NAV_LINKS = [
  { label: "How it works", href: "#how-it-works" },
  { label: "Grade a batch", href: "/login" },
  { label: "For buyers", href: "#for-buyers" },
  { label: "FAQ", href: "#faq" },
];

const HOW_IT_WORKS_STEPS: { title: string; body: string; wobble: 1 | 2 | 3 }[] = [
  {
    title: "Set order",
    body: "Define your volume and quality grade via Telegram.",
    wobble: 1,
  },
  {
    title: "Farms matched",
    body: "We dispatch requirements to verified farm clusters.",
    wobble: 2,
  },
  {
    title: "Quality checked",
    body: "Every batch is physically inspected at handoff points.",
    wobble: 3,
  },
  {
    title: "Delivery",
    body: "Synchronized logistics deliver directly to your door.",
    wobble: 1,
  },
];

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function gradeToBadgeGrade(grade: Batch["farmGrade"]): "A" | "B" | "Waste" {
  if (grade === "A") return "A";
  if (grade === "B") return "B";
  return "Waste";
}

/** Human-readable label for a batch status pill (mirrors mockup's "Delivered" pill). */
function statusLabel(status: Batch["status"]): string {
  switch (status) {
    case "DELIVERED":
    case "DELIVERED_SECONDARY":
      return "Delivered";
    case "PAID":
      return "Paid";
    case "REROUTED":
      return "Rerouted";
    case "COMPOSTED":
      return "Composted";
    case "DISPUTED":
      return "Disputed";
    case "LOST":
      return "Lost";
    default:
      return "In transit";
  }
}

type TrackerState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "found"; batch: Batch }
  | { kind: "not-found"; query: string }
  | { kind: "error"; message: string };

export default function Home() {
  return (
    <>
      <Header />
      <Hero />
      <HowItWorks />
      <TrackerSection />
      <CtaBand />
      <Footer />
    </>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header() {
  return (
    <header className="relative z-10 mx-auto flex w-full max-w-[1024px] items-center justify-between px-4 py-6 md:px-8">
      <div className="flex items-center gap-8">
        <div className="-rotate-2 flex items-center justify-center rounded-full border-2 border-primary bg-white px-4 py-1 shadow-hard">
          <span className="text-2xl font-bold tracking-tight">TRACE</span>
        </div>
        <nav className="hidden items-center gap-6 md:flex">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.label}
              href={link.href}
              className="text-lg transition-colors hover:text-accent"
            >
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
      <Link href="/login">
        <Button variant="white">Grade a batch</Button>
      </Link>
    </header>
  );
}

// ---------------------------------------------------------------------------
// Hero
// ---------------------------------------------------------------------------

function Hero() {
  return (
    <main className="mx-auto grid w-full max-w-[1024px] grid-cols-1 items-center gap-16 px-4 pb-24 pt-12 md:px-8 lg:grid-cols-2">
      {/* Left column: copy */}
      <div className="flex flex-col items-start gap-6">
        <h1 className="text-5xl font-bold leading-tight md:text-6xl">
          Enterprise-Scale Supply, <br /> Rooted in <br />
          <span className="wobble-2 inline-block underline decoration-accent decoration-4">
            Local Farming<span className="text-accent">.</span>
          </span>
        </h1>
        <p className="max-w-xl text-xl leading-relaxed text-gray-800 md:text-2xl">
          Quality-graded, fully traceable produce from Jamaican smallholder
          farms, sourced reliably enough for your menu or your shelf.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-8">
          <Link href="/login">
            <Button variant="accent" className="wobble-1 text-xl">
              <span className="material-symbols-outlined text-xl">
                chevron_right
              </span>
              Get supply
            </Button>
          </Link>
          <Button
            variant="white"
            className="wobble-2 text-xl"
            onClick={() =>
              document
                .getElementById("tracker")
                ?.scrollIntoView({ behavior: "smooth" })
            }
          >
            Track a batch ↓
          </Button>
        </div>
        {/* Decorative testimonial card */}
        <Card wobble={3} className="-ml-32 mt-8 max-w-[200px] p-3">
          <p className="text-sm font-bold leading-snug">
            &ldquo;We saved $12k in our first month just by rerouting Grade B
            produce.&rdquo;
          </p>
          <p className="mt-2 text-xs text-gray-500">— Happy Farm Co.</p>
        </Card>
        <div className="mt-8 opacity-20">
          <span className="material-symbols-outlined text-6xl">gesture</span>
        </div>
      </div>

      {/* Right column: live-tracing demo card */}
      <div className="relative mx-auto w-full max-w-md lg:mx-0 lg:max-w-none">
        <Card wobble={1} className="relative">
          <div className="mb-6 flex items-center justify-between">
            <h3 className="flex items-center gap-2 text-xl font-bold">
              <span className="material-symbols-outlined material-symbols-filled text-accent">
                sensors
              </span>
              Batch #0412 — traced live
            </h3>
            <div className="h-3 w-3 animate-pulse rounded-full border border-primary bg-green-500" />
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col items-center justify-center gap-2 rounded-card border-2 border-dashed border-gray-400 bg-paper/50 p-4 text-center">
              <span className="material-symbols-outlined text-3xl">
                agriculture
              </span>
              <div className="text-lg font-bold">Farm: Grade A</div>
            </div>
            <div className="relative flex flex-col items-center justify-center gap-2 overflow-hidden rounded-card border-2 border-dashed border-gray-400 bg-paper/50 p-4 text-center">
              <div className="pointer-events-none absolute inset-0 bg-accent/5" />
              <span className="material-symbols-outlined material-symbols-filled text-3xl text-accent">
                warehouse
              </span>
              <div className="text-lg font-bold text-accent">
                Handoff: Grade B
              </div>
            </div>
          </div>
          <div className="mt-6 flex items-center gap-2 border-t-2 border-primary/10 pt-4">
            <span className="material-symbols-outlined text-sm">info</span>
            <p className="text-lg italic leading-tight">
              Rerouted to school feeding · payout unchanged
            </p>
          </div>
          {/* Floating badge */}
          <div className="wobble-2 absolute -bottom-6 -right-6 flex h-24 w-24 items-center justify-center rounded-full border-2 border-primary bg-accent p-2 text-center leading-none text-white shadow-hard">
            <span className="text-lg font-bold">40kg saved</span>
          </div>
        </Card>
        {/* Background decorative tilted outline */}
        <div className="wobble-3 absolute -inset-4 -z-10 rounded-card border-2 border-primary opacity-10" />

        {/* Illustration placeholder */}
        <div className="relative mt-16">
          <div className="h-48 w-full -rotate-1 transform overflow-hidden rounded-card border-2 border-primary bg-white shadow-hard">
            <div
              className="h-full w-full bg-gray-300"
              role="img"
              aria-label="Hand-drawn illustration of a delivery truck driving through farm fields"
            />
          </div>
          <Tape className="left-1/2" />
        </div>
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// How it works
// ---------------------------------------------------------------------------

function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="mx-auto max-w-[1024px] px-4 py-12 md:px-8"
    >
      <h2 className="wobble-1 mb-16 inline-block w-full text-center text-3xl font-bold underline decoration-accent decoration-4 md:text-4xl">
        How your supply comes together
      </h2>
      <div className="relative">
        <div className="absolute left-0 top-12 -z-10 hidden h-0.5 w-full border-t-2 border-dashed border-primary/30 md:block" />
        <div className="grid grid-cols-1 gap-8 md:grid-cols-4">
          {HOW_IT_WORKS_STEPS.map((step, i) => (
            <div
              key={step.title}
              className="flex flex-col items-center gap-4 text-center"
            >
              <div
                className={`wobble-${step.wobble} flex h-16 w-16 items-center justify-center border-2 border-primary bg-white shadow-hard`}
              >
                <span
                  className={`text-3xl font-bold ${i === 0 ? "text-accent" : ""}`}
                >
                  {i + 1}
                </span>
              </div>
              <div>
                <h3 className="mb-2 text-xl font-bold">{step.title}</h3>
                <p className="text-lg leading-snug text-gray-800">
                  {step.body}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Tracker section
// ---------------------------------------------------------------------------

function TrackerSection() {
  const [query, setQuery] = useState("");
  const [state, setState] = useState<TrackerState>({ kind: "idle" });

  async function handleTrack(e: FormEvent) {
    e.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    setState({ kind: "loading" });
    try {
      const batch = await getBatch(trimmed);
      if (batch) {
        setState({ kind: "found", batch });
      } else {
        setState({ kind: "not-found", query: trimmed });
      }
    } catch (err) {
      setState({
        kind: "error",
        message: err instanceof Error ? err.message : "Something went wrong.",
      });
    }
  }

  return (
    <section id="tracker" className="bg-white/40 px-4 py-12 md:px-8">
      <div className="mx-auto max-w-[1024px]">
        <div className="grid grid-cols-1 items-start gap-12 lg:grid-cols-2">
          {/* Left column: input + status */}
          <div className="flex flex-col gap-8">
            <div className="rounded-xl border border-primary/10 bg-white p-8 shadow-sm">
              <h2 className="mb-6 text-2xl font-bold">Enter a batch number</h2>
              <form className="flex gap-4" onSubmit={handleTrack}>
                <Input
                  placeholder="0412"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  aria-label="Batch number"
                />
                <Button
                  type="submit"
                  variant="accent"
                  disabled={state.kind === "loading"}
                >
                  {state.kind === "loading" ? "Tracking…" : "Track"}
                </Button>
              </form>
            </div>

            <TrackerResult state={state} />
          </div>

          {/* Right column: restricted teaser */}
          <RestrictedPanel />
        </div>
      </div>
    </section>
  );
}

function TrackerResult({ state }: { state: TrackerState }) {
  if (state.kind === "idle") {
    return (
      <Card className="text-gray-500">
        <p className="text-lg">
          Enter a batch number above to see its farm-to-buyer trace — try{" "}
          <span className="font-bold text-primary">0412</span>.
        </p>
      </Card>
    );
  }

  if (state.kind === "loading") {
    return (
      <div className="rounded-xl border border-primary/10 bg-white p-8 shadow-sm">
        <p className="text-lg text-gray-500">Looking up batch…</p>
      </div>
    );
  }

  if (state.kind === "not-found") {
    return (
      <div className="rounded-xl border border-primary/10 bg-white p-8 shadow-sm">
        <p className="text-lg font-bold">
          No batch found for &ldquo;{state.query}&rdquo;.
        </p>
        <p className="mt-2 text-sm text-gray-500">
          Double-check the batch number, or try the demo batch{" "}
          <span className="font-bold text-primary">0412</span>.
        </p>
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="rounded-xl border border-accent/40 bg-white p-8 shadow-sm">
        <p className="text-lg font-bold text-accent">Couldn&apos;t load that batch.</p>
        <p className="mt-2 text-sm text-gray-500">{state.message}</p>
      </div>
    );
  }

  const { batch } = state;
  return (
    <div className="rounded-xl border border-primary/10 bg-white p-8 shadow-sm">
      <div className="mb-8 flex items-center justify-between">
        <h3 className="text-xl font-bold">
          Batch #{batch.batchNumber} · {batch.crop}
        </h3>
        <span className="flex items-center gap-2 rounded-full bg-green-500/10 px-3 py-1 text-sm font-bold text-green-500">
          <span className="h-2 w-2 rounded-full bg-green-500" />
          {statusLabel(batch.status)}
        </span>
      </div>
      <div className="mb-8 grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-dashed border-gray-300 p-4">
          <p className="text-xs uppercase text-gray-500">Farm Level</p>
          <p className="text-lg font-bold">
            {batch.farmGrade ? `Grade ${batch.farmGrade}` : "Pending"}
          </p>
          <p className="text-sm text-gray-600">
            {batch.farmLocationLabel ?? "—"}
          </p>
        </div>
        <div className="rounded-lg border border-dashed border-gray-300 p-4">
          <p className="text-xs uppercase text-gray-500">Handoff Level</p>
          <p className="text-lg font-bold">
            {batch.handoffGrade ? `Grade ${batch.handoffGrade}` : "Pending"}
          </p>
          <p className="text-sm text-gray-600">
            {batch.handoffLocationLabel ?? "—"}
          </p>
        </div>
      </div>
      {batch.finalGrade && (
        <div className="mb-8">
          <Badge grade={gradeToBadgeGrade(batch.finalGrade)} />
        </div>
      )}
      <div className="relative space-y-6 before:absolute before:bottom-2 before:left-[7px] before:top-2 before:w-0.5 before:bg-gray-100">
        {batch.timeline.map((entry) => (
          <div
            key={`${entry.label}-${entry.timestamp}`}
            className="relative flex items-start gap-4"
          >
            <div className="z-10 mt-1.5 h-4 w-4 rounded-full bg-accent" />
            <div>
              <p className="font-bold">{entry.label}</p>
              <p className="text-sm text-gray-500">
                {formatTimestamp(entry.timestamp)}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Static, non-interactive "graders only" teaser. Batch/access-code fields
 * are visually masked (not real inputs), grade buttons render but do
 * nothing, and the submit button doesn't submit anything real — actual
 * grading corrections happen in the authenticated /admin view (not built
 * yet). This is marketing-only per the approved scope decision.
 */
function RestrictedPanel() {
  return (
    <Card wobble={2} className="bg-white p-8">
      <div className="mb-6 flex items-center gap-2 text-gray-500">
        <span className="material-symbols-outlined text-lg">lock</span>
        <span className="text-lg">Restricted — graders only</span>
      </div>

      <div className="space-y-4">
        <div>
          <label className="mb-2 block text-gray-600">Batch number</label>
          <div className="rounded-lg border-2 border-primary bg-primary p-4 font-bold text-white/70">
            0412
          </div>
        </div>

        <div>
          <label className="mb-2 block text-gray-600">Access code</label>
          <div className="rounded-lg border-2 border-primary bg-primary p-4 tracking-widest text-white/70">
            ••••••
          </div>
        </div>

        <div>
          <label className="mb-4 block text-gray-600">New grade</label>
          <div className="grid grid-cols-3 gap-3">
            <button
              type="button"
              disabled
              aria-disabled="true"
              className="cursor-not-allowed rounded-lg border-2 border-green-500 bg-green-500/10 py-3 font-bold text-green-500"
            >
              A
            </button>
            <button
              type="button"
              disabled
              aria-disabled="true"
              className="cursor-not-allowed rounded-lg border-2 border-gray-300 bg-white py-3 font-bold"
            >
              B
            </button>
            <button
              type="button"
              disabled
              aria-disabled="true"
              className="cursor-not-allowed rounded-lg border-2 border-gray-300 bg-white py-3 font-bold"
            >
              Waste
            </button>
          </div>
        </div>

        <Button
          type="button"
          variant="primary"
          disabled
          className="mt-4 w-full"
        >
          Submit correction
        </Button>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// CTA band + footer
// ---------------------------------------------------------------------------

function CtaBand() {
  return (
    <section className="bg-[#fdf1f0] px-4 py-24 text-center md:px-8">
      <div className="mx-auto max-w-[1024px]">
        <h2 className="mb-4 text-5xl font-bold md:text-6xl">
          Ready to source{" "}
          <span className="text-accent underline decoration-accent decoration-4">
            better?
          </span>
        </h2>
        <p className="mb-12 text-xl text-gray-800 md:text-2xl">
          Join 40+ Caribbean hospitality partners sourcing through TRACE.
        </p>
        <a href={TELEGRAM_BOT_URL} target="_blank" rel="noreferrer">
          <Button variant="accent" className="wobble-1 mx-auto text-2xl">
            <span className="material-symbols-outlined text-2xl">send</span>
            Launch Telegram Bot
          </Button>
        </a>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t-2 border-dashed border-primary/20 bg-white px-4 py-12 md:px-8">
      <div className="mx-auto flex max-w-[1024px] flex-col items-center justify-between gap-8 md:flex-row">
        <div className="flex flex-col items-center gap-2 md:items-start">
          <span className="text-3xl font-bold tracking-tight">TRACE</span>
          <p className="text-sm text-gray-500">
            © 2026 TRACE. Human Logistics Sourcing.
          </p>
        </div>
        <nav className="flex flex-wrap justify-center gap-6 text-lg">
          <a href="#" className="transition-colors hover:text-accent">
            Privacy Policy
          </a>
          <a href="#" className="transition-colors hover:text-accent">
            Terms of Service
          </a>
          <a href="#" className="transition-colors hover:text-accent">
            Sustainability Report
          </a>
        </nav>
        <div className="flex gap-4">
          <div className="wobble-2 flex h-10 w-10 items-center justify-center border-2 border-primary bg-white shadow-hard">
            <span className="material-symbols-outlined text-xl">public</span>
          </div>
          <div className="wobble-3 flex h-10 w-10 items-center justify-center border-2 border-primary bg-white shadow-hard">
            <span className="material-symbols-outlined text-xl">mail</span>
          </div>
        </div>
      </div>
    </footer>
  );
}
