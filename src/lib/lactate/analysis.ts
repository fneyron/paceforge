/**
 * Lactate threshold detection algorithms.
 * LT1 (aerobic threshold): baseline + 1.0 mmol/L
 * LT2 (anaerobic threshold): Dmax method or OBLA (4.0 mmol/L)
 */

export interface LactateStep {
  value: number;    // speed (km/h) or power (W)
  lactate: number;  // mmol/L
  hr?: number;      // bpm (optional)
}

export interface ThresholdResult {
  speed: number;     // value at threshold (km/h or W)
  lactate: number;   // mmol/L at threshold
  hr?: number;       // bpm at threshold
}

export interface AnalysisResult {
  lt1: ThresholdResult | null;
  lt2Dmax: ThresholdResult | null;
  lt2Obla: ThresholdResult | null;
  splinePoints: Array<{ value: number; lactate: number }>;
}

/**
 * Natural cubic spline interpolation.
 * Returns a function that evaluates the spline at any x.
 */
function cubicSpline(xs: number[], ys: number[]): (x: number) => number {
  const n = xs.length - 1;
  if (n < 1) return () => ys[0] || 0;
  if (n === 1) {
    // Linear interpolation
    const slope = (ys[1] - ys[0]) / (xs[1] - xs[0]);
    return (x: number) => ys[0] + slope * (x - xs[0]);
  }

  const h: number[] = [];
  const alpha: number[] = [0];

  for (let i = 0; i < n; i++) {
    h[i] = xs[i + 1] - xs[i];
  }

  for (let i = 1; i < n; i++) {
    alpha[i] =
      (3 / h[i]) * (ys[i + 1] - ys[i]) -
      (3 / h[i - 1]) * (ys[i] - ys[i - 1]);
  }

  // Solve tridiagonal system
  const l: number[] = [1];
  const mu: number[] = [0];
  const z: number[] = [0];

  for (let i = 1; i < n; i++) {
    l[i] = 2 * (xs[i + 1] - xs[i - 1]) - h[i - 1] * mu[i - 1];
    mu[i] = h[i] / l[i];
    z[i] = (alpha[i] - h[i - 1] * z[i - 1]) / l[i];
  }

  const c: number[] = new Array(n + 1).fill(0);
  const b: number[] = new Array(n).fill(0);
  const d: number[] = new Array(n).fill(0);

  for (let j = n - 1; j >= 0; j--) {
    c[j] = z[j] - mu[j] * c[j + 1];
    b[j] = (ys[j + 1] - ys[j]) / h[j] - h[j] * (c[j + 1] + 2 * c[j]) / 3;
    d[j] = (c[j + 1] - c[j]) / (3 * h[j]);
  }

  return (x: number) => {
    // Find segment
    let i = 0;
    for (let k = n - 1; k >= 0; k--) {
      if (x >= xs[k]) {
        i = k;
        break;
      }
    }
    i = Math.min(i, n - 1);

    const dx = x - xs[i];
    return ys[i] + b[i] * dx + c[i] * dx * dx + d[i] * dx * dx * dx;
  };
}

/**
 * Perpendicular distance from point (px, py) to line through (x1, y1) and (x2, y2).
 */
function perpendicularDistance(
  px: number, py: number,
  x1: number, y1: number,
  x2: number, y2: number
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.sqrt(dx * dx + dy * dy);
  if (len === 0) return Math.sqrt((px - x1) ** 2 + (py - y1) ** 2);
  return Math.abs(dy * px - dx * py + x2 * y1 - y2 * x1) / len;
}

/**
 * Interpolate HR at a given value using linear interpolation from steps.
 */
function interpolateHR(steps: LactateStep[], targetValue: number): number | undefined {
  const withHR = steps.filter((s) => s.hr !== undefined);
  if (withHR.length < 2) return undefined;

  for (let i = 1; i < withHR.length; i++) {
    if (withHR[i].value >= targetValue) {
      const ratio = (targetValue - withHR[i - 1].value) / (withHR[i].value - withHR[i - 1].value);
      return Math.round(withHR[i - 1].hr! + ratio * (withHR[i].hr! - withHR[i - 1].hr!));
    }
  }
  return withHR[withHR.length - 1].hr;
}

/**
 * Analyze lactate test data and detect thresholds.
 */
export function analyzeLactate(steps: LactateStep[]): AnalysisResult {
  if (steps.length < 3) {
    return { lt1: null, lt2Dmax: null, lt2Obla: null, splinePoints: [] };
  }

  // Sort by value
  const sorted = [...steps].sort((a, b) => a.value - b.value);
  const xs = sorted.map((s) => s.value);
  const ys = sorted.map((s) => s.lactate);

  // Build spline
  const spline = cubicSpline(xs, ys);

  // Generate smooth curve points
  const splinePoints: Array<{ value: number; lactate: number }> = [];
  const xMin = xs[0];
  const xMax = xs[xs.length - 1];
  const numPoints = 100;
  for (let i = 0; i <= numPoints; i++) {
    const x = xMin + (i / numPoints) * (xMax - xMin);
    splinePoints.push({ value: x, lactate: Math.max(0, spline(x)) });
  }

  // LT1: first point where lactate > baseline + 1.0 mmol/L
  const baseline = ys[0];
  let lt1: ThresholdResult | null = null;
  for (const pt of splinePoints) {
    if (pt.lactate >= baseline + 1.0) {
      lt1 = {
        speed: pt.value,
        lactate: pt.lactate,
        hr: interpolateHR(sorted, pt.value),
      };
      break;
    }
  }

  // LT2 Dmax: maximum perpendicular distance from line (first → last)
  let lt2Dmax: ThresholdResult | null = null;
  {
    const x1 = splinePoints[0].value;
    const y1 = splinePoints[0].lactate;
    const x2 = splinePoints[splinePoints.length - 1].value;
    const y2 = splinePoints[splinePoints.length - 1].lactate;

    let maxDist = 0;
    let maxPt = splinePoints[0];

    for (const pt of splinePoints) {
      const dist = perpendicularDistance(pt.value, pt.lactate, x1, y1, x2, y2);
      if (dist > maxDist) {
        maxDist = dist;
        maxPt = pt;
      }
    }

    if (maxDist > 0) {
      lt2Dmax = {
        speed: maxPt.value,
        lactate: maxPt.lactate,
        hr: interpolateHR(sorted, maxPt.value),
      };
    }
  }

  // LT2 OBLA: fixed 4.0 mmol/L threshold
  let lt2Obla: ThresholdResult | null = null;
  for (let i = 1; i < splinePoints.length; i++) {
    if (splinePoints[i].lactate >= 4.0 && splinePoints[i - 1].lactate < 4.0) {
      // Linear interpolation to find exact crossing
      const ratio = (4.0 - splinePoints[i - 1].lactate) /
        (splinePoints[i].lactate - splinePoints[i - 1].lactate);
      const crossValue = splinePoints[i - 1].value + ratio *
        (splinePoints[i].value - splinePoints[i - 1].value);

      lt2Obla = {
        speed: crossValue,
        lactate: 4.0,
        hr: interpolateHR(sorted, crossValue),
      };
      break;
    }
  }

  return { lt1, lt2Dmax, lt2Obla, splinePoints };
}
