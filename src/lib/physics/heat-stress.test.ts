import { describe, it, expect } from "vitest";
import { computeWBGT, wbgtPerformanceFactor, heatPerformanceFactor } from "./heat-stress";

describe("WBGT Computation", () => {
  it("cool dry conditions → low WBGT", () => {
    const wbgt = computeWBGT(15, 40);
    expect(wbgt).toBeLessThan(20);
  });

  it("hot humid conditions → high WBGT", () => {
    const wbgt = computeWBGT(35, 80);
    expect(wbgt).toBeGreaterThan(30);
  });

  it("increases with temperature", () => {
    const w20 = computeWBGT(20, 50);
    const w30 = computeWBGT(30, 50);
    const w35 = computeWBGT(35, 50);
    expect(w30).toBeGreaterThan(w20);
    expect(w35).toBeGreaterThan(w30);
  });

  it("increases with humidity", () => {
    const wLow = computeWBGT(30, 30);
    const wHigh = computeWBGT(30, 90);
    expect(wHigh).toBeGreaterThan(wLow);
  });
});

describe("WBGT Performance Factor", () => {
  it("WBGT < 18 → no degradation", () => {
    expect(wbgtPerformanceFactor(15)).toBe(1.0);
    expect(wbgtPerformanceFactor(18)).toBe(1.0);
  });

  it("WBGT 25 → ~7% degradation", () => {
    const f = wbgtPerformanceFactor(25);
    expect(f).toBeGreaterThan(0.9);
    expect(f).toBeLessThan(0.96);
  });

  it("WBGT 32 → significant degradation", () => {
    const f = wbgtPerformanceFactor(32);
    expect(f).toBeLessThan(0.88);
    expect(f).toBeGreaterThan(0.5);
  });

  it("acclimatization reduces degradation", () => {
    const fNone = wbgtPerformanceFactor(28, 0);
    const fFull = wbgtPerformanceFactor(28, 1);
    expect(fFull).toBeGreaterThan(fNone);
  });

  it("never goes below 0.5", () => {
    expect(wbgtPerformanceFactor(50, 0)).toBe(0.5);
  });
});

describe("Heat Performance Factor (convenience)", () => {
  it("cool weather → no degradation", () => {
    expect(heatPerformanceFactor(10, 50)).toBe(1.0);
  });

  it("hot weather → degradation", () => {
    const f = heatPerformanceFactor(35, 80);
    expect(f).toBeLessThan(1.0);
    expect(f).toBeGreaterThan(0.5);
  });
});
