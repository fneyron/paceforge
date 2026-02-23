"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { PMCDataPoint } from "@/types/route";
import { getCurrentFitness } from "@/lib/training/pmc";

interface Props {
  data: PMCDataPoint[];
}

export function TrainingSummary({ data }: Props) {
  const fitness = getCurrentFitness(data);
  if (!fitness) return null;

  const trendColors = {
    improving: "text-green-600",
    maintaining: "text-yellow-600",
    declining: "text-red-600",
  };

  const trendLabels = {
    improving: "Improving",
    maintaining: "Stable",
    declining: "Declining",
  };

  return (
    <div className="grid grid-cols-3 gap-3">
      <Card>
        <CardContent className="pt-4 pb-3 text-center">
          <p className="text-2xl font-bold text-blue-600">
            {fitness.ctl.toFixed(0)}
          </p>
          <p className="text-xs text-muted-foreground">CTL (Fitness)</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4 pb-3 text-center">
          <p className="text-2xl font-bold text-pink-600">
            {fitness.atl.toFixed(0)}
          </p>
          <p className="text-xs text-muted-foreground">ATL (Fatigue)</p>
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4 pb-3 text-center">
          <p
            className={`text-2xl font-bold ${fitness.tsb >= 0 ? "text-green-600" : "text-red-600"}`}
          >
            {fitness.tsb > 0 ? "+" : ""}
            {fitness.tsb.toFixed(0)}
          </p>
          <p className="text-xs text-muted-foreground">
            TSB (Form){" "}
            <Badge
              variant="outline"
              className={`ml-1 text-[10px] ${trendColors[fitness.trend]}`}
            >
              {trendLabels[fitness.trend]}
            </Badge>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
