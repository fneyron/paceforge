/**
 * Personal Records extraction and management.
 * Identifies PRs from Strava activity data.
 */

export interface PersonalRecord {
  sport: string;
  category: string;
  value: number; // seconds (time) or watts (power)
  activityId?: string;
  date: Date;
}

/** Standard PR categories by sport */
export const PR_CATEGORIES: Record<string, { label: string; distanceM?: number; type: "time" | "power" }[]> = {
  running: [
    { label: "1K", distanceM: 1000, type: "time" },
    { label: "5K", distanceM: 5000, type: "time" },
    { label: "10K", distanceM: 10000, type: "time" },
    { label: "Half Marathon", distanceM: 21097.5, type: "time" },
    { label: "Marathon", distanceM: 42195, type: "time" },
  ],
  cycling: [
    { label: "20min Power", type: "power" },
    { label: "1h Power", type: "power" },
    { label: "40km TT", distanceM: 40000, type: "time" },
    { label: "100km", distanceM: 100000, type: "time" },
  ],
  swimming: [
    { label: "400m", distanceM: 400, type: "time" },
    { label: "1500m", distanceM: 1500, type: "time" },
    { label: "3800m", distanceM: 3800, type: "time" },
  ],
};

/**
 * Extract potential PRs from a list of activities.
 * For time-based PRs: finds the fastest activity matching each distance (±10%).
 * For power-based PRs: finds the highest average power for the duration.
 */
export function extractPRsFromActivities(
  activities: {
    id: string;
    sport: string;
    distance: number; // meters
    movingTime: number; // seconds
    averagePower?: number | null;
    normalizedPower?: number | null;
    startDate: Date;
  }[]
): PersonalRecord[] {
  const prs: PersonalRecord[] = [];

  for (const activity of activities) {
    const sportKey = mapStravaToSport(activity.sport);
    if (!sportKey) continue;

    const categories = PR_CATEGORIES[sportKey];
    if (!categories) continue;

    for (const cat of categories) {
      if (cat.type === "time" && cat.distanceM) {
        // Check if activity distance is within 10% of target
        const tolerance = cat.distanceM * 0.1;
        if (Math.abs(activity.distance - cat.distanceM) <= tolerance) {
          prs.push({
            sport: sportKey,
            category: cat.label,
            value: activity.movingTime,
            activityId: activity.id,
            date: activity.startDate,
          });
        }
      } else if (cat.type === "power") {
        // Power-based PRs
        const power = activity.normalizedPower || activity.averagePower;
        if (power && power > 0) {
          if (cat.label === "20min Power" && activity.movingTime >= 1100 && activity.movingTime <= 1500) {
            prs.push({
              sport: sportKey,
              category: cat.label,
              value: power,
              activityId: activity.id,
              date: activity.startDate,
            });
          } else if (cat.label === "1h Power" && activity.movingTime >= 3300 && activity.movingTime <= 3900) {
            prs.push({
              sport: sportKey,
              category: cat.label,
              value: power,
              activityId: activity.id,
              date: activity.startDate,
            });
          }
        }
      }
    }
  }

  return prs;
}

/**
 * Deduplicate PRs, keeping only the best for each category.
 * For time-based: lowest value wins.
 * For power-based: highest value wins.
 */
export function bestPRs(prs: PersonalRecord[]): PersonalRecord[] {
  const best = new Map<string, PersonalRecord>();

  for (const pr of prs) {
    const key = `${pr.sport}:${pr.category}`;
    const existing = best.get(key);
    if (!existing) {
      best.set(key, pr);
      continue;
    }

    const isPower = PR_CATEGORIES[pr.sport]?.find((c) => c.label === pr.category)?.type === "power";
    if (isPower ? pr.value > existing.value : pr.value < existing.value) {
      best.set(key, pr);
    }
  }

  return Array.from(best.values());
}

function mapStravaToSport(stravaSport: string): string | null {
  switch (stravaSport) {
    case "Run":
    case "TrailRun":
    case "VirtualRun":
      return "running";
    case "Ride":
    case "VirtualRide":
    case "GravelRide":
      return "cycling";
    case "Swim":
      return "swimming";
    default:
      return null;
  }
}
