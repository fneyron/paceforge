"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { LactateChart } from "./lactate-chart";
import { analyzeLactate, type LactateStep, type AnalysisResult } from "@/lib/lactate/analysis";

interface LactateTestSummary {
  id: string;
  testDate: string;
  protocol: "running" | "cycling";
  steps: LactateStep[];
  lt1Speed: number | null;
  lt2Speed: number | null;
  lt1Lactate: number | null;
  lt2Lactate: number | null;
  lt1HR: number | null;
  lt2HR: number | null;
}

interface Props {
  onThresholdApplied?: (lt2Speed: number, lt2HR?: number) => void;
}

export function LactateHistory({ onThresholdApplied }: Props) {
  const [tests, setTests] = useState<LactateTestSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [analyses, setAnalyses] = useState<Record<string, AnalysisResult>>({});

  useEffect(() => {
    fetch("/api/athlete/lactate-tests")
      .then((r) => (r.ok ? r.json() : []))
      .then((data) => {
        setTests(data);
        // Pre-compute analyses
        const a: Record<string, AnalysisResult> = {};
        for (const test of data) {
          a[test.id] = analyzeLactate(test.steps);
        }
        setAnalyses(a);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleDelete = async (id: string) => {
    const res = await fetch(`/api/athlete/lactate-tests/${id}`, { method: "DELETE" });
    if (res.ok) {
      setTests((prev) => prev.filter((t) => t.id !== id));
    }
  };

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading tests...</p>;
  }

  if (tests.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No lactate tests yet. Click &ldquo;New Test&rdquo; to start a protocol.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {tests.map((test) => {
        const analysis = analyses[test.id];
        const isExpanded = expanded === test.id;
        const valueUnit = test.protocol === "running" ? "km/h" : "W";

        return (
          <div key={test.id} className="border rounded-lg p-3">
            <div
              className="flex items-center justify-between cursor-pointer"
              onClick={() => setExpanded(isExpanded ? null : test.id)}
            >
              <div className="flex items-center gap-3">
                <span className="text-sm font-medium">
                  {new Date(test.testDate).toLocaleDateString()}
                </span>
                <span className="text-xs text-muted-foreground capitalize">
                  {test.protocol}
                </span>
              </div>
              <div className="flex items-center gap-3 text-xs">
                {test.lt1Speed && (
                  <span className="text-green-600">
                    LT1: {test.lt1Speed.toFixed(1)} {valueUnit}
                  </span>
                )}
                {test.lt2Speed && (
                  <span className="text-red-600">
                    LT2: {test.lt2Speed.toFixed(1)} {valueUnit}
                  </span>
                )}
                <span className="text-muted-foreground">
                  {isExpanded ? "▲" : "▼"}
                </span>
              </div>
            </div>

            {isExpanded && analysis && (
              <div className="mt-3 space-y-3">
                <LactateChart
                  steps={test.steps}
                  analysis={analysis}
                  protocol={test.protocol}
                  compact
                />
                <div className="flex gap-2 justify-end">
                  {test.lt2HR && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => onThresholdApplied?.(test.lt2Speed!, test.lt2HR ?? undefined)}
                    >
                      Apply to Profile
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-destructive"
                    onClick={() => handleDelete(test.id)}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
