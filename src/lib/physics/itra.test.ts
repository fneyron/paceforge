import { describe, it, expect } from "vitest";
import { computeITRAPoints, classifyITRA, computePerformanceIndex } from "./itra";

describe("ITRA Points", () => {
  it("flat 10km → 10 points", () => {
    expect(computeITRAPoints(10, 0)).toBe(10);
  });

  it("UTMB ~170km/10000D+ → ~270 points", () => {
    const pts = computeITRAPoints(170, 10000);
    expect(pts).toBe(270);
  });

  it("marathon trail 42km/2000D+ → 62 points", () => {
    expect(computeITRAPoints(42, 2000)).toBe(62);
  });

  it("ultra 100km/5000D+ → 150 points", () => {
    expect(computeITRAPoints(100, 5000)).toBe(150);
  });
});

describe("ITRA Classification", () => {
  it("10 points → XXS", () => {
    expect(classifyITRA(10).category).toBe("XXS");
    expect(classifyITRA(10).stars).toBe(1);
  });

  it("62 points → S (2 stars)", () => {
    const c = classifyITRA(62);
    expect(c.category).toBe("S");
    expect(c.stars).toBe(2);
  });

  it("150 points → XL (5 stars)", () => {
    const c = classifyITRA(150);
    expect(c.category).toBe("XL");
    expect(c.stars).toBe(5);
  });

  it("270 points → XXL (6 stars)", () => {
    const c = classifyITRA(270);
    expect(c.category).toBe("XXL");
    expect(c.stars).toBe(6);
  });
});

describe("ITRA Performance Index", () => {
  it("elite pace → ~1000", () => {
    // 100 points in ~8.3 hours = 12 km-effort/h
    const pi = computePerformanceIndex(100, 8.33);
    expect(pi).toBeGreaterThan(950);
    expect(pi).toBeLessThanOrEqual(1000);
  });

  it("recreational → lower PI", () => {
    // 100 points in 20 hours = 5 km-effort/h
    const pi = computePerformanceIndex(100, 20);
    expect(pi).toBeLessThan(500);
    expect(pi).toBeGreaterThan(300);
  });

  it("zero time → 0", () => {
    expect(computePerformanceIndex(100, 0)).toBe(0);
  });
});
