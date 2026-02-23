"use client";

import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import type { DuathlonConfig, FatigueConfig } from "@/types/route";
import { DEFAULT_FATIGUE } from "@/lib/physics/fatigue";

interface Props {
  onSimulate: (config: DuathlonConfig, fatigue?: FatigueConfig) => void;
  running: boolean;
}

export function DuathlonConfigForm({ onSimulate, running }: Props) {
  const [vdot, setVdot] = useState(50);
  const [runWeight, setRunWeight] = useState(75);
  const [ftp, setFtp] = useState(250);
  const [bikeWeight, setBikeWeight] = useState(8);
  const [cda, setCda] = useState(0.32);
  const [crr, setCrr] = useState(0.005);
  const [efficiency, setEfficiency] = useState(0.97);
  const [t1Time, setT1Time] = useState(60);
  const [t2Time, setT2Time] = useState(45);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(DEFAULT_FATIGUE.duathlon.halfLife);
  const [fatigueMin, setFatigueMin] = useState(DEFAULT_FATIGUE.duathlon.minFactor);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) {
          if (data.vdot) setVdot(data.vdot);
          if (data.weight) setRunWeight(data.weight);
          if (data.ftp) setFtp(data.ftp);
          if (data.bikeWeight) setBikeWeight(data.bikeWeight);
          if (data.cda) setCda(data.cda);
          if (data.crr) setCrr(data.crr);
          if (data.efficiency) setEfficiency(data.efficiency);
        }
      })
      .catch(() => {});
  }, []);

  const handleSimulate = () => {
    const runConfig = { vdot, weight: runWeight };
    const cyclingConfig = {
      ftp, weight: runWeight, bikeWeight, cda, crr, efficiency,
      powerTargets: [],
    };
    onSimulate(
      {
        run1Config: runConfig,
        cyclingConfig,
        run2Config: runConfig,
        t1Time,
        t2Time,
        raceFormat: "standard" as const,
      },
      { halfLife: fatigueHalfLife, minFactor: fatigueMin }
    );
  };

  return (
    <div className="space-y-4 pt-3">
      <p className="text-xs text-muted-foreground">Run 1 → Bike → Run 2</p>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label htmlFor="dua-vdot" className="text-xs">VDOT (Running)</Label>
          <Input id="dua-vdot" type="number" value={vdot} onChange={(e) => setVdot(Number(e.target.value))} min={30} max={85} step={0.5} />
        </div>
        <div>
          <Label htmlFor="dua-weight" className="text-xs">Weight (kg)</Label>
          <Input id="dua-weight" type="number" value={runWeight} onChange={(e) => setRunWeight(Number(e.target.value))} min={40} max={150} step={0.5} />
        </div>
        <div>
          <Label htmlFor="dua-ftp" className="text-xs">FTP (W)</Label>
          <Input id="dua-ftp" type="number" value={ftp} onChange={(e) => setFtp(Number(e.target.value))} min={50} max={500} step={5} />
        </div>
        <div>
          <Label htmlFor="dua-bikeweight" className="text-xs">Bike Weight (kg)</Label>
          <Input id="dua-bikeweight" type="number" value={bikeWeight} onChange={(e) => setBikeWeight(Number(e.target.value))} min={4} max={20} step={0.5} />
        </div>
        <div>
          <Label htmlFor="dua-t1" className="text-xs">T1 Time (s)</Label>
          <Input id="dua-t1" type="number" value={t1Time} onChange={(e) => setT1Time(Number(e.target.value))} min={0} max={300} step={5} />
        </div>
        <div>
          <Label htmlFor="dua-t2" className="text-xs">T2 Time (s)</Label>
          <Input id="dua-t2" type="number" value={t2Time} onChange={(e) => setT2Time(Number(e.target.value))} min={0} max={300} step={5} />
        </div>
      </div>

      <div className="space-y-2">
        <Label className="text-xs">Fatigue Half-Life: {fatigueHalfLife}h</Label>
        <Slider value={[fatigueHalfLife]} onValueChange={([v]) => setFatigueHalfLife(v)} min={2} max={12} step={0.5} />
        <Label className="text-xs">Min Performance: {(fatigueMin * 100).toFixed(0)}%</Label>
        <Slider value={[fatigueMin]} onValueChange={([v]) => setFatigueMin(v)} min={0.4} max={0.9} step={0.05} />
      </div>

      <Button onClick={handleSimulate} disabled={running} className="w-full">
        {running ? "Simulating..." : "Simulate Duathlon"}
      </Button>
    </div>
  );
}
