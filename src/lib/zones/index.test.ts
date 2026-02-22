import { describe, it, expect } from "vitest";
import {
  powerZones,
  classifyPowerZone,
  paceZones,
  classifyPaceZone,
  hrZones,
  classifyHRZone,
  swimZones,
  classifySwimZone,
} from "./index";

describe("Power Zones (Coggan)", () => {
  const ftp = 250;

  it("computes 7 zones from FTP", () => {
    const zones = powerZones(ftp);
    expect(zones).toHaveLength(7);
    expect(zones[0].name).toBe("Recovery");
    expect(zones[0].min).toBe(0);
    expect(zones[0].max).toBe(138); // 55% of 250
    expect(zones[3].name).toBe("Threshold");
    expect(zones[3].min).toBe(225); // 90%
    expect(zones[3].max).toBe(263); // 105%
    expect(zones[6].name).toBe("Neuromuscular");
    expect(zones[6].max).toBe(Infinity);
  });

  it("classifies power in correct zone", () => {
    expect(classifyPowerZone(100, ftp).number).toBe(1); // Recovery
    expect(classifyPowerZone(160, ftp).number).toBe(2); // Endurance
    expect(classifyPowerZone(200, ftp).number).toBe(3); // Tempo
    expect(classifyPowerZone(245, ftp).number).toBe(4); // Threshold
    expect(classifyPowerZone(280, ftp).number).toBe(5); // VO2max
    expect(classifyPowerZone(320, ftp).number).toBe(6); // Anaerobic
    expect(classifyPowerZone(400, ftp).number).toBe(7); // Neuromuscular
  });
});

describe("Running Pace Zones (Daniels)", () => {
  const vdot = 50;

  it("computes 5 zones from VDOT", () => {
    const zones = paceZones(vdot);
    expect(zones).toHaveLength(5);
    expect(zones[0].name).toBe("Easy");
    expect(zones[2].name).toBe("Threshold");
    expect(zones[4].name).toBe("Repetition");
  });

  it("Easy pace is slower than Threshold", () => {
    const zones = paceZones(vdot);
    // Higher sec/km = slower
    expect(zones[0].max).toBeGreaterThan(zones[2].max);
  });

  it("classifies pace in correct zone", () => {
    const zones = paceZones(vdot);
    // A very slow pace should be Easy
    expect(classifyPaceZone(400, vdot).number).toBe(1);
    // A pace near threshold should be Z3
    const thresholdPace = (zones[2].min + zones[2].max) / 2;
    expect(classifyPaceZone(thresholdPace, vdot).number).toBe(3);
  });
});

describe("Heart Rate Zones", () => {
  const fcMax = 190;

  it("computes 5 HR zones", () => {
    const zones = hrZones(fcMax);
    expect(zones).toHaveLength(5);
    expect(zones[0].min).toBe(0);
    expect(zones[0].max).toBe(Math.round(190 * 0.68));
    expect(zones[4].name).toBe("Max");
  });

  it("anchors Z4 to lactate threshold when provided", () => {
    const zones = hrZones(fcMax, 170);
    expect(zones[3].min).toBe(Math.round(170 * 0.95));
    expect(zones[3].max).toBe(Math.round(170 * 1.05));
  });

  it("classifies HR correctly", () => {
    expect(classifyHRZone(100, fcMax).number).toBe(1);
    expect(classifyHRZone(140, fcMax).number).toBe(2);
    expect(classifyHRZone(170, fcMax).number).toBe(4);
    expect(classifyHRZone(185, fcMax).number).toBe(5);
  });
});

describe("Swimming Zones", () => {
  const css = 95; // sec/100m

  it("computes 5 swim zones from CSS", () => {
    const zones = swimZones(css);
    expect(zones).toHaveLength(5);
    expect(zones[0].name).toBe("EN1");
    expect(zones[2].name).toBe("Threshold");
    expect(zones[4].name).toBe("Sprint");
  });

  it("Threshold zone brackets CSS", () => {
    const zones = swimZones(css);
    expect(zones[2].min).toBe(css - 2);
    expect(zones[2].max).toBe(css + 2);
  });

  it("classifies swim pace correctly", () => {
    expect(classifySwimZone(110, css).number).toBe(1); // EN1 (slow)
    expect(classifySwimZone(100, css).number).toBe(2); // EN2 (css+2 to css+8)
    expect(classifySwimZone(95, css).number).toBe(3);  // Threshold (css-2 to css+2)
    expect(classifySwimZone(90, css).number).toBe(4);  // VO2max (css-8 to css-2)
    expect(classifySwimZone(80, css).number).toBe(5);  // Sprint (< css-8)
  });
});
