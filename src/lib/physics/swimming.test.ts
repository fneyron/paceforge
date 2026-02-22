import { describe, it, expect } from "vitest";
import { computeSwimSpeed, computeSwimmingSegmentTime, swimmingCalories } from "./swimming";
import type { SwimmingConfig } from "@/types/route";

const poolConfig: SwimmingConfig = {
  css: 90, // 1:30/100m
  weight: 75,
  height: 180,
  isOpenWater: false,
  hasWetsuit: false,
};

describe("computeSwimSpeed", () => {
  it("pool speed at CSS 90s/100m → ~1.11 m/s", () => {
    const speed = computeSwimSpeed(poolConfig);
    expect(speed).toBeCloseTo(100 / 90, 1);
  });

  it("open water is slower than pool", () => {
    const pool = computeSwimSpeed(poolConfig);
    const openWater = computeSwimSpeed({ ...poolConfig, isOpenWater: true });
    expect(openWater).toBeLessThan(pool);
  });

  it("wetsuit gives a bonus", () => {
    const noWetsuit = computeSwimSpeed({ ...poolConfig, isOpenWater: true });
    const wetsuit = computeSwimSpeed({ ...poolConfig, isOpenWater: true, hasWetsuit: true });
    expect(wetsuit).toBeGreaterThan(noWetsuit);
  });

  it("cold water penalty", () => {
    const normal = computeSwimSpeed(poolConfig);
    const cold = computeSwimSpeed({ ...poolConfig, waterTemperature: 12 });
    expect(cold).toBeLessThan(normal);
  });

  it("warm water penalty", () => {
    const normal = computeSwimSpeed(poolConfig);
    const warm = computeSwimSpeed({ ...poolConfig, waterTemperature: 30 });
    expect(warm).toBeLessThan(normal);
  });

  it("favorable current increases speed", () => {
    const noCurrent = computeSwimSpeed(poolConfig);
    const current = computeSwimSpeed({ ...poolConfig, currentSpeed: 0.3 });
    expect(current).toBeGreaterThan(noCurrent);
  });

  it("never goes below 0.3 m/s", () => {
    const speed = computeSwimSpeed({
      ...poolConfig,
      css: 300, // very slow swimmer
      isOpenWater: true,
      waterTemperature: 10,
    });
    expect(speed).toBeGreaterThanOrEqual(0.3);
  });
});

describe("computeSwimmingSegmentTime", () => {
  it("1500m at CSS 90s/100m → ~22.5 min", () => {
    const time = computeSwimmingSegmentTime(1500, poolConfig);
    expect(time / 60).toBeCloseTo(1500 / (100 / 90) / 60, 0);
  });
});

describe("swimmingCalories", () => {
  it("1 hour moderate swimming burns 500-700 kcal for 75kg", () => {
    const cal = swimmingCalories(1, 75, 0.5);
    expect(cal).toBeGreaterThan(400);
    expect(cal).toBeLessThan(800);
  });

  it("higher intensity burns more", () => {
    const low = swimmingCalories(1, 75, 0.3);
    const high = swimmingCalories(1, 75, 0.9);
    expect(high).toBeGreaterThan(low);
  });
});
