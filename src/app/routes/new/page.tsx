"use client";

import { useEffect, useCallback, useRef } from "react";
import * as d3 from "d3";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { DrawMap } from "@/components/map/draw-map";
import { DrawControls } from "@/components/map/draw-controls";
import { useDrawStore } from "@/store/draw-store";
import type { RoutePoint } from "@/types/route";

function MiniElevationProfile({ points }: { points: RoutePoint[] }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const draw = useCallback(() => {
    if (!svgRef.current || !containerRef.current || points.length < 2) return;

    const rect = containerRef.current.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;

    const width = rect.width;
    const height = rect.height;
    const margin = { top: 5, right: 10, bottom: 20, left: 40 };

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();
    svg.attr("width", width).attr("height", height);

    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;
    const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

    const xScale = d3.scaleLinear().domain([0, points[points.length - 1].distance]).range([0, innerW]);
    const eleExtent = d3.extent(points, (p) => p.ele) as [number, number];
    const pad = (eleExtent[1] - eleExtent[0]) * 0.1 || 50;
    const yScale = d3.scaleLinear().domain([eleExtent[0] - pad, eleExtent[1] + pad]).range([innerH, 0]);

    // Area
    const area = d3
      .area<RoutePoint>()
      .x((d) => xScale(d.distance))
      .y0(innerH)
      .y1((d) => yScale(d.ele))
      .curve(d3.curveMonotoneX);

    g.append("path")
      .datum(points)
      .attr("fill", "#22c55e")
      .attr("fill-opacity", 0.3)
      .attr("d", area);

    // Line
    const line = d3
      .line<RoutePoint>()
      .x((d) => xScale(d.distance))
      .y((d) => yScale(d.ele))
      .curve(d3.curveMonotoneX);

    g.append("path")
      .datum(points)
      .attr("fill", "none")
      .attr("stroke", "#22c55e")
      .attr("stroke-width", 2)
      .attr("d", line);

    // Axes
    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(d3.axisBottom(xScale).ticks(5).tickFormat((d) => `${((d as number) / 1000).toFixed(0)}km`))
      .selectAll("text")
      .style("font-size", "9px");

    g.append("g")
      .call(d3.axisLeft(yScale).ticks(3).tickFormat((d) => `${d}m`))
      .selectAll("text")
      .style("font-size", "9px");
  }, [points]);

  useEffect(() => {
    draw();
    const observer = new ResizeObserver(() => draw());
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [draw]);

  if (points.length < 2) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-muted-foreground">
        Place points on the map to see the elevation profile
      </div>
    );
  }

  return (
    <div ref={containerRef} className="w-full h-full">
      <svg ref={svgRef} className="w-full h-full" />
    </div>
  );
}

export default function DrawRoutePage() {
  const routedPoints = useDrawStore((s) => s.routedPoints);
  const clear = useDrawStore((s) => s.clear);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      clear();
    };
  }, [clear]);

  return (
    <div className="h-screen flex flex-col">
      <header className="border-b px-4 py-2 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <Button asChild variant="ghost" size="sm">
            <Link href="/">← Routes</Link>
          </Button>
          <h1 className="text-lg font-semibold">Create New Route</h1>
        </div>
      </header>

      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-[3] min-h-0 relative">
          <DrawMap />
          <DrawControls />
        </div>
        <div className="flex-[1] min-h-[150px] border-t">
          <MiniElevationProfile points={routedPoints} />
        </div>
      </div>
    </div>
  );
}
