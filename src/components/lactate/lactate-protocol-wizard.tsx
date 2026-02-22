"use client";

import { useState, useMemo } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LactateChart } from "./lactate-chart";
import { analyzeLactate, type LactateStep } from "@/lib/lactate/analysis";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: () => void;
  onThresholdApplied?: (lt2Speed: number, lt2HR?: number) => void;
}

export function LactateProtocolWizard({ open, onOpenChange, onSaved, onThresholdApplied }: Props) {
  const [step, setStep] = useState(1);
  const [protocol, setProtocol] = useState<"running" | "cycling">("running");
  const [stepDuration, setStepDuration] = useState(protocol === "running" ? 180 : 240); // seconds
  const [startValue, setStartValue] = useState(protocol === "running" ? 8 : 100);
  const [increment, setIncrement] = useState(protocol === "running" ? 1 : 25);
  const [steps, setSteps] = useState<LactateStep[]>([]);
  const [showDmax, setShowDmax] = useState(true);
  const [saving, setSaving] = useState(false);

  const analysis = useMemo(() => {
    if (steps.length < 3) return null;
    return analyzeLactate(steps);
  }, [steps]);

  const handleProtocolChange = (p: "running" | "cycling") => {
    setProtocol(p);
    setStepDuration(p === "running" ? 180 : 240);
    setStartValue(p === "running" ? 8 : 100);
    setIncrement(p === "running" ? 1 : 25);
  };

  const addStep = () => {
    const nextValue = steps.length === 0
      ? startValue
      : steps[steps.length - 1].value + increment;
    setSteps([...steps, { value: nextValue, lactate: 0, hr: undefined }]);
  };

  const removeStep = (index: number) => {
    setSteps(steps.filter((_, i) => i !== index));
  };

  const updateStep = (index: number, field: keyof LactateStep, value: number) => {
    setSteps(steps.map((s, i) => (i === index ? { ...s, [field]: value } : s)));
  };

  const handleSave = async () => {
    if (steps.length < 3) {
      toast.error("Need at least 3 steps");
      return;
    }

    setSaving(true);
    try {
      const lt2 = showDmax ? analysis?.lt2Dmax : analysis?.lt2Obla;

      const res = await fetch("/api/athlete/lactate-tests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          protocol,
          stepDuration,
          startValue,
          increment,
          steps,
          lt1: analysis?.lt1 || null,
          lt2: lt2 || null,
        }),
      });

      if (res.ok) {
        onSaved?.();
        onOpenChange(false);
        // Reset
        setStep(1);
        setSteps([]);
      } else {
        toast.error("Failed to save test");
      }
    } catch {
      toast.error("Failed to save test");
    } finally {
      setSaving(false);
    }
  };

  const handleApplyToProfile = () => {
    const lt2 = showDmax ? analysis?.lt2Dmax : analysis?.lt2Obla;
    if (lt2) {
      onThresholdApplied?.(lt2.speed, lt2.hr);
    }
  };

  const valueUnit = protocol === "running" ? "km/h" : "W";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Lactate Protocol Test</DialogTitle>
        </DialogHeader>

        {/* Step indicator */}
        <div className="flex gap-2 mb-4">
          {[1, 2, 3].map((s) => (
            <div
              key={s}
              className={`flex-1 h-1 rounded-full ${
                s <= step ? "bg-primary" : "bg-muted"
              }`}
            />
          ))}
        </div>

        {/* Step 1: Configuration */}
        {step === 1 && (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Protocol type</Label>
              <Select value={protocol} onValueChange={(v) => handleProtocolChange(v as "running" | "cycling")}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="running">Running</SelectItem>
                  <SelectItem value="cycling">Cycling</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label>Step duration (sec)</Label>
                <Input
                  type="number"
                  value={stepDuration}
                  onChange={(e) => setStepDuration(Number(e.target.value))}
                />
              </div>
              <div className="space-y-2">
                <Label>Start value ({valueUnit})</Label>
                <Input
                  type="number"
                  value={startValue}
                  onChange={(e) => setStartValue(Number(e.target.value))}
                />
              </div>
              <div className="space-y-2">
                <Label>Increment ({valueUnit})</Label>
                <Input
                  type="number"
                  step={protocol === "running" ? 0.5 : 5}
                  value={increment}
                  onChange={(e) => setIncrement(Number(e.target.value))}
                />
              </div>
            </div>

            <Button onClick={() => setStep(2)} className="w-full">
              Next: Enter Data
            </Button>
          </div>
        )}

        {/* Step 2: Data entry */}
        {step === 2 && (
          <div className="space-y-4">
            <div className="overflow-auto max-h-[300px]">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-background">
                  <tr className="border-b">
                    <th className="text-left p-2 w-10">#</th>
                    <th className="text-left p-2">Target ({valueUnit})</th>
                    <th className="text-left p-2">Lactate (mmol/L)</th>
                    <th className="text-left p-2">HR (bpm)</th>
                    <th className="w-10"></th>
                  </tr>
                </thead>
                <tbody>
                  {steps.map((s, i) => (
                    <tr key={i} className="border-b">
                      <td className="p-2 text-muted-foreground">{i + 1}</td>
                      <td className="p-2">
                        <Input
                          type="number"
                          step={protocol === "running" ? 0.5 : 5}
                          value={s.value}
                          onChange={(e) => updateStep(i, "value", Number(e.target.value))}
                          className="h-8"
                        />
                      </td>
                      <td className="p-2">
                        <Input
                          type="number"
                          step={0.1}
                          value={s.lactate || ""}
                          onChange={(e) => updateStep(i, "lactate", Number(e.target.value))}
                          className="h-8"
                          placeholder="0.0"
                        />
                      </td>
                      <td className="p-2">
                        <Input
                          type="number"
                          value={s.hr || ""}
                          onChange={(e) => updateStep(i, "hr", Number(e.target.value))}
                          className="h-8"
                          placeholder="Optional"
                        />
                      </td>
                      <td className="p-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-8 w-8 p-0 text-destructive"
                          onClick={() => removeStep(i)}
                        >
                          ×
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <Button variant="outline" onClick={addStep} className="w-full">
              + Add step
            </Button>

            {/* Live preview */}
            {steps.length >= 2 && (
              <LactateChart
                steps={steps}
                analysis={analysis}
                protocol={protocol}
                showDmax={showDmax}
                compact
              />
            )}

            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setStep(1)}>
                Back
              </Button>
              <Button
                onClick={() => setStep(3)}
                disabled={steps.length < 3}
                className="flex-1"
              >
                Next: Results
              </Button>
            </div>
          </div>
        )}

        {/* Step 3: Results */}
        {step === 3 && analysis && (
          <div className="space-y-4">
            <LactateChart
              steps={steps}
              analysis={analysis}
              protocol={protocol}
              showDmax={showDmax}
            />

            {/* Toggle Dmax / OBLA */}
            <div className="flex items-center gap-2">
              <Button
                variant={showDmax ? "default" : "outline"}
                size="sm"
                onClick={() => setShowDmax(true)}
              >
                Dmax
              </Button>
              <Button
                variant={!showDmax ? "default" : "outline"}
                size="sm"
                onClick={() => setShowDmax(false)}
              >
                OBLA (4 mmol/L)
              </Button>
            </div>

            {/* Threshold summary */}
            <div className="grid grid-cols-2 gap-4 text-sm">
              {analysis.lt1 && (
                <div className="rounded-lg border border-green-200 bg-green-50/50 p-3">
                  <p className="font-medium text-green-700">LT1 (Aerobic)</p>
                  <p>{analysis.lt1.speed.toFixed(1)} {valueUnit}</p>
                  <p>{analysis.lt1.lactate.toFixed(1)} mmol/L</p>
                  {analysis.lt1.hr && <p>{analysis.lt1.hr} bpm</p>}
                </div>
              )}
              {(showDmax ? analysis.lt2Dmax : analysis.lt2Obla) && (
                <div className="rounded-lg border border-red-200 bg-red-50/50 p-3">
                  <p className="font-medium text-red-700">
                    LT2 ({showDmax ? "Dmax" : "OBLA"})
                  </p>
                  <p>
                    {(showDmax ? analysis.lt2Dmax! : analysis.lt2Obla!).speed.toFixed(1)} {valueUnit}
                  </p>
                  <p>
                    {(showDmax ? analysis.lt2Dmax! : analysis.lt2Obla!).lactate.toFixed(1)} mmol/L
                  </p>
                  {(showDmax ? analysis.lt2Dmax! : analysis.lt2Obla!).hr && (
                    <p>{(showDmax ? analysis.lt2Dmax! : analysis.lt2Obla!).hr} bpm</p>
                  )}
                </div>
              )}
            </div>

            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setStep(2)}>
                Back
              </Button>
              <Button variant="outline" onClick={handleApplyToProfile}>
                Apply to Profile
              </Button>
              <Button onClick={handleSave} disabled={saving} className="flex-1">
                {saving ? "Saving..." : "Save Test"}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
