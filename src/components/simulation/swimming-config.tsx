"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import type { SwimmingConfig, FatigueConfig } from "@/types/route";

interface Props {
  onSimulate: (config: SwimmingConfig, fatigue: FatigueConfig) => void;
  running: boolean;
}

export function SwimmingConfigForm({ onSimulate, running }: Props) {
  const [css, setCss] = useState(95); // sec/100m
  const [weight, setWeight] = useState(70);
  const [height, setHeight] = useState(175);
  const [isOpenWater, setIsOpenWater] = useState(true);
  const [hasWetsuit, setHasWetsuit] = useState(false);
  const [waterTemp, setWaterTemp] = useState(20);
  const [currentSpeed, setCurrentSpeed] = useState(0);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => r.json())
      .then((data) => {
        if (data && !data.error) {
          if (data.css != null) setCss(data.css);
          if (data.weight != null) setWeight(data.weight);
          if (data.height != null) setHeight(data.height);
          if (data.swimHasWetsuit != null) setHasWetsuit(data.swimHasWetsuit);
        }
      })
      .catch(() => {});
  }, []);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(3);
  const [fatigueMin, setFatigueMin] = useState(0.8);

  return (
    <div className="space-y-4 pt-4">
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>CSS (sec/100m)</Label>
          <Input
            type="number"
            value={css}
            onChange={(e) => setCss(Number(e.target.value))}
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
          <Label>Height (cm)</Label>
          <Input
            type="number"
            value={height}
            onChange={(e) => setHeight(Number(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Water temp (°C)</Label>
          <Input
            type="number"
            value={waterTemp}
            onChange={(e) => setWaterTemp(Number(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Current speed (m/s)</Label>
          <Input
            type="number"
            step="0.1"
            value={currentSpeed}
            onChange={(e) => setCurrentSpeed(Number(e.target.value))}
          />
        </div>
      </div>

      <div className="flex gap-4">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={isOpenWater}
            onChange={(e) => setIsOpenWater(e.target.checked)}
            className="rounded border-input"
          />
          Open water
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={hasWetsuit}
            onChange={(e) => setHasWetsuit(e.target.checked)}
            className="rounded border-input"
          />
          Wetsuit
        </label>
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
          min={0.5}
          max={1}
          step={0.05}
        />
      </div>

      <Button
        onClick={() =>
          onSimulate(
            {
              css,
              weight,
              height,
              isOpenWater,
              hasWetsuit,
              waterTemperature: waterTemp,
              currentSpeed: currentSpeed || undefined,
            },
            { halfLife: fatigueHalfLife, minFactor: fatigueMin }
          )
        }
        disabled={running}
        className="w-full"
      >
        {running ? "Simulating..." : "Run Swimming Simulation"}
      </Button>
    </div>
  );
}
