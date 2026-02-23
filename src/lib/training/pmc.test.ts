import { describe, it, expect } from "vitest";
import { computePMC, getCurrentFitness } from "./pmc";

describe("PMC Computation", () => {
  it("empty activities → empty PMC", () => {
    expect(computePMC([])).toHaveLength(0);
  });

  it("constant training builds CTL", () => {
    const today = new Date();
    const activities: { date: string; tss: number }[] = [];

    // 60 days of 80 TSS training
    for (let i = 60; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      activities.push({ date: d.toISOString(), tss: 80 });
    }

    const pmc = computePMC(activities, 90);
    const latest = pmc[pmc.length - 1];

    // After 60 days of 80 TSS, CTL should approach 80
    expect(latest.ctl).toBeGreaterThan(50);
    expect(latest.ctl).toBeLessThan(85);
    // ATL should be close to 80 (7-day constant)
    expect(latest.atl).toBeGreaterThan(60);
  });

  it("rest reduces ATL faster than CTL", () => {
    const today = new Date();
    const activities: { date: string; tss: number }[] = [];

    // 30 days training, then 7 days rest
    for (let i = 37; i > 7; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      activities.push({ date: d.toISOString(), tss: 100 });
    }

    const pmc = computePMC(activities, 45);
    const latest = pmc[pmc.length - 1];

    // After rest, TSB should be positive (CTL > ATL)
    expect(latest.tsb).toBeGreaterThan(0);
  });

  it("TSB = CTL - ATL", () => {
    const today = new Date();
    const activities = [{ date: today.toISOString(), tss: 100 }];
    const pmc = computePMC(activities, 7);

    for (const point of pmc) {
      expect(point.tsb).toBeCloseTo(point.ctl - point.atl, 1);
    }
  });
});

describe("Current Fitness", () => {
  it("returns null for insufficient data", () => {
    expect(getCurrentFitness([])).toBeNull();
  });

  it("returns trend for sufficient data", () => {
    const data = Array.from({ length: 30 }, (_, i) => ({
      date: `2024-01-${String(i + 1).padStart(2, "0")}`,
      tss: 80,
      ctl: 40 + i,
      atl: 60 + (i > 25 ? -5 : i),
      tsb: 0,
    }));

    const result = getCurrentFitness(data);
    expect(result).not.toBeNull();
    expect(result!.trend).toBe("improving");
  });
});
