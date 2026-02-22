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
import type { TriathlonConfig, FatigueConfig, TriathlonFormat } from "@/types/route";

interface Props {
  onSimulate: (config: TriathlonConfig, fatigue: FatigueConfig) => void;
  running: boolean;
}

export function TriathlonConfigForm({ onSimulate, running }: Props) {
  const [raceFormat, setRaceFormat] = useState<TriathlonFormat>("olympic");

  // Swim
  const [css, setCss] = useState(95);
  const [isOpenWater, setIsOpenWater] = useState(true);
  const [hasWetsuit, setHasWetsuit] = useState(true);

  // Bike
  const [ftp, setFtp] = useState(250);
  const [bikeWeight, setBikeWeight] = useState(8);
  const [cda, setCda] = useState(0.3);

  // Run
  const [vdot, setVdot] = useState(50);

  // Common
  const [weight, setWeight] = useState(70);
  const [height, setHeight] = useState(175);
  const [t1Time, setT1Time] = useState(120);
  const [t2Time, setT2Time] = useState(60);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(8);
  const [fatigueMin, setFatigueMin] = useState(0.6);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => r.json())
      .then((data) => {
        if (data && !data.error) {
          if (data.css != null) setCss(data.css);
          if (data.ftp != null) setFtp(data.ftp);
          if (data.bikeWeight != null) setBikeWeight(data.bikeWeight);
          if (data.cda != null) setCda(data.cda);
          if (data.vdot != null) setVdot(data.vdot);
          if (data.weight != null) setWeight(data.weight);
          if (data.height != null) setHeight(data.height);
          if (data.t1Time != null) setT1Time(data.t1Time);
          if (data.t2Time != null) setT2Time(data.t2Time);
          if (data.swimHasWetsuit != null) setHasWetsuit(data.swimHasWetsuit);
        }
      })
      .catch(() => {});
  }, []);

  return (
    <div className="space-y-4 pt-4">
      <div className="space-y-2">
        <Label>Race format</Label>
        <Select value={raceFormat} onValueChange={(v) => setRaceFormat(v as TriathlonFormat)}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="sprint">Sprint (750/20k/5k)</SelectItem>
            <SelectItem value="olympic">Olympic (1.5k/40k/10k)</SelectItem>
            <SelectItem value="half_ironman">Half Ironman (1.9k/90k/21k)</SelectItem>
            <SelectItem value="ironman">Ironman (3.8k/180k/42k)</SelectItem>
            <SelectItem value="custom">Custom</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-2">
        <Label>Weight (kg)</Label>
        <Input
          type="number"
          value={weight}
          onChange={(e) => setWeight(Number(e.target.value))}
        />
      </div>

      <div className="border rounded-md p-3 space-y-3">
        <h4 className="text-sm font-medium">Swim</h4>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label className="text-xs">CSS (sec/100m)</Label>
            <Input type="number" value={css} onChange={(e) => setCss(Number(e.target.value))} />
          </div>
        </div>
        <div className="flex gap-4">
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={isOpenWater} onChange={(e) => setIsOpenWater(e.target.checked)} className="rounded" />
            Open water
          </label>
          <label className="flex items-center gap-2 text-xs">
            <input type="checkbox" checked={hasWetsuit} onChange={(e) => setHasWetsuit(e.target.checked)} className="rounded" />
            Wetsuit
          </label>
        </div>
      </div>

      <div className="border rounded-md p-3 space-y-3">
        <h4 className="text-sm font-medium">Bike</h4>
        <div className="grid grid-cols-3 gap-3">
          <div className="space-y-1">
            <Label className="text-xs">FTP (W)</Label>
            <Input type="number" value={ftp} onChange={(e) => setFtp(Number(e.target.value))} />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Bike (kg)</Label>
            <Input type="number" value={bikeWeight} onChange={(e) => setBikeWeight(Number(e.target.value))} />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">CdA (m²)</Label>
            <Input type="number" step="0.01" value={cda} onChange={(e) => setCda(Number(e.target.value))} />
          </div>
        </div>
      </div>

      <div className="border rounded-md p-3 space-y-3">
        <h4 className="text-sm font-medium">Run</h4>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label className="text-xs">VDOT</Label>
            <Input type="number" value={vdot} onChange={(e) => setVdot(Number(e.target.value))} min={30} max={85} />
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>T1 (sec)</Label>
          <Input type="number" value={t1Time} onChange={(e) => setT1Time(Number(e.target.value))} />
        </div>
        <div className="space-y-2">
          <Label>T2 (sec)</Label>
          <Input type="number" value={t2Time} onChange={(e) => setT2Time(Number(e.target.value))} />
        </div>
      </div>

      <div className="space-y-2">
        <Label>Fatigue half-life: {fatigueHalfLife}h</Label>
        <Slider
          value={[fatigueHalfLife]}
          onValueChange={([v]) => setFatigueHalfLife(v)}
          min={2}
          max={20}
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
            {
              swimConfig: {
                css,
                weight,
                height,
                isOpenWater,
                hasWetsuit,
              },
              cyclingConfig: {
                ftp,
                weight,
                bikeWeight,
                cda,
                crr: 0.005,
                efficiency: 0.25,
                powerTargets: [],
              },
              runConfig: {
                vdot,
                weight,
              },
              t1Time,
              t2Time,
              raceFormat,
            },
            { halfLife: fatigueHalfLife, minFactor: fatigueMin }
          )
        }
        disabled={running}
        className="w-full"
      >
        {running ? "Simulating..." : "Run Triathlon Simulation"}
      </Button>
    </div>
  );
}
