import type { Segment, RoutePoint, CyclingConfig } from "@/types/route";
import { solveSpeed } from "@/lib/physics/cycling";

interface EquipmentSetup {
  name: string;
  cda: number;
  crr: number;
  bikeWeight: number;
}

interface ComparisonResult {
  segmentId: string;
  segmentType: string;
  distance: number;
  timeA: number; // seconds
  timeB: number; // seconds
  deltaTime: number; // seconds (negative = B is faster)
  wattsA: number;
  wattsB: number;
}

/**
 * Compare two equipment setups over the same route.
 * Same rider power, different aero/rolling characteristics.
 */
export function compareEquipment(
  segments: Segment[],
  points: RoutePoint[],
  setupA: EquipmentSetup,
  setupB: EquipmentSetup,
  riderWeight: number,
  ftp: number,
  efficiency: number = 0.25
): {
  results: ComparisonResult[];
  totalDeltaTime: number;
  totalTimeA: number;
  totalTimeB: number;
} {
  const results: ComparisonResult[] = [];
  let totalTimeA = 0;
  let totalTimeB = 0;

  for (const segment of segments) {
    const segPoints = points.slice(segment.startIndex, segment.endIndex + 1);
    const avgAltitude =
      segPoints.reduce((s, p) => s + p.ele, 0) / segPoints.length;

    const configA: CyclingConfig = {
      ftp,
      weight: riderWeight,
      bikeWeight: setupA.bikeWeight,
      cda: setupA.cda,
      crr: setupA.crr,
      efficiency,
      powerTargets: [],
    };

    const configB: CyclingConfig = {
      ftp,
      weight: riderWeight,
      bikeWeight: setupB.bikeWeight,
      cda: setupB.cda,
      crr: setupB.crr,
      efficiency,
      powerTargets: [],
    };

    const speedA = solveSpeed(
      ftp,
      segment.averageGrade,
      avgAltitude,
      configA
    );
    const speedB = solveSpeed(
      ftp,
      segment.averageGrade,
      avgAltitude,
      configB
    );

    const timeA = segment.length / speedA;
    const timeB = segment.length / speedB;

    results.push({
      segmentId: segment.id || `seg-${results.length}`,
      segmentType: segment.type,
      distance: segment.length,
      timeA,
      timeB,
      deltaTime: timeB - timeA,
      wattsA: ftp,
      wattsB: ftp,
    });

    totalTimeA += timeA;
    totalTimeB += timeB;
  }

  return {
    results,
    totalDeltaTime: totalTimeB - totalTimeA,
    totalTimeA,
    totalTimeB,
  };
}
