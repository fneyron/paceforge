"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { PersonalRecord } from "@/lib/training/personal-records";

interface Props {
  records: PersonalRecord[];
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.round(seconds % 60);
  if (h > 0)
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function formatDate(date: Date): string {
  return new Date(date).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function PersonalRecordsTable({ records }: Props) {
  if (records.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Personal Records</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No records found. Sync more Strava activities.
          </p>
        </CardContent>
      </Card>
    );
  }

  // Group by sport
  const bySport = new Map<string, PersonalRecord[]>();
  for (const pr of records) {
    if (!bySport.has(pr.sport)) bySport.set(pr.sport, []);
    bySport.get(pr.sport)!.push(pr);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Personal Records</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {Array.from(bySport.entries()).map(([sport, prs]) => (
          <div key={sport}>
            <Badge variant="secondary" className="mb-2 capitalize">
              {sport}
            </Badge>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">Category</TableHead>
                  <TableHead className="text-xs">Best</TableHead>
                  <TableHead className="text-xs">Date</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {prs.map((pr) => (
                  <TableRow key={`${pr.sport}-${pr.category}`}>
                    <TableCell className="text-xs font-medium">
                      {pr.category}
                    </TableCell>
                    <TableCell className="text-xs font-mono">
                      {pr.category.includes("Power")
                        ? `${pr.value.toFixed(0)}W`
                        : formatTime(pr.value)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {formatDate(pr.date)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
