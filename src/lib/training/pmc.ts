/**
 * Performance Management Chart (PMC).
 * Computes CTL (fitness), ATL (fatigue), and TSB (form) from training history.
 */

import type { PMCDataPoint } from "@/types/route";

const CTL_TIME_CONSTANT = 42; // days
const ATL_TIME_CONSTANT = 7; // days

/**
 * Compute PMC data from daily TSS values.
 * Uses exponentially weighted moving averages.
 *
 * CTL_n = CTL_{n-1} + (TSS_n - CTL_{n-1}) / 42
 * ATL_n = ATL_{n-1} + (TSS_n - ATL_{n-1}) / 7
 * TSB = CTL - ATL
 *
 * @param activities - Array of {date: ISO string, tss: number}
 * @param daysBack - How many days of history to compute (default 180)
 */
export function computePMC(
  activities: { date: string; tss: number }[],
  daysBack: number = 180
): PMCDataPoint[] {
  if (activities.length === 0) return [];

  // Build daily TSS map
  const dailyTSS = new Map<string, number>();
  for (const a of activities) {
    const dateKey = a.date.slice(0, 10); // YYYY-MM-DD
    dailyTSS.set(dateKey, (dailyTSS.get(dateKey) || 0) + a.tss);
  }

  // Determine date range
  const endDate = new Date();
  const startDate = new Date(endDate);
  startDate.setDate(startDate.getDate() - daysBack);

  const result: PMCDataPoint[] = [];
  let ctl = 0;
  let atl = 0;

  // Iterate day by day
  const current = new Date(startDate);
  while (current <= endDate) {
    const dateKey = current.toISOString().slice(0, 10);
    const tss = dailyTSS.get(dateKey) || 0;

    ctl = ctl + (tss - ctl) / CTL_TIME_CONSTANT;
    atl = atl + (tss - atl) / ATL_TIME_CONSTANT;
    const tsb = ctl - atl;

    result.push({
      date: dateKey,
      tss: Math.round(tss * 10) / 10,
      ctl: Math.round(ctl * 10) / 10,
      atl: Math.round(atl * 10) / 10,
      tsb: Math.round(tsb * 10) / 10,
    });

    current.setDate(current.getDate() + 1);
  }

  return result;
}

/**
 * Get current fitness summary from PMC data.
 */
export function getCurrentFitness(pmcData: PMCDataPoint[]): {
  ctl: number;
  atl: number;
  tsb: number;
  trend: "improving" | "maintaining" | "declining";
} | null {
  if (pmcData.length < 7) return null;

  const latest = pmcData[pmcData.length - 1];
  const weekAgo = pmcData[pmcData.length - 7];

  const ctlDelta = latest.ctl - weekAgo.ctl;
  const trend = ctlDelta > 2 ? "improving" : ctlDelta < -2 ? "declining" : "maintaining";

  return {
    ctl: latest.ctl,
    atl: latest.atl,
    tsb: latest.tsb,
    trend,
  };
}
