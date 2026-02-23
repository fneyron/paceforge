/**
 * Glycogen depletion model.
 * Three-compartment model: muscle glycogen, liver glycogen, fat oxidation.
 * Used to predict "bonking" risk and optimize nutrition timing.
 */

import type { GlycogenDataPoint } from "@/types/route";

const INITIAL_MUSCLE_GLYCOGEN = 400; // grams
const INITIAL_LIVER_GLYCOGEN = 100; // grams
const KCAL_PER_GRAM_GLYCOGEN = 4; // kcal/g
const KCAL_PER_GRAM_FAT = 9; // kcal/g
const MAX_FAT_OX_RATE = 1.0; // g/min (maximum fat oxidation rate)
const MAX_CARB_ABSORPTION = 1.5; // g/min (gut absorption rate)

interface GlycogenModelState {
  muscleGlycogen: number; // grams
  liverGlycogen: number; // grams
  cumulativeCarbIntake: number; // grams
  cumulativeTime: number; // seconds
}

/**
 * Compute caloric expenditure in kcal/min from power output.
 * Approximate: 1 watt ≈ 3.6 kJ/h = 0.86 kcal/h = 0.0143 kcal/min
 * With ~25% efficiency, metabolic cost = power / 0.25
 */
function expenditureKcalPerMin(powerWatts: number, efficiency: number = 0.25): number {
  const metabolicPowerW = powerWatts / efficiency;
  return (metabolicPowerW * 60) / 4184; // watts → kcal/min
}

/**
 * Compute the glycogen utilization ratio based on exercise intensity.
 * Higher intensity = more glycogen, less fat.
 * @param intensity - fraction of threshold (0-2)
 * @returns fraction of energy from glycogen (0-1)
 */
function glycogenRatio(intensity: number): number {
  // At low intensity (0.5): ~50% glycogen, 50% fat
  // At threshold (1.0): ~85% glycogen
  // Above threshold (1.5): ~95% glycogen
  return Math.min(0.98, 0.4 + 0.45 * intensity);
}

/**
 * Run glycogen depletion simulation for a race.
 * @param segments - Array of { timeS, powerW, intensity (fraction of threshold), carbIntakeG }
 * @param initialState - Optional starting glycogen state
 * @returns Array of glycogen data points
 */
export function simulateGlycogenDepletion(
  segments: {
    timeS: number;
    distanceM: number;
    powerW?: number;
    intensity: number; // fraction of threshold (0-2)
    carbIntakeG: number; // grams consumed during this segment
  }[],
  initialState?: Partial<GlycogenModelState>
): GlycogenDataPoint[] {
  const state: GlycogenModelState = {
    muscleGlycogen: initialState?.muscleGlycogen ?? INITIAL_MUSCLE_GLYCOGEN,
    liverGlycogen: initialState?.liverGlycogen ?? INITIAL_LIVER_GLYCOGEN,
    cumulativeCarbIntake: initialState?.cumulativeCarbIntake ?? 0,
    cumulativeTime: initialState?.cumulativeTime ?? 0,
  };

  const timeline: GlycogenDataPoint[] = [];
  let cumulativeDistance = 0;

  for (const seg of segments) {
    const durationMin = seg.timeS / 60;
    if (durationMin <= 0) continue;

    // Total energy expenditure
    const power = seg.powerW ?? (seg.intensity * 200); // rough estimate if no power
    const totalKcalPerMin = expenditureKcalPerMin(power);
    const totalKcal = totalKcalPerMin * durationMin;

    // Glycogen vs fat split
    const gRatio = glycogenRatio(seg.intensity);
    const glycogenKcal = totalKcal * gRatio;
    const fatKcal = totalKcal * (1 - gRatio);

    // Fat oxidation (capped at max rate)
    const actualFatG = Math.min(fatKcal / KCAL_PER_GRAM_FAT, MAX_FAT_OX_RATE * durationMin);
    const fatOxRate = actualFatG / durationMin;

    // Glycogen depletion
    const glycogenG = glycogenKcal / KCAL_PER_GRAM_GLYCOGEN;

    // Carb absorption (capped at gut absorption rate)
    const absorbedCarbs = Math.min(seg.carbIntakeG, MAX_CARB_ABSORPTION * durationMin);
    state.cumulativeCarbIntake += absorbedCarbs;

    // Muscle glycogen depletes first, liver maintains blood glucose
    const netGlycogenDrain = glycogenG - absorbedCarbs;

    // 80% from muscle, 20% from liver
    const muscleDrain = netGlycogenDrain * 0.8;
    const liverDrain = netGlycogenDrain * 0.2;

    state.muscleGlycogen = Math.max(0, state.muscleGlycogen - muscleDrain);
    state.liverGlycogen = Math.max(0, state.liverGlycogen - liverDrain);

    state.cumulativeTime += seg.timeS;
    cumulativeDistance += seg.distanceM;

    // Bonk risk: high when muscle glycogen < 10% AND liver < 20%
    const musclePct = state.muscleGlycogen / INITIAL_MUSCLE_GLYCOGEN;
    const liverPct = state.liverGlycogen / INITIAL_LIVER_GLYCOGEN;
    let bonkRisk = 0;
    if (musclePct < 0.15 || liverPct < 0.25) {
      bonkRisk = Math.min(1, (1 - musclePct) * 0.6 + (1 - liverPct) * 0.4);
    }

    timeline.push({
      time: state.cumulativeTime,
      distance: cumulativeDistance,
      muscleGlycogen: Math.round(state.muscleGlycogen * 10) / 10,
      liverGlycogen: Math.round(state.liverGlycogen * 10) / 10,
      fatOxRate: Math.round(fatOxRate * 100) / 100,
      carbIntake: Math.round(state.cumulativeCarbIntake * 10) / 10,
      bonkRisk: Math.round(bonkRisk * 100) / 100,
    });
  }

  return timeline;
}

/**
 * Estimate time to bonk (muscle glycogen depletion) at given intensity.
 * Useful for quick planning.
 */
export function estimateTimeToBonk(
  intensityFraction: number,
  carbsPerHourG: number = 0,
  powerW: number = 200
): number {
  const kcalPerMin = expenditureKcalPerMin(powerW);
  const gRatio = glycogenRatio(intensityFraction);
  const glycogenBurnPerMin = (kcalPerMin * gRatio) / KCAL_PER_GRAM_GLYCOGEN;
  const carbAbsorptionPerMin = Math.min(carbsPerHourG / 60, MAX_CARB_ABSORPTION);
  const netDrainPerMin = glycogenBurnPerMin - carbAbsorptionPerMin;

  if (netDrainPerMin <= 0) return Infinity; // Intake exceeds burn

  const totalGlycogen = INITIAL_MUSCLE_GLYCOGEN + INITIAL_LIVER_GLYCOGEN;
  const minutesToBonk = (totalGlycogen * 0.85) / netDrainPerMin; // bonk at 15% remaining
  return minutesToBonk * 60; // seconds
}
