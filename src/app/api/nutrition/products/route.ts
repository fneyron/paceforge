import { NextRequest, NextResponse } from "next/server";
import { db } from "@/lib/db";
import { nutritionProducts } from "@/lib/db/schema";
import { getSessionUserId } from "@/lib/auth/session";
import { eq, or, isNull, and } from "drizzle-orm";
import { nanoid } from "nanoid";

export async function GET() {
  try {
    const userId = await getSessionUserId();

    // Default products (userId is null) + user's custom products
    const products = userId
      ? await db
          .select()
          .from(nutritionProducts)
          .where(or(isNull(nutritionProducts.userId), eq(nutritionProducts.userId, userId)))
      : await db
          .select()
          .from(nutritionProducts)
          .where(isNull(nutritionProducts.userId));

    return NextResponse.json(products);
  } catch (error) {
    console.error("Error fetching products:", error);
    return NextResponse.json({ error: "Failed to fetch products" }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  try {
    const userId = await getSessionUserId();
    const body = await request.json();

    const id = nanoid();
    await db.insert(nutritionProducts).values({
      id,
      userId,
      name: body.name,
      brand: body.brand || null,
      type: body.type,
      calories: body.calories,
      carbs: body.carbs,
      sodium: body.sodium || 0,
      caffeine: body.caffeine || 0,
      fluidMl: body.fluidMl || 0,
      servingSize: body.servingSize || null,
      isDefault: false,
    });

    const product = await db.select().from(nutritionProducts).where(eq(nutritionProducts.id, id)).get();
    return NextResponse.json(product);
  } catch (error) {
    console.error("Error creating product:", error);
    return NextResponse.json({ error: "Failed to create product" }, { status: 500 });
  }
}

export async function PUT(request: NextRequest) {
  try {
    const userId = await getSessionUserId();
    const body = await request.json();

    if (!body.id) {
      return NextResponse.json({ error: "Product ID required" }, { status: 400 });
    }

    // Verify ownership (can't edit default products)
    const product = await db.select().from(nutritionProducts).where(eq(nutritionProducts.id, body.id)).get();
    if (!product) {
      return NextResponse.json({ error: "Product not found" }, { status: 404 });
    }
    if (product.isDefault) {
      return NextResponse.json({ error: "Cannot edit default products" }, { status: 403 });
    }
    if (userId && product.userId !== userId) {
      return NextResponse.json({ error: "Not authorized" }, { status: 403 });
    }

    await db
      .update(nutritionProducts)
      .set({
        name: body.name,
        brand: body.brand,
        type: body.type,
        calories: body.calories,
        carbs: body.carbs,
        sodium: body.sodium,
        caffeine: body.caffeine,
        fluidMl: body.fluidMl,
        servingSize: body.servingSize,
      })
      .where(eq(nutritionProducts.id, body.id));

    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error updating product:", error);
    return NextResponse.json({ error: "Failed to update product" }, { status: 500 });
  }
}

export async function DELETE(request: NextRequest) {
  try {
    const userId = await getSessionUserId();
    const { searchParams } = new URL(request.url);
    const id = searchParams.get("id");

    if (!id) {
      return NextResponse.json({ error: "Product ID required" }, { status: 400 });
    }

    const product = await db.select().from(nutritionProducts).where(eq(nutritionProducts.id, id)).get();
    if (!product) {
      return NextResponse.json({ error: "Product not found" }, { status: 404 });
    }
    if (product.isDefault) {
      return NextResponse.json({ error: "Cannot delete default products" }, { status: 403 });
    }
    if (userId && product.userId !== userId) {
      return NextResponse.json({ error: "Not authorized" }, { status: 403 });
    }

    await db.delete(nutritionProducts).where(eq(nutritionProducts.id, id));
    return NextResponse.json({ success: true });
  } catch (error) {
    console.error("Error deleting product:", error);
    return NextResponse.json({ error: "Failed to delete product" }, { status: 500 });
  }
}
