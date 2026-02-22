"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useRouteStore } from "@/store/route-store";
import { compareEquipment } from "@/lib/aero/equipment-compare";
import { formatTime } from "@/lib/physics/simulate";

interface EquipmentProfile {
  id: string;
  name: string;
  bikeWeight: number | null;
  cda: number | null;
  crr: number | null;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  profiles: EquipmentProfile[];
  riderWeight: number;
  ftp: number;
  efficiency: number;
}

export function EquipmentCompareDialog({
  open,
  onOpenChange,
  profiles,
  riderWeight,
  ftp,
  efficiency,
}: Props) {
  const [setupAId, setSetupAId] = useState(profiles[0]?.id || "");
  const [setupBId, setSetupBId] = useState(profiles[1]?.id || "");
  const segments = useRouteStore((s) => s.segments);
  const points = useRouteStore((s) => s.points);

  const setupA = profiles.find((p) => p.id === setupAId);
  const setupB = profiles.find((p) => p.id === setupBId);

  const canCompare = setupA && setupB && setupAId !== setupBId && segments.length > 0;

  const comparison = canCompare
    ? compareEquipment(
        segments,
        points,
        {
          name: setupA.name,
          cda: setupA.cda ?? 0.32,
          crr: setupA.crr ?? 0.005,
          bikeWeight: setupA.bikeWeight ?? 8,
        },
        {
          name: setupB.name,
          cda: setupB.cda ?? 0.32,
          crr: setupB.crr ?? 0.005,
          bikeWeight: setupB.bikeWeight ?? 8,
        },
        riderWeight,
        ftp,
        efficiency
      )
    : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Compare Equipment</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1">
              <Label className="text-xs">Setup A</Label>
              <Select value={setupAId} onValueChange={setSetupAId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {profiles.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Setup B</Label>
              <Select value={setupBId} onValueChange={setSetupBId}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {profiles.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          {comparison && (
            <div className="space-y-3">
              <div className="border rounded-md p-3 text-sm space-y-1">
                <div className="flex justify-between">
                  <span>{setupA?.name} total:</span>
                  <span className="font-medium">{formatTime(comparison.totalTimeA)}</span>
                </div>
                <div className="flex justify-between">
                  <span>{setupB?.name} total:</span>
                  <span className="font-medium">{formatTime(comparison.totalTimeB)}</span>
                </div>
                <div className="flex justify-between border-t pt-1 mt-1">
                  <span>Delta:</span>
                  <span
                    className={`font-bold ${
                      comparison.totalDeltaTime < 0
                        ? "text-green-600"
                        : comparison.totalDeltaTime > 0
                          ? "text-red-600"
                          : ""
                    }`}
                  >
                    {comparison.totalDeltaTime > 0 ? "+" : ""}
                    {formatTime(Math.abs(comparison.totalDeltaTime))}
                    {comparison.totalDeltaTime < 0
                      ? ` (B faster)`
                      : comparison.totalDeltaTime > 0
                        ? ` (A faster)`
                        : ""}
                  </span>
                </div>
              </div>

              <div className="max-h-48 overflow-y-auto space-y-1">
                {comparison.results.map((r, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between text-xs border rounded px-2 py-1"
                  >
                    <span className="capitalize">{r.segmentType}</span>
                    <span className="text-muted-foreground">
                      {(r.distance / 1000).toFixed(1)}km
                    </span>
                    <span
                      className={
                        r.deltaTime < -1
                          ? "text-green-600"
                          : r.deltaTime > 1
                            ? "text-red-600"
                            : "text-muted-foreground"
                      }
                    >
                      {r.deltaTime > 0 ? "+" : ""}
                      {r.deltaTime.toFixed(1)}s
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {!canCompare && setupAId === setupBId && (
            <p className="text-xs text-muted-foreground">Select two different setups to compare.</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
