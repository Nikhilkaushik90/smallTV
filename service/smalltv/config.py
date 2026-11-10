"""Load and validate config.toml. Every site-specific value lives in that file."""
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEJAVU = "/usr/share/fonts/truetype/dejavu"


@dataclass
class Device:
    host: str
    madctl: int = 0          # 0 = RGB, 8 = BGR. Swap if red and blue look reversed.
    dwell_seconds: int = 15  # How long each screen stays up.


@dataclass
class Display:
    timezone: str = "UTC"
    clock_24h: bool = False
    screens: list = field(default_factory=lambda: ["weather", "forecast", "clock", "hosts"])
    font_regular: str = f"{DEJAVU}/DejaVuSans.ttf"
    font_bold: str = f"{DEJAVU}/DejaVuSans-Bold.ttf"


@dataclass
class Brightness:
    day: int = 60
    night: int = 20
    night_start: int = 22  # Hour of day, 24-hour clock.
    night_end: int = 6


@dataclass
class Weather:
    enabled: bool = False
    location: str = ""       # BOM place name, for example "Parramatta".
    state: str = ""          # Optional state filter when names repeat, for example "NSW".
    place_id: str = ""       # Optional: pin a BOM place id instead of searching by name.
    station_id: str = ""     # Optional: observation station, otherwise BOM's nearest.
    refresh_minutes: list = field(default_factory=lambda: [8, 38])
    alert_warnings: str = "severe|thunderstorm|flood|fire|heat|storm|cyclone"
    ignore_warnings: str = "cancel|marine|sheep|coastal waters"
    user_agent: str = ""     # Optional override of the HTTP User-Agent sent to BOM.


@dataclass
class Alerts:
    disk_percent: float = 90
    memory_percent: float = 95
    cpu_temp: float = 85
    gpu_temp: float = 90
    stale_seconds: int = 120


@dataclass
class Host:
    name: str
    url: str        # http://host:8766/host.json from the collector, or file:///path/host.json
    label: str = ""


@dataclass
class Config:
    device: Device
    display: Display
    brightness: Brightness
    weather: Weather
    alerts: Alerts
    hosts: list


SCREENS = {"weather", "forecast", "clock", "hosts"}


def _build(cls, data, where):
    if not isinstance(data, dict):
        raise ValueError(f"[{where}] must be a table")
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"[{where}] unknown setting(s): {', '.join(sorted(unknown))}")
    try:
        return cls(**data)
    except TypeError as error:
        raise ValueError(f"[{where}] {error}") from None


def load(path):
    with open(Path(path), "rb") as f:
        data = tomllib.load(f)
    unknown = set(data) - {"device", "display", "brightness", "weather", "alerts", "hosts"}
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(sorted(unknown))}")
    if "device" not in data:
        raise ValueError("[device] with host = \"...\" is required")
    config = Config(
        device=_build(Device, data["device"], "device"),
        display=_build(Display, data.get("display", {}), "display"),
        brightness=_build(Brightness, data.get("brightness", {}), "brightness"),
        weather=_build(Weather, data.get("weather", {}), "weather"),
        alerts=_build(Alerts, data.get("alerts", {}), "alerts"),
        hosts=[_build(Host, h, f"hosts.{i}") for i, h in enumerate(data.get("hosts", []))],
    )
    bad = set(config.display.screens) - SCREENS
    if bad:
        raise ValueError(f"[display] screens: unknown {sorted(bad)}, choose from {sorted(SCREENS)}")
    if config.weather.enabled and not (config.weather.location or config.weather.place_id):
        raise ValueError("[weather] needs location or place_id when enabled")
    return config
