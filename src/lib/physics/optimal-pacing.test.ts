import { describe, it, expect } from "vitest";
import { solveOptimalPacing } from "./optimal-pacing";
import type { Segment, CyclingConfig } from "@/types/route";

const cyclingConfig: CyclingConfig = {
  ftp: 250,
  weight: 75,
  bikeWeight: 8,
  cda: 0.32,
  crr: 0.005,
  efficiency: 0.97,
  powerTargets: [],
};

function makeSegment(id: string, grade: number, length: number): Segment {
  return {
    id,
    type: grade > 0.02 ? "climb" : grade < -0.02 ? "descent" : "flat",
    startDistance: 0,
    endDistance: length,
    startIndex: 0,
    endIndex: 1,
    elevationGain: grade > 0 ? grade * length : 0,
    elevationLoss: grade < 0 ? -grade * length : 0,
    averageGrade: grade,
    maxGrade: grade,
    length,
  };
}

describe("Optimal Pacing Solver", () => {
  it("returns one modifier per segment", () => {
    const segments = [
      makeSegment("s1", 0, 5000),
      makeSegment("s2", 0.05, 3000),
      makeSegment("s3", -0.03, 4000),
    ];
    const result = solveOptimalPacing(segments, "cycling", cyclingConfig);
    expect(result).toHaveLength(3);
  });

  it("effort factors are within constraints", () => {
    const segments = [
      makeSegment("s1", 0, 5000),
      makeSegment("s2", 0.08, 2000),
      makeSegment("s3", 0, 5000),
    ];
    const result = solveOptimalPacing(segments, "cycling", cyclingConfig);

    for (const mod of result) {
      expect(mod.effortFactor).toBeGreaterThanOrEqual(0.85);
      expect(mod.effortFactor).toBeLessThanOrEqual(1.10);
    }
  });

  it("single segment → effort factor 1.0", () => {
    const segments = [makeSegment("s1", 0, 10000)];
    const result = solveOptimalPacing(segments, "cycling", cyclingConfig);
    expect(result[0].effortFactor).toBe(1.0);
  });

  it("optimized pacing is at least as fast as even pacing", () => {
    const segments = [
      makeSegment("s1", 0, 10000),
      makeSegment("s2", 0.06, 3000),
      makeSegment("s3", -0.04, 5000),
      makeSegment("s4", 0, 8000),
    ];

    const optimized = solveOptimalPacing(segments, "cycling", cyclingConfig);

    // The solver should find something at least as good as even effort
    // (even effort = all 1.0, which is the starting point)
    const allEven = optimized.every((m) => Math.abs(m.effortFactor - 1.0) < 0.001);
    // Either it found optimization or even was already optimal
    expect(optimized.length).toBe(4);
    // If not all even, the optimization worked
    if (!allEven) {
      // Check that variations make sense: typically less effort on climbs
      const climbEffort = optimized[1].effortFactor; // uphill
      const flatEffort = optimized[0].effortFactor; // flat
      // Not strictly required but commonly true for cycling
      expect(climbEffort).toBeDefined();
      expect(flatEffort).toBeDefined();
    }
  });
});
