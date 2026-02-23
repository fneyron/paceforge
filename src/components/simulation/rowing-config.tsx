"use client";

import { useState } from "react";
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
import type { RowingConfig, BoatClass, FatigueConfig } from "@/types/route";
import { DEFAULT_FATIGUE } from "@/lib/physics/fatigue";

interface Props {
  onSimulate: (config: RowingConfig, fatigue?: FatigueConfig) => void;
  running: boolean;
}

export function RowingConfigForm({ onSimulate, running }: Props) {
  const [power, setPower] = useState(250);
  const [weight, setWeight] = useState(90);
  const [boatClass, setBoatClass] = useState<BoatClass>("1x");
  const [currentSpeed, setCurrentSpeed] = useState(0);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(DEFAULT_FATIGUE.rowing.halfLife);
  const [fatigueMin, setFatigueMin] = useState(DEFAULT_FATIGUE.rowing.minFactor);

  const handleSimulate = () => {
    onSimulate(
      { power, weight, boatClass, currentSpeed: currentSpeed || undefined },
      { halfLife: fatigueHalfLife, minFactor: fatigueMin }
    );
  };

  return (
    <div className="space-y-4 pt-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="row-power" className="text-xs">Power per Rower (W)</Label>
          <Input id="row-power" type="number" value={power} onChange={(e) => setPower(Number(e.target.value))} min={50} max={600} step={5} />
        </div>
        <div>
          <Label htmlFor="row-weight" className="text-xs">Total Weight (kg)</Label>
          <Input id="row-weight" type="number" value={weight} onChange={(e) => setWeight(Number(e.target.value))} min={50} max={1000} step={1} />
        </div>
        <div>
          <Label htmlFor="row-boat" className="text-xs">Boat Class</Label>
          <Select value={boatClass} onValueChange={(v) => setBoatClass(v as BoatClass)}>
            <SelectTrigger id="row-boat"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="1x">1x (Single Scull)</SelectItem>
              <SelectItem value="2x">2x (Double Scull)</SelectItem>
              <SelectItem value="2-">2- (Pair)</SelectItem>
              <SelectItem value="4x">4x (Quad Scull)</SelectItem>
              <SelectItem value="4-">4- (Four)</SelectItem>
              <SelectItem value="8+">8+ (Eight)</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label htmlFor="row-current" className="text-xs">Current (m/s)</Label>
          <Input id="row-current" type="number" value={currentSpeed} onChange={(e) => setCurrentSpeed(Number(e.target.value))} min={-2} max={2} step={0.1} />
        </div>
      </div>

      <div className="space-y-2">
        <Label className="text-xs">Fatigue Half-Life: {fatigueHalfLife}h</Label>
        <Slider value={[fatigueHalfLife]} onValueChange={([v]) => setFatigueHalfLife(v)} min={1} max={8} step={0.5} />
        <Label className="text-xs">Min Performance: {(fatigueMin * 100).toFixed(0)}%</Label>
        <Slider value={[fatigueMin]} onValueChange={([v]) => setFatigueMin(v)} min={0.4} max={0.9} step={0.05} />
      </div>

      <Button onClick={handleSimulate} disabled={running} className="w-full">
        {running ? "Simulating..." : "Simulate Rowing"}
      </Button>
    </div>
  );
}
