import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { Zone } from "@/lib/zones";

interface Props {
  zones: Zone[];
  unit?: string;
  formatValue?: (val: number) => string;
}

export function ZoneTable({ zones, unit, formatValue }: Props) {
  const fmt = formatValue || ((v: number) => `${v}`);

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-12">Zone</TableHead>
          <TableHead>Name</TableHead>
          <TableHead>Range{unit ? ` (${unit})` : ""}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {zones.map((zone) => (
          <TableRow key={zone.number}>
            <TableCell>
              <div className="flex items-center gap-2">
                <div
                  className="w-2 h-6 rounded-sm shrink-0"
                  style={{ backgroundColor: zone.color }}
                />
                <span className="font-medium">Z{zone.number}</span>
              </div>
            </TableCell>
            <TableCell className="text-sm">{zone.name}</TableCell>
            <TableCell className="text-sm text-muted-foreground">
              {zone.max === Infinity
                ? `> ${fmt(zone.min)}`
                : `${fmt(zone.min)} – ${fmt(zone.max)}`}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
