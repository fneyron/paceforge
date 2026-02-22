"use client";

import { useRef, useEffect, useCallback } from "react";
import * as d3 from "d3";
import type { SplitResult } from "@/types/route";
import type { Zone } from "@/lib/zones";

interface Props {
  splits: SplitResult[];
  zones?: Zone[];
  yLabel: string;
  getValue: (split: SplitResult) => number;
}

const MARGIN = { top: 10, right: 15, bottom: 30, left: 50 };

export function PacePowerGraph({ splits, zones, yLabel, getValue }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const draw = useCallback(() => {
    if (!svgRef.current || !containerRef.current || splits.length === 0) return;

    const rect = containerRef.current.getBoundingClientRect();
    if (rect.width === 0) return;

    const width = rect.width;
    const height = 180;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    const innerW = width - MARGIN.left - MARGIN.right;
    const innerH = height - MARGIN.top - MARGIN.bottom;
    const g = svg.append("g").attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    // Total distance
    const totalDist = splits.reduce((s, sp) => s + sp.distance, 0);
    const values = splits.map(getValue);
    const vMin = Math.min(...values) * 0.9;
    const vMax = Math.max(...values) * 1.1;

    const xScale = d3.scaleLinear().domain([0, totalDist]).range([0, innerW]);
    const yScale = d3.scaleLinear().domain([vMin, vMax]).range([innerH, 0]);

    // Draw zone bands
    if (zones) {
      for (const zone of zones) {
        const yTop = yScale(Math.min(zone.max === Infinity ? vMax : zone.max, vMax));
        const yBot = yScale(Math.max(zone.min, vMin));
        if (yBot > yTop) {
          g.append("rect")
            .attr("x", 0)
            .attr("y", yTop)
            .attr("width", innerW)
            .attr("height", yBot - yTop)
            .attr("fill", zone.color)
            .attr("opacity", 0.1);
        }
      }
    }

    // Draw step curve of splits
    let cumDist = 0;
    const stepData: Array<{ x: number; y: number; color: string }> = [];
    for (const split of splits) {
      const val = getValue(split);
      const color = split.zone?.color || "#3b82f6";
      stepData.push({ x: cumDist, y: val, color });
      cumDist += split.distance;
      stepData.push({ x: cumDist, y: val, color });
    }

    // Draw colored segments
    for (let i = 0; i < stepData.length - 1; i += 2) {
      const p1 = stepData[i];
      const p2 = stepData[i + 1];
      g.append("line")
        .attr("x1", xScale(p1.x))
        .attr("y1", yScale(p1.y))
        .attr("x2", xScale(p2.x))
        .attr("y2", yScale(p2.y))
        .attr("stroke", p1.color)
        .attr("stroke-width", 2.5);
    }

    // Axes
    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(
        d3.axisBottom(xScale)
          .ticks(Math.min(8, innerW / 80))
          .tickFormat((d) => `${((d as number) / 1000).toFixed(0)} km`)
      )
      .selectAll("text")
      .attr("fill", "currentColor")
      .style("font-size", "10px");

    g.append("g")
      .call(d3.axisLeft(yScale).ticks(5))
      .selectAll("text")
      .attr("fill", "currentColor")
      .style("font-size", "10px");

    // Y label
    g.append("text")
      .attr("transform", "rotate(-90)")
      .attr("y", -40)
      .attr("x", -innerH / 2)
      .attr("text-anchor", "middle")
      .attr("fill", "currentColor")
      .style("font-size", "11px")
      .text(yLabel);
  }, [splits, zones, yLabel, getValue]);

  useEffect(() => {
    draw();
    const observer = new ResizeObserver(() => draw());
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [draw]);

  return (
    <div ref={containerRef} className="w-full">
      <svg ref={svgRef} className="w-full" />
    </div>
  );
}
