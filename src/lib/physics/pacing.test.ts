import { describe, it, expect } from "vitest";
import { resolveStrategy } from "./pacing";
import type { Segment } from "@/types/route";

function makeSegments(count: number, totalDistance: number): Segment[] {
  const segLen = totalDistance / count;
  return Array.from({ length: count }, (_, i) => ({
    type: i % 3 === 0 ? "climb" : i % 3 === 1 ? "flat" : "descent",
    startDistance: i * segLen,
    endDistance: (i + 1) * segLen,
    startIndex: i * 10,
    endIndex: (i + 1) * 10,
    elevationGain: i % 3 === 0 ? 100 : 0,
    elevationLoss: i % 3 === 2 ? 100 : 0,
    averageGrade: i % 3 === 0 ? 0.05 : i % 3 === 2 ? -0.05 : 0,
    maxGrade: i % 3 === 0 ? 0.1 : i % 3 === 2 ? -0.1 : 0.01,
    length: segLen,
    id: `seg-${i}`,
  })) as Segment[];
}

describe("resolveStrategy", () => {
  const segments = makeSegments(10, 100000);
  const totalDistance = 100000;

  it("even_effort returns 1.0 for all segments", () => {
    const mods = resolveStrategy({ type: "even_effort" }, segments, totalDistance);
    expect(mods).toHaveLength(10);
    for (const m of mods) {
      expect(m.effortFactor).toBe(1.0);
    }
  });

  it("negative_split has lower first half, higher second half", () => {
    const mods = resolveStrategy(
      {
        type: "negative_split",
        firstHalfFactor: 0.93,
        secondHalfFactor: 1.03,
        transitionPoint: 0.5,
      },
      segments,
      totalDistance
    );

    // First segment (center at 5000m, well before 50000m)
    expect(mods[0].effortFactor).toBeCloseTo(0.93, 1);

    // Last segment (center at 95000m, well after 50000m)
    expect(mods[9].effortFactor).toBeCloseTo(1.03, 1);

    // Middle segments should transition
    const midMod = mods[5]; // center at 55000m, just past transition
    expect(midMod.effortFactor).toBeGreaterThan(0.93);
    expect(midMod.effortFactor).toBeLessThanOrEqual(1.03);
  });

  it("positive_split has higher first half, lower second half", () => {
    const mods = resolveStrategy(
      {
        type: "positive_split",
        firstHalfFactor: 1.03,
        secondHalfFactor: 0.93,
        transitionPoint: 0.5,
      },
      segments,
      totalDistance
    );

    expect(mods[0].effortFactor).toBeCloseTo(1.03, 1);
    expect(mods[9].effortFactor).toBeCloseTo(0.93, 1);
  });

  it("race_strategy assigns factors by segment type", () => {
    const mods = resolveStrategy(
      {
        type: "race_strategy",
        climbFactor: 0.9,
        flatFactor: 1.0,
        descentFactor: 1.05,
      },
      segments,
      totalDistance
    );

    // seg-0 is climb
    expect(mods[0].effortFactor).toBe(0.9);
    // seg-1 is flat
    expect(mods[1].effortFactor).toBe(1.0);
    // seg-2 is descent
    expect(mods[2].effortFactor).toBe(1.05);
  });
});
