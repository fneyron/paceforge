"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Equipment {
  id: string;
  name: string;
  bikeType: string;
  bikeWeight: number;
  cda: number;
  crr: number;
  wheelType: string | null;
  helmetType: string | null;
  position: string | null;
}

export function EquipmentManager() {
  const [items, setItems] = useState<Equipment[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [newItem, setNewItem] = useState({
    name: "",
    bikeType: "road",
    bikeWeight: 7,
    cda: 0.32,
    crr: 0.005,
  });

  useEffect(() => {
    fetch("/api/equipment")
      .then((r) => r.json())
      .then(setItems)
      .catch(console.error);
  }, []);

  const handleAdd = async () => {
    const res = await fetch("/api/equipment", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newItem),
    });
    if (res.ok) {
      const item = await res.json();
      setItems([...items, item]);
      setShowAdd(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Equipment Profiles</h3>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setShowAdd(!showAdd)}
        >
          {showAdd ? "Cancel" : "Add Setup"}
        </Button>
      </div>

      {showAdd && (
        <div className="border rounded-md p-3 space-y-3">
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs">Name</Label>
              <Input
                value={newItem.name}
                onChange={(e) =>
                  setNewItem({ ...newItem, name: e.target.value })
                }
                placeholder="e.g. Race setup"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Bike type</Label>
              <Select
                value={newItem.bikeType}
                onValueChange={(v) =>
                  setNewItem({ ...newItem, bikeType: v })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="road">Road</SelectItem>
                  <SelectItem value="tt">TT/Tri</SelectItem>
                  <SelectItem value="gravel">Gravel</SelectItem>
                  <SelectItem value="mtb">MTB</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Bike weight (kg)</Label>
              <Input
                type="number"
                step="0.1"
                value={newItem.bikeWeight}
                onChange={(e) =>
                  setNewItem({
                    ...newItem,
                    bikeWeight: Number(e.target.value),
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">CdA (m²)</Label>
              <Input
                type="number"
                step="0.01"
                value={newItem.cda}
                onChange={(e) =>
                  setNewItem({ ...newItem, cda: Number(e.target.value) })
                }
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Crr</Label>
              <Input
                type="number"
                step="0.001"
                value={newItem.crr}
                onChange={(e) =>
                  setNewItem({ ...newItem, crr: Number(e.target.value) })
                }
              />
            </div>
          </div>
          <Button size="sm" onClick={handleAdd} className="w-full">
            Add
          </Button>
        </div>
      )}

      <div className="space-y-2">
        {items.map((eq) => (
          <div key={eq.id} className="border rounded-md p-3">
            <div className="flex items-center justify-between">
              <p className="font-medium text-sm">{eq.name}</p>
              <span className="text-xs bg-muted px-2 py-0.5 rounded">
                {eq.bikeType}
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              {eq.bikeWeight}kg | CdA {eq.cda}m² | Crr {eq.crr}
            </p>
          </div>
        ))}
        {items.length === 0 && (
          <p className="text-sm text-muted-foreground text-center py-4">
            No equipment profiles yet
          </p>
        )}
      </div>
    </div>
  );
}
