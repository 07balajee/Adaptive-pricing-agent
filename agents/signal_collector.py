import requests

def get_weather(city, api_key):
    if not api_key:
        raise ValueError("OPENWEATHER_API_KEY is not set")

    base_url = "http://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": api_key,
        "units": "metric"
    }
    response = requests.get(base_url, params=params, timeout=15)
    data = response.json()
    if response.status_code != 200:
        # OpenWeather returns {"cod": ..., "message": "..."} on errors (bad key, unknown city)
        raise RuntimeError(f"OpenWeather returned {response.status_code}: {data.get('message', response.text)}")

    name = data['name']
    temperature = data['main']['temp']
    description = data['weather'][0]['description']
    return (f"Weather in {name}: {temperature} C, {description}")


_RAIN_CONDITIONS = {"Rain", "Drizzle", "Thunderstorm"}


def get_weather_conditions(lat, lon, api_key):
    """Structured weather at a coordinate, for pricing rules that need to know if it's raining."""
    if not api_key:
        raise ValueError("OPENWEATHER_API_KEY is not set")

    response = requests.get(
        "http://api.openweathermap.org/data/2.5/weather",
        params={"lat": lat, "lon": lon, "appid": api_key, "units": "metric"},
        timeout=15,
    )
    data = response.json()
    if response.status_code != 200:
        raise RuntimeError(f"OpenWeather returned {response.status_code}: {data.get('message', response.text)}")

    main = data['weather'][0]['main']
    description = data['weather'][0]['description']
    is_raining = main in _RAIN_CONDITIONS
    heavy = main == "Thunderstorm" or "heavy" in description or "extreme" in description
    return {
        "temp_c": data['main']['temp'],
        "condition": main,
        "description": description,
        "is_raining": is_raining,
        "rain_intensity": ("heavy" if heavy else "light") if is_raining else "none",
    }
