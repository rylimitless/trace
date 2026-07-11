"use client";

import { useEffect, useState } from "react";
import { Badge } from "@/components/handdrawn/Badge";
import { Button } from "@/components/handdrawn/Button";
import { Card } from "@/components/handdrawn/Card";
import {
  confirmContract,
  disputeBatch,
  getMyContracts,
  type Contract,
} from "@/lib/api";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready" };

/** Contract.grade is a free-form string on the wire; narrow it for the Badge component. */
function toBadgeGrade(grade: string): "A" | "B" | "Waste" {
  if (grade === "A") return "A";
  if (grade === "B") return "B";
  return "Waste";
}

function fulfillmentLabel(status: Contract["status"]): string {
  switch (status) {
    case "fulfilled":
      return "fully fulfilled";
    case "fulfilling":
      return "partially fulfilled — still filling";
    case "short":
      return "running short";
    default:
      return "open, awaiting supply";
  }
}

export default function PremiumBuyerPage() {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const mine = await getMyContracts();
        if (cancelled) return;
        setContracts(mine);
        setState({ kind: "ready" });
      } catch (err) {
        if (cancelled) return;
        setState({
          kind: "error",
          message: err instanceof Error ? err.message : "Failed to load your contracts.",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleConfirm(contract: Contract) {
    setBusyId(contract.id);
    setNotice(null);
    try {
      await confirmContract(contract.id);
      setContracts((prev) =>
        prev.map((c) => (c.id === contract.id ? { ...c, status: "fulfilled" } : c))
      );
      setNotice(`Confirmed your ${contract.crop} contract.`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Couldn't confirm that contract.");
    } finally {
      setBusyId(null);
    }
  }

  // NOTE: the Contract type has no linked batchId in this task's data model —
  // /batches/{id}/dispute is called using the contract's own id as the batch
  // reference. Good enough for this slice; a real batchId field on Contract
  // would replace this once the backend contract shape carries one.
  async function handleDispute(contract: Contract) {
    setBusyId(contract.id);
    setNotice(null);
    try {
      await disputeBatch(contract.id);
      setNotice(`Dispute filed for your ${contract.crop} contract's batch.`);
    } catch (err) {
      setNotice(err instanceof Error ? err.message : "Couldn't file that dispute.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="mx-auto w-full max-w-[1024px] px-4 py-12 md:px-8">
      <h1 className="mb-2 text-4xl font-bold">Your contracts</h1>
      <p className="mb-10 text-lg text-gray-600">
        These are your own premium-market contracts only.
      </p>

      {state.kind === "loading" && <p className="text-lg text-gray-500">Loading your contracts…</p>}

      {state.kind === "error" && (
        <Card className="border-accent text-accent">
          <p className="font-bold">Couldn&apos;t load your contracts.</p>
          <p className="mt-2 text-sm">{state.message}</p>
        </Card>
      )}

      {notice && (
        <div className="mb-6 rounded-card border-2 border-primary bg-white px-4 py-3 text-sm font-bold shadow-hard">
          {notice}
        </div>
      )}

      {state.kind === "ready" && contracts.length === 0 && (
        <Card className="text-gray-500">
          <p className="text-lg">You have no contracts yet.</p>
        </Card>
      )}

      {state.kind === "ready" && contracts.length > 0 && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          {contracts.map((contract, i) => (
            <Card key={contract.id} wobble={((i % 3) + 1) as 1 | 2 | 3}>
              <div className="mb-4 flex items-start justify-between gap-4">
                <h2 className="text-xl font-bold">
                  Your {contract.crop} contract
                </h2>
                <Badge grade={toBadgeGrade(contract.grade)} />
              </div>
              <p className="mb-2 text-lg text-gray-800">
                Target: <span className="font-bold">{contract.kgTarget} kg</span>
              </p>
              <p className="mb-6 text-sm text-gray-600">
                Status: {fulfillmentLabel(contract.status)}
              </p>
              <div className="flex flex-wrap gap-3">
                <Button
                  variant="primary"
                  disabled={busyId === contract.id}
                  onClick={() => handleConfirm(contract)}
                >
                  {busyId === contract.id ? "Working…" : "Confirm"}
                </Button>
                <Button
                  variant="accent"
                  disabled={busyId === contract.id}
                  onClick={() => handleDispute(contract)}
                >
                  Dispute
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </main>
  );
}
