import { describe, it, expect } from "vitest";
import {
  vdotToMarathonSpeed,
  vdotToThresholdSpeed,
  riegelPredict,
  thermalCorrectionFactor,
  computeRoadRunningSpeed,
  computeRoadRunningSegmentTime,
} from "./road-running";
import type { RoadRunningConfig } from "@/types/route";

describe("vdotToMarathonSpeed", () => {
  it("VDOT 50 → marathon ~3:40 (pace ~5:12/km)", () => {
    const speed = vdotToMarathonSpeed(50);
    const paceMinKm = 1000 / speed / 60;
    // VDOT 50 marathon: ~3:37 → pace ~5:09/km
    expect(paceMinKm).toBeGreaterThan(4.5);
    expect(paceMinKm).toBeLessThan(5.8);
  });

  it("VDOT 60 is faster than VDOT 40", () => {
    const speed60 = vdotToMarathonSpeed(60);
    const speed40 = vdotToMarathonSpeed(40);
    expect(speed60).toBeGreaterThan(speed40);
  });

  it("VDOT 40 → marathon ~4:30 (pace ~6:24/km)", () => {
    const speed = vdotToMarathonSpeed(40);
    const paceMinKm = 1000 / speed / 60;
    expect(paceMinKm).toBeGreaterThan(5.0);
    expect(paceMinKm).toBeLessThan(7.5);
  });

  it("VDOT 70 → sub-3h marathon (pace < 4:16/km)", () => {
    const speed = vdotToMarathonSpeed(70);
    const paceMinKm = 1000 / speed / 60;
    expect(paceMinKm).toBeLessThan(4.5);
  });
});

describe("vdotToThresholdSpeed", () => {
  it("threshold is faster than marathon pace", () => {
    const marathon = vdotToMarathonSpeed(50);
    const threshold = vdotToThresholdSpeed(50);
    expect(threshold).toBeGreaterThan(marathon);
  });
});

describe("riegelPredict", () => {
  it("predicts longer time for longer distance", () => {
    // 10k in 40min → marathon should be > 40min × 4.2195
    const marathonTime = riegelPredict(10000, 40 * 60, 42195);
    expect(marathonTime).toBeGreaterThan(40 * 60 * 4.2);
  });

  it("predicts exact time at same distance", () => {
    const time = riegelPredict(10000, 2400, 10000);
    expect(time).toBeCloseTo(2400, 1);
  });
});

describe("thermalCorrectionFactor", () => {
  it("returns 1.0 at 15°C or below", () => {
    expect(thermalCorrectionFactor(15)).toBe(1.0);
    expect(thermalCorrectionFactor(10)).toBe(1.0);
    expect(thermalCorrectionFactor(0)).toBe(1.0);
  });

  it("degrades performance above 15°C", () => {
    expect(thermalCorrectionFactor(25)).toBeLessThan(1.0);
    expect(thermalCorrectionFactor(35)).toBeLessThan(thermalCorrectionFactor(25));
  });

  it("never goes below 0.85", () => {
    expect(thermalCorrectionFactor(45, 100)).toBeGreaterThanOrEqual(0.85);
  });

  it("humidity amplifies heat effect", () => {
    const dry = thermalCorrectionFactor(30, 30);
    const humid = thermalCorrectionFactor(30, 90);
    expect(humid).toBeLessThan(dry);
  });

  it("returns 1.0 when temperature is undefined", () => {
    expect(thermalCorrectionFactor(undefined)).toBe(1.0);
  });
});

describe("computeRoadRunningSpeed", () => {
  const config: RoadRunningConfig = { vdot: 50, weight: 70 };

  it("is slower uphill", () => {
    const flat = computeRoadRunningSpeed(0, config);
    const uphill = computeRoadRunningSpeed(0.08, config);
    expect(uphill).toBeLessThan(flat);
  });

  it("hot weather slows down", () => {
    const cool = computeRoadRunningSpeed(0, { ...config, temperature: 12 });
    const hot = computeRoadRunningSpeed(0, { ...config, temperature: 35 });
    expect(hot).toBeLessThan(cool);
  });

  it("never goes below 0.5 m/s", () => {
    const speed = computeRoadRunningSpeed(0.4, config);
    expect(speed).toBeGreaterThanOrEqual(0.5);
  });
});

describe("computeRoadRunningSegmentTime", () => {
  it("1km flat at VDOT 50 → ~5 min", () => {
    const config: RoadRunningConfig = { vdot: 50, weight: 70 };
    const time = computeRoadRunningSegmentTime(1000, 0, config);
    expect(time).toBeGreaterThan(4 * 60);
    expect(time).toBeLessThan(6.5 * 60);
  });
});
