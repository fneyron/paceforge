"use client";

import { useRouteStore } from "@/store/route-store";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { sportLabel } from "@/lib/sport-labels";

function formatDistance(meters: number): string {
  if (meters >= 1000) return `${(meters / 1000).toFixed(1)} km`;
  return `${Math.round(meters)} m`;
}

function formatGrade(grade: number): string {
  return `${(grade * 100).toFixed(1)}%`;
}

export function RouteSidebar() {
  const { name, sport, stats, segments, waypoints, routeId, raceDate, raceStartTime } = useRouteStore();
  const setRaceDate = useRouteStore((s) => s.setRaceDate);
  const setRaceStartTime = useRouteStore((s) => s.setRaceStartTime);

  if (!stats) return null;

  const saveRaceDate = async (value: string) => {
    const date = value || null;
    setRaceDate(date);
    if (routeId) {
      await fetch(`/api/routes/${routeId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raceDate: date }),
      });
    }
  };

  const saveRaceStartTime = async (value: string) => {
    const time = value || null;
    setRaceStartTime(time);
    if (routeId) {
      await fetch(`/api/routes/${routeId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raceStartTime: time }),
      });
    }
  };

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-4">
        {/* Route Info */}
        <div>
          <h2 className="text-lg font-semibold truncate">{name}</h2>
          <Badge variant="secondary" className="mt-1">
            {sportLabel(sport)}
          </Badge>
        </div>

        <Separator />

        {/* Stats */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Route Stats</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Distance</span>
              <span className="font-medium">
                {formatDistance(stats.totalDistance)}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">D+</span>
              <span className="font-medium text-red-500">
                {stats.elevationGain} m
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">D-</span>
              <span className="font-medium text-blue-500">
                {stats.elevationLoss} m
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Min elev.</span>
              <span className="font-medium">{stats.minElevation} m</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Max elev.</span>
              <span className="font-medium">{stats.maxElevation} m</span>
            </div>
          </CardContent>
        </Card>

        {/* Race Settings */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Race Settings</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1">
              <Label className="text-xs">Race Date</Label>
              <Input
                type="date"
                value={raceDate || ""}
                onBlur={(e) => saveRaceDate(e.target.value)}
                onChange={(e) => setRaceDate(e.target.value || null)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Start Time</Label>
              <Input
                type="time"
                value={raceStartTime || ""}
                onBlur={(e) => saveRaceStartTime(e.target.value)}
                onChange={(e) => setRaceStartTime(e.target.value || null)}
              />
            </div>
          </CardContent>
        </Card>
      </div>
    </ScrollArea>
  );
}

export function RouteSegmentsSidebar() {
  const { segments, waypoints } = useRouteStore();

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-4">
        {/* Segments */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              Segments ({segments.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            {segments.length === 0 ? (
              <p className="text-xs text-muted-foreground">No segments detected</p>
            ) : (
              segments.map((seg, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 text-xs p-2 rounded hover:bg-muted/50 transition-colors"
                >
                  <div
                    className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                      seg.type === "climb"
                        ? "bg-red-500"
                        : seg.type === "descent"
                          ? "bg-blue-500"
                          : "bg-green-500"
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex justify-between">
                      <span className="capitalize font-medium">{seg.type}</span>
                      <span className="text-muted-foreground">
                        {formatDistance(seg.length)}
                      </span>
                    </div>
                    <div className="flex justify-between text-muted-foreground">
                      <span>{formatGrade(seg.averageGrade)} avg</span>
                      {seg.type === "climb" && (
                        <span className="text-red-500">+{seg.elevationGain} m</span>
                      )}
                      {seg.type === "descent" && (
                        <span className="text-blue-500">-{seg.elevationLoss} m</span>
                      )}
                    </div>
                  </div>
                </div>
              ))
            )}
          </CardContent>
        </Card>

        {/* Waypoints */}
        {waypoints.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                Waypoints ({waypoints.length})
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1">
              {waypoints.map((wp) => (
                <div
                  key={wp.id}
                  className="flex items-center gap-2 text-xs p-2 rounded hover:bg-muted/50 transition-colors"
                >
                  <div
                    className={`w-2.5 h-2.5 rounded-full shrink-0 ${
                      wp.type === "aid_station"
                        ? "bg-emerald-500"
                        : wp.type === "power_target"
                          ? "bg-amber-500"
                          : wp.type === "pace_change"
                            ? "bg-purple-500"
                            : "bg-slate-500"
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="truncate font-medium">{wp.name}</div>
                    <div className="text-muted-foreground">
                      {formatDistance(wp.distance)} | {Math.round(wp.ele)} m
                    </div>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        )}
      </div>
    </ScrollArea>
  );
}
