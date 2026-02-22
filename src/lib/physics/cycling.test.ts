import { describe, it, expect } from "vitest";
import { solveSpeed, computeCyclingSegmentTime } from "./cycling";
import type { CyclingConfig } from "@/types/route";

const defaultConfig: CyclingConfig = {
  ftp: 250,
  weight: 75,
  bikeWeight: 8,
  cda: 0.32,
  crr: 0.005,
  efficiency: 0.97,
  powerTargets: [],
};

describe("solveSpeed", () => {
  it("returns reasonable flat speed at 200W", () => {
    const speed = solveSpeed(200, 0, 0, defaultConfig);
    const kmh = speed * 3.6;
    // A cyclist at 200W on flat should do ~33-37 km/h
    expect(kmh).toBeGreaterThan(30);
    expect(kmh).toBeLessThan(40);
  });

  it("returns reasonable flat speed at FTP (250W)", () => {
    const speed = solveSpeed(250, 0, 0, defaultConfig);
    const kmh = speed * 3.6;
    // ~36-42 km/h at 250W
    expect(kmh).toBeGreaterThan(34);
    expect(kmh).toBeLessThan(45);
  });

  it("is slower uphill than on flat", () => {
    const flat = solveSpeed(200, 0, 0, defaultConfig);
    const uphill = solveSpeed(200, 0.05, 0, defaultConfig); // 5% grade
    expect(uphill).toBeLessThan(flat);
  });

  it("is faster downhill than on flat", () => {
    const flat = solveSpeed(200, 0, 0, defaultConfig);
    const downhill = solveSpeed(200, -0.05, 0, defaultConfig); // -5% grade
    expect(downhill).toBeGreaterThan(flat);
  });

  it("is slower at altitude (thinner air affects less)", () => {
    const seaLevel = solveSpeed(200, 0, 0, defaultConfig);
    const altitude = solveSpeed(200, 0, 2000, defaultConfig);
    // Less air drag at altitude → faster (aero dominant on flat)
    expect(altitude).toBeGreaterThan(seaLevel);
  });

  it("is slower with headwind", () => {
    const noWind = solveSpeed(200, 0, 0, defaultConfig);
    const headwind = solveSpeed(200, 0, 0, defaultConfig, 5); // 5 m/s headwind
    expect(headwind).toBeLessThan(noWind);
  });

  it("is faster with tailwind", () => {
    const noWind = solveSpeed(200, 0, 0, defaultConfig);
    const tailwind = solveSpeed(200, 0, 0, defaultConfig, -5); // 5 m/s tailwind
    expect(tailwind).toBeGreaterThan(noWind);
  });

  it("clamps to minimum speed on steep climbs", () => {
    const speed = solveSpeed(100, 0.15, 0, defaultConfig); // 15% at 100W
    expect(speed).toBeGreaterThanOrEqual(1); // min 1 m/s
  });

  it("more power = faster", () => {
    const low = solveSpeed(150, 0, 0, defaultConfig);
    const high = solveSpeed(300, 0, 0, defaultConfig);
    expect(high).toBeGreaterThan(low);
  });
});

describe("computeCyclingSegmentTime", () => {
  it("computes reasonable time for 1km flat segment", () => {
    const time = computeCyclingSegmentTime(1000, 0, 0, 200, defaultConfig);
    // 1km at ~35 km/h ≈ 100s
    expect(time).toBeGreaterThan(80);
    expect(time).toBeLessThan(130);
  });

  it("uphill segment takes longer", () => {
    const flat = computeCyclingSegmentTime(1000, 0, 0, 200, defaultConfig);
    const uphill = computeCyclingSegmentTime(1000, 0.08, 500, 200, defaultConfig);
    expect(uphill).toBeGreaterThan(flat);
  });
});
