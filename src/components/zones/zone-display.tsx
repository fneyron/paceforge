"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ZoneBar } from "./zone-bar";
import { ZoneTable } from "./zone-table";
import {
  powerZones,
  paceZones,
  hrZones,
  swimZones,
  formatPace,
  formatSwimPace,
} from "@/lib/zones";

interface AthleteData {
  ftp: number | null;
  vdot: number | null;
  fcMax: number | null;
  lactateThreshold: number | null;
  css: number | null;
}

interface Props {
  athlete: AthleteData;
}

export function ZoneDisplay({ athlete }: Props) {
  const hasFtp = !!athlete.ftp && athlete.ftp > 0;
  const hasVdot = !!athlete.vdot && athlete.vdot > 0;
  const hasFcMax = !!athlete.fcMax && athlete.fcMax > 0;
  const hasCss = !!athlete.css && athlete.css > 0;

  if (!hasFtp && !hasVdot && !hasFcMax && !hasCss) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Training Zones</CardTitle>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Power Zones */}
        {hasFtp && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Power Zones (FTP: {athlete.ftp}W)</h3>
            <ZoneBar zones={powerZones(athlete.ftp!)} unit="W" />
            <ZoneTable
              zones={powerZones(athlete.ftp!)}
              unit="W"
              formatValue={(v) => `${v}W`}
            />
          </div>
        )}

        {/* Running Pace Zones */}
        {hasVdot && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Running Pace Zones (VDOT: {athlete.vdot})</h3>
            <ZoneBar zones={paceZones(athlete.vdot!)} unit="min/km" />
            <ZoneTable
              zones={paceZones(athlete.vdot!)}
              unit="min/km"
              formatValue={(v) => formatPace(v)}
            />
          </div>
        )}

        {/* Heart Rate Zones */}
        {hasFcMax && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">
              Heart Rate Zones (FCmax: {athlete.fcMax} bpm)
              {athlete.lactateThreshold ? ` | LT: ${athlete.lactateThreshold} bpm` : ""}
            </h3>
            <ZoneBar
              zones={hrZones(athlete.fcMax!, athlete.lactateThreshold ?? undefined)}
              unit="bpm"
            />
            <ZoneTable
              zones={hrZones(athlete.fcMax!, athlete.lactateThreshold ?? undefined)}
              unit="bpm"
              formatValue={(v) => `${v}`}
            />
          </div>
        )}

        {/* Swimming Zones */}
        {hasCss && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">
              Swimming Zones (CSS: {formatSwimPace(athlete.css!)}/100m)
            </h3>
            <ZoneBar zones={swimZones(athlete.css!)} unit="/100m" />
            <ZoneTable
              zones={swimZones(athlete.css!)}
              unit="/100m"
              formatValue={(v) => formatSwimPace(v)}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
