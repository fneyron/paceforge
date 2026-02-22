"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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

export function RacePredictions({ athlete }: Props) {
  const hasVdot = !!athlete.vdot && athlete.vdot > 0;
  const hasFtp = !!athlete.ftp && athlete.ftp > 0;
  const hasCss = !!athlete.css && athlete.css > 0;

  if (!hasVdot && !hasFtp && !hasCss) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Race Predictions</CardTitle>
        <p className="text-xs text-muted-foreground">
          Estimates based on your Strava training data (median of recent best efforts). Actual race times depend on course, weather, and race-day conditions.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Running */}
        {hasVdot && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Running (VDOT: {athlete.vdot})</h3>
            <PredictionTable
              predictions={runningPredictions(athlete.vdot!)}
              paceUnit="min/km"
            />
          </div>
        )}

        {/* Cycling */}
        {hasFtp && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Cycling (FTP: {athlete.ftp}W)</h3>
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
          </div>
        )}

        {/* Swimming */}
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
