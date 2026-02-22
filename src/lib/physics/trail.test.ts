import { describe, it, expect } from "vitest";
import { minettiCost, computeTrailSpeed, computeTrailSegmentTime, speedToPace } from "./trail";
import type { TrailConfig } from "@/types/route";

const defaultConfig: TrailConfig = {
  vma: 15, // 15 km/h VMA
  weight: 70,
  packWeight: 5,
};

describe("minettiCost", () => {
  it("returns ~3.6 J/kg/m on flat", () => {
    const cost = minettiCost(0);
    expect(cost).toBeCloseTo(3.6, 0);
  });

  it("increases with uphill gradient", () => {
    const flat = minettiCost(0);
    const uphill = minettiCost(0.1); // 10%
    expect(uphill).toBeGreaterThan(flat);
  });

  it("has a minimum cost floor of 2 J/kg/m", () => {
    const cost = minettiCost(-0.1); // downhill
    expect(cost).toBeGreaterThanOrEqual(2);
  });

  it("minimum cost is around -10% to -20% grade (optimal downhill)", () => {
    const minus10 = minettiCost(-0.1);
    const flat = minettiCost(0);
    // Running downhill at moderate grade is more efficient than flat
    expect(minus10).toBeLessThan(flat);
  });
});

describe("computeTrailSpeed", () => {
  it("returns reasonable flat speed for VMA 15", () => {
    const speed = computeTrailSpeed(0, defaultConfig);
    const kmh = speed * 3.6;
    // Trail pace on flat with pack: should be well below VMA
    expect(kmh).toBeGreaterThan(8);
    expect(kmh).toBeLessThan(15);
  });

  it("is slower uphill", () => {
    const flat = computeTrailSpeed(0, defaultConfig);
    const uphill = computeTrailSpeed(0.15, defaultConfig);
    expect(uphill).toBeLessThan(flat);
  });

  it("speed decreases with heavier pack", () => {
    const light: TrailConfig = { vma: 15, weight: 70, packWeight: 0 };
    const heavy: TrailConfig = { vma: 15, weight: 70, packWeight: 15 };
    const speedLight = computeTrailSpeed(0.05, light);
    const speedHeavy = computeTrailSpeed(0.05, heavy);
    expect(speedHeavy).toBeLessThan(speedLight);
  });

  it("never exceeds VMA", () => {
    const speed = computeTrailSpeed(-0.15, defaultConfig);
    const vmaMs = defaultConfig.vma / 3.6;
    expect(speed).toBeLessThanOrEqual(vmaMs);
  });

  it("never goes below 0.5 m/s", () => {
    const speed = computeTrailSpeed(0.5, defaultConfig); // 50% grade
    expect(speed).toBeGreaterThanOrEqual(0.5);
  });
});

describe("computeTrailSegmentTime", () => {
  it("computes reasonable time for 1km flat", () => {
    const time = computeTrailSegmentTime(1000, 0, defaultConfig);
    // ~10 km/h on flat trail with pack → ~360s for 1km
    expect(time).toBeGreaterThan(240);
    expect(time).toBeLessThan(500);
  });
});

describe("speedToPace", () => {
  it("converts correctly: 10 km/h = 6 min/km", () => {
    const pace = speedToPace(10 / 3.6);
    expect(pace).toBeCloseTo(6, 0);
  });

  it("returns 999 for zero speed", () => {
    expect(speedToPace(0)).toBe(999);
  });
});
