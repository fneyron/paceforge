"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import type { TrailConfig, FatigueConfig } from "@/types/route";

interface Props {
  onSimulate: (config: TrailConfig, fatigue: FatigueConfig) => void;
  running: boolean;
}

export function TrailConfigForm({ onSimulate, running }: Props) {
  const [vma, setVma] = useState(15);
  const [weight, setWeight] = useState(70);
  const [packWeight, setPackWeight] = useState(3);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => r.json())
      .then((data) => {
        if (data && !data.error) {
          if (data.vma != null) setVma(data.vma);
          if (data.weight != null) setWeight(data.weight);
        }
      })
      .catch(() => {});
  }, []);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(10);
  const [fatigueMin, setFatigueMin] = useState(0.6);

  return (
    <div className="space-y-4 pt-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>VMA (km/h)</Label>
          <Input
            type="number"
            step="0.5"
            value={vma}
            onChange={(e) => setVma(Number(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Weight (kg)</Label>
          <Input
            type="number"
            value={weight}
            onChange={(e) => setWeight(Number(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Pack weight (kg)</Label>
          <Input
            type="number"
            step="0.5"
            value={packWeight}
            onChange={(e) => setPackWeight(Number(e.target.value))}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label>Fatigue half-life: {fatigueHalfLife}h</Label>
        <Slider
          value={[fatigueHalfLife]}
          onValueChange={([v]) => setFatigueHalfLife(v)}
          min={2}
          max={30}
          step={1}
        />
      </div>

      <div className="space-y-2">
        <Label>Min performance: {(fatigueMin * 100).toFixed(0)}%</Label>
        <Slider
          value={[fatigueMin]}
          onValueChange={([v]) => setFatigueMin(v)}
          min={0.3}
          max={1}
          step={0.05}
        />
      </div>

      <Button
        onClick={() =>
          onSimulate(
            { vma, weight, packWeight },
            { halfLife: fatigueHalfLife, minFactor: fatigueMin }
          )
        }
        disabled={running}
        className="w-full"
      >
        {running ? "Simulating..." : "Run Trail Simulation"}
      </Button>
    </div>
  );
}
