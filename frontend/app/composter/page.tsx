"use client";

import { useEffect, useState } from "react";
import { Card } from "@/components/handdrawn/Card";
import { getPickups, type Pickup } from "@/lib/api";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready" };

function etaLabel(minutes: number): string {
  if (minutes <= 0) return "arriving now";
  if (minutes < 60) return `~${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  return rem === 0 ? `~${hours}h` : `~${hours}h ${rem}m`;
}

/**
 * Composter waste-pickup view. Per product requirement, this view must never
 * render a contract or a specific buyer identity — only kg and the returning
 * leg's ETA are shown, even if the Pickup object were to carry other fields.
 */
export default function ComposterPage() {
  const [pickups, setPickups] = useState<Pickup[]>([]);
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getPickups();
        if (cancelled) return;
        setPickups(data);
        setState({ kind: "ready" });
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to load pickups.",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto w-full max-w-[1024px] px-4 py-12 md:px-8">
      <h1 className="mb-2 text-4xl font-bold">Inbound waste pickups</h1>
      <p className="mb-10 text-lg text-gray-600">
        Weight and estimated arrival for produce routed to compost.
      </p>

      {state.kind === "loading" && <p className="text-lg text-gray-500">Loading pickups…</p>}

      {state.kind === "error" && (
        <Card className="border-accent text-accent">
          <p className="font-bold">Couldn&apos;t load pickups.</p>
          <p className="mt-2 text-sm">{state.message}</p>
        </Card>
      )}

      {state.kind === "ready" && pickups.length === 0 && (
        <Card className="text-gray-500">
          <p className="text-lg">No pickups scheduled right now.</p>
        </Card>
      )}

      {state.kind === "ready" && pickups.length > 0 && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {pickups.map((pickup, i) => (
            <Card key={pickup.id} wobble={((i % 3) + 1) as 1 | 2 | 3}>
              <h2 className="mb-4 text-xl font-bold capitalize">{pickup.crop}</h2>
              <p className="mb-2 text-lg text-gray-800">
                <span className="font-bold">{pickup.kg} kg</span> for compost
              </p>
              <p className="text-lg text-gray-800">
                ETA: <span className="font-bold">{etaLabel(pickup.etaMinutes)}</span>
              </p>
            </Card>
          ))}
        </div>
      )}
    </main>
  );
}
