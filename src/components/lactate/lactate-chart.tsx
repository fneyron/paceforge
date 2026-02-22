"use client";

import { useRef, useEffect, useCallback } from "react";
import * as d3 from "d3";
import type { LactateStep, AnalysisResult } from "@/lib/lactate/analysis";

interface Props {
  steps: LactateStep[];
  analysis: AnalysisResult | null;
  protocol: "running" | "cycling";
  showDmax?: boolean;
  compact?: boolean;
}

const MARGIN = { top: 10, right: 50, bottom: 35, left: 45 };

export function LactateChart({ steps, analysis, protocol, showDmax = true, compact = false }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const xLabel = protocol === "running" ? "Speed (km/h)" : "Power (W)";

  const draw = useCallback(() => {
    if (!svgRef.current || !containerRef.current || steps.length < 2) return;

    const rect = containerRef.current.getBoundingClientRect();
    if (rect.width === 0) return;

    const width = rect.width;
    const height = compact ? 160 : 260;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = height - MARGIN.top - MARGIN.bottom;
    const g = svg.append("g").attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    // Data ranges
    const xValues = steps.map((s) => s.value);
    const lacValues = steps.map((s) => s.lactate);
    const xMin = Math.min(...xValues) * 0.95;
    const xMax = Math.max(...xValues) * 1.05;
    const yMax = Math.max(...lacValues, 6) * 1.1;

    const xScale = d3.scaleLinear().domain([xMin, xMax]).range([0, innerW]);
    const yScale = d3.scaleLinear().domain([0, yMax]).range([innerH, 0]);

    // Zone fills
    if (analysis?.lt1 && analysis?.lt2Dmax) {
      const lt1x = xScale(analysis.lt1.speed);
      const lt2x = xScale(showDmax ? analysis.lt2Dmax.speed : (analysis.lt2Obla?.speed || analysis.lt2Dmax.speed));

      // Green zone (< LT1)
      g.append("rect")
        .attr("x", 0).attr("y", 0)
        .attr("width", lt1x).attr("height", innerH)
        .attr("fill", "#22c55e").attr("opacity", 0.08);

      // Yellow zone (LT1–LT2)
      g.append("rect")
        .attr("x", lt1x).attr("y", 0)
        .attr("width", lt2x - lt1x).attr("height", innerH)
        .attr("fill", "#eab308").attr("opacity", 0.08);

      // Red zone (> LT2)
      g.append("rect")
        .attr("x", lt2x).attr("y", 0)
        .attr("width", innerW - lt2x).attr("height", innerH)
        .attr("fill", "#ef4444").attr("opacity", 0.08);
    }

    // Horizontal reference lines at 2 and 4 mmol/L
    [2, 4].forEach((val) => {
      if (val < yMax) {
        g.append("line")
          .attr("x1", 0).attr("x2", innerW)
          .attr("y1", yScale(val)).attr("y2", yScale(val))
          .attr("stroke", "#94a3b8").attr("stroke-width", 1)
          .attr("stroke-dasharray", "4,4").attr("opacity", 0.5);

        g.append("text")
          .attr("x", innerW + 4).attr("y", yScale(val) + 4)
          .attr("fill", "#94a3b8").style("font-size", "9px")
          .text(`${val} mmol/L`);
      }
    });

    // Spline curve
    if (analysis?.splinePoints && analysis.splinePoints.length > 0) {
      const line = d3.line<{ value: number; lactate: number }>()
        .x((d) => xScale(d.value))
        .y((d) => yScale(d.lactate))
        .curve(d3.curveMonotoneX);

      g.append("path")
        .datum(analysis.splinePoints)
        .attr("fill", "none")
        .attr("stroke", "#3b82f6")
        .attr("stroke-width", 2)
        .attr("d", line);
    }

    // Data points
    g.selectAll("circle.data-point")
      .data(steps)
      .join("circle")
      .attr("class", "data-point")
      .attr("cx", (d) => xScale(d.value))
      .attr("cy", (d) => yScale(d.lactate))
      .attr("r", compact ? 3 : 5)
      .attr("fill", "#3b82f6")
      .attr("stroke", "var(--color-background, white)")
      .attr("stroke-width", 2);

    // Threshold vertical lines
    if (analysis?.lt1) {
      const x = xScale(analysis.lt1.speed);
      g.append("line")
        .attr("x1", x).attr("x2", x)
        .attr("y1", 0).attr("y2", innerH)
        .attr("stroke", "#22c55e").attr("stroke-width", 2)
        .attr("stroke-dasharray", "6,3");

      if (!compact) {
        g.append("text")
          .attr("x", x - 4).attr("y", -2)
          .attr("text-anchor", "end")
          .attr("fill", "#22c55e").style("font-size", "10px")
          .text(`LT1 ${analysis.lt1.speed.toFixed(1)}`);
      }
    }

    const lt2 = showDmax ? analysis?.lt2Dmax : analysis?.lt2Obla;
    if (lt2) {
      const x = xScale(lt2.speed);
      g.append("line")
        .attr("x1", x).attr("x2", x)
        .attr("y1", 0).attr("y2", innerH)
        .attr("stroke", "#ef4444").attr("stroke-width", 2)
        .attr("stroke-dasharray", "6,3");

      if (!compact) {
        g.append("text")
          .attr("x", x + 4).attr("y", -2)
          .attr("text-anchor", "start")
          .attr("fill", "#ef4444").style("font-size", "10px")
          .text(`LT2 ${lt2.speed.toFixed(1)}`);
      }
    }

    // HR overlay (secondary Y axis)
    const stepsWithHR = steps.filter((s) => s.hr !== undefined);
    if (stepsWithHR.length >= 2 && !compact) {
      const hrValues = stepsWithHR.map((s) => s.hr!);
      const hrMin = Math.min(...hrValues) * 0.95;
      const hrMax = Math.max(...hrValues) * 1.05;
      const hrScale = d3.scaleLinear().domain([hrMin, hrMax]).range([innerH, 0]);

      // Right axis
      g.append("g")
        .attr("transform", `translate(${innerW},0)`)
        .call(d3.axisRight(hrScale).ticks(4).tickFormat((d) => `${d}`))
        .selectAll("text")
        .attr("fill", "#f97316").style("font-size", "9px");

      // HR line
      const hrLine = d3.line<LactateStep>()
        .x((d) => xScale(d.value))
        .y((d) => hrScale(d.hr!))
        .curve(d3.curveMonotoneX);

      g.append("path")
        .datum(stepsWithHR)
        .attr("fill", "none")
        .attr("stroke", "#f97316")
        .attr("stroke-width", 1.5)
        .attr("stroke-dasharray", "4,2")
        .attr("opacity", 0.7)
        .attr("d", hrLine);
    }

    // Axes
    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(d3.axisBottom(xScale).ticks(Math.min(8, innerW / 60)))
      .selectAll("text")
      .attr("fill", "currentColor").style("font-size", "10px");

    g.append("g")
      .call(d3.axisLeft(yScale).ticks(5))
      .selectAll("text")
      .attr("fill", "currentColor").style("font-size", "10px");

    // Axis labels
    if (!compact) {
      g.append("text")
        .attr("x", innerW / 2).attr("y", innerH + 30)
        .attr("text-anchor", "middle")
        .attr("fill", "currentColor").style("font-size", "11px")
        .text(xLabel);

      g.append("text")
        .attr("transform", "rotate(-90)")
        .attr("x", -innerH / 2).attr("y", -35)
        .attr("text-anchor", "middle")
        .attr("fill", "currentColor").style("font-size", "11px")
        .text("Lactate (mmol/L)");
    }
  }, [steps, analysis, protocol, showDmax, compact, xLabel]);

  useEffect(() => {
    draw();
    const observer = new ResizeObserver(() => draw());
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [draw]);

  if (steps.length < 2) {
    return (
      <div className="text-center text-sm text-muted-foreground py-8">
        Add at least 2 steps to see the chart
      </div>
    );
  }

  return (
    <div ref={containerRef} className="w-full">
      <svg ref={svgRef} className="w-full" />
    </div>
  );
}
