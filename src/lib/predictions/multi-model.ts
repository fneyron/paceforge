/**
 * Multi-model prediction aggregator.
 * Combines predictions from multiple models with confidence intervals.
 */

import type { Prediction } from "./index";
import type { MultiModelPrediction, ModelPrediction } from "@/types/route";
import type { CyclingConfig } from "@/types/route";
import { runningPredictions } from "./index";
import { riegelPredictions } from "./riegel";
import { cameronPredictions } from "./cameron";
import { cyclingPredictions, type CyclingPredictionInput } from "./index";
import { cpPredictions, estimateCPFromFTP } from "./critical-power";

/**
 * Generate multi-model running predictions.
 */
export function multiModelRunningPredictions(
  vdot: number,
  refRace?: { distanceM: number; timeS: number }
): MultiModelPrediction[] {
  const models: { name: string; preds: Prediction[]; confidence: number }[] = [];

  // Daniels VDOT (primary, most validated model)
  models.push({
    name: "daniels",
    preds: runningPredictions(vdot),
    confidence: 0.9,
  });

  // Cameron model
  models.push({
    name: "cameron",
    preds: cameronPredictions(vdot),
    confidence: 0.75,
  });

  // Riegel model (requires reference race)
  if (refRace && refRace.distanceM > 0 && refRace.timeS > 0) {
    models.push({
      name: "riegel",
      preds: riegelPredictions(refRace.distanceM, refRace.timeS),
      confidence: 0.85,
    });
  }

  // Aggregate by distance
  const distanceMap = new Map<number, { label: string; models: ModelPrediction[] }>();

  for (const { name, preds, confidence } of models) {
    for (const pred of preds) {
      if (!distanceMap.has(pred.distance)) {
        distanceMap.set(pred.distance, { label: pred.distanceLabel, models: [] });
      }
      distanceMap.get(pred.distance)!.models.push({
        model: name,
        time: pred.time,
        pace: pred.pace,
        speed: pred.speed,
        confidence,
      });
    }
  }

  const results: MultiModelPrediction[] = [];
  for (const [distance, data] of distanceMap) {
    const times = data.models.map((m) => m.time);
    const weights = data.models.map((m) => m.confidence);
    const totalWeight = weights.reduce((a, b) => a + b, 0);
    const consensusTime = times.reduce((sum, t, i) => sum + t * weights[i], 0) / totalWeight;

    results.push({
      distance,
      distanceLabel: data.label,
      models: data.models,
      consensusTime,
      rangeMin: Math.min(...times),
      rangeMax: Math.max(...times),
    });
  }

  return results.sort((a, b) => a.distance - b.distance);
}

/**
 * Generate multi-model cycling predictions.
 */
export function multiModelCyclingPredictions(
  input: CyclingPredictionInput,
  cp?: number,
  wPrime?: number
): MultiModelPrediction[] {
  const models: { name: string; preds: Prediction[]; confidence: number }[] = [];

  // FTP-based (primary)
  models.push({
    name: "ftp",
    preds: cyclingPredictions(input),
    confidence: 0.85,
  });

  // CP/W' model
  const cpVal = cp ?? input.ftp * 0.96;
  const wVal = wPrime ?? 20000;
  const config: CyclingConfig = {
    ftp: input.ftp,
    weight: input.weight,
    bikeWeight: input.bikeWeight,
    cda: input.cda,
    crr: input.crr,
    efficiency: input.efficiency,
    powerTargets: [],
  };
  models.push({
    name: "cp_wprime",
    preds: cpPredictions(cpVal, wVal, config),
    confidence: cp ? 0.9 : 0.7, // higher confidence if CP directly measured
  });

  // Aggregate
  const distanceMap = new Map<number, { label: string; models: ModelPrediction[] }>();

  for (const { name, preds, confidence } of models) {
    for (const pred of preds) {
      if (!distanceMap.has(pred.distance)) {
        distanceMap.set(pred.distance, { label: pred.distanceLabel, models: [] });
      }
      distanceMap.get(pred.distance)!.models.push({
        model: name,
        time: pred.time,
        pace: pred.pace,
        speed: pred.speed,
        confidence,
      });
    }
  }

  const results: MultiModelPrediction[] = [];
  for (const [distance, data] of distanceMap) {
    const times = data.models.map((m) => m.time);
    const weights = data.models.map((m) => m.confidence);
    const totalWeight = weights.reduce((a, b) => a + b, 0);
    const consensusTime = times.reduce((sum, t, i) => sum + t * weights[i], 0) / totalWeight;

    results.push({
      distance,
      distanceLabel: data.label,
      models: data.models,
      consensusTime,
      rangeMin: Math.min(...times),
      rangeMax: Math.max(...times),
    });
  }

  return results.sort((a, b) => a.distance - b.distance);
}
