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
import { EquipmentCompareDialog } from "./equipment-compare-dialog";
import type { CyclingConfig, FatigueConfig } from "@/types/route";

interface EquipmentProfile {
  id: string;
  name: string;
  bikeWeight: number | null;
  cda: number | null;
  crr: number | null;
}

interface Props {
  onSimulate: (config: CyclingConfig, fatigue: FatigueConfig) => void;
  running: boolean;
}

export function CyclingConfigForm({ onSimulate, running }: Props) {
  const [ftp, setFtp] = useState(250);
  const [weight, setWeight] = useState(70);
  const [bikeWeight, setBikeWeight] = useState(8);
  const [cda, setCda] = useState(0.32);
  const [crr, setCrr] = useState(0.005);
  const [efficiency, setEfficiency] = useState(0.25);
  const [fatigueHalfLife, setFatigueHalfLife] = useState(5);
  const [fatigueMin, setFatigueMin] = useState(0.7);
  const [equipmentProfiles, setEquipmentProfiles] = useState<EquipmentProfile[]>([]);
  const [selectedEquipment, setSelectedEquipment] = useState<string>("custom");
  const [showCompare, setShowCompare] = useState(false);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => r.json())
      .then((data) => {
        if (data && !data.error) {
          if (data.ftp != null) setFtp(data.ftp);
          if (data.weight != null) setWeight(data.weight);
          if (data.bikeWeight != null) setBikeWeight(data.bikeWeight);
          if (data.cda != null) setCda(data.cda);
          if (data.crr != null) setCrr(data.crr);
          if (data.efficiency != null) setEfficiency(data.efficiency);
        }
      })
      .catch(() => {});

    fetch("/api/equipment")
      .then((r) => r.json())
      .then((profiles: EquipmentProfile[]) => {
        setEquipmentProfiles(profiles);
      })
      .catch(console.error);
  }, []);

  const handleEquipmentChange = (value: string) => {
    setSelectedEquipment(value);
    if (value === "custom") return;

    const profile = equipmentProfiles.find((p) => p.id === value);
    if (profile) {
      if (profile.bikeWeight != null) setBikeWeight(profile.bikeWeight);
      if (profile.cda != null) setCda(profile.cda);
      if (profile.crr != null) setCrr(profile.crr);
    }
  };

  return (
    <div className="space-y-4 pt-4">
      {equipmentProfiles.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label>Equipment Profile</Label>
            {equipmentProfiles.length >= 2 && (
              <Button
                size="sm"
                variant="ghost"
                className="h-6 text-xs"
                onClick={() => setShowCompare(true)}
              >
                Compare
              </Button>
            )}
          </div>
          <Select value={selectedEquipment} onValueChange={handleEquipmentChange}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="custom">Custom</SelectItem>
              {equipmentProfiles.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>FTP (W)</Label>
          <Input
            type="number"
            value={ftp}
            onChange={(e) => setFtp(Number(e.target.value))}
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
          <Label>Bike weight (kg)</Label>
          <Input
            type="number"
            value={bikeWeight}
            onChange={(e) => setBikeWeight(Number(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>CdA (m²)</Label>
          <Input
            type="number"
            step="0.01"
            value={cda}
            onChange={(e) => setCda(Number(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Crr</Label>
          <Input
            type="number"
            step="0.001"
            value={crr}
            onChange={(e) => setCrr(Number(e.target.value))}
          />
        </div>
        <div className="space-y-2">
          <Label>Efficiency</Label>
          <Input
            type="number"
            step="0.01"
            value={efficiency}
            onChange={(e) => setEfficiency(Number(e.target.value))}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label>Fatigue half-life: {fatigueHalfLife}h</Label>
        <Slider
          value={[fatigueHalfLife]}
          onValueChange={([v]) => setFatigueHalfLife(v)}
          min={1}
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
            { ftp, weight, bikeWeight, cda, crr, efficiency, powerTargets: [] },
            { halfLife: fatigueHalfLife, minFactor: fatigueMin }
          )
        }
        disabled={running}
        className="w-full"
      >
        {running ? "Simulating..." : "Run Cycling Simulation"}
      </Button>

      {showCompare && (
        <EquipmentCompareDialog
          open={showCompare}
          onOpenChange={setShowCompare}
          profiles={equipmentProfiles}
          riderWeight={weight}
          ftp={ftp}
          efficiency={efficiency}
        />
      )}
    </div>
  );
}
