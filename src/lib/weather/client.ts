import type { RoutePoint, WeatherCondition } from "@/types/route";
import { db } from "@/lib/db";
import { weatherCache } from "@/lib/db/schema/weather-cache";
import { eq, and } from "drizzle-orm";
import { nanoid } from "nanoid";

const OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast";
const CACHE_EXPIRY_MS = 3 * 60 * 60 * 1000; // 3 hours
const SAMPLE_INTERVAL_KM = 10; // sample weather every 10 km

interface OpenMeteoHourly {
  time: string[];
  temperature_2m: number[];
  relative_humidity_2m: number[];
  wind_speed_10m: number[];
  wind_direction_10m: number[];
  surface_pressure: number[];
  precipitation: number[];
  cloud_cover: number[];
}

interface OpenMeteoResponse {
  hourly: OpenMeteoHourly;
}

/**
 * Sample points along a route at regular intervals.
 */
function sampleRoutePoints(
  points: RoutePoint[],
  intervalM: number
): RoutePoint[] {
  const sampled: RoutePoint[] = [points[0]];
  let lastDist = 0;

  for (const pt of points) {
    if (pt.distance - lastDist >= intervalM) {
      sampled.push(pt);
      lastDist = pt.distance;
    }
  }

  // Always include last point
  if (sampled[sampled.length - 1] !== points[points.length - 1]) {
    sampled.push(points[points.length - 1]);
  }

  return sampled;
}

/**
 * Fetch weather for a single point from Open-Meteo.
 */
async function fetchOpenMeteoWeather(
  lat: number,
  lon: number,
  date: string
): Promise<OpenMeteoHourly | null> {
  const params = new URLSearchParams({
    latitude: lat.toFixed(4),
    longitude: lon.toFixed(4),
    start_date: date,
    end_date: date,
    hourly:
      "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,surface_pressure,precipitation,cloud_cover",
    wind_speed_unit: "ms",
  });

  const res = await fetch(`${OPEN_METEO_BASE}?${params}`);
  if (!res.ok) {
    console.error("Open-Meteo error:", await res.text());
    return null;
  }

  const data: OpenMeteoResponse = await res.json();
  return data.hourly;
}

/**
 * Fetch weather conditions along a route for a given race date/time.
 * Uses Open-Meteo API (free, no key needed).
 * Results are cached in DB for 3 hours.
 */
export async function fetchWeatherForRoute(
  points: RoutePoint[],
  raceDate: string,
  raceStartTime: string = "08:00"
): Promise<WeatherCondition[]> {
  const startHour = parseInt(raceStartTime.split(":")[0], 10);
  const sampled = sampleRoutePoints(points, SAMPLE_INTERVAL_KM * 1000);

  const conditions: WeatherCondition[] = [];

  for (const pt of sampled) {
    // Check cache
    const roundLat = Math.round(pt.lat * 100) / 100;
    const roundLon = Math.round(pt.lon * 100) / 100;

    const cached = await db
      .select()
      .from(weatherCache)
      .where(
        and(
          eq(weatherCache.lat, roundLat),
          eq(weatherCache.lon, roundLon),
          eq(weatherCache.date, raceDate),
          eq(weatherCache.hour, startHour)
        )
      )
      .limit(1);

    if (
      cached.length > 0 &&
      Date.now() - cached[0].fetchedAt.getTime() < CACHE_EXPIRY_MS
    ) {
      conditions.push({
        lat: pt.lat,
        lon: pt.lon,
        distance: pt.distance,
        temperature: cached[0].temperature,
        humidity: cached[0].humidity,
        windSpeed: cached[0].windSpeed,
        windDirection: cached[0].windDirection,
        pressure: cached[0].pressure,
        precipitation: cached[0].precipitation,
        cloudCover: cached[0].cloudCover,
      });
      continue;
    }

    // Fetch from API
    const hourly = await fetchOpenMeteoWeather(pt.lat, pt.lon, raceDate);
    if (!hourly) continue;

    // Find closest hour
    const hourIdx = Math.min(startHour, hourly.time.length - 1);

    const wx: WeatherCondition = {
      lat: pt.lat,
      lon: pt.lon,
      distance: pt.distance,
      temperature: hourly.temperature_2m[hourIdx],
      humidity: hourly.relative_humidity_2m[hourIdx],
      windSpeed: hourly.wind_speed_10m[hourIdx],
      windDirection: hourly.wind_direction_10m[hourIdx],
      pressure: hourly.surface_pressure[hourIdx],
      precipitation: hourly.precipitation[hourIdx],
      cloudCover: hourly.cloud_cover[hourIdx],
    };

    conditions.push(wx);

    // Cache
    if (cached.length > 0) {
      await db
        .update(weatherCache)
        .set({
          temperature: wx.temperature,
          humidity: wx.humidity,
          windSpeed: wx.windSpeed,
          windDirection: wx.windDirection,
          pressure: wx.pressure,
          precipitation: wx.precipitation,
          cloudCover: wx.cloudCover,
          fetchedAt: new Date(),
        })
        .where(eq(weatherCache.id, cached[0].id));
    } else {
      await db.insert(weatherCache).values({
        id: nanoid(),
        lat: roundLat,
        lon: roundLon,
        date: raceDate,
        hour: startHour,
        temperature: wx.temperature,
        humidity: wx.humidity,
        windSpeed: wx.windSpeed,
        windDirection: wx.windDirection,
        pressure: wx.pressure,
        precipitation: wx.precipitation,
        cloudCover: wx.cloudCover,
      });
    }
  }

  return conditions;
}
