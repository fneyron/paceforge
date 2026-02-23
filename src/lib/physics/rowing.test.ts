import { describe, it, expect } from "vitest";
import { computeRowingSegmentTime, solveRowingSpeed, getCrewSize } from "./rowing";
import type { RowingConfig } from "@/types/route";

const defaultConfig: RowingConfig = {
  power: 250, // watts
  weight: 90, // kg
  boatClass: "1x",
};

describe("Rowing Engine", () => {
  it("2000m at 250W produces realistic time", () => {
    const time = computeRowingSegmentTime(2000, defaultConfig);
    // 250W single sculler: ~7-8 min for 2000m
    expect(time).toBeGreaterThan(5 * 60);
    expect(time).toBeLessThan(10 * 60);
  });

  it("more power → faster", () => {
    const slow = computeRowingSegmentTime(2000, { ...defaultConfig, power: 200 });
    const fast = computeRowingSegmentTime(2000, { ...defaultConfig, power: 350 });
    expect(fast).toBeLessThan(slow);
  });

  it("larger boat class → faster at same power per rower", () => {
    const singleTime = computeRowingSegmentTime(2000, { ...defaultConfig, boatClass: "1x", power: 250 });
    // 8 rowers at 250W each = 2000W total
    const eightTime = computeRowingSegmentTime(2000, { ...defaultConfig, boatClass: "8+", power: 2000 });
    // 8+ is hydrodynamically more efficient per rower
    expect(eightTime).toBeLessThan(singleTime);
  });

  it("favorable current → faster", () => {
    const still = computeRowingSegmentTime(2000, defaultConfig);
    const withCurrent = computeRowingSegmentTime(2000, { ...defaultConfig, currentSpeed: 0.5 });
    expect(withCurrent).toBeLessThan(still);
  });

  it("crew size is correct", () => {
    expect(getCrewSize("1x")).toBe(1);
    expect(getCrewSize("2x")).toBe(2);
    expect(getCrewSize("4-")).toBe(4);
    expect(getCrewSize("8+")).toBe(9);
  });

  it("speed is positive", () => {
    const speed = solveRowingSpeed(250, defaultConfig);
    expect(speed).toBeGreaterThan(0);
  });
});
