"""Погода через Open-Meteo — без ключей и регистрации."""
import httpx

from app import config

URL = "https://api.open-meteo.com/v1/forecast"

CODES = {
    0: "ясно", 1: "почти ясно", 2: "переменная облачность", 3: "облачно",
    45: "туман", 48: "изморозь", 51: "морось", 53: "морось", 55: "морось",
    61: "дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь",
    71: "снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "ливень", 81: "ливень", 82: "сильный ливень",
    85: "снегопад", 86: "снегопад", 95: "гроза", 96: "гроза", 99: "гроза",
}


async def today() -> dict | None:
    params = {
        "latitude": config.WEATHER_LAT,
        "longitude": config.WEATHER_LON,
        "hourly": "temperature_2m,precipitation_probability",
        "daily": "temperature_2m_min,temperature_2m_max,weather_code,"
                 "precipitation_probability_max",
        "timezone": config.TZ_NAME,
        "forecast_days": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None

    from app.render import spark

    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    try:
        tmin = round(daily["temperature_2m_min"][0])
        tmax = round(daily["temperature_2m_max"][0])
        code = daily["weather_code"][0]
        rain = daily.get("precipitation_probability_max", [0])[0] or 0
    except (KeyError, IndexError):
        return None

    temps = hourly.get("temperature_2m", [])[6:22:2]
    desc = CODES.get(code, "")
    return {
        "summary": f"{tmin}° {desc} → {tmax}°",
        "spark": spark(temps),
        "rain": int(rain),
        "tmin": tmin,
        "tmax": tmax,
    }
