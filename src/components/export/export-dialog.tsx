"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from "@/components/ui/label";

interface Props {
  routeId: string;
  simulationId?: string;
}

type ExportFormat = "fit" | "tcx" | "csv" | "pdf";

export function ExportDialog({ routeId, simulationId }: Props) {
  const [format, setFormat] = useState<ExportFormat>("tcx");
  const [exporting, setExporting] = useState(false);

  const handleExport = async () => {
    setExporting(true);

    try {
      const params = simulationId ? `?simulationId=${simulationId}` : "";
      const url = `/api/routes/${routeId}/export/${format}${params}`;

      const res = await fetch(url);
      if (!res.ok) {
        throw new Error("Export failed");
      }

      const blob = await res.blob();
      const downloadUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = downloadUrl;

      const extensions: Record<ExportFormat, string> = {
        fit: ".fit",
        tcx: ".tcx",
        csv: ".csv",
        pdf: ".pdf",
      };
      a.download = `paceforge_export${extensions[format]}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(downloadUrl);
    } catch (err) {
      console.error("Export error:", err);
    } finally {
      setExporting(false);
    }
  };

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          Export
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Export Race Plan</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Format</Label>
            <Select
              value={format}
              onValueChange={(v) => setFormat(v as ExportFormat)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="tcx">
                  TCX — Garmin / Wahoo / General
                </SelectItem>
                <SelectItem value="fit">
                  FIT — Garmin native
                </SelectItem>
                <SelectItem value="csv">
                  CSV — Splits spreadsheet
                </SelectItem>
                <SelectItem value="pdf">
                  PDF — Race plan document
                </SelectItem>
              </SelectContent>
            </Select>
          </div>

          {!simulationId && (format === "csv" || format === "pdf") && (
            <p className="text-xs text-muted-foreground">
              Run a simulation first to export splits and race plan data.
            </p>
          )}

          <Button
            onClick={handleExport}
            disabled={exporting || (!simulationId && (format === "csv" || format === "pdf"))}
            className="w-full"
          >
            {exporting ? "Exporting..." : `Download ${format.toUpperCase()}`}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
