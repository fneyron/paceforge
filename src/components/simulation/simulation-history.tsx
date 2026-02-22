"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { formatTime } from "@/lib/physics/simulate";
import type { SimulationResult } from "@/types/route";

interface SimulationSummary {
  id: string;
  name: string | null;
  sport: string;
  totalTime: number;
  createdAt: string;
}

interface Props {
  routeId: string;
  onLoad: (results: SimulationResult & { id: string }) => void;
  onCompare: (a: SimulationResult & { id: string; name: string }, b: SimulationResult & { id: string; name: string }) => void;
}

export function SimulationHistory({ routeId, onLoad, onCompare }: Props) {
  const [sims, setSims] = useState<SimulationSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [compareIds, setCompareIds] = useState<Set<string>>(new Set());
  const [comparing, setComparing] = useState(false);

  useEffect(() => {
    fetch(`/api/routes/${routeId}/simulations`)
      .then((r) => r.json())
      .then(setSims)
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [routeId]);

  const handleLoad = async (simId: string) => {
    const res = await fetch(`/api/routes/${routeId}/simulations/${simId}`);
    if (res.ok) {
      const data = await res.json();
      onLoad({ ...data.results, id: data.id });
    }
  };

  const toggleCompare = (id: string) => {
    setCompareIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else if (next.size < 2) {
        next.add(id);
      }
      return next;
    });
  };

  const handleCompare = async () => {
    const ids = Array.from(compareIds);
    if (ids.length !== 2) return;

    setComparing(true);
    try {
      const [resA, resB] = await Promise.all(
        ids.map((id) =>
          fetch(`/api/routes/${routeId}/simulations/${id}`).then((r) => r.json())
        )
      );
      onCompare(
        { ...resA.results, id: resA.id, name: resA.name || "Simulation A" },
        { ...resB.results, id: resB.id, name: resB.name || "Simulation B" }
      );
    } finally {
      setComparing(false);
    }
  };

  if (loading) return <p className="text-xs text-muted-foreground">Loading history...</p>;
  if (sims.length === 0) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium">History ({sims.length})</h4>
        {compareIds.size === 2 && (
          <Button size="sm" variant="outline" className="h-6 text-xs" onClick={handleCompare} disabled={comparing}>
            {comparing ? "Loading..." : "Compare Selected"}
          </Button>
        )}
      </div>
      <div className="space-y-1 max-h-48 overflow-y-auto">
        {sims.map((sim) => (
          <div
            key={sim.id}
            className="flex items-center gap-2 text-xs border rounded px-2 py-1.5"
          >
            <input
              type="checkbox"
              checked={compareIds.has(sim.id)}
              onChange={() => toggleCompare(sim.id)}
              disabled={!compareIds.has(sim.id) && compareIds.size >= 2}
              className="shrink-0"
            />
            <div className="flex-1 min-w-0">
              <p className="truncate font-medium">{sim.name || sim.sport}</p>
              <p className="text-muted-foreground">
                {formatTime(sim.totalTime)} &middot;{" "}
                {new Date(sim.createdAt).toLocaleDateString()}
              </p>
            </div>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 text-xs shrink-0"
              onClick={() => handleLoad(sim.id)}
            >
              Load
            </Button>
          </div>
        ))}
      </div>
    </div>
  );
}
