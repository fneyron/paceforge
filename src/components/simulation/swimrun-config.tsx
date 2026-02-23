"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { Checkbox } from "@/components/ui/checkbox";
import type { SwimRunConfig, FatigueConfig } from "@/types/route";
import { DEFAULT_FATIGUE } from "@/lib/physics/fatigue";

interface Props {
  onSimulate: (config: SwimRunConfig, fatigue?: FatigueConfig) => void;
  running: boolean;
}

export function SwimRunConfigForm({ onSimulate, running }: Props) {
  const [css, setCss] = useState(100);
  const [vdot, setVdot] = useState(50);
  const [weight, setWeight] = useState(75);
  const [height, setHeight] = useState(175);
  const [hasPullBuoy, setHasPullBuoy] = useState(true);
  const [hasHandPaddles, setHasHandPaddles] = useState(false);
  const [wetsuitRunPenalty, setWetsuitRunPenalty] = useState(0.03);
  const [shoeSwimPenalty, setShoeSwimPenalty] = useState(0.05);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(DEFAULT_FATIGUE.swimrun.halfLife);
  const [fatigueMin, setFatigueMin] = useState(DEFAULT_FATIGUE.swimrun.minFactor);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) {
          if (data.css) setCss(data.css);
          if (data.vdot) setVdot(data.vdot);
          if (data.weight) setWeight(data.weight);
          if (data.height) setHeight(data.height);
        }
      })
      .catch(() => {});
  }, []);

  const handleSimulate = () => {
    onSimulate(
      {
        swimConfig: { css, weight, height, isOpenWater: true, hasWetsuit: true },
        runConfig: { vdot, weight },
        hasPullBuoy,
        hasHandPaddles,
        wetsuitRunPenalty,
        shoeSwimPenalty,
      },
      { halfLife: fatigueHalfLife, minFactor: fatigueMin }
    );
  };

  return (
    <div className="space-y-4 pt-3">
      <p className="text-xs text-muted-foreground">Alternating swim & run legs</p>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="sr-css" className="text-xs">CSS (sec/100m)</Label>
          <Input id="sr-css" type="number" value={css} onChange={(e) => setCss(Number(e.target.value))} min={60} max={180} step={1} />
        </div>
        <div>
          <Label htmlFor="sr-vdot" className="text-xs">VDOT (Running)</Label>
          <Input id="sr-vdot" type="number" value={vdot} onChange={(e) => setVdot(Number(e.target.value))} min={30} max={85} step={0.5} />
        </div>
        <div>
          <Label htmlFor="sr-weight" className="text-xs">Weight (kg)</Label>
          <Input id="sr-weight" type="number" value={weight} onChange={(e) => setWeight(Number(e.target.value))} min={40} max={150} step={0.5} />
        </div>
        <div>
          <Label htmlFor="sr-height" className="text-xs">Height (cm)</Label>
          <Input id="sr-height" type="number" value={height} onChange={(e) => setHeight(Number(e.target.value))} min={140} max={210} step={1} />
        </div>
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <Checkbox id="sr-pullbuoy" checked={hasPullBuoy} onCheckedChange={(v) => setHasPullBuoy(!!v)} />
          <Label htmlFor="sr-pullbuoy" className="text-xs">Pull Buoy (+3% swim)</Label>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox id="sr-paddles" checked={hasHandPaddles} onCheckedChange={(v) => setHasHandPaddles(!!v)} />
          <Label htmlFor="sr-paddles" className="text-xs">Hand Paddles (+2% swim)</Label>
        </div>
      </div>

      <div className="space-y-2">
        <Label className="text-xs">Wetsuit Run Penalty: {(wetsuitRunPenalty * 100).toFixed(0)}%</Label>
        <Slider value={[wetsuitRunPenalty]} onValueChange={([v]) => setWetsuitRunPenalty(v)} min={0} max={0.10} step={0.01} />
        <Label className="text-xs">Shoe Swim Penalty: {(shoeSwimPenalty * 100).toFixed(0)}%</Label>
        <Slider value={[shoeSwimPenalty]} onValueChange={([v]) => setShoeSwimPenalty(v)} min={0} max={0.10} step={0.01} />
      </div>

      <div className="space-y-2">
        <Label className="text-xs">Fatigue Half-Life: {fatigueHalfLife}h</Label>
        <Slider value={[fatigueHalfLife]} onValueChange={([v]) => setFatigueHalfLife(v)} min={2} max={15} step={0.5} />
        <Label className="text-xs">Min Performance: {(fatigueMin * 100).toFixed(0)}%</Label>
        <Slider value={[fatigueMin]} onValueChange={([v]) => setFatigueMin(v)} min={0.4} max={0.9} step={0.05} />
      </div>

      <Button onClick={handleSimulate} disabled={running} className="w-full">
        {running ? "Simulating..." : "Simulate SwimRun"}
      </Button>
    </div>
  );
}
