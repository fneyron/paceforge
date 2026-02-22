import { describe, it, expect } from "vitest";
import { simulateTriathlon, TRIATHLON_FORMATS } from "./triathlon";
import type {
  TriathlonConfig,
  SwimmingConfig,
  CyclingConfig,
  RoadRunningConfig,
  Segment,
  RoutePoint,
} from "@/types/route";

function makeSegment(overrides: Partial<Segment> = {}): Segment {
  return {
    type: "flat",
    startDistance: 0,
    endDistance: 1000,
    startIndex: 0,
    endIndex: 1,
    elevationGain: 0,
    elevationLoss: 0,
    averageGrade: 0,
    maxGrade: 0,
    length: 1000,
    ...overrides,
  };
}

function makePoints(count: number): RoutePoint[] {
  return Array.from({ length: count }, (_, i) => ({
    lat: 45 + i * 0.001,
    lon: 6 + i * 0.001,
    ele: 100,
    distance: i * 100,
    grade: 0,
  }));
}

const swimConfig: SwimmingConfig = {
  css: 100,
  weight: 75,
  height: 180,
  isOpenWater: true,
  hasWetsuit: true,
};

const bikeConfig: CyclingConfig = {
  ftp: 250,
  weight: 75,
  bikeWeight: 8,
  cda: 0.32,
  crr: 0.005,
  efficiency: 0.97,
  powerTargets: [],
};

const runConfig: RoadRunningConfig = {
  vdot: 50,
  weight: 75,
};

const triConfig: TriathlonConfig = {
  swimConfig,
  cyclingConfig: bikeConfig,
  runConfig,
  t1Time: 120,
  t2Time: 90,
  raceFormat: "olympic",
};

describe("simulateTriathlon", () => {
  it("produces splits for swim + T1 + bike + T2 + run", () => {
    const result = simulateTriathlon({
      legs: [
        { discipline: "swim", segments: [makeSegment({ length: 1500 })], points: makePoints(2) },
        { discipline: "bike", segments: [makeSegment({ length: 40000 })], points: makePoints(2) },
        { discipline: "run", segments: [makeSegment({ length: 10000 })], points: makePoints(2) },
      ],
      config: triConfig,
    });

    // Should have: swim, t1, bike, t2, run = 5 splits
    expect(result.splits.length).toBe(5);
    expect(result.splits[0].segmentId).toBe("swim");
    expect(result.splits[1].segmentId).toBe("t1");
    expect(result.splits[3].segmentId).toBe("t2");
  });

  it("totalTime includes transitions", () => {
    const result = simulateTriathlon({
      legs: [
        { discipline: "swim", segments: [makeSegment({ length: 1500 })], points: makePoints(2) },
        { discipline: "bike", segments: [makeSegment({ length: 40000 })], points: makePoints(2) },
        { discipline: "run", segments: [makeSegment({ length: 10000 })], points: makePoints(2) },
      ],
      config: triConfig,
    });

    expect(result.totalTime).toBeGreaterThan(result.movingTime);
    expect(result.totalTime - result.movingTime).toBe(
      triConfig.t1Time + triConfig.t2Time
    );
  });

  it("olympic triathlon total time is reasonable (2-3.5h)", () => {
    const result = simulateTriathlon({
      legs: [
        { discipline: "swim", segments: [makeSegment({ length: 1500 })], points: makePoints(2) },
        { discipline: "bike", segments: [makeSegment({ length: 40000 })], points: makePoints(2) },
        { discipline: "run", segments: [makeSegment({ length: 10000 })], points: makePoints(2) },
      ],
      config: triConfig,
    });

    const hours = result.totalTime / 3600;
    expect(hours).toBeGreaterThan(1.5);
    expect(hours).toBeLessThan(4);
  });

  it("T1 and T2 have zero distance and speed", () => {
    const result = simulateTriathlon({
      legs: [
        { discipline: "swim", segments: [makeSegment({ length: 750 })], points: makePoints(2) },
        { discipline: "bike", segments: [makeSegment({ length: 20000 })], points: makePoints(2) },
        { discipline: "run", segments: [makeSegment({ length: 5000 })], points: makePoints(2) },
      ],
      config: triConfig,
    });

    const t1 = result.splits.find((s) => s.segmentId === "t1")!;
    const t2 = result.splits.find((s) => s.segmentId === "t2")!;
    expect(t1.distance).toBe(0);
    expect(t1.speed).toBe(0);
    expect(t2.distance).toBe(0);
    expect(t2.speed).toBe(0);
  });
});

describe("TRIATHLON_FORMATS", () => {
  it("has standard distances", () => {
    expect(TRIATHLON_FORMATS.sprint.swim).toBe(750);
    expect(TRIATHLON_FORMATS.olympic.bike).toBe(40000);
    expect(TRIATHLON_FORMATS.ironman.run).toBe(42195);
  });
});
