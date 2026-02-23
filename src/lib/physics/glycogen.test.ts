import { describe, it, expect } from "vitest";
import { simulateGlycogenDepletion, estimateTimeToBonk } from "./glycogen";

describe("Glycogen Depletion Model", () => {
  it("glycogen decreases over time without nutrition", () => {
    const segments = [
      { timeS: 1800, distanceM: 5000, intensity: 0.8, carbIntakeG: 0 },
      { timeS: 1800, distanceM: 5000, intensity: 0.8, carbIntakeG: 0 },
      { timeS: 1800, distanceM: 5000, intensity: 0.8, carbIntakeG: 0 },
    ];
    const timeline = simulateGlycogenDepletion(segments);

    expect(timeline).toHaveLength(3);
    expect(timeline[0].muscleGlycogen).toBeLessThan(400);
    expect(timeline[2].muscleGlycogen).toBeLessThan(timeline[0].muscleGlycogen);
  });

  it("nutrition intake slows depletion", () => {
    const withoutFood = simulateGlycogenDepletion([
      { timeS: 3600, distanceM: 15000, intensity: 0.8, carbIntakeG: 0 },
    ]);
    const withFood = simulateGlycogenDepletion([
      { timeS: 3600, distanceM: 15000, intensity: 0.8, carbIntakeG: 60 },
    ]);

    expect(withFood[0].muscleGlycogen).toBeGreaterThan(withoutFood[0].muscleGlycogen);
  });

  it("higher intensity depletes glycogen faster", () => {
    const low = simulateGlycogenDepletion([
      { timeS: 3600, distanceM: 12000, intensity: 0.6, carbIntakeG: 0 },
    ]);
    const high = simulateGlycogenDepletion([
      { timeS: 3600, distanceM: 12000, intensity: 1.2, carbIntakeG: 0 },
    ]);

    expect(high[0].muscleGlycogen).toBeLessThan(low[0].muscleGlycogen);
  });

  it("bonk risk increases as glycogen depletes", () => {
    const segments = Array.from({ length: 12 }, () => ({
      timeS: 1800,
      distanceM: 5000,
      intensity: 0.9,
      carbIntakeG: 0,
    }));
    const timeline = simulateGlycogenDepletion(segments);

    const lastBonkRisk = timeline[timeline.length - 1].bonkRisk;
    expect(lastBonkRisk).toBeGreaterThan(0);
  });

  it("tracks cumulative carb intake", () => {
    const segments = [
      { timeS: 1800, distanceM: 5000, intensity: 0.8, carbIntakeG: 30 },
      { timeS: 1800, distanceM: 5000, intensity: 0.8, carbIntakeG: 30 },
    ];
    const timeline = simulateGlycogenDepletion(segments);

    expect(timeline[1].carbIntake).toBeGreaterThan(timeline[0].carbIntake);
  });
});

describe("Time to Bonk Estimation", () => {
  it("without nutrition, bonk occurs in reasonable time", () => {
    const time = estimateTimeToBonk(0.85, 0, 200);
    // Should be 2-4 hours
    expect(time).toBeGreaterThan(1.5 * 3600);
    expect(time).toBeLessThan(5 * 3600);
  });

  it("with nutrition, bonk is delayed", () => {
    const without = estimateTimeToBonk(0.85, 0, 200);
    const withCarbs = estimateTimeToBonk(0.85, 60, 200);
    expect(withCarbs).toBeGreaterThan(without);
  });

  it("sufficient nutrition → infinite time", () => {
    // Very high carb intake at low intensity → no bonk
    const time = estimateTimeToBonk(0.3, 90, 100);
    expect(time).toBe(Infinity);
  });
});
