import logging

import httpx

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


async def get_weather_forecast(lat: float, lon: float, date: str) -> dict | None:
    """Fetch weather forecast from Open-Meteo for a specific date.

    Args:
        lat: Latitude of the race location
        lon: Longitude of the race location
        date: ISO date string (YYYY-MM-DD)

    Returns:
        Dict with temperature_c, humidity_pct, wind_speed_kmh, heat_factor
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(OPEN_METEO_URL, params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                "start_date": date,
                "end_date": date,
                "timezone": "auto",
            })
            response.raise_for_status()
            data = response.json()

        hourly = data.get("hourly", {})
        temps = hourly.get("temperature_2m", [])
        humidity = hourly.get("relative_humidity_2m", [])
        wind = hourly.get("wind_speed_10m", [])

        if not temps:
            return None

        # Average over race hours (6h-18h roughly)
        race_hours = list(range(6, 18))
        avg_temp = _avg_slice(temps, race_hours)
        avg_humidity = _avg_slice(humidity, race_hours)
        avg_wind = _avg_slice(wind, race_hours)

        heat_factor = compute_heat_factor(avg_temp, avg_humidity)

        return {
            "temperature_c": round(avg_temp, 1),
            "humidity_pct": round(avg_humidity, 0),
            "wind_speed_kmh": round(avg_wind, 1),
            "heat_factor": round(heat_factor, 3),
            "date": date,
        }
    except Exception:
        logger.exception("Failed to fetch weather from Open-Meteo")
        return None


def _avg_slice(data: list, indices: list) -> float:
    vals = [data[i] for i in indices if i < len(data) and data[i] is not None]
    return sum(vals) / len(vals) if vals else 0


def compute_heat_factor(temp_c: float, humidity_pct: float) -> float:
    """Compute pace penalty factor based on temperature and humidity.

    Returns a multiplier >= 1.0 (1.0 = no penalty).
    """
    if temp_c < 15:
        factor = 1.0
    elif temp_c < 20:
        factor = 1.0 + (temp_c - 15) * 0.004  # up to +2%
    elif temp_c < 25:
        factor = 1.02 + (temp_c - 20) * 0.008  # up to +6%
    elif temp_c < 30:
        factor = 1.06 + (temp_c - 25) * 0.012  # up to +12%
    elif temp_c < 35:
        factor = 1.12 + (temp_c - 30) * 0.016  # up to +20%
    else:
        factor = 1.20 + (temp_c - 35) * 0.02   # aggressive above 35

    # Humidity amplifier above 65%
    if humidity_pct > 65:
        factor += (humidity_pct - 65) * 0.001

    return min(factor, 1.5)  # Cap at 50% slower
