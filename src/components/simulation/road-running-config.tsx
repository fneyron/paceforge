"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import type { RoadRunningConfig, FatigueConfig } from "@/types/route";

interface Props {
  onSimulate: (config: RoadRunningConfig, fatigue: FatigueConfig) => void;
  running: boolean;
}

export function RoadRunningConfigForm({ onSimulate, running }: Props) {
  const [vdot, setVdot] = useState(50);
  const [weight, setWeight] = useState(70);
  const [temperature, setTemperature] = useState(15);
  const [humidity, setHumidity] = useState(50);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => r.json())
      .then((data) => {
        if (data && !data.error) {
          if (data.vdot != null) setVdot(data.vdot);
          if (data.weight != null) setWeight(data.weight);
        }
      })
      .catch(() => {});
  }, []);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(4);
  const [fatigueMin, setFatigueMin] = useState(0.75);

  return (
    <div className="space-y-4 pt-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>VDOT</Label>
          <Input
            type="number"
            value={vdot}
            onChange={(e) => setVdot(Number(e.target.value))}
            min={30}
            max={85}
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
          <Label>Temperature (°C)</Label>
          <Input
            type="number"
            value={temperature}
            onChange={(e) => setTemperature(Number(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Humidity (%)</Label>
          <Input
            type="number"
            value={humidity}
            onChange={(e) => setHumidity(Number(e.target.value))}
            min={0}
            max={100}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label>Fatigue half-life: {fatigueHalfLife}h</Label>
        <Slider
          value={[fatigueHalfLife]}
          onValueChange={([v]) => setFatigueHalfLife(v)}
          min={1}
          max={10}
          step={0.5}
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
            { vdot, weight, temperature, humidity },
            { halfLife: fatigueHalfLife, minFactor: fatigueMin }
          )
        }
        disabled={running}
        className="w-full"
      >
        {running ? "Simulating..." : "Run Road Running Simulation"}
      </Button>
    </div>
  );
}
