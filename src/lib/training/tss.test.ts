import { describe, it, expect } from "vitest";
import {
  computeCyclingTSS,
  computeRunningTSS,
  computeHRBasedTSS,
  computeSwimmingTSS,
} from "./tss";

describe("Cycling TSS", () => {
  it("1 hour at FTP = 100 TSS", () => {
    const tss = computeCyclingTSS(3600, 250, 250);
    expect(tss).toBeCloseTo(100, 0);
  });

  it("1 hour at 80% FTP ≈ 64 TSS", () => {
    const tss = computeCyclingTSS(3600, 200, 250);
    expect(tss).toBeCloseTo(64, 0);
  });

  it("zero duration → 0", () => {
    expect(computeCyclingTSS(0, 250, 250)).toBe(0);
  });

  it("zero FTP → 0", () => {
    expect(computeCyclingTSS(3600, 250, 0)).toBe(0);
  });
});

describe("Running TSS", () => {
  it("1 hour at threshold = 100 TSS", () => {
    const tss = computeRunningTSS(3600, 270, 270); // both 4:30/km
    expect(tss).toBeCloseTo(100, 0);
  });

  it("easier effort → lower TSS", () => {
    const easy = computeRunningTSS(3600, 360, 270); // 6:00/km vs 4:30 threshold
    const hard = computeRunningTSS(3600, 270, 270); // at threshold
    expect(easy).toBeLessThan(hard);
  });
});

describe("HR-based TSS", () => {
  it("1 hour at LTHR = 100 TSS", () => {
    expect(computeHRBasedTSS(3600, 170, 170)).toBeCloseTo(100, 0);
  });

  it("lower HR → lower TSS", () => {
    const low = computeHRBasedTSS(3600, 140, 170);
    const high = computeHRBasedTSS(3600, 170, 170);
    expect(low).toBeLessThan(high);
  });
});

describe("Swimming TSS", () => {
  it("1 hour at CSS = 100 TSS", () => {
    expect(computeSwimmingTSS(3600, 95, 95)).toBeCloseTo(100, 0);
  });

  it("slower pace → lower TSS", () => {
    const slow = computeSwimmingTSS(3600, 110, 95);
    const fast = computeSwimmingTSS(3600, 95, 95);
    expect(slow).toBeLessThan(fast);
  });
});
