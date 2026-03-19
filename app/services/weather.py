import logging
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CLIMATE_URL = "https://climate-api.open-meteo.com/v1/climate"


async def get_weather_forecast(lat: float, lon: float, date: str) -> dict | None:
    """Fetch weather for a race date. Uses forecast if <15 days, climate averages otherwise."""
    try:
        race_date = datetime.strptime(date, "%Y-%m-%d").date()
        days_away = (race_date - datetime.now().date()).days

        if days_away <= 15:
            return await _fetch_forecast(lat, lon, date)
        else:
            return await _fetch_climate(lat, lon, race_date)
    except Exception:
        logger.exception("Failed to get weather for %s", date)
        return None


async def _fetch_forecast(lat: float, lon: float, date: str) -> dict | None:
    """Real forecast for dates within 15 days."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(FORECAST_URL, params={
            "latitude": lat, "longitude": lon,
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m",
            "start_date": date, "end_date": date, "timezone": "auto",
        })
        response.raise_for_status()
        data = response.json()

    hourly = data.get("hourly", {})
    temps = hourly.get("temperature_2m", [])
    humidity = hourly.get("relative_humidity_2m", [])
    wind = hourly.get("wind_speed_10m", [])

    if not temps:
        return None

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
        "source": "prevision",
        "date": date,
    }


async def _fetch_climate(lat: float, lon: float, race_date) -> dict | None:
    """Climate averages for dates beyond forecast range."""
    # Use 30-year climate data for the same month
    month = race_date.month
    # Climate API uses date ranges — get the whole month of a reference year
    start = f"1991-{month:02d}-01"
    end = f"2020-{month:02d}-28"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(CLIMATE_URL, params={
                "latitude": lat, "longitude": lon,
                "monthly": "temperature_2m_mean,relative_humidity_2m_mean,wind_speed_10m_mean",
                "start_date": start, "end_date": end,
                "models": "EC_Earth3P_HR",
            })
            response.raise_for_status()
            data = response.json()

        monthly = data.get("monthly", {})
        temps = monthly.get("temperature_2m_mean", [])
        humidity = monthly.get("relative_humidity_2m_mean", [])
        wind = monthly.get("wind_speed_10m_mean", [])

        avg_temp = sum(t for t in temps if t is not None) / max(len([t for t in temps if t is not None]), 1) if temps else 20
        avg_humidity = sum(h for h in humidity if h is not None) / max(len([h for h in humidity if h is not None]), 1) if humidity else 60
        avg_wind = sum(w for w in wind if w is not None) / max(len([w for w in wind if w is not None]), 1) if wind else 10
        heat_factor = compute_heat_factor(avg_temp, avg_humidity)

        return {
            "temperature_c": round(avg_temp, 1),
            "humidity_pct": round(avg_humidity, 0),
            "wind_speed_kmh": round(avg_wind, 1),
            "heat_factor": round(heat_factor, 3),
            "source": "moyennes climatiques",
            "date": race_date.isoformat(),
        }
    except Exception:
        logger.exception("Climate API failed, using rough estimate")
        # Rough estimate based on latitude and month
        return _estimate_climate(lat, month, race_date)


def _estimate_climate(lat: float, month: int, race_date) -> dict:
    """Very rough climate estimate when API fails."""
    # Base temp by latitude band
    abs_lat = abs(lat)
    if abs_lat < 23:  # Tropical
        base_temp = 28
    elif abs_lat < 35:  # Subtropical
        base_temp = 22
    elif abs_lat < 50:  # Temperate
        base_temp = 15
    else:  # Cold
        base_temp = 8

    # Season adjustment (Northern hemisphere)
    summer_months = {6, 7, 8} if lat > 0 else {12, 1, 2}
    winter_months = {12, 1, 2} if lat > 0 else {6, 7, 8}
    if month in summer_months:
        base_temp += 8
    elif month in winter_months:
        base_temp -= 8

    heat_factor = compute_heat_factor(base_temp, 60)
    return {
        "temperature_c": base_temp,
        "humidity_pct": 60,
        "wind_speed_kmh": 12,
        "heat_factor": round(heat_factor, 3),
        "source": "estimation",
        "date": race_date.isoformat(),
    }


def _avg_slice(data: list, indices: list) -> float:
    vals = [data[i] for i in indices if i < len(data) and data[i] is not None]
    return sum(vals) / len(vals) if vals else 0


def compute_heat_factor(temp_c: float, humidity_pct: float) -> float:
    """Pace penalty factor based on temperature and humidity. >= 1.0."""
    if temp_c < 15:
        factor = 1.0
    elif temp_c < 20:
        factor = 1.0 + (temp_c - 15) * 0.004
    elif temp_c < 25:
        factor = 1.02 + (temp_c - 20) * 0.008
    elif temp_c < 30:
        factor = 1.06 + (temp_c - 25) * 0.012
    elif temp_c < 35:
        factor = 1.12 + (temp_c - 30) * 0.016
    else:
        factor = 1.20 + (temp_c - 35) * 0.02

    if humidity_pct > 65:
        factor += (humidity_pct - 65) * 0.001

    return min(factor, 1.5)
