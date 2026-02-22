import { describe, it, expect, vi } from "vitest";
import { fetchElevation } from "./elevation";
import type { RoutePoint } from "@/types/route";

function makePoint(lat: number, lon: number, ele: number, distance: number): RoutePoint {
  return { lat, lon, ele, distance, grade: 0 };
}

describe("fetchElevation", () => {
  it("returns points unchanged when elevation is present", async () => {
    const points = [
      makePoint(45.0, 6.0, 500, 0),
      makePoint(45.1, 6.1, 600, 1000),
      makePoint(45.2, 6.2, 700, 2000),
    ];
    const result = await fetchElevation(points);
    expect(result).toEqual(points);
  });

  it("returns empty array for empty input", async () => {
    const result = await fetchElevation([]);
    expect(result).toEqual([]);
  });

  it("detects missing elevation when all zeros", async () => {
    const points = [
      makePoint(45.0, 6.0, 0, 0),
      makePoint(45.1, 6.1, 0, 1000),
    ];

    // Mock fetch to avoid real API calls
    const mockElevations = { elevation: [450, 550] };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockElevations),
      })
    );

    const result = await fetchElevation(points);
    expect(result[0].ele).toBe(450);
    expect(result[1].ele).toBe(550);

    vi.unstubAllGlobals();
  });

  it("detects missing elevation when all quasi-identical", async () => {
    const points = [
      makePoint(45.0, 6.0, 100.00, 0),
      makePoint(45.1, 6.1, 100.01, 1000),
      makePoint(45.2, 6.2, 100.02, 2000),
    ];

    const mockElevations = { elevation: [450, 550, 650] };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockElevations),
      })
    );

    const result = await fetchElevation(points);
    expect(result[0].ele).toBe(450);
    expect(result[2].ele).toBe(650);

    vi.unstubAllGlobals();
  });

  it("does not modify original points array", async () => {
    const points = [
      makePoint(45.0, 6.0, 0, 0),
      makePoint(45.1, 6.1, 0, 1000),
    ];
    const originalEle = points[0].ele;

    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ elevation: [999, 888] }),
      })
    );

    await fetchElevation(points);
    expect(points[0].ele).toBe(originalEle); // unchanged

    vi.unstubAllGlobals();
  });

  it("retries on 429 rate limit", async () => {
    const points = [makePoint(45.0, 6.0, 0, 0)];

    let callCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => {
        callCount++;
        if (callCount === 1) {
          return Promise.resolve({
            ok: false,
            status: 429,
            statusText: "Too Many Requests",
          });
        }
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ elevation: [500] }),
        });
      })
    );

    const result = await fetchElevation(points);
    expect(result[0].ele).toBe(500);
    expect(callCount).toBe(2);

    vi.unstubAllGlobals();
  });
});
