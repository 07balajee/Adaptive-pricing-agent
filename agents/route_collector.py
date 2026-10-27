"""Geocoding, place search and driving routes using free OpenStreetMap services (no API key).

- Nominatim turns a typed place name into coordinates (max ~1 request/second, needs a User-Agent).
- Photon (komoot) powers search-as-you-type suggestions and reverse geocoding — Nominatim's
  usage policy forbids autocomplete, Photon's doesn't.
- OSRM's public demo server returns driving distance, duration and the route line.
Lookups are cached in SQLite, so repeat trips (station -> airport) are instant.
If OSRM is unreachable we fall back to straight-line distance x a road factor.
"""
import math

import requests

from storage import store

_HEADERS = {"User-Agent": "AdaptivePricingAgent/1.0 (local dev)"}
_ROAD_FACTOR = 1.35   # straight-line -> typical city road distance
_FALLBACK_KMPH = 25   # average city speed when estimating duration
_BIAS = {"lat": 11.0168, "lon": 76.9558}  # Coimbatore: rank nearby places first
_INDIA_BBOX = "68.1,6.5,97.4,35.7"  # lon_min,lat_min,lon_max,lat_max


def geocode(place: str) -> dict:
    key = f"geocode:{place.strip().lower()}"
    if cached := store.cache_get(key):
        return cached

    res = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1, "countrycodes": "in"},
        headers=_HEADERS,
        timeout=15,
    )
    res.raise_for_status()
    results = res.json()
    if not results:
        raise ValueError(f"Could not find a location for '{place}'. Try adding the area or city, e.g. 'Gandhipuram, Coimbatore'.")
    top = results[0]
    point = {
        "lat": float(top["lat"]),
        "lon": float(top["lon"]),
        "name": top.get("name") or place,
        "display_name": top["display_name"],
    }
    store.cache_set(key, point)
    return point


def _photon_label(props: dict) -> tuple[str, str]:
    name = props.get("name") or props.get("street") or "Unnamed place"
    context = [props.get(k) for k in ("district", "locality", "city", "state") if props.get(k) and props.get(k) != name]
    return name, ", ".join(dict.fromkeys(context))


def search_places(query: str, limit: int = 6) -> list[dict]:
    """Search-as-you-type suggestions, biased towards Coimbatore and limited to India."""
    key = f"search:{query.strip().lower()}"
    if (cached := store.cache_get(key)) is not None:
        return cached

    res = requests.get(
        "https://photon.komoot.io/api/",
        params={"q": query, "limit": limit, "lat": _BIAS["lat"], "lon": _BIAS["lon"], "bbox": _INDIA_BBOX},
        headers=_HEADERS,
        timeout=10,
    )
    res.raise_for_status()
    places = []
    for f in res.json().get("features", []):
        if f["properties"].get("countrycode") not in (None, "IN"):
            continue
        name, context = _photon_label(f["properties"])
        lon, lat = f["geometry"]["coordinates"]
        places.append({"name": name, "context": context, "lat": lat, "lon": lon})
    store.cache_set(key, places)
    return places


def reverse_geocode(lat: float, lon: float) -> dict:
    res = requests.get("https://photon.komoot.io/reverse", params={"lat": lat, "lon": lon},
                       headers=_HEADERS, timeout=10)
    res.raise_for_status()
    features = res.json().get("features", [])
    if not features:
        return {"name": f"{lat:.5f}, {lon:.5f}", "context": "", "lat": lat, "lon": lon}
    name, context = _photon_label(features[0]["properties"])
    return {"name": name, "context": context, "lat": lat, "lon": lon}


def _haversine_km(a: dict, b: dict) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _resolve(place: str, point: dict | None) -> dict:
    # A suggestion picked in the UI already carries coordinates; skip geocoding it
    if point and point.get("lat") is not None and point.get("lon") is not None:
        return {"lat": float(point["lat"]), "lon": float(point["lon"]),
                "name": point.get("name") or place, "display_name": point.get("display_name") or place}
    return geocode(place)


def get_route(pickup: str, drop: str, pickup_point: dict | None = None, drop_point: dict | None = None) -> dict:
    start, end = _resolve(pickup, pickup_point), _resolve(drop, drop_point)

    key = f"route:{start['lat']:.5f},{start['lon']:.5f};{end['lat']:.5f},{end['lon']:.5f}"
    leg = store.cache_get(key)
    if leg is None:
        try:
            res = requests.get(
                f"https://router.project-osrm.org/route/v1/driving/{start['lon']},{start['lat']};{end['lon']},{end['lat']}",
                params={"overview": "simplified", "geometries": "geojson"},
                headers=_HEADERS,
                timeout=15,
            )
            res.raise_for_status()
            route = res.json()["routes"][0]
            leg = {
                "distance_km": route["distance"] / 1000,
                "duration_min": route["duration"] / 60,
                # GeoJSON is [lon, lat]; maps want [lat, lon]
                "path": [[lat, lon] for lon, lat in route["geometry"]["coordinates"]],
                "source": "osrm",
            }
            store.cache_set(key, leg)
        except Exception:
            distance_km = _haversine_km(start, end) * _ROAD_FACTOR
            leg = {
                "distance_km": distance_km,
                "duration_min": distance_km / _FALLBACK_KMPH * 60,
                "path": [[start["lat"], start["lon"]], [end["lat"], end["lon"]]],
                "source": "estimate",
            }  # not cached: retry OSRM next time

    return {
        "pickup": start,
        "drop": end,
        "distance_km": round(leg["distance_km"], 2),
        "duration_min": round(leg["duration_min"], 1),
        "path": leg["path"],
        "source": leg["source"],
    }
