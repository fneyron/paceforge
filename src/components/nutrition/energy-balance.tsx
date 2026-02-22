"use client";

import { useRef, useEffect } from "react";
import * as d3 from "d3";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { totalExpenditure } from "@/lib/nutrition/expenditure";
import type { NutritionItem } from "@/lib/nutrition/planner";

interface Props {
  sport: string;
  totalTimeSeconds: number;
  weightKg: number;
  averagePower?: number;
  totalDistance?: number;
  nutritionItems: NutritionItem[];
}

export function EnergyBalance({
  sport,
  totalTimeSeconds,
  weightKg,
  averagePower,
  totalDistance,
  nutritionItems,
}: Props) {
  const svgRef = useRef<SVGSVGElement>(null);

  const expenditure = totalExpenditure(
    sport,
    totalTimeSeconds,
    weightKg,
    averagePower,
    totalDistance
  );

  const intake = nutritionItems.reduce((sum, item) => sum + item.calories, 0);
  const balance = intake - expenditure;

  useEffect(() => {
    if (!svgRef.current || nutritionItems.length === 0 || totalTimeSeconds <= 0) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll("*").remove();

    const width = 300;
    const height = 150;
    const margin = { top: 10, right: 10, bottom: 25, left: 45 };
    const innerW = width - margin.left - margin.right;
    const innerH = height - margin.top - margin.bottom;

    svg.attr("width", width).attr("height", height);
    const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

    const xScale = d3.scaleLinear().domain([0, totalTimeSeconds]).range([0, innerW]);

    // Build cumulative expenditure (linear)
    const expenditureLine: [number, number][] = [
      [0, 0],
      [totalTimeSeconds, expenditure],
    ];

    // Build cumulative intake
    const sorted = [...nutritionItems].sort((a, b) => a.time - b.time);
    const intakeLine: [number, number][] = [[0, 0]];
    let cumIntake = 0;
    for (const item of sorted) {
      intakeLine.push([item.time, cumIntake]);
      cumIntake += item.calories;
      intakeLine.push([item.time, cumIntake]);
    }
    intakeLine.push([totalTimeSeconds, cumIntake]);

    const maxVal = Math.max(expenditure, cumIntake, 1);
    const yScale = d3.scaleLinear().domain([0, maxVal * 1.1]).range([innerH, 0]);

    // Axes
    g.append("g")
      .attr("transform", `translate(0,${innerH})`)
      .call(
        d3
          .axisBottom(xScale)
          .ticks(5)
          .tickFormat((d) => `${((d as number) / 3600).toFixed(1)}h`)
      )
      .selectAll("text")
      .style("font-size", "9px");

    g.append("g")
      .call(d3.axisLeft(yScale).ticks(4).tickFormat((d) => `${d} kcal`))
      .selectAll("text")
      .style("font-size", "9px");

    // Expenditure line (red)
    const lineGen = d3
      .line<[number, number]>()
      .x((d) => xScale(d[0]))
      .y((d) => yScale(d[1]));

    g.append("path")
      .datum(expenditureLine)
      .attr("fill", "none")
      .attr("stroke", "#ef4444")
      .attr("stroke-width", 2)
      .attr("d", lineGen);

    // Intake line (green, step)
    g.append("path")
      .datum(intakeLine)
      .attr("fill", "none")
      .attr("stroke", "#22c55e")
      .attr("stroke-width", 2)
      .attr("d", lineGen);

    // Legend
    const legend = g.append("g").attr("transform", `translate(${innerW - 80}, 0)`);
    legend.append("line").attr("x1", 0).attr("x2", 15).attr("y1", 5).attr("y2", 5).attr("stroke", "#ef4444").attr("stroke-width", 2);
    legend.append("text").attr("x", 20).attr("y", 9).text("Expenditure").style("font-size", "9px");
    legend.append("line").attr("x1", 0).attr("x2", 15).attr("y1", 20).attr("y2", 20).attr("stroke", "#22c55e").attr("stroke-width", 2);
    legend.append("text").attr("x", 20).attr("y", 24).text("Intake").style("font-size", "9px");
  }, [nutritionItems, totalTimeSeconds, expenditure]);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">Energy Balance</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="grid grid-cols-3 gap-2 text-sm">
          <div>
            <p className="text-xs text-muted-foreground">Expenditure</p>
            <p className="font-medium text-red-500">{Math.round(expenditure)} kcal</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Intake</p>
            <p className="font-medium text-green-500">{Math.round(intake)} kcal</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Balance</p>
            <p
              className={`font-medium ${
                balance < -200
                  ? "text-red-500"
                  : balance > 200
                    ? "text-green-500"
                    : "text-yellow-500"
              }`}
            >
              {balance > 0 ? "+" : ""}
              {Math.round(balance)} kcal
            </p>
          </div>
        </div>

        {nutritionItems.length > 0 && (
          <svg ref={svgRef} className="w-full" />
        )}
      </CardContent>
    </Card>
  );
}
