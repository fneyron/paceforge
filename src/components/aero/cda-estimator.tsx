"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface Props {
  onEstimate?: (cda: number) => void;
}

export function CdAEstimator({ onEstimate }: Props) {
  const [estimating, setEstimating] = useState(false);
  const [result, setResult] = useState<{
    cda: number;
    confidence: number;
    sampleCount: number;
  } | null>(null);
  const [weight, setWeight] = useState(75);
  const [bikeWeight, setBikeWeight] = useState(8);
  const [error, setError] = useState<string | null>(null);

  const handleEstimate = async () => {
    setEstimating(true);
    setError(null);

    try {
      const res = await fetch("/api/aero/estimate-cda", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ weight, bikeWeight }),
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.error || "Estimation failed");
      }

      const data = await res.json();
      setResult(data);
      if (data.cda && onEstimate) {
        onEstimate(data.cda);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to estimate CdA"
      );
    } finally {
      setEstimating(false);
    }
  };

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium">CdA Estimation from Strava</h3>
      <p className="text-xs text-muted-foreground">
        Estimates your CdA from power and speed data on flat segments in your
        recent Strava rides.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1">
          <Label className="text-xs">Rider weight (kg)</Label>
          <Input
            type="number"
            value={weight}
            onChange={(e) => setWeight(Number(e.target.value))}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Bike weight (kg)</Label>
          <Input
            type="number"
            value={bikeWeight}
            onChange={(e) => setBikeWeight(Number(e.target.value))}
          />
        </div>
      </div>

      <Button
        onClick={handleEstimate}
        disabled={estimating}
        className="w-full"
      >
        {estimating ? "Analyzing rides..." : "Estimate CdA from Strava"}
      </Button>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {result && (
        <div className="border rounded-md p-3 space-y-2">
          <div className="flex justify-between">
            <span className="text-sm">Estimated CdA</span>
            <span className="font-medium">{result.cda} m²</span>
          </div>
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>Confidence: {(result.confidence * 100).toFixed(0)}%</span>
            <span>{result.sampleCount} data points</span>
          </div>
        </div>
      )}
    </div>
  );
}
