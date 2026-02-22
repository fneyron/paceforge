"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useRouteStore } from "@/store/route-store";
import {
  runningPredictions,
  cyclingPredictions,
  swimmingPredictions,
  formatPredictionTime,
  formatPace,
} from "@/lib/predictions";
import type { Prediction } from "@/lib/predictions";

interface AthleteData {
  ftp: number | null;
  vdot: number | null;
  css: number | null;
  weight: number;
  bikeWeight: number;
  cda: number | null;
  crr: number;
  efficiency: number;
}

function PredictionList({ predictions, paceUnit }: { predictions: Prediction[]; paceUnit: string }) {
  return (
    <div className="space-y-1">
      {predictions.map((pred) => (
        <div
          key={pred.distanceLabel}
          className="flex items-center justify-between text-xs p-2 rounded hover:bg-muted/50 transition-colors"
        >
          <span className="font-medium">{pred.distanceLabel}</span>
          <div className="flex items-center gap-3 tabular-nums">
            <span className="font-semibold">{formatPredictionTime(pred.time)}</span>
            <span className="text-muted-foreground w-14 text-right">
              {formatPace(pred.pace)}{paceUnit}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function RoutePredictionsSidebar() {
  const sport = useRouteStore((s) => s.sport);
  const [athlete, setAthlete] = useState<AthleteData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/athlete")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) setAthlete(data);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="p-4 space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-20 animate-pulse rounded-md bg-muted" />
        ))}
      </div>
    );
  }

  if (!athlete) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        Connect Strava or configure your athlete profile to see predictions.
      </div>
    );
  }

  const hasVdot = !!athlete.vdot && athlete.vdot > 0;
  const hasFtp = !!athlete.ftp && athlete.ftp > 0;
  const hasCss = !!athlete.css && athlete.css > 0;

  if (!hasVdot && !hasFtp && !hasCss) {
    return (
      <div className="p-4 text-sm text-muted-foreground">
        No performance data available. Set your FTP, VDOT, or CSS in your athlete profile.
      </div>
    );
  }

  // Determine which predictions to show based on route sport
  const showRunning = hasVdot && ["road_running", "trail", "ultra_trail", "triathlon"].includes(sport);
  const showCycling = hasFtp && ["cycling", "gravel", "triathlon"].includes(sport);
  const showSwimming = hasCss && ["swimming", "triathlon"].includes(sport);

  // If sport doesn't match any, show all available
  const showAll = !showRunning && !showCycling && !showSwimming;

  return (
    <ScrollArea className="h-full">
      <div className="p-4 space-y-4">
        {(showRunning || (showAll && hasVdot)) && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                Running <span className="text-muted-foreground font-normal">(VDOT {athlete.vdot})</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="px-2">
              <PredictionList
                predictions={runningPredictions(athlete.vdot!)}
                paceUnit="/km"
              />
            </CardContent>
          </Card>
        )}

        {(showCycling || (showAll && hasFtp)) && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                Cycling <span className="text-muted-foreground font-normal">(FTP {athlete.ftp}W)</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="px-2">
              <PredictionList
                predictions={cyclingPredictions({
                  ftp: athlete.ftp!,
                  weight: athlete.weight,
                  bikeWeight: athlete.bikeWeight,
                  cda: athlete.cda ?? 0.32,
                  crr: athlete.crr,
                  efficiency: athlete.efficiency,
                })}
                paceUnit="/km"
              />
            </CardContent>
          </Card>
        )}

        {(showSwimming || (showAll && hasCss)) && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">
                Swimming <span className="text-muted-foreground font-normal">(CSS {athlete.css}s/100m)</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="px-2">
              <PredictionList
                predictions={swimmingPredictions(athlete.css!)}
                paceUnit="/100m"
              />
            </CardContent>
          </Card>
        )}
      </div>
    </ScrollArea>
  );
}
