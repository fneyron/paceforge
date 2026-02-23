"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { CrossCountrySkiingConfig, FatigueConfig } from "@/types/route";
import { DEFAULT_FATIGUE } from "@/lib/physics/fatigue";

interface Props {
  onSimulate: (config: CrossCountrySkiingConfig, fatigue?: FatigueConfig) => void;
  running: boolean;
}

export function XCSkiingConfigForm({ onSimulate, running }: Props) {
  const [vo2max, setVo2max] = useState(55);
  const [weight, setWeight] = useState(75);
  const [technique, setTechnique] = useState<"classic" | "skating">("skating");
  const [snowFriction, setSnowFriction] = useState(0.04);
  const [temperature, setTemperature] = useState(-5);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(DEFAULT_FATIGUE.cross_country_skiing.halfLife);
  const [fatigueMin, setFatigueMin] = useState(DEFAULT_FATIGUE.cross_country_skiing.minFactor);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) {
          if (data.vo2max) setVo2max(data.vo2max);
          if (data.weight) setWeight(data.weight);
        }
      })
      .catch(() => {});
  }, []);

  const handleSimulate = () => {
    onSimulate(
      { vo2max, weight, technique, snowFriction, temperature },
      { halfLife: fatigueHalfLife, minFactor: fatigueMin }
    );
  };

  return (
    <div className="space-y-4 pt-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="xc-vo2max" className="text-xs">VO2max (ml/kg/min)</Label>
          <Input id="xc-vo2max" type="number" value={vo2max} onChange={(e) => setVo2max(Number(e.target.value))} min={30} max={90} step={1} />
        </div>
        <div>
          <Label htmlFor="xc-weight" className="text-xs">Weight (kg)</Label>
          <Input id="xc-weight" type="number" value={weight} onChange={(e) => setWeight(Number(e.target.value))} min={40} max={150} step={0.5} />
        </div>
        <div>
          <Label htmlFor="xc-technique" className="text-xs">Technique</Label>
          <Select value={technique} onValueChange={(v) => setTechnique(v as "classic" | "skating")}>
            <SelectTrigger id="xc-technique"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="skating">Skating</SelectItem>
              <SelectItem value="classic">Classic</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label htmlFor="xc-temp" className="text-xs">Temperature (°C)</Label>
          <Input id="xc-temp" type="number" value={temperature} onChange={(e) => setTemperature(Number(e.target.value))} min={-30} max={10} step={1} />
        </div>
      </div>

      <div>
        <Label className="text-xs">Snow Friction (μ): {snowFriction.toFixed(3)}</Label>
        <Slider value={[snowFriction]} onValueChange={([v]) => setSnowFriction(v)} min={0.02} max={0.15} step={0.005} className="mt-1" />
      </div>

      <div className="space-y-2">
        <Label className="text-xs">Fatigue Half-Life: {fatigueHalfLife}h</Label>
        <Slider value={[fatigueHalfLife]} onValueChange={([v]) => setFatigueHalfLife(v)} min={1} max={10} step={0.5} />
        <Label className="text-xs">Min Performance: {(fatigueMin * 100).toFixed(0)}%</Label>
        <Slider value={[fatigueMin]} onValueChange={([v]) => setFatigueMin(v)} min={0.4} max={0.9} step={0.05} />
      </div>

      <Button onClick={handleSimulate} disabled={running} className="w-full">
        {running ? "Simulating..." : "Simulate XC Skiing"}
      </Button>
    </div>
  );
}
