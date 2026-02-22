"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

interface Product {
  id: string;
  name: string;
  brand: string | null;
  type: string;
  calories: number;
  carbs: number;
  sodium: number;
  caffeine: number;
  fluidMl: number;
  servingSize: string | null;
  isDefault: boolean;
}

const EMPTY_PRODUCT = {
  name: "",
  brand: "",
  type: "gel",
  calories: 100,
  carbs: 25,
  sodium: 50,
  caffeine: 0,
  fluidMl: 0,
  servingSize: "",
};

export function ProductManager() {
  const [products, setProducts] = useState<Product[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [newProduct, setNewProduct] = useState({ ...EMPTY_PRODUCT });
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/nutrition/products")
      .then((r) => r.json())
      .then(setProducts)
      .catch(console.error);
  }, []);

  const handleAdd = async () => {
    const res = await fetch("/api/nutrition/products", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newProduct),
    });
    if (res.ok) {
      const product = await res.json();
      setProducts([...products, product]);
      setShowAdd(false);
      setNewProduct({ ...EMPTY_PRODUCT });
    }
  };

  const handleEdit = async (product: Product) => {
    const res = await fetch("/api/nutrition/products", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(product),
    });
    if (res.ok) {
      setProducts((prev) => prev.map((p) => (p.id === product.id ? product : p)));
      setEditingId(null);
    }
  };

  const handleDelete = async (id: string) => {
    setDeletingId(id);
    const res = await fetch(`/api/nutrition/products?id=${id}`, { method: "DELETE" });
    if (res.ok) {
      setProducts((prev) => prev.filter((p) => p.id !== id));
    }
    setDeletingId(null);
  };

  const typeGroups = ["gel", "bar", "drink", "chew", "real_food", "custom"];

  const renderProductForm = (
    data: typeof EMPTY_PRODUCT,
    onChange: (d: typeof EMPTY_PRODUCT) => void,
    onSave: () => void,
    saveLabel: string
  ) => (
    <div className="border rounded-md p-3 space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <Label className="text-xs">Name</Label>
          <Input
            value={data.name}
            onChange={(e) => onChange({ ...data, name: e.target.value })}
            placeholder="Product name"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Brand</Label>
          <Input
            value={data.brand}
            onChange={(e) => onChange({ ...data, brand: e.target.value })}
            placeholder="Brand"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Type</Label>
          <Select
            value={data.type}
            onValueChange={(v) => onChange({ ...data, type: v })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {typeGroups.map((t) => (
                <SelectItem key={t} value={t}>
                  {t.replace("_", " ")}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Serving size</Label>
          <Input
            value={data.servingSize}
            onChange={(e) => onChange({ ...data, servingSize: e.target.value })}
            placeholder="e.g. 1 gel"
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Calories</Label>
          <Input
            type="number"
            value={data.calories}
            onChange={(e) => onChange({ ...data, calories: Number(e.target.value) })}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Carbs (g)</Label>
          <Input
            type="number"
            value={data.carbs}
            onChange={(e) => onChange({ ...data, carbs: Number(e.target.value) })}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Sodium (mg)</Label>
          <Input
            type="number"
            value={data.sodium}
            onChange={(e) => onChange({ ...data, sodium: Number(e.target.value) })}
          />
        </div>
        <div className="space-y-1">
          <Label className="text-xs">Caffeine (mg)</Label>
          <Input
            type="number"
            value={data.caffeine}
            onChange={(e) => onChange({ ...data, caffeine: Number(e.target.value) })}
          />
        </div>
        <div className="space-y-1 col-span-2">
          <Label className="text-xs">Fluid (ml)</Label>
          <Input
            type="number"
            value={data.fluidMl}
            onChange={(e) => onChange({ ...data, fluidMl: Number(e.target.value) })}
          />
        </div>
      </div>
      <Button size="sm" onClick={onSave} className="w-full">
        {saveLabel}
      </Button>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Nutrition Products</h3>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setShowAdd(!showAdd)}
        >
          {showAdd ? "Cancel" : "Add Product"}
        </Button>
      </div>

      {showAdd &&
        renderProductForm(
          newProduct,
          setNewProduct,
          handleAdd,
          "Add"
        )}

      <div className="space-y-2 max-h-64 overflow-y-auto">
        {products.map((p) => (
          <div key={p.id}>
            {editingId === p.id ? (
              <div className="space-y-2">
                {renderProductForm(
                  {
                    name: p.name,
                    brand: p.brand || "",
                    type: p.type,
                    calories: p.calories,
                    carbs: p.carbs,
                    sodium: p.sodium,
                    caffeine: p.caffeine,
                    fluidMl: p.fluidMl,
                    servingSize: p.servingSize || "",
                  },
                  (data) =>
                    setProducts((prev) =>
                      prev.map((prod) =>
                        prod.id === p.id
                          ? { ...prod, ...data, brand: data.brand || null, servingSize: data.servingSize || null }
                          : prod
                      )
                    ),
                  () => handleEdit(products.find((prod) => prod.id === p.id)!),
                  "Save"
                )}
                <Button size="sm" variant="ghost" onClick={() => setEditingId(null)} className="w-full">
                  Cancel
                </Button>
              </div>
            ) : (
              <div className="flex items-center justify-between text-sm border rounded-md p-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-medium truncate">{p.name}</p>
                    <Badge variant={p.isDefault ? "secondary" : "outline"} className="text-[10px] shrink-0">
                      {p.isDefault ? "Default" : "Custom"}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {p.brand ? `${p.brand} · ` : ""}
                    {p.calories}kcal | {p.carbs}g carbs
                    {p.sodium > 0 ? ` | ${p.sodium}mg Na` : ""}
                    {p.caffeine > 0 ? ` | ${p.caffeine}mg caf` : ""}
                    {p.fluidMl > 0 ? ` | ${p.fluidMl}ml` : ""}
                    {p.servingSize ? ` (${p.servingSize})` : ""}
                  </p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <span className="text-xs bg-muted px-2 py-0.5 rounded">
                    {p.type.replace("_", " ")}
                  </span>
                  {!p.isDefault && (
                    <>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 w-6 p-0 text-xs"
                        onClick={() => setEditingId(p.id)}
                      >
                        &#9998;
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 w-6 p-0 text-xs text-destructive"
                        onClick={() => handleDelete(p.id)}
                        disabled={deletingId === p.id}
                      >
                        &#10005;
                      </Button>
                    </>
                  )}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
