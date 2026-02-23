import { describe, it, expect } from "vitest";
import { computeXCSkiSegmentTime, solveXCSkiSpeed } from "./cross-country-skiing";
import type { CrossCountrySkiingConfig } from "@/types/route";

const defaultConfig: CrossCountrySkiingConfig = {
  vo2max: 60,
  weight: 75,
  technique: "skating",
  snowFriction: 0.04,
};

describe("Cross-Country Skiing Engine", () => {
  it("flat terrain produces reasonable speed", () => {
    const time = computeXCSkiSegmentTime(1000, 0, defaultConfig);
    const speed = 1000 / time; // m/s
    const speedKmh = speed * 3.6;
    // Elite XC skier: ~25 km/h flat, recreational: ~15 km/h
    expect(speedKmh).toBeGreaterThan(10);
    expect(speedKmh).toBeLessThan(35);
  });

  it("uphill is slower than flat", () => {
    const flatTime = computeXCSkiSegmentTime(1000, 0, defaultConfig);
    const uphillTime = computeXCSkiSegmentTime(1000, 0.08, defaultConfig);
    expect(uphillTime).toBeGreaterThan(flatTime);
  });

  it("downhill is faster than flat", () => {
    const flatTime = computeXCSkiSegmentTime(1000, 0, defaultConfig);
    const downhillTime = computeXCSkiSegmentTime(1000, -0.05, defaultConfig);
    expect(downhillTime).toBeLessThan(flatTime);
  });

  it("classic is slower than skating", () => {
    const classicTime = computeXCSkiSegmentTime(1000, 0, { ...defaultConfig, technique: "classic" });
    const skatingTime = computeXCSkiSegmentTime(1000, 0, { ...defaultConfig, technique: "skating" });
    expect(classicTime).toBeGreaterThan(skatingTime);
  });

  it("higher VO2max → faster", () => {
    const lowVO2 = computeXCSkiSegmentTime(1000, 0, { ...defaultConfig, vo2max: 45 });
    const highVO2 = computeXCSkiSegmentTime(1000, 0, { ...defaultConfig, vo2max: 70 });
    expect(highVO2).toBeLessThan(lowVO2);
  });

  it("higher friction → slower", () => {
    const low = computeXCSkiSegmentTime(1000, 0, { ...defaultConfig, snowFriction: 0.02 });
    const high = computeXCSkiSegmentTime(1000, 0, { ...defaultConfig, snowFriction: 0.10 });
    expect(high).toBeGreaterThan(low);
  });
});
