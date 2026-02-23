import { describe, it, expect } from "vitest";
import { riegelPredictions, riegelPredict } from "./riegel";
import { cameronPredictions } from "./cameron";
import { estimateCPFromFTP } from "./critical-power";
import { multiModelRunningPredictions, multiModelCyclingPredictions } from "./multi-model";

describe("Riegel Predictions", () => {
  it("predicts longer races as slower", () => {
    const preds = riegelPredictions(10000, 40 * 60); // 10K in 40:00
    const fiveK = preds.find((p) => p.distanceLabel === "5K")!;
    const marathon = preds.find((p) => p.distanceLabel === "Marathon")!;
    expect(fiveK.time).toBeLessThan(40 * 60);
    expect(marathon.time).toBeGreaterThan(40 * 60);
  });

  it("basic Riegel formula works", () => {
    // 10K in 40:00, predict 5K
    const t = riegelPredict(10000, 2400, 5000);
    expect(t).toBeGreaterThan(18 * 60); // > 18:00
    expect(t).toBeLessThan(21 * 60); // < 21:00
  });

  it("returns 4 predictions", () => {
    expect(riegelPredictions(10000, 2400)).toHaveLength(4);
  });
});

describe("Cameron Predictions", () => {
  it("VDOT 50 gives reasonable marathon time", () => {
    const preds = cameronPredictions(50);
    const marathon = preds.find((p) => p.distanceLabel === "Marathon")!;
    expect(marathon.time).toBeGreaterThan(2 * 3600); // > 2:00
    expect(marathon.time).toBeLessThan(4 * 3600); // < 4:00
  });

  it("higher VDOT = faster times", () => {
    const slow = cameronPredictions(40);
    const fast = cameronPredictions(60);
    expect(fast[0].time).toBeLessThan(slow[0].time);
  });
});

describe("Critical Power Model", () => {
  it("estimates CP from FTP", () => {
    const { cp, wPrime } = estimateCPFromFTP(250);
    expect(cp).toBe(240);
    expect(wPrime).toBe(20000);
  });
});

describe("Multi-Model Running Predictions", () => {
  it("returns predictions with multiple models", () => {
    const preds = multiModelRunningPredictions(50);
    expect(preds.length).toBeGreaterThanOrEqual(4);

    const fiveK = preds.find((p) => p.distanceLabel === "5K")!;
    expect(fiveK.models.length).toBeGreaterThanOrEqual(2); // daniels + cameron
    expect(fiveK.consensusTime).toBeGreaterThan(0);
    expect(fiveK.rangeMin).toBeLessThanOrEqual(fiveK.rangeMax);
  });

  it("includes Riegel when reference race provided", () => {
    const preds = multiModelRunningPredictions(50, { distanceM: 10000, timeS: 40 * 60 });
    const fiveK = preds.find((p) => p.distanceLabel === "5K")!;
    const riegelModel = fiveK.models.find((m) => m.model === "riegel");
    expect(riegelModel).toBeDefined();
  });
});

describe("Multi-Model Cycling Predictions", () => {
  it("returns predictions with FTP and CP models", () => {
    const input = { ftp: 250, weight: 75, bikeWeight: 8, cda: 0.32, crr: 0.005, efficiency: 0.97 };
    const preds = multiModelCyclingPredictions(input);
    expect(preds.length).toBeGreaterThanOrEqual(2); // both models contribute on overlapping distances

    // Each prediction should have at least 1 model
    for (const pred of preds) {
      expect(pred.models.length).toBeGreaterThanOrEqual(1);
      expect(pred.consensusTime).toBeGreaterThan(0);
    }
  });
});
