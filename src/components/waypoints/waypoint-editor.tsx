"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { useRouteStore } from "@/store/route-store";
import { pointAtDistance } from "@/lib/gpx/snap";
import type { WaypointType } from "@/types/route";

export function WaypointEditor() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [type, setType] = useState<WaypointType>("aid_station");
  const [distanceKm, setDistanceKm] = useState(0);
  const [saving, setSaving] = useState(false);

  const routeId = useRouteStore((s) => s.routeId);
  const points = useRouteStore((s) => s.points);
  const stats = useRouteStore((s) => s.stats);
  const addWaypoint = useRouteStore((s) => s.addWaypoint);

  const maxDistKm = stats ? stats.totalDistance / 1000 : 100;

  const handleSave = async () => {
    if (!routeId || !name.trim()) return;

    const distanceM = distanceKm * 1000;
    const pt = pointAtDistance(distanceM, points);
    if (!pt) return;

    setSaving(true);

    try {
      const res = await fetch(`/api/routes/${routeId}/waypoints`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type,
          name: name.trim(),
          distance: distanceM,
          lat: pt.lat,
          lon: pt.lon,
          ele: pt.ele,
          config: {},
        }),
      });

      if (res.ok) {
        const wp = await res.json();
        addWaypoint(wp);
        setOpen(false);
        setName("");
        setDistanceKm(0);
      }
    } catch (err) {
      console.error("Failed to save waypoint:", err);
    } finally {
      setSaving(false);
    }
  };

  if (!routeId) return null;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          + Waypoint
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add Waypoint</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Name</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Aid Station 1"
            />
          </div>

          <div className="space-y-2">
            <Label>Type</Label>
            <Select
              value={type}
              onValueChange={(v) => setType(v as WaypointType)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="aid_station">Aid Station</SelectItem>
                <SelectItem value="power_target">Power Target</SelectItem>
                <SelectItem value="pace_change">Pace Change</SelectItem>
                <SelectItem value="custom">Custom</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label>
              Distance: {distanceKm.toFixed(1)} km
            </Label>
            <Slider
              value={[distanceKm]}
              onValueChange={([v]) => setDistanceKm(v)}
              min={0}
              max={maxDistKm}
              step={0.1}
            />
          </div>

          {distanceKm > 0 && (
            <p className="text-sm text-muted-foreground">
              Elevation:{" "}
              {Math.round(
                pointAtDistance(distanceKm * 1000, points)?.ele ?? 0
              )}{" "}
              m
            </p>
          )}

          <Button
            onClick={handleSave}
            disabled={!name.trim() || saving}
            className="w-full"
          >
            {saving ? "Saving..." : "Add Waypoint"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
