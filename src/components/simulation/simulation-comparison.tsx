"use client";

import { formatTime } from "@/lib/physics/simulate";
import type { SimulationResult } from "@/types/route";

interface Props {
  a: SimulationResult & { name: string };
  b: SimulationResult & { name: string };
}

export function SimulationComparison({ a, b }: Props) {
  const avgSpeedA = a.splits.reduce((sum, s) => sum + s.speed, 0) / a.splits.length;
  const avgSpeedB = b.splits.reduce((sum, s) => sum + s.speed, 0) / b.splits.length;
  const delta = b.totalTime - a.totalTime;

  return (
    <div className="space-y-4 mt-4">
      <h4 className="text-sm font-medium">Comparison</h4>

      {/* Summary */}
      <div className="border rounded-md p-3 text-sm space-y-2">
        <div className="grid grid-cols-3 gap-2 text-xs font-medium text-muted-foreground">
          <span />
          <span className="text-blue-600">{a.name}</span>
          <span className="text-orange-600">{b.name}</span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-sm">
          <span className="text-muted-foreground">Total time</span>
          <span className="font-medium">{formatTime(a.totalTime)}</span>
          <span className="font-medium">{formatTime(b.totalTime)}</span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-sm">
          <span className="text-muted-foreground">Avg speed</span>
          <span>{(avgSpeedA * 3.6).toFixed(1)} km/h</span>
          <span>{(avgSpeedB * 3.6).toFixed(1)} km/h</span>
        </div>
        <div className="grid grid-cols-3 gap-2 text-sm border-t pt-1">
          <span className="text-muted-foreground">Delta</span>
          <span
            className={`font-bold col-span-2 ${
              delta < 0 ? "text-green-600" : delta > 0 ? "text-red-600" : ""
            }`}
          >
            {delta > 0 ? "+" : ""}{formatTime(Math.abs(delta))}
            {delta < 0 ? " (B faster)" : delta > 0 ? " (A faster)" : ""}
          </span>
        </div>
      </div>

      {/* Split-by-split */}
      <div className="border rounded-md overflow-hidden">
        <div className="grid grid-cols-4 gap-1 text-xs font-medium bg-muted/50 px-2 py-1">
          <span>Segment</span>
          <span className="text-blue-600">A time</span>
          <span className="text-orange-600">B time</span>
          <span>Delta</span>
        </div>
        <div className="max-h-64 overflow-y-auto">
          {a.splits.map((splitA, i) => {
            const splitB = b.splits[i];
            if (!splitB) return null;
            const splitDelta = splitB.time - splitA.time;
            return (
              <div
                key={i}
                className="grid grid-cols-4 gap-1 text-xs px-2 py-1 border-t"
              >
                <span className="truncate">
                  {(splitA.distance / 1000).toFixed(1)}km
                </span>
                <span>{formatTime(splitA.time)}</span>
                <span>{formatTime(splitB.time)}</span>
                <span
                  className={
                    splitDelta < -1
                      ? "text-green-600"
                      : splitDelta > 1
                        ? "text-red-600"
                        : "text-muted-foreground"
                  }
                >
                  {splitDelta > 0 ? "+" : ""}
                  {splitDelta.toFixed(0)}s
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
