import logging
import math
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
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,weathercode",
            "start_date": date, "end_date": date, "timezone": "auto",
        })
        response.raise_for_status()
        data = response.json()

    hourly = data.get("hourly", {})
    temps = hourly.get("temperature_2m", [])
    humidity = hourly.get("relative_humidity_2m", [])
    wind = hourly.get("wind_speed_10m", [])
    wind_dir = hourly.get("wind_direction_10m", [])
    codes = hourly.get("weathercode", [])

    if not temps:
        return None

    race_hours = list(range(6, 18))
    avg_temp = _avg_slice(temps, race_hours)
    avg_humidity = _avg_slice(humidity, race_hours)
    avg_wind = _avg_slice(wind, race_hours)
    avg_wind_dir = _avg_wind_direction(wind_dir, race_hours)
    heat_factor = compute_heat_factor(avg_temp, avg_humidity)
    day_code = _dominant_code([codes[i] for i in race_hours if i < len(codes)])

    # Full 24h profile so the simulator can derive per-checkpoint temperatures
    # and conditions from each section's estimated time of day.
    hourly_temps = [temps[h] if h < len(temps) and temps[h] is not None else avg_temp for h in range(24)]
    hourly_hum = [humidity[h] if h < len(humidity) and humidity[h] is not None else avg_humidity for h in range(24)]
    hourly_codes = [int(codes[h]) if h < len(codes) and codes[h] is not None else (day_code or 0) for h in range(24)]

    return {
        "temperature_c": round(avg_temp, 1),
        "humidity_pct": round(avg_humidity, 0),
        "wind_speed_kmh": round(avg_wind, 1),
        "wind_direction_deg": round(avg_wind_dir, 0),
        "heat_factor": round(heat_factor, 3),
        "weather_code": day_code,
        "source": "prevision",
        "date": date,
        "hourly": {
            "temps": [round(t, 1) for t in hourly_temps],
            "humidity": [round(h, 0) for h in hourly_hum],
            "codes": hourly_codes,
        },
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
            "wind_direction_deg": None,
            "heat_factor": round(heat_factor, 3),
            "weather_code": None,  # climate averages carry no condition code
            "source": "moyennes climatiques",
            "date": race_date.isoformat(),
            "hourly": _diurnal_profile(avg_temp, avg_humidity),
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
        "wind_direction_deg": None,
        "heat_factor": round(heat_factor, 3),
        "source": "estimation",
        "date": race_date.isoformat(),
        "hourly": _diurnal_profile(base_temp, 60),
    }


def _diurnal_profile(mean_temp: float, humidity_pct: float, amplitude: float = 5.0) -> dict:
    """Synthesize a 24h temperature curve from a daily mean.

    Used when only daily/monthly averages are available (races beyond the
    forecast horizon). Coldest ~03:00, warmest ~15:00 — the typical mountain
    diurnal cycle, so a night/dawn start reads colder than a midday section.
    """
    temps = [round(mean_temp + amplitude * math.cos((h - 15) * math.pi / 12), 1) for h in range(24)]
    return {"temps": temps, "humidity": [round(humidity_pct, 0)] * 24}


def _avg_slice(data: list, indices: list) -> float:
    vals = [data[i] for i in indices if i < len(data) and data[i] is not None]
    return sum(vals) / len(vals) if vals else 0


def _avg_wind_direction(directions_deg: list, indices: list) -> float:
    """Circular mean of wind direction (degrees). Plain average breaks at 350°/10°."""
    import math as _m

    vals = [directions_deg[i] for i in indices if i < len(directions_deg) and directions_deg[i] is not None]
    if not vals:
        return 0
    sin_sum = sum(_m.sin(_m.radians(d)) for d in vals)
    cos_sum = sum(_m.cos(_m.radians(d)) for d in vals)
    angle = _m.degrees(_m.atan2(sin_sum, cos_sum))
    return angle % 360


def _dominant_code(codes: list) -> int | None:
    """Most representative WMO weather code over a set of hours.

    Prefers the most "significant" condition (storm > snow > rain > drizzle >
    fog > cloud > clear) so a mostly-sunny day with an afternoon storm still
    flags the storm, rather than averaging it away.
    """
    vals = [int(c) for c in codes if c is not None]
    if not vals:
        return None
    # Rank by severity, then by frequency among equally-severe codes.
    by_sev = sorted(vals, key=lambda c: (_code_severity(c), vals.count(c)), reverse=True)
    return by_sev[0]


def _code_severity(code: int) -> int:
    if code >= 95:
        return 7  # thunderstorm
    if code in (71, 73, 75, 77, 85, 86):
        return 6  # snow
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return 5  # rain
    if code in (51, 53, 55, 56, 57):
        return 4  # drizzle
    if code in (45, 48):
        return 3  # fog
    if code in (2, 3):
        return 2  # cloudy
    if code == 1:
        return 1  # mainly clear
    return 0  # clear


# WMO weather code → (category slug, French label). The slug drives the icon
# rendered in templates (see partials/_weather_icons.html).
def wmo_category(code: int | None) -> str:
    if code is None:
        return "unknown"
    if code == 0:
        return "clear"
    if code == 1:
        return "mostly-clear"
    if code in (2,):
        return "partly-cloudy"
    if code in (3,):
        return "overcast"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57):
        return "drizzle"
    if code in (61, 63, 65, 66, 67, 80, 81, 82):
        return "rain"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "unknown"


WMO_LABELS = {
    "clear": "Ciel dégagé",
    "mostly-clear": "Peu nuageux",
    "partly-cloudy": "Partiellement nuageux",
    "overcast": "Couvert",
    "fog": "Brouillard",
    "drizzle": "Bruine",
    "rain": "Pluie",
    "snow": "Neige",
    "storm": "Orage",
    "unknown": "Condition inconnue",
}


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
