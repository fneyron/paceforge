import { describe, it, expect } from "vitest";
import { altitudePerformanceFactor } from "./altitude";

describe("Altitude Performance Model", () => {
  it("sea level → 1.0", () => {
    expect(altitudePerformanceFactor(0)).toBe(1.0);
  });

  it("1500m → 1.0 (no effect below threshold)", () => {
    expect(altitudePerformanceFactor(1500)).toBe(1.0);
  });

  it("2000m → ~0.9675", () => {
    const f = altitudePerformanceFactor(2000);
    expect(f).toBeCloseTo(0.9675, 3);
  });

  it("3000m → ~0.9025", () => {
    const f = altitudePerformanceFactor(3000);
    expect(f).toBeCloseTo(0.9025, 3);
  });

  it("5000m → ~0.7725", () => {
    const f = altitudePerformanceFactor(5000);
    expect(f).toBeCloseTo(0.7725, 3);
  });

  it("never goes below 0.5", () => {
    expect(altitudePerformanceFactor(10000)).toBe(0.5);
  });

  it("monotonically decreasing above 1500m", () => {
    const f2000 = altitudePerformanceFactor(2000);
    const f3000 = altitudePerformanceFactor(3000);
    const f4000 = altitudePerformanceFactor(4000);
    expect(f3000).toBeLessThan(f2000);
    expect(f4000).toBeLessThan(f3000);
  });
});
