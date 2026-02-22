"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDrawStore } from "@/store/draw-store";
import { SPORT_OPTIONS } from "@/lib/sport-labels";

export function DrawControls() {
  const router = useRouter();
  const drawPoints = useDrawStore((s) => s.drawPoints);
  const isDrawing = useDrawStore((s) => s.isDrawing);
  const startDrawing = useDrawStore((s) => s.startDrawing);
  const stopDrawing = useDrawStore((s) => s.stopDrawing);
  const removeLastPoint = useDrawStore((s) => s.removeLastPoint);
  const clear = useDrawStore((s) => s.clear);

  const [name, setName] = useState("My Route");
  const [sport, setSport] = useState("cycling");
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (drawPoints.length < 2) return;
    setSaving(true);
    try {
      const res = await fetch("/api/routes/draw", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ points: drawPoints, sport, name }),
      });
      if (res.ok) {
        const data = await res.json();
        router.push(`/routes/${data.id}`);
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="absolute top-4 left-4 z-10 bg-white rounded-lg shadow-lg p-3 space-y-3 w-64">
      <h3 className="text-sm font-medium">Draw Route</h3>

      <div className="space-y-1">
        <Label className="text-xs">Name</Label>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Route name"
          className="h-8"
        />
      </div>

      <div className="space-y-1">
        <Label className="text-xs">Sport</Label>
        <Select value={sport} onValueChange={setSport}>
          <SelectTrigger className="h-8">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SPORT_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="text-xs text-muted-foreground">
        {drawPoints.length} points placed
        {drawPoints.length >= 2 && " (ready to save)"}
      </div>

      <div className="flex flex-wrap gap-1">
        {!isDrawing ? (
          <Button size="sm" className="flex-1" onClick={startDrawing}>
            Start Drawing
          </Button>
        ) : (
          <Button size="sm" variant="outline" className="flex-1" onClick={stopDrawing}>
            Stop
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          onClick={removeLastPoint}
          disabled={drawPoints.length === 0}
        >
          Undo
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={clear}
          disabled={drawPoints.length === 0}
        >
          Clear
        </Button>
      </div>

      <Button
        size="sm"
        className="w-full"
        onClick={handleSave}
        disabled={saving || drawPoints.length < 2}
      >
        {saving ? "Saving..." : "Save Route"}
      </Button>
    </div>
  );
}
