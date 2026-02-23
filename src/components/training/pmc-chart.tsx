"use client";

import { useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { PMCDataPoint } from "@/types/route";

interface Props {
  data: PMCDataPoint[];
}

export function PMCChart({ data }: Props) {
  const { width, height, margin } = {
    width: 600,
    height: 300,
    margin: { top: 20, right: 20, bottom: 30, left: 40 },
  };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const { paths, xTicks, yTicks, yMin, yMax } = useMemo(() => {
    if (data.length === 0)
      return {
        paths: { ctl: "", atl: "", tsbPos: "", tsbNeg: "" },
        xTicks: [],
        yTicks: [],
        yMin: 0,
        yMax: 100,
      };

    const allVals = data.flatMap((d) => [d.ctl, d.atl, d.tsb]);
    const yMin = Math.floor(Math.min(...allVals) / 10) * 10 - 10;
    const yMax = Math.ceil(Math.max(...allVals) / 10) * 10 + 10;

    const xScale = (i: number) => (i / (data.length - 1)) * innerW;
    const yScale = (v: number) =>
      innerH - ((v - yMin) / (yMax - yMin)) * innerH;

    const toPath = (accessor: (d: PMCDataPoint) => number) =>
      data
        .map(
          (d, i) =>
            `${i === 0 ? "M" : "L"}${xScale(i).toFixed(1)},${yScale(accessor(d)).toFixed(1)}`
        )
        .join(" ");

    // TSB area (positive = green, negative = red) filled to zero line
    const zeroY = yScale(0);
    const tsbPoints = data.map((d, i) => ({ x: xScale(i), y: yScale(d.tsb) }));
    const tsbPosPath =
      `M${tsbPoints[0].x},${zeroY} ` +
      tsbPoints
        .map(
          (p) =>
            `L${p.x.toFixed(1)},${Math.min(p.y, zeroY).toFixed(1)}`
        )
        .join(" ") +
      ` L${tsbPoints[tsbPoints.length - 1].x},${zeroY} Z`;
    const tsbNegPath =
      `M${tsbPoints[0].x},${zeroY} ` +
      tsbPoints
        .map(
          (p) =>
            `L${p.x.toFixed(1)},${Math.max(p.y, zeroY).toFixed(1)}`
        )
        .join(" ") +
      ` L${tsbPoints[tsbPoints.length - 1].x},${zeroY} Z`;

    // X-axis ticks (every 30 days)
    const xTicks: { x: number; label: string }[] = [];
    for (let i = 0; i < data.length; i += 30) {
      xTicks.push({ x: xScale(i), label: data[i].date.slice(5) }); // MM-DD
    }

    // Y-axis ticks
    const yTicks: { y: number; label: string }[] = [];
    for (let v = Math.ceil(yMin / 20) * 20; v <= yMax; v += 20) {
      yTicks.push({ y: yScale(v), label: String(v) });
    }

    return {
      paths: {
        ctl: toPath((d) => d.ctl),
        atl: toPath((d) => d.atl),
        tsbPos: tsbPosPath,
        tsbNeg: tsbNegPath,
      },
      xTicks,
      yTicks,
      yMin,
      yMax,
    };
  }, [data, innerW, innerH]);

  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">
            Performance Management Chart
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Sync Strava activities to see your fitness chart.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Performance Management Chart</CardTitle>
        <div className="flex gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="w-3 h-0.5 bg-blue-500 inline-block" /> CTL
            (Fitness)
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-0.5 bg-pink-500 inline-block" /> ATL
            (Fatigue)
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-2 bg-emerald-500/30 inline-block" /> TSB
            (Form)
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-auto"
        >
          <g transform={`translate(${margin.left},${margin.top})`}>
            {/* Y-axis grid */}
            {yTicks.map((t, i) => (
              <g key={i}>
                <line
                  x1={0}
                  x2={innerW}
                  y1={t.y}
                  y2={t.y}
                  stroke="currentColor"
                  strokeOpacity={0.1}
                />
                <text
                  x={-8}
                  y={t.y + 4}
                  textAnchor="end"
                  fontSize={10}
                  fill="currentColor"
                  opacity={0.5}
                >
                  {t.label}
                </text>
              </g>
            ))}

            {/* TSB area fills */}
            <path d={paths.tsbPos} fill="rgb(16, 185, 129)" opacity={0.15} />
            <path d={paths.tsbNeg} fill="rgb(239, 68, 68)" opacity={0.15} />

            {/* Zero line */}
            <line
              x1={0}
              x2={innerW}
              y1={
                innerH - ((0 - yMin) / (yMax - yMin)) * innerH
              }
              y2={
                innerH - ((0 - yMin) / (yMax - yMin)) * innerH
              }
              stroke="currentColor"
              strokeOpacity={0.3}
              strokeDasharray="4,4"
            />

            {/* CTL line (blue) */}
            <path
              d={paths.ctl}
              fill="none"
              stroke="rgb(59, 130, 246)"
              strokeWidth={2}
            />
            {/* ATL line (pink) */}
            <path
              d={paths.atl}
              fill="none"
              stroke="rgb(236, 72, 153)"
              strokeWidth={1.5}
            />

            {/* X-axis ticks */}
            {xTicks.map((t, i) => (
              <text
                key={i}
                x={t.x}
                y={innerH + 20}
                textAnchor="middle"
                fontSize={10}
                fill="currentColor"
                opacity={0.5}
              >
                {t.label}
              </text>
            ))}
          </g>
        </svg>
      </CardContent>
    </Card>
  );
}
