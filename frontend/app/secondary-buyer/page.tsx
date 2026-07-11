"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/handdrawn/Badge";
import { Card } from "@/components/handdrawn/Card";
import { getOffers, type Offer } from "@/lib/api";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready" };

function toBadgeGrade(grade: string): "A" | "B" | "Waste" {
  if (grade === "A") return "A";
  if (grade === "B") return "B";
  return "Waste";
}

/**
 * Secondary-buyer reroute offers. Per product requirement, this view must
 * never render a contract or a specific buyer identity — only grade, kg, and
 * price are shown, even if the Offer object were to carry other fields.
 */
export default function SecondaryBuyerPage() {
  const [offers, setOffers] = useState<Offer[]>([]);
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getOffers();
        if (cancelled) return;
        setOffers(data);
        setState({ kind: "ready" });
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to load offers.",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="mx-auto w-full max-w-[1024px] px-4 py-12 md:px-8">
      <h1 className="mb-2 text-4xl font-bold">Incoming reroute offers</h1>
      <p className="mb-10 text-lg text-gray-600">
        Grade, quantity, and price for produce available to reroute your way.
      </p>

      {state.kind === "loading" && <p className="text-lg text-gray-500">Loading offers…</p>}

      {state.kind === "error" && (
        <Card className="border-accent text-accent">
          <p className="font-bold">Couldn&apos;t load offers.</p>
          <p className="mt-2 text-sm">{state.message}</p>
        </Card>
      )}

      {state.kind === "ready" && offers.length === 0 && (
        <Card className="text-gray-500">
          <p className="text-lg">No offers right now — check back soon.</p>
        </Card>
      )}

      {state.kind === "ready" && offers.length > 0 && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 lg:grid-cols-3">
          {offers.map((offer, i) => (
            <Card key={offer.id} wobble={((i % 3) + 1) as 1 | 2 | 3}>
              <div className="mb-4 flex items-start justify-between gap-4">
                <h2 className="text-xl font-bold capitalize">{offer.crop}</h2>
                <Badge grade={toBadgeGrade(offer.grade)} />
              </div>
              <p className="mb-2 text-lg text-gray-800">
                <span className="font-bold">{offer.kg} kg</span> available
              </p>
              <p className="text-lg text-gray-800">
                <span className="font-bold">${offer.pricePerKg.toFixed(2)}</span> / kg
              </p>
            </Card>
          ))}
        </div>
      )}
    </main>
  );
}
