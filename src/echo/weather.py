"""
Weather via Open-Meteo — free, no API key, privacy-friendly.

Two steps:
1. Geocode a city name -> latitude/longitude (Open-Meteo geocoding API).
2. Fetch current weather for those coordinates (Open-Meteo forecast API).

Both are simple HTTPS GETs. This is one of the few genuinely online tools in
Echo (weather can't be offline) — everything else stays local.
"""

from __future__ import annotations

import datetime as dt
import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 10

# Open-Meteo weather codes -> human descriptions (common subset)
_WEATHER_CODES = {
    0: "clear sky",
    1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    61: "light rain", 63: "moderate rain", 65: "heavy rain",
    71: "light snow", 73: "moderate snow", 75: "heavy snow",
    80: "rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm",
}


def _geocode(city: str) -> dict | None:
    """Resolve a city name to coordinates. Returns None if not found.

    Open-Meteo's geocoder works best with a bare city name, so we try the full
    string first, then fall back to just the part before the first comma
    (e.g. 'Patna, Bihar' -> 'Patna'). Retries once on a transient network error.
    """
    candidates = [city.strip()]
    if "," in city:
        first = city.split(",")[0].strip()
        if first and first not in candidates:
            candidates.append(first)

    last_err: Exception | None = None
    for name in candidates:
        for attempt in range(2):  # one retry on transient failure
            try:
                r = requests.get(
                    GEOCODE_URL,
                    params={"name": name, "count": 1, "language": "en",
                            "format": "json"},
                    timeout=TIMEOUT,
                )
                r.raise_for_status()
                results = r.json().get("results")
                if results:
                    top = results[0]
                    return {
                        "name": top["name"],
                        "country": top.get("country", ""),
                        "latitude": top["latitude"],
                        "longitude": top["longitude"],
                    }
                break  # valid response but no match — try next candidate
            except requests.RequestException as e:
                last_err = e
                # retry once, then move on
    if last_err is not None:
        raise last_err
    return None


def get_weather(city: str, date: str | None = None, hourly: bool = False) -> dict:
    """
    Weather for a city. If `date` (YYYY-MM-DD) is given, return that day's
    forecast (high/low). If `hourly` is True, return the forecast for the next
    few hours. Otherwise return current conditions.
    """
    if not city or not city.strip():
        return {"error": "no city provided; ask the user which city."}

    try:
        loc = _geocode(city.strip())
    except requests.RequestException as e:
        return {"error": f"could not reach the geocoding service: {e}"}

    if loc is None:
        return {"error": f"couldn't find a place called '{city}'. Ask the user to clarify."}

    place = loc["name"] + (f", {loc['country']}" if loc["country"] else "")

    # --- hourly forecast ---
    if hourly:
        return _hourly_forecast(loc, place)

    # --- forecast for a specific date ---
    if date:
        return _forecast_for_date(loc, place, date)

    # --- current weather (default) ---
    try:
        r = requests.get(
            FORECAST_URL,
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"could not reach the weather service: {e}"}

    cur = r.json().get("current", {})
    temp = cur.get("temperature_2m")
    feels = cur.get("apparent_temperature")
    humidity = cur.get("relative_humidity_2m")
    code = cur.get("weather_code")
    desc = _WEATHER_CODES.get(code, "")

    spoken = f"It's currently {round(temp)}°C in {place}"
    if desc:
        spoken += f" with {desc}"
    if feels is not None and abs(feels - temp) >= 2:
        spoken += f", feels like {round(feels)}°C"
    spoken += "."

    return {
        "city": place,
        "temperature_c": temp,
        "feels_like_c": feels,
        "humidity_pct": humidity,
        "conditions": desc,
        "spoken": spoken,
    }


def _forecast_for_date(loc: dict, place: str, date: str) -> dict:
    """Fetch a single day's forecast (high/low/conditions) for `date`."""
    try:
        r = requests.get(
            FORECAST_URL,
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "start_date": date,
                "end_date": date,
                "timezone": "auto",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"could not reach the weather service: {e}"}

    daily = r.json().get("daily", {})
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    codes = daily.get("weather_code") or []
    if not highs:
        return {
            "error": f"I couldn't get a forecast for {date} — it may be too far "
            "ahead (forecasts go about 16 days out)."
        }

    high, low = highs[0], lows[0]
    desc = _WEATHER_CODES.get(codes[0] if codes else None, "")
    spoken = f"On {date} in {place}, expect a high of {round(high)}°C and a low of {round(low)}°C"
    if desc:
        spoken += f", with {desc}"
    spoken += "."

    return {
        "city": place,
        "date": date,
        "high_c": high,
        "low_c": low,
        "conditions": desc,
        "spoken": spoken,
    }


def get_rain_forecast(city: str, date: str | None = None) -> dict:
    """
    Chance-of-rain forecast for a city on a given day (defaults to today).
    Returns the hours sorted by precipitation probability, highest first, so
    the caller can say when rain is most likely rather than reading off a
    flat hour-by-hour list.
    """
    if not city or not city.strip():
        return {"error": "no city provided; ask the user which city."}

    try:
        loc = _geocode(city.strip())
    except requests.RequestException as e:
        return {"error": f"could not reach the geocoding service: {e}"}

    if loc is None:
        return {"error": f"couldn't find a place called '{city}'. Ask the user to clarify."}

    place = loc["name"] + (f", {loc['country']}" if loc["country"] else "")
    target_date = date or dt.date.today().isoformat()

    try:
        r = requests.get(
            FORECAST_URL,
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "hourly": "precipitation_probability,weather_code",
                "start_date": target_date,
                "end_date": target_date,
                "timezone": "auto",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"could not reach the weather service: {e}"}

    hourly_data = r.json().get("hourly", {})
    times = hourly_data.get("time", [])
    probs = hourly_data.get("precipitation_probability", [])
    codes = hourly_data.get("weather_code", [])

    if not times:
        return {
            "error": f"I couldn't get an hourly forecast for {target_date} — it may "
            "be too far ahead (forecasts go about 16 days out)."
        }

    is_today = target_date == dt.date.today().isoformat()
    now_hour = dt.datetime.now().hour

    hours = []
    for i, time_str in enumerate(times):
        try:
            dt_obj = dt.datetime.fromisoformat(time_str)
        except Exception:
            continue
        if is_today and dt_obj.hour < now_hour:
            continue  # skip hours already past today
        prob = probs[i] if i < len(probs) else None
        hour12 = dt_obj.hour % 12 or 12
        ampm = "AM" if dt_obj.hour < 12 else "PM"
        hours.append({
            "time": time_str,
            "time_spoken": f"{hour12} {ampm}",
            "precipitation_probability_pct": prob,
            "conditions": _WEATHER_CODES.get(codes[i], "") if i < len(codes) else "",
        })

    if not hours:
        return {"error": f"no remaining hours to check for {target_date}."}

    ranked = sorted(
        hours, key=lambda h: h["precipitation_probability_pct"] or 0, reverse=True
    )
    peak = [h for h in ranked if (h["precipitation_probability_pct"] or 0) > 0][:5]
    max_prob = ranked[0]["precipitation_probability_pct"] or 0

    day_label = "today" if is_today else f"on {target_date}"
    if max_prob < 20:
        spoken = (
            f"Rain looks unlikely {day_label} in {place} — the chance stays "
            f"below {max(h['precipitation_probability_pct'] or 0 for h in hours)}% all day."
        )
    else:
        parts = [f"{h['time_spoken']} ({h['precipitation_probability_pct']}%)" for h in peak]
        spoken = (
            f"The best chance of rain {day_label} in {place} is around "
            + ", then ".join(parts) + "."
        )

    return {
        "city": place,
        "date": target_date,
        "hourly": hours,
        "peak_rain_times": peak,
        "spoken": spoken,
    }


def _hourly_forecast(loc: dict, place: str) -> dict:
    """Fetch the forecast for the next 6 hours."""
    try:
        r = requests.get(
            FORECAST_URL,
            params={
                "latitude": loc["latitude"],
                "longitude": loc["longitude"],
                "hourly": "temperature_2m,weather_code",
                "forecast_hours": 6,
                "timezone": "auto",
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"could not reach the weather service: {e}"}

    hourly_data = r.json().get("hourly", {})
    times = hourly_data.get("time", [])
    temps = hourly_data.get("temperature_2m", [])
    codes = hourly_data.get("weather_code", [])
    
    if not times:
        return {"error": "no hourly data returned"}
        
    forecasts = []
    spoken_parts = []
    
    for i in range(len(times)):
        try:
            dt_obj = dt.datetime.fromisoformat(times[i])
            hour12 = dt_obj.hour % 12 or 12
            ampm = "AM" if dt_obj.hour < 12 else "PM"
            time_str = f"{hour12} {ampm}"
        except Exception:
            time_str = times[i][-5:]
            
        temp = round(temps[i]) if i < len(temps) else "?"
        desc = _WEATHER_CODES.get(codes[i], "") if i < len(codes) else ""
        
        forecasts.append({"time": times[i], "temperature_c": temp, "conditions": desc})
        
        part = f"at {time_str}, {temp}°C"
        if desc:
            part += f" and {desc}"
        spoken_parts.append(part)

    spoken = f"For the next few hours in {place}: " + "; ".join(spoken_parts) + "."
    
    return {
        "city": place,
        "hourly": forecasts,
        "spoken": spoken,
    }