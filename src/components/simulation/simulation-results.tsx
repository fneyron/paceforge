"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatTime } from "@/lib/physics/simulate";
import { PacePowerGraph } from "./pace-power-graph";
import type { SimulationResult, SplitResult } from "@/types/route";

interface Props {
  results: SimulationResult;
  sport?: string;
  athleteVdot?: number;
}

function formatPace(pace: number): string {
  if (pace >= 100) return "-";
  const m = Math.floor(pace);
  const s = Math.round((pace % 1) * 60);
  return `${m}:${s.toString().padStart(2, "0")}/km`;
}

export function SimulationResults({ results, sport, athleteVdot }: Props) {
  const [expanded, setExpanded] = useState(false);

  const hasPower = results.splits.some((s) => s.power !== undefined);
  const hasZones = results.splits.some((s) => s.zone !== undefined);

  // Prediction suggestion
  let suggestion: string | null = null;
  if (athleteVdot && athleteVdot > 0 && (sport === "road_running" || sport === "trail")) {
    const totalDist = results.splits.reduce((s, sp) => s + sp.distance, 0);
    const avgSpeed = totalDist / results.totalTime; // m/s
    const simPace = 1000 / avgSpeed / 60; // min/km

    // Marathon pace from VDOT (rough approximation: 80% VO2max)
    const vo2 = 0.80 * athleteVdot;
    const a = 0.000104;
    const b = 0.182258;
    const c = -4.6 - vo2;
    const disc = b * b - 4 * a * c;
    const marathonSpeedMPerMin = (-b + Math.sqrt(disc)) / (2 * a);
    const marathonPace = 1000 / marathonSpeedMPerMin; // min/km

    const diff = ((simPace - marathonPace) / marathonPace) * 100;
    const sign = diff > 0 ? "+" : "";

    suggestion = `VDOT ${athleteVdot} → marathon pace: ${formatPace(marathonPace)}. This simulation: ${formatPace(simPace)} (${sign}${diff.toFixed(1)}%)`;
  }

  // Compute cumulative values
  const splitsWithCumul = results.splits.map((split, i) => {
    const cumulDist = results.splits.slice(0, i + 1).reduce((s, sp) => s + sp.distance, 0);
    const cumulTime = results.splits.slice(0, i + 1).reduce((s, sp) => s + sp.time, 0);
    return { ...split, cumulDist, cumulTime };
  });

  const visibleSplits = expanded ? splitsWithCumul : splitsWithCumul.slice(0, 6);
  const totalDist = results.splits.reduce((s, sp) => s + sp.distance, 0);
  const avgSpeed = (totalDist / results.totalTime) * 3.6;
  const avgPace = 1000 / (totalDist / results.totalTime) / 60;

  return (
    <div className="space-y-4">
      {/* Suggestion */}
      {suggestion && (
        <div className="rounded-lg border border-blue-200 bg-blue-50/50 px-3 py-2">
          <p className="text-xs text-blue-700">{suggestion}</p>
        </div>
      )}

      {/* Summary */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Summary</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-3 gap-3 text-center">
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Time</p>
              <p className="text-lg font-bold tabular-nums">
                {formatTime(results.totalTime)}
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Avg Speed</p>
              <p className="text-lg font-bold tabular-nums">
                {avgSpeed.toFixed(1)} km/h
              </p>
            </div>
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">Avg Pace</p>
              <p className="text-lg font-bold tabular-nums">
                {formatPace(avgPace)}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Pace/Power Graph */}
      {results.splits.length > 1 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">
              {hasPower ? "Power Profile" : "Pace Profile"}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <PacePowerGraph
              splits={results.splits}
              yLabel={hasPower ? "Power (W)" : "Speed (km/h)"}
              getValue={
                hasPower
                  ? (s: SplitResult) => s.power || 0
                  : (s: SplitResult) => s.speed * 3.6
              }
            />
          </CardContent>
        </Card>
      )}

      {/* Splits */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">
            Splits ({results.splits.length})
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1.5 px-3">
          {visibleSplits.map((split, i) => (
            <div
              key={i}
              className="flex items-center gap-2 p-2 rounded-md hover:bg-muted/50 transition-colors text-xs border border-transparent hover:border-border"
            >
              {/* Split number + zone */}
              <div className="w-8 shrink-0 text-center">
                {hasZones && split.zone ? (
                  <Badge
                    variant="outline"
                    className="text-[10px] px-1 py-0 font-bold"
                    style={{
                      borderColor: split.zone.color,
                      color: split.zone.color,
                    }}
                  >
                    Z{split.zone.number}
                  </Badge>
                ) : (
                  <span className="text-muted-foreground font-medium">{i + 1}</span>
                )}
              </div>

              {/* Main info */}
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline justify-between gap-1">
                  <span className="font-medium tabular-nums">
                    {formatTime(split.time)}
                  </span>
                  <span className="text-muted-foreground tabular-nums">
                    {(split.speed * 3.6).toFixed(1)} km/h
                  </span>
                </div>
                <div className="flex items-baseline justify-between gap-1 text-muted-foreground">
                  <span className="tabular-nums">
                    km {(split.cumulDist / 1000).toFixed(1)}
                  </span>
                  <span className="tabular-nums">
                    <span className="text-red-500/70">+{Math.round(split.elevationGain)}</span>
                    {" / "}
                    <span className="text-blue-500/70">-{Math.round(split.elevationLoss)}</span>
                  </span>
                </div>
              </div>

              {/* Cumulative time */}
              <div className="w-16 shrink-0 text-right">
                <span className="font-semibold tabular-nums text-[11px]">
                  {formatTime(split.cumulTime)}
                </span>
              </div>
            </div>
          ))}

          {/* Show more / less */}
          {splitsWithCumul.length > 6 && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full text-xs text-muted-foreground"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded
                ? "Show less"
                : `Show all ${splitsWithCumul.length} splits`}
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
