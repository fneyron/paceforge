"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import type { NutritionStrategy } from "@/types/route";

interface Props {
  onGenerate: (strategy: NutritionStrategy) => void;
  generating: boolean;
}

export function NutritionConfig({ onGenerate, generating }: Props) {
  const [carbsPerHour, setCarbsPerHour] = useState(60);
  const [fluidPerHour, setFluidPerHour] = useState(600);
  const [sodiumPerHour, setSodiumPerHour] = useState(600);
  const [useCaffeine, setUseCaffeine] = useState(false);
  const [caffeineStart, setCaffeineStart] = useState(2);
  const [caffeineDose, setCaffeineDose] = useState(100);
  const [caffeineMax, setCaffeineMax] = useState(400);

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Carbs: {carbsPerHour} g/h</Label>
        <Slider
          value={[carbsPerHour]}
          onValueChange={([v]) => setCarbsPerHour(v)}
          min={30}
          max={120}
          step={5}
        />
        <p className="text-xs text-muted-foreground">
          60-90g/h for events 2h+. Up to 120g/h with dual-source carbs.
        </p>
      </div>

      <div className="space-y-2">
        <Label>Fluid: {fluidPerHour} ml/h</Label>
        <Slider
          value={[fluidPerHour]}
          onValueChange={([v]) => setFluidPerHour(v)}
          min={200}
          max={1000}
          step={50}
        />
      </div>

      <div className="space-y-2">
        <Label>Sodium: {sodiumPerHour} mg/h</Label>
        <Slider
          value={[sodiumPerHour]}
          onValueChange={([v]) => setSodiumPerHour(v)}
          min={200}
          max={1500}
          step={50}
        />
      </div>

      <div className="space-y-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={useCaffeine}
            onChange={(e) => setUseCaffeine(e.target.checked)}
            className="rounded border-input"
          />
          Caffeine strategy
        </label>

        {useCaffeine && (
          <div className="grid grid-cols-3 gap-3 pl-6">
            <div className="space-y-1">
              <Label className="text-xs">Start after (h)</Label>
              <Input
                type="number"
                value={caffeineStart}
                onChange={(e) => setCaffeineStart(Number(e.target.value))}
                min={0}
                step={0.5}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Dose (mg)</Label>
              <Input
                type="number"
                value={caffeineDose}
                onChange={(e) => setCaffeineDose(Number(e.target.value))}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Max (mg)</Label>
              <Input
                type="number"
                value={caffeineMax}
                onChange={(e) => setCaffeineMax(Number(e.target.value))}
              />
            </div>
          </div>
        )}
      </div>

      <Button
        onClick={() =>
          onGenerate({
            carbsPerHour,
            fluidPerHour,
            sodiumPerHour,
            caffeineStrategy: useCaffeine
              ? {
                  startAfterHours: caffeineStart,
                  dosePerIntake: caffeineDose,
                  maxTotal: caffeineMax,
                }
              : undefined,
          })
        }
        disabled={generating}
        className="w-full"
      >
        {generating ? "Generating..." : "Generate Nutrition Plan"}
      </Button>
    </div>
  );
}
