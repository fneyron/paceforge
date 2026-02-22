import { describe, it, expect } from "vitest";
import { runningPredictions, cyclingPredictions, swimmingPredictions } from "./index";

describe("Running Predictions (Daniels VDOT)", () => {
  it("VDOT 50 → 5K ~20:00, marathon ~3:10-3:15", () => {
    const preds = runningPredictions(50);

    const fiveK = preds.find((p) => p.distanceLabel === "5K")!;
    expect(fiveK.time).toBeGreaterThan(18 * 60); // > 18:00
    expect(fiveK.time).toBeLessThan(22 * 60);    // < 22:00

    const marathon = preds.find((p) => p.distanceLabel === "Marathon")!;
    expect(marathon.time).toBeGreaterThan(3 * 3600);         // > 3:00:00
    expect(marathon.time).toBeLessThan(3 * 3600 + 20 * 60); // < 3:20:00
  });

  it("higher VDOT gives faster times", () => {
    const slow = runningPredictions(40);
    const fast = runningPredictions(60);

    const slow5K = slow.find((p) => p.distanceLabel === "5K")!;
    const fast5K = fast.find((p) => p.distanceLabel === "5K")!;
    expect(fast5K.time).toBeLessThan(slow5K.time);
  });

  it("returns 4 predictions", () => {
    expect(runningPredictions(50)).toHaveLength(4);
  });
});

describe("Cycling Predictions (FTP)", () => {
  const input = {
    ftp: 250,
    weight: 75,
    bikeWeight: 8,
    cda: 0.32,
    crr: 0.005,
    efficiency: 0.97,
  };

  it("returns 4 predictions with realistic speeds", () => {
    const preds = cyclingPredictions(input);
    expect(preds).toHaveLength(4);

    // 20km TT: ~35-45 km/h for FTP 250
    const tt20 = preds.find((p) => p.distanceLabel === "20km TT")!;
    expect(tt20.speed).toBeGreaterThan(33);
    expect(tt20.speed).toBeLessThan(50);

    // 180km: should be slower (lower power %)
    const long = preds.find((p) => p.distanceLabel === "180km")!;
    expect(long.speed).toBeLessThan(tt20.speed);
  });
});

describe("Swimming Predictions (CSS)", () => {
  it("CSS 95s/100m → reasonable swim times", () => {
    const preds = swimmingPredictions(95);
    expect(preds).toHaveLength(5);

    // 400m: should be ~6-7 min
    const m400 = preds.find((p) => p.distanceLabel === "400m")!;
    expect(m400.time).toBeGreaterThan(5 * 60);
    expect(m400.time).toBeLessThan(8 * 60);

    // 3800m: should be ~60-80 min
    const m3800 = preds.find((p) => p.distanceLabel === "3800m")!;
    expect(m3800.time).toBeGreaterThan(50 * 60);
    expect(m3800.time).toBeLessThan(90 * 60);
  });

  it("longer distances have slower pace per 100m", () => {
    const preds = swimmingPredictions(95);
    const short = preds[0]; // 400m
    const long = preds[4];  // 3800m
    expect(long.pace).toBeGreaterThan(short.pace);
  });
});
