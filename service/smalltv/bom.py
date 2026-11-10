"""Bureau of Meteorology (Australia) weather client.

Adapted from bom_weather.py by Nikhil Kaushik. It uses the JSON API behind the BOM website,
which is undocumented and may change without notice. Read the BOM section of service/README.md
before using it, and keep the request rate low.
"""
import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

BASE_API = "https://api.bom.gov.au/apikey/v1"
DETAILS_MAX_AGE = 86400
SSL_CONTEXT = ssl.create_default_context()
HEADERS = {
    "Accept": "application/json, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Origin": "https://www.bom.gov.au",
    "Referer": "https://www.bom.gov.au/",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/125.0.0.0 Safari/537.36",
}


def safe_round(value, decimals=1):
    try:
        return round(float(value), decimals)
    except (TypeError, ValueError):
        return None


def ms_to_kmh(value):
    speed = safe_round(value, 3)
    return None if speed is None else safe_round(speed * 3.6)


def local_dt(date_utc, tz_name):
    try:
        moment = datetime.fromisoformat(date_utc.replace("Z", "+00:00"))
        return moment.astimezone(ZoneInfo(tz_name)) if tz_name else moment
    except (AttributeError, ValueError):
        return None


def state_of(place):
    state = place.get("state", "")
    if isinstance(state, dict):
        return state.get("abbreviation", state.get("name", ""))
    return str(state or "")


def build_weather_data(place, details, daily_resp, texts_resp, obs_resp, warnings_resp, tz_name):
    place_obj = (details or {}).get("place", {})
    coord = place_obj.get("coordinate", {})

    current = {}
    if obs_resp:
        obs, stn = obs_resp.get("obs", {}), obs_resp.get("stn", {})
        temp, wind = obs.get("temp", {}), obs.get("wind", {})
        obs_dt = local_dt(obs.get("datetime_utc", ""), tz_name)
        current = {
            "station_name": stn.get("identity", {}).get("bom_stn_name", ""),
            "obs_time": obs_dt.strftime("%I:%M %p").lstrip("0") if obs_dt else "",
            "temp": safe_round(temp.get("dry_bulb_1min_cel")),
            "feels_like": safe_round(temp.get("apparent_1min_cel")),
            "humidity": safe_round(temp.get("rel_hum_percent"), 0),
            "temp_max_today": safe_round(temp.get("dry_bulb_max_cel")),
            "temp_min_today": safe_round(temp.get("dry_bulb_min_cel")),
            "pressure": safe_round(obs.get("pres", {}).get("msl_hpa")),
            "wind_kmh": ms_to_kmh(wind.get("speed_10m_mps")),
            "wind_gust_kmh": ms_to_kmh(wind.get("gust_speed_10m_mps")),
            "wind_dir": wind.get("dirn_10m_ord", ""),
            "rain_since_9am": safe_round(obs.get("precip", {}).get("since_0900lct_total_mm")),
        }

    text_by_date = {}
    for day in (texts_resp or {}).get("fcst", {}).get("daily", []):
        precis = day.get("atm", {}).get("surf_air", {}).get("weather", {}).get("precis_text", "")
        if day.get("date_utc") and precis:
            text_by_date[day["date_utc"][:10]] = precis

    daily = []
    for day in (daily_resp or {}).get("fcst", {}).get("daily", []):
        date_utc = day.get("date_utc", "")
        surf = day.get("atm", {}).get("surf_air", {})
        precip = surf.get("precip", {})
        moment = local_dt(date_utc, tz_name)
        icon = surf.get("weather", {}).get("icon_code")
        daily.append({
            "date_utc": date_utc,
            "day_name": moment.strftime("%a") if moment else date_utc[:10],
            "display_date": f"{moment.day} {moment.strftime('%b')}" if moment else date_utc[:10],
            "temp_max": safe_round(surf.get("temp_max_cel")),
            "temp_min": safe_round(surf.get("temp_min_cel")),
            "rain_prob": safe_round(precip.get("any_probability_percent"), 0),
            "rain_mm": safe_round(precip.get("exceeding_50percentchance_total_mm")),
            "icon_code": int(icon) if str(icon or "").isdigit() and int(icon) > 0 else None,
            "uv_max": safe_round(surf.get("radiation", {}).get("uv_clear_sky_max_code")),
            "precis": text_by_date.get(date_utc[:10], ""),
        })

    warnings = [{"title": w.get("title", ""), "type": w.get("type", "")}
                for w in (warnings_resp or {}).get("warnings", [])]

    return {
        "location_name": place_obj.get("name") or place.get("name", ""),
        "location_state": state_of(place),
        "lat": coord.get("latitude"),
        "lon": coord.get("longitude"),
        "timezone": tz_name,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "current": current,
        "daily": daily,
        "warnings": warnings,
    }


class BomClient:
    def __init__(self, settings, default_timezone):
        self.settings = settings
        self.default_timezone = default_timezone
        self.headers = dict(HEADERS)
        if settings.user_agent:
            self.headers["User-Agent"] = settings.user_agent
        self.place = None
        self.details = None
        self.details_at = 0.0

    def _get(self, path, params=None):
        url = f"{BASE_API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
        request = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(request, timeout=20, context=SSL_CONTEXT) as response:
            return json.loads(response.read().decode("utf-8"))

    def _optional(self, path, params=None):
        try:
            return self._get(path, params)
        except Exception as error:
            log.warning("BOM %s unavailable: %s", path, error)
            return None

    def search(self, name, limit=5):
        params = {"name": name, "website-sort": "true", "website-filter": "true", "limit": str(limit)}
        data = self._get("/locations/places/autocomplete", params)
        return data.get("candidates", data.get("places", []))

    def resolve_place(self):
        if self.place:
            return self.place
        s = self.settings
        if s.place_id:
            self.place = {"id": s.place_id, "name": s.location, "state": s.state}
            return self.place
        candidates = self.search(s.location)
        places = [c for c in candidates if c.get("type") == "place"] or candidates
        if s.state:
            places = [p for p in places if state_of(p).upper() == s.state.upper()]
        if not places:
            raise RuntimeError(f"BOM found no place matching {s.location!r} {s.state}".strip())
        self.place = places[0]
        log.info("BOM place %s %s id=%s", self.place.get("name"), state_of(self.place), self.place.get("id"))
        return self.place

    def location_details(self):
        if self.details and time.time() - self.details_at < DETAILS_MAX_AGE:
            return self.details
        params = {
            "filter": "nearby_type:bom_stn,elevation:300,capability:SENSOR_TEMPERATURE_DB,match_coastal_status:true",
            "radius": "100000",
            "nearby_limit": "10",
        }
        self.details = self._get(f"/locations/places/details/place/{self.resolve_place()['id']}", params)
        self.details_at = time.time()
        return self.details

    def fetch(self):
        place = self.resolve_place()
        details = self.location_details()
        info = details.get("place", {})
        grid = info.get("gridcells", {}).get("forecast", {})
        hierarchy = info.get("location_hierarchy", {})
        tz_name = info.get("timezone") or self.default_timezone
        text_area = (hierarchy.get("precis_fcst") or {}).get("aac", "") or \
            (hierarchy.get("public_district") or {}).get("aac", "")
        warning_area = (hierarchy.get("public_district") or {}).get("aac", text_area)
        station = self.settings.station_id or (hierarchy.get("nearest") or {}).get("id")
        if not grid.get("x") or not grid.get("y"):
            raise RuntimeError("BOM returned no forecast grid for this place")

        daily = self._get(f"/forecasts/daily/{grid['x']}/{grid['y']}", {"timezone": tz_name})
        observations = self._get(f"/observations/latest/{station}/atm/surf_air",
                                 {"include_qc_results": "false"}) if station else None
        texts = self._optional("/forecasts/texts", {"aac": text_area, "timezone": tz_name}) if text_area else None
        warnings = self._optional("/warnings/list", {"area_type": "aac", "area_code": warning_area}) \
            if warning_area else None

        data = build_weather_data(place, details, daily, texts, observations, warnings, tz_name)
        if not data["daily"] or data["current"].get("temp") is None:
            raise RuntimeError("BOM forecast or observations missing")
        return data
