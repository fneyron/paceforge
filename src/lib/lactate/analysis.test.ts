import { describe, it, expect } from "vitest";
import { analyzeLactate, type LactateStep } from "./analysis";

describe("Lactate Analysis", () => {
  // Realistic running lactate curve
  const runningSteps: LactateStep[] = [
    { value: 8, lactate: 0.8, hr: 130 },
    { value: 9, lactate: 0.9, hr: 140 },
    { value: 10, lactate: 1.0, hr: 148 },
    { value: 11, lactate: 1.2, hr: 155 },
    { value: 12, lactate: 1.6, hr: 162 },
    { value: 13, lactate: 2.2, hr: 168 },
    { value: 14, lactate: 3.0, hr: 174 },
    { value: 15, lactate: 4.5, hr: 180 },
    { value: 16, lactate: 7.0, hr: 186 },
    { value: 17, lactate: 10.0, hr: 192 },
  ];

  it("detects LT1 (aerobic threshold)", () => {
    const result = analyzeLactate(runningSteps);
    expect(result.lt1).not.toBeNull();
    // LT1 should be where lactate > baseline + 1.0
    // Baseline is ~0.8, so threshold at ~1.8 mmol/L
    expect(result.lt1!.speed).toBeGreaterThan(10);
    expect(result.lt1!.speed).toBeLessThan(14);
    expect(result.lt1!.lactate).toBeGreaterThanOrEqual(1.7);
    expect(result.lt1!.hr).toBeDefined();
  });

  it("detects LT2 via Dmax method", () => {
    const result = analyzeLactate(runningSteps);
    expect(result.lt2Dmax).not.toBeNull();
    // Dmax should be around 13-15 km/h for this curve
    expect(result.lt2Dmax!.speed).toBeGreaterThan(12);
    expect(result.lt2Dmax!.speed).toBeLessThan(16);
    expect(result.lt2Dmax!.hr).toBeDefined();
  });

  it("detects LT2 via OBLA (4.0 mmol/L)", () => {
    const result = analyzeLactate(runningSteps);
    expect(result.lt2Obla).not.toBeNull();
    expect(result.lt2Obla!.lactate).toBeCloseTo(4.0, 1);
    // OBLA at 4 mmol/L should be around 14.5-15.5 km/h
    expect(result.lt2Obla!.speed).toBeGreaterThan(14);
    expect(result.lt2Obla!.speed).toBeLessThan(16);
  });

  it("generates spline points", () => {
    const result = analyzeLactate(runningSteps);
    expect(result.splinePoints.length).toBeGreaterThan(50);
    // Spline should be monotonically increasing-ish
    const first = result.splinePoints[0];
    const last = result.splinePoints[result.splinePoints.length - 1];
    expect(last.lactate).toBeGreaterThan(first.lactate);
  });

  it("handles too few steps gracefully", () => {
    const result = analyzeLactate([{ value: 10, lactate: 1.0 }]);
    expect(result.lt1).toBeNull();
    expect(result.lt2Dmax).toBeNull();
    expect(result.lt2Obla).toBeNull();
  });

  // Cycling curve
  it("works with cycling power data", () => {
    const cyclingSteps: LactateStep[] = [
      { value: 100, lactate: 0.9, hr: 110 },
      { value: 125, lactate: 1.0, hr: 120 },
      { value: 150, lactate: 1.3, hr: 132 },
      { value: 175, lactate: 1.8, hr: 144 },
      { value: 200, lactate: 2.5, hr: 155 },
      { value: 225, lactate: 3.5, hr: 165 },
      { value: 250, lactate: 5.0, hr: 175 },
      { value: 275, lactate: 8.0, hr: 185 },
    ];

    const result = analyzeLactate(cyclingSteps);
    expect(result.lt1).not.toBeNull();
    expect(result.lt2Dmax).not.toBeNull();
    expect(result.lt2Obla).not.toBeNull();
    // LT2 OBLA should be between 200-250W
    expect(result.lt2Obla!.speed).toBeGreaterThan(200);
    expect(result.lt2Obla!.speed).toBeLessThan(260);
  });
});
