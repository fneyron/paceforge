"use client";

import { useRef, useEffect, useCallback } from "react";
import * as d3 from "d3";
import { useRouteStore } from "@/store/route-store";
import { useCursorStore } from "@/store/cursor-store";
import type { RoutePoint } from "@/types/route";

const MARGIN = { top: 10, right: 20, bottom: 30, left: 50 };

function gradeColor(grade: number): string {
  if (grade > 0.12) return "#dc2626"; // red-600
  if (grade > 0.08) return "#ef4444"; // red-500
  if (grade > 0.04) return "#f97316"; // orange-500
  if (grade > 0.02) return "#eab308"; // yellow-500
  if (grade > -0.02) return "#22c55e"; // green-500
  if (grade > -0.05) return "#3b82f6"; // blue-500
  return "#6366f1"; // indigo-500
}

interface Props {
  comparisonResults?: {
    a: { splits: Array<{ distance: number; speed: number }> };
    b: { splits: Array<{ distance: number; speed: number }> };
  };
}

export function ElevationProfile({ comparisonResults }: Props = {}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const points = useRouteStore((s) => s.points);
  const waypoints = useRouteStore((s) => s.waypoints);
  const cursorDistance = useCursorStore((s) => s.distance);
  const cursorSource = useCursorStore((s) => s.source);
  const setDistance = useCursorStore((s) => s.setDistance);

  const draw = useCallback(() => {
    if (!svgRef.current || !containerRef.current || points.length === 0) return;

    const container = containerRef.current;
    const rect = container.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;

    const width = rect.width;
    const height = rect.height;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    const innerWidth = width - MARGIN.left - MARGIN.right;
    const innerHeight = height - MARGIN.top - MARGIN.bottom;

    const g = svg
      .append("g")
      .attr("transform", `translate(${MARGIN.left},${MARGIN.top})`);

    // Scales
    const xScale = d3
      .scaleLinear()
      .domain([0, points[points.length - 1].distance])
      .range([0, innerWidth]);

    const eleExtent = d3.extent(points, (p) => p.ele) as [number, number];
    const elevPadding = (eleExtent[1] - eleExtent[0]) * 0.1 || 50;
    const yScale = d3
      .scaleLinear()
      .domain([eleExtent[0] - elevPadding, eleExtent[1] + elevPadding])
      .range([innerHeight, 0]);

    // Draw colored area segments
    const sampleStep = Math.max(1, Math.floor(points.length / 500));
    const sampled: RoutePoint[] = [];
    for (let i = 0; i < points.length; i += sampleStep) {
      sampled.push(points[i]);
    }
    if (sampled[sampled.length - 1] !== points[points.length - 1]) {
      sampled.push(points[points.length - 1]);
    }

    // Draw colored vertical strips for grade
    for (let i = 1; i < sampled.length; i++) {
      const x1 = xScale(sampled[i - 1].distance);
      const x2 = xScale(sampled[i].distance);
      const y1 = yScale(sampled[i - 1].ele);
      const y2 = yScale(sampled[i].ele);

      g.append("polygon")
        .attr(
          "points",
          `${x1},${innerHeight} ${x1},${y1} ${x2},${y2} ${x2},${innerHeight}`
        )
        .attr("fill", gradeColor(sampled[i].grade))
        .attr("opacity", 0.6);
    }

    // Elevation line
    const line = d3
      .line<RoutePoint>()
      .x((d) => xScale(d.distance))
      .y((d) => yScale(d.ele))
      .curve(d3.curveMonotoneX);

    const strokeColor = getComputedStyle(document.documentElement)
      .getPropertyValue("--foreground").trim() || "#1e293b";

    g.append("path")
      .datum(sampled)
      .attr("fill", "none")
      .attr("stroke", `oklch(${strokeColor})`)
      .attr("stroke-width", 1.5)
      .attr("d", line);

    // Axes
    const formatDist = (d: number) => `${(d as number / 1000).toFixed(0)} km`;

    g.append("g")
      .attr("transform", `translate(0,${innerHeight})`)
      .call(
        d3
          .axisBottom(xScale)
          .ticks(Math.min(10, innerWidth / 80))
          .tickFormat((d) => formatDist(d as number))
      )
      .selectAll("text")
      .attr("fill", "currentColor")
      .attr("class", "text-muted-foreground")
      .style("font-size", "11px");

    g.append("g")
      .call(
        d3
          .axisLeft(yScale)
          .ticks(5)
          .tickFormat((d) => `${d} m`)
      )
      .selectAll("text")
      .attr("fill", "currentColor")
      .attr("class", "text-muted-foreground")
      .style("font-size", "11px");

    // Waypoint flags
    waypoints.forEach((wp) => {
      const x = xScale(wp.distance);
      g.append("line")
        .attr("x1", x)
        .attr("x2", x)
        .attr("y1", 0)
        .attr("y2", innerHeight)
        .attr("stroke", "#f59e0b")
        .attr("stroke-width", 1)
        .attr("stroke-dasharray", "4,2")
        .attr("opacity", 0.7);

      g.append("text")
        .attr("x", x)
        .attr("y", -2)
        .attr("text-anchor", "middle")
        .attr("fill", "#f59e0b")
        .style("font-size", "10px")
        .text(wp.name);
    });

    // Comparison pace overlay
    if (comparisonResults) {
      const { a: compA, b: compB } = comparisonResults;

      // Build secondary Y axis for speed
      const allSpeeds = [
        ...compA.splits.map((s) => s.speed),
        ...compB.splits.map((s) => s.speed),
      ];
      const speedExtent = d3.extent(allSpeeds) as [number, number];
      const speedPad = (speedExtent[1] - speedExtent[0]) * 0.1 || 1;
      const ySpeedScale = d3
        .scaleLinear()
        .domain([speedExtent[0] - speedPad, speedExtent[1] + speedPad])
        .range([innerHeight, 0]);

      // Right axis
      g.append("g")
        .attr("transform", `translate(${innerWidth},0)`)
        .call(
          d3
            .axisRight(ySpeedScale)
            .ticks(4)
            .tickFormat((d) => `${((d as number) * 3.6).toFixed(0)} km/h`)
        )
        .selectAll("text")
        .attr("fill", "#64748b")
        .style("font-size", "10px");

      // Helper to draw step-curve from splits
      const drawSplitLine = (
        splits: Array<{ distance: number; speed: number }>,
        color: string
      ) => {
        let cumDist = 0;
        const lineData: Array<{ x: number; y: number }> = [];
        for (const split of splits) {
          lineData.push({ x: cumDist, y: split.speed });
          cumDist += split.distance;
          lineData.push({ x: cumDist, y: split.speed });
        }
        const stepLine = d3
          .line<{ x: number; y: number }>()
          .x((d) => xScale(d.x))
          .y((d) => ySpeedScale(d.y));

        g.append("path")
          .datum(lineData)
          .attr("fill", "none")
          .attr("stroke", color)
          .attr("stroke-width", 2)
          .attr("stroke-dasharray", "4,2")
          .attr("opacity", 0.8)
          .attr("d", stepLine);
      };

      drawSplitLine(compA.splits, "#3b82f6"); // blue for A
      drawSplitLine(compB.splits, "#f97316"); // orange for B
    }

    // Cursor crosshair
    const crosshairG = g.append("g").style("display", "none");
    crosshairG
      .append("line")
      .attr("class", "crosshair-v")
      .attr("y1", 0)
      .attr("y2", innerHeight)
      .attr("stroke", "#f97316")
      .attr("stroke-width", 1.5);
    crosshairG
      .append("circle")
      .attr("class", "crosshair-dot")
      .attr("r", 4)
      .attr("fill", "#f97316")
      .attr("stroke", "white")
      .attr("stroke-width", 2);
    const tooltip = crosshairG
      .append("g")
      .attr("class", "tooltip-group");
    tooltip
      .append("rect")
      .attr("fill", "var(--color-popover, white)")
      .attr("stroke", "var(--color-border, #e2e8f0)")
      .attr("rx", 4);
    const tooltipText = tooltip.append("text").style("font-size", "11px");

    // Update crosshair position function
    function updateCrosshair(dist: number | null) {
      if (dist == null || points.length === 0) {
        crosshairG.style("display", "none");
        return;
      }
      crosshairG.style("display", null);

      // Find point at distance
      let pt: RoutePoint = points[0];
      for (let i = 1; i < points.length; i++) {
        if (points[i].distance >= dist) {
          const ratio =
            (dist - points[i - 1].distance) /
            (points[i].distance - points[i - 1].distance);
          pt = {
            lat: points[i - 1].lat + ratio * (points[i].lat - points[i - 1].lat),
            lon: points[i - 1].lon + ratio * (points[i].lon - points[i - 1].lon),
            ele: points[i - 1].ele + ratio * (points[i].ele - points[i - 1].ele),
            distance: dist,
            grade: points[i].grade,
          };
          break;
        }
      }

      const x = xScale(dist);
      const y = yScale(pt.ele);

      crosshairG.select(".crosshair-v").attr("x1", x).attr("x2", x);
      crosshairG.select(".crosshair-dot").attr("cx", x).attr("cy", y);

      const label = `${(dist / 1000).toFixed(1)} km | ${Math.round(pt.ele)} m | ${(pt.grade * 100).toFixed(1)}%`;
      tooltipText.text(label);
      const textBBox = (tooltipText.node() as SVGTextElement).getBBox();
      const tooltipX = Math.min(x + 8, innerWidth - textBBox.width - 12);
      const tooltipY = Math.max(y - 25, 5);

      tooltip.attr("transform", `translate(${tooltipX}, ${tooltipY})`);
      tooltip
        .select("rect")
        .attr("x", -4)
        .attr("y", -textBBox.height)
        .attr("width", textBBox.width + 8)
        .attr("height", textBBox.height + 4);
    }

    // Store references for external updates
    (svgRef.current as SVGSVGElement & { __updateCrosshair?: (d: number | null) => void }).__updateCrosshair = updateCrosshair;
    (svgRef.current as SVGSVGElement & { __xScale?: d3.ScaleLinear<number, number> }).__xScale = xScale;
    (svgRef.current as SVGSVGElement & { __innerWidth?: number }).__innerWidth = innerWidth;

    // Interaction overlay
    svg
      .append("rect")
      .attr("x", MARGIN.left)
      .attr("y", MARGIN.top)
      .attr("width", innerWidth)
      .attr("height", innerHeight)
      .attr("fill", "transparent")
      .attr("cursor", "crosshair")
      .on("mousemove", (event: MouseEvent) => {
        const [mx] = d3.pointer(event, g.node());
        const dist = xScale.invert(mx);
        setDistance(dist, "profile");
      })
      .on("mouseleave", () => {
        useCursorStore.getState().clear();
      });

  }, [points, waypoints, setDistance, comparisonResults]);

  // Draw on mount and resize
  useEffect(() => {
    draw();

    const observer = new ResizeObserver(() => draw());
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [draw]);

  // Update crosshair without full redraw
  useEffect(() => {
    const el = svgRef.current as SVGSVGElement & {
      __updateCrosshair?: (d: number | null) => void;
    };
    el?.__updateCrosshair?.(cursorDistance);
  }, [cursorDistance, cursorSource]);

  return (
    <div ref={containerRef} className="w-full h-full min-h-[200px]">
      <svg ref={svgRef} className="w-full h-full" />
    </div>
  );
}
