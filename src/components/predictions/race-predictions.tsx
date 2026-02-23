"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import {
  runningPredictions,
  cyclingPredictions,
  swimmingPredictions,
  formatPredictionTime,
  formatPace,
} from "@/lib/predictions";
import type { Prediction } from "@/lib/predictions";
import {
  multiModelRunningPredictions,
  multiModelCyclingPredictions,
} from "@/lib/predictions/multi-model";
import type { MultiModelPrediction } from "@/types/route";

interface AthleteData {
  ftp: number | null;
  vdot: number | null;
  css: number | null;
  weight: number;
  bikeWeight: number;
  cda: number | null;
  crr: number;
  efficiency: number;
  cp?: number | null;
  wPrime?: number | null;
  referenceRaceDistance?: number | null;
  referenceRaceTime?: number | null;
}

interface Props {
  athlete: AthleteData;
}

function PredictionTable({
  predictions,
  paceUnit,
}: {
  predictions: Prediction[];
  paceUnit: string;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Distance</TableHead>
          <TableHead>Time</TableHead>
          <TableHead>Pace ({paceUnit})</TableHead>
          <TableHead>Speed</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {predictions.map((pred) => (
          <TableRow key={pred.distanceLabel}>
            <TableCell className="font-medium">{pred.distanceLabel}</TableCell>
            <TableCell>{formatPredictionTime(pred.time)}</TableCell>
            <TableCell>{formatPace(pred.pace)}</TableCell>
            <TableCell>{pred.speed.toFixed(1)} km/h</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function MultiModelTable({
  predictions,
  paceUnit,
}: {
  predictions: MultiModelPrediction[];
  paceUnit: string;
}) {
  if (predictions.length === 0) return null;

  // Collect all unique model names
  const modelNames = Array.from(
    new Set(predictions.flatMap((p) => p.models.map((m) => m.model)))
  );

  const MODEL_LABELS: Record<string, string> = {
    daniels: "Daniels",
    riegel: "Riegel",
    cameron: "Cameron",
    ftp: "FTP",
    cp_wprime: "CP/W'",
  };

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Distance</TableHead>
          {modelNames.map((name) => (
            <TableHead key={name}>{MODEL_LABELS[name] || name}</TableHead>
          ))}
          <TableHead className="font-semibold">Consensus</TableHead>
          <TableHead>Range</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {predictions.map((pred) => (
          <TableRow key={pred.distanceLabel}>
            <TableCell className="font-medium">{pred.distanceLabel}</TableCell>
            {modelNames.map((name) => {
              const model = pred.models.find((m) => m.model === name);
              return (
                <TableCell key={name} className="text-muted-foreground">
                  {model ? formatPredictionTime(model.time) : "-"}
                </TableCell>
              );
            })}
            <TableCell className="font-semibold">
              {formatPredictionTime(pred.consensusTime)}
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">
              {formatPredictionTime(pred.rangeMin)} - {formatPredictionTime(pred.rangeMax)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export function RacePredictions({ athlete }: Props) {
  const [showMultiModel, setShowMultiModel] = useState(false);

  const hasVdot = !!athlete.vdot && athlete.vdot > 0;
  const hasFtp = !!athlete.ftp && athlete.ftp > 0;
  const hasCss = !!athlete.css && athlete.css > 0;

  if (!hasVdot && !hasFtp && !hasCss) return null;

  const refRace =
    athlete.referenceRaceDistance && athlete.referenceRaceTime
      ? { distanceM: athlete.referenceRaceDistance, timeS: athlete.referenceRaceTime }
      : undefined;

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Race Predictions</CardTitle>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowMultiModel(!showMultiModel)}
          >
            {showMultiModel ? "Simple View" : "Multi-Model"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {showMultiModel
            ? "Comparing predictions from multiple scientific models. Consensus is a confidence-weighted average."
            : "Estimates based on your training data. Actual race times depend on course, weather, and race-day conditions."}
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Running */}
        {hasVdot && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Running (VDOT: {athlete.vdot})</h3>
            {showMultiModel ? (
              <MultiModelTable
                predictions={multiModelRunningPredictions(athlete.vdot!, refRace)}
                paceUnit="min/km"
              />
            ) : (
              <PredictionTable
                predictions={runningPredictions(athlete.vdot!)}
                paceUnit="min/km"
              />
            )}
          </div>
        )}

        {/* Cycling */}
        {hasFtp && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Cycling (FTP: {athlete.ftp}W)</h3>
            {showMultiModel ? (
              <MultiModelTable
                predictions={multiModelCyclingPredictions(
                  {
                    ftp: athlete.ftp!,
                    weight: athlete.weight,
                    bikeWeight: athlete.bikeWeight,
                    cda: athlete.cda ?? 0.32,
                    crr: athlete.crr,
                    efficiency: athlete.efficiency,
                  },
                  athlete.cp ?? undefined,
                  athlete.wPrime ?? undefined
                )}
                paceUnit="min/km"
              />
            ) : (
              <PredictionTable
                predictions={cyclingPredictions({
                  ftp: athlete.ftp!,
                  weight: athlete.weight,
                  bikeWeight: athlete.bikeWeight,
                  cda: athlete.cda ?? 0.32,
                  crr: athlete.crr,
                  efficiency: athlete.efficiency,
                })}
                paceUnit="min/km"
              />
            )}
          </div>
        )}

        {/* Swimming (no multi-model yet) */}
        {hasCss && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Swimming (CSS: {athlete.css}s/100m)</h3>
            <PredictionTable
              predictions={swimmingPredictions(athlete.css!)}
              paceUnit="min/100m"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
