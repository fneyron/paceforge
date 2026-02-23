"use client";

import { useState } from "react";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { PacingStrategy, PacingStrategyType } from "@/types/pacing";

interface Props {
  onChange: (strategy: PacingStrategy | null) => void;
}

const STRATEGY_LABELS: Record<PacingStrategyType, string> = {
  even_effort: "Even Effort",
  negative_split: "Negative Split",
  positive_split: "Positive Split",
  race_strategy: "Race Strategy (terrain-based)",
  optimal: "Optimal (auto-computed)",
};

export function PacingStrategySelector({ onChange }: Props) {
  const [type, setType] = useState<PacingStrategyType | "none">("none");

  // Negative/Positive split params
  const [firstHalfFactor, setFirstHalfFactor] = useState(0.93);
  const [secondHalfFactor, setSecondHalfFactor] = useState(1.03);
  const [transitionPoint, setTransitionPoint] = useState(0.5);

  // Race strategy params
  const [climbFactor, setClimbFactor] = useState(0.92);
  const [flatFactor, setFlatFactor] = useState(1.0);
  const [descentFactor, setDescentFactor] = useState(1.0);

  // Optimal pacing params
  const [maxEffort, setMaxEffort] = useState(1.1);
  const [minEffort, setMinEffort] = useState(0.85);

  const handleTypeChange = (value: string) => {
    const t = value as PacingStrategyType | "none";
    setType(t);
    emitStrategy(t);
  };

  const emitStrategy = (currentType: PacingStrategyType | "none") => {
    switch (currentType) {
      case "none":
        onChange(null);
        break;
      case "even_effort":
        onChange({ type: "even_effort" });
        break;
      case "negative_split":
        onChange({
          type: "negative_split",
          firstHalfFactor,
          secondHalfFactor,
          transitionPoint,
        });
        break;
      case "positive_split":
        onChange({
          type: "positive_split",
          firstHalfFactor: secondHalfFactor, // positive = start fast
          secondHalfFactor: firstHalfFactor,
          transitionPoint,
        });
        break;
      case "race_strategy":
        onChange({
          type: "race_strategy",
          climbFactor,
          flatFactor,
          descentFactor,
        });
        break;
      case "optimal":
        onChange({
          type: "optimal",
          maxEffort,
          minEffort,
        });
        break;
    }
  };

  const updateAndEmit = <T,>(setter: (v: T) => void, value: T) => {
    setter(value);
    // Use setTimeout to emit after state update
    setTimeout(() => emitStrategy(type), 0);
  };

  return (
    <div className="space-y-3 border rounded-md p-3">
      <div className="space-y-1">
        <Label className="text-xs font-medium">Pacing Strategy</Label>
        <Select value={type} onValueChange={handleTypeChange}>
          <SelectTrigger className="h-8">
            <SelectValue placeholder="No pacing strategy" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">No pacing strategy</SelectItem>
            {Object.entries(STRATEGY_LABELS).map(([key, label]) => (
              <SelectItem key={key} value={key}>
                {label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {(type === "negative_split" || type === "positive_split") && (
        <div className="space-y-3 pt-1">
          <div className="space-y-1">
            <Label className="text-xs">
              First half effort: {(firstHalfFactor * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[firstHalfFactor]}
              onValueChange={([v]) => updateAndEmit(setFirstHalfFactor, v)}
              min={0.85}
              max={1.1}
              step={0.01}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              Second half effort: {(secondHalfFactor * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[secondHalfFactor]}
              onValueChange={([v]) => updateAndEmit(setSecondHalfFactor, v)}
              min={0.85}
              max={1.1}
              step={0.01}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              Transition point: {(transitionPoint * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[transitionPoint]}
              onValueChange={([v]) => updateAndEmit(setTransitionPoint, v)}
              min={0.3}
              max={0.7}
              step={0.05}
            />
          </div>
        </div>
      )}

      {type === "race_strategy" && (
        <div className="space-y-3 pt-1">
          <div className="space-y-1">
            <Label className="text-xs">
              Climb effort: {(climbFactor * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[climbFactor]}
              onValueChange={([v]) => updateAndEmit(setClimbFactor, v)}
              min={0.8}
              max={1.1}
              step={0.01}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              Flat effort: {(flatFactor * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[flatFactor]}
              onValueChange={([v]) => updateAndEmit(setFlatFactor, v)}
              min={0.85}
              max={1.1}
              step={0.01}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              Descent effort: {(descentFactor * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[descentFactor]}
              onValueChange={([v]) => updateAndEmit(setDescentFactor, v)}
              min={0.85}
              max={1.15}
              step={0.01}
            />
          </div>
        </div>
      )}

      {type === "optimal" && (
        <div className="space-y-3 pt-1">
          <p className="text-xs text-muted-foreground">
            Auto-computes the fastest pacing by redistributing effort across segments.
          </p>
          <div className="space-y-1">
            <Label className="text-xs">
              Max effort: {(maxEffort * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[maxEffort]}
              onValueChange={([v]) => updateAndEmit(setMaxEffort, v)}
              min={1.0}
              max={1.2}
              step={0.01}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">
              Min effort: {(minEffort * 100).toFixed(0)}%
            </Label>
            <Slider
              value={[minEffort]}
              onValueChange={([v]) => updateAndEmit(setMinEffort, v)}
              min={0.7}
              max={0.95}
              step={0.01}
            />
          </div>
        </div>
      )}
    </div>
  );
}
