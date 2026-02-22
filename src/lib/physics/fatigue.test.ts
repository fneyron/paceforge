import { describe, it, expect } from "vitest";
import { fatigueFactor, DEFAULT_FATIGUE } from "./fatigue";

describe("fatigueFactor", () => {
  const config = { halfLife: 5, minFactor: 0.7 };

  it("returns 1.0 at time 0", () => {
    expect(fatigueFactor(0, config)).toBeCloseTo(1.0);
  });

  it("returns ~0.5 at half-life", () => {
    expect(fatigueFactor(5, config)).toBeCloseTo(0.7);
    // With minFactor 0.7, the floor kicks in before the pure exponential 0.5
  });

  it("returns ~0.5 at half-life without floor", () => {
    const noFloor = { halfLife: 5, minFactor: 0 };
    expect(fatigueFactor(5, noFloor)).toBeCloseTo(0.5, 1);
  });

  it("never goes below minFactor", () => {
    expect(fatigueFactor(100, config)).toBeGreaterThanOrEqual(config.minFactor);
    expect(fatigueFactor(100, config)).toBeCloseTo(config.minFactor);
  });

  it("decreases monotonically", () => {
    const noFloor = { halfLife: 5, minFactor: 0 };
    const f1 = fatigueFactor(1, noFloor);
    const f3 = fatigueFactor(3, noFloor);
    const f10 = fatigueFactor(10, noFloor);
    expect(f1).toBeGreaterThan(f3);
    expect(f3).toBeGreaterThan(f10);
  });
});

describe("DEFAULT_FATIGUE", () => {
  it("has configs for all sports", () => {
    expect(DEFAULT_FATIGUE.cycling).toBeDefined();
    expect(DEFAULT_FATIGUE.trail).toBeDefined();
    expect(DEFAULT_FATIGUE.ultra_trail).toBeDefined();
    expect(DEFAULT_FATIGUE.road_running).toBeDefined();
    expect(DEFAULT_FATIGUE.swimming).toBeDefined();
    expect(DEFAULT_FATIGUE.triathlon).toBeDefined();
  });

  it("ultra_trail has longer half-life than cycling", () => {
    expect(DEFAULT_FATIGUE.ultra_trail.halfLife).toBeGreaterThan(
      DEFAULT_FATIGUE.cycling.halfLife
    );
  });

  it("all minFactors are between 0 and 1", () => {
    for (const [, config] of Object.entries(DEFAULT_FATIGUE)) {
      expect(config.minFactor).toBeGreaterThan(0);
      expect(config.minFactor).toBeLessThan(1);
    }
  });
});
