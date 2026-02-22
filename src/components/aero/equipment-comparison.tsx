"use client";

import { formatTime } from "@/lib/physics/simulate";

interface ComparisonResult {
  segmentId: string;
  segmentType: string;
  distance: number;
  timeA: number;
  timeB: number;
  deltaTime: number;
}

interface Props {
  setupAName: string;
  setupBName: string;
  results: ComparisonResult[];
  totalDeltaTime: number;
  totalTimeA: number;
  totalTimeB: number;
}

export function EquipmentComparison({
  setupAName,
  setupBName,
  results,
  totalDeltaTime,
  totalTimeA,
  totalTimeB,
}: Props) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <div className="border rounded-md p-3 text-center">
          <p className="text-xs text-muted-foreground">{setupAName}</p>
          <p className="font-medium">{formatTime(totalTimeA)}</p>
        </div>
        <div className="border rounded-md p-3 text-center">
          <p className="text-xs text-muted-foreground">Difference</p>
          <p
            className={`font-medium ${
              totalDeltaTime < 0
                ? "text-green-600"
                : totalDeltaTime > 0
                ? "text-red-600"
                : ""
            }`}
          >
            {totalDeltaTime > 0 ? "+" : ""}
            {formatTime(Math.abs(totalDeltaTime))}
          </p>
        </div>
        <div className="border rounded-md p-3 text-center">
          <p className="text-xs text-muted-foreground">{setupBName}</p>
          <p className="font-medium">{formatTime(totalTimeB)}</p>
        </div>
      </div>

      <div className="text-xs space-y-1 max-h-48 overflow-y-auto">
        <div className="flex justify-between font-medium py-1 border-b">
          <span>Segment</span>
          <span>{setupAName}</span>
          <span>{setupBName}</span>
          <span>Delta</span>
        </div>
        {results.map((r, i) => (
          <div
            key={i}
            className="flex justify-between py-1 border-b last:border-b-0"
          >
            <span className="text-muted-foreground">
              {(r.distance / 1000).toFixed(1)}km {r.segmentType}
            </span>
            <span>{formatTime(r.timeA)}</span>
            <span>{formatTime(r.timeB)}</span>
            <span
              className={
                r.deltaTime < 0
                  ? "text-green-600"
                  : r.deltaTime > 0
                  ? "text-red-600"
                  : ""
              }
            >
              {r.deltaTime > 0 ? "+" : ""}
              {r.deltaTime.toFixed(0)}s
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
