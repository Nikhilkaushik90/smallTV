"""Load and validate settings.

config.toml holds every site-specific value. Changes made in the web dashboard are stored separately
in a small JSON file (settings.json) layered on top, so config.toml itself is never rewritten.
"""
import copy
import json
import os
import tempfile
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEJAVU = "/usr/share/fonts/truetype/dejavu"
SCREENS = ("weather", "forecast", "clock", "hosts")
THEMES = ("gradient", "dark")


@dataclass
class Device:
    host: str
    madctl: int = 0          # 0 = RGB, 8 = BGR. Swap if red and blue look reversed.
    dwell_seconds: int = 15  # How long each screen stays up.


@dataclass
class Display:
    timezone: str = "UTC"
    theme: str = "gradient"  # "gradient" or "dark" (black background)
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
class Web:
    enabled: bool = True
    bind: str = "0.0.0.0"
    port: int = 8780
    password: str = ""  # Set to require a password (HTTP basic auth, any user name).


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
    web: Web
    hosts: list


SECTIONS = {"device": Device, "display": Display, "brightness": Brightness, "weather": Weather,
            "alerts": Alerts, "web": Web}

# What the web dashboard may change. Everything else stays in config.toml only.
EDITABLE = {
    "device": {"madctl", "dwell_seconds"},
    "display": {"theme", "clock_24h", "screens"},
    "brightness": {"day", "night", "night_start", "night_end"},
    "weather": {"enabled", "location", "state", "place_id", "station_id"},
    "alerts": {"disk_percent", "memory_percent", "cpu_temp", "gpu_temp"},
}


def _build(cls, data, where):
    if not isinstance(data, dict):
        raise ValueError(f"[{where}] must be a table")
    unknown = set(data) - {f.name for f in fields(cls)}
    if unknown:
        raise ValueError(f"[{where}] unknown setting(s): {', '.join(sorted(unknown))}")
    try:
        return cls(**data)
    except TypeError as error:
        raise ValueError(f"[{where}] {error}") from None


def _check(config):
    d, b = config.display, config.brightness
    if d.theme not in THEMES:
        raise ValueError(f"[display] theme must be one of {THEMES}")
    bad = set(d.screens) - set(SCREENS)
    if bad:
        raise ValueError(f"[display] screens: unknown {sorted(bad)}, choose from {list(SCREENS)}")
    if not d.screens:
        raise ValueError("[display] screens: choose at least one screen")
    if len(set(d.screens)) != len(d.screens):
        raise ValueError("[display] screens: each screen can appear once")
    for name in ("day", "night"):
        if not 0 <= getattr(b, name) <= 100:
            raise ValueError(f"[brightness] {name} must be 0 to 100")
    for name in ("night_start", "night_end"):
        if not 0 <= getattr(b, name) <= 23:
            raise ValueError(f"[brightness] {name} must be an hour from 0 to 23")
    if not 3 <= config.device.dwell_seconds <= 600:
        raise ValueError("[device] dwell_seconds must be 3 to 600")
    if config.device.madctl not in (0, 8):
        raise ValueError("[device] madctl must be 0 (RGB) or 8 (BGR)")
    if config.weather.enabled and not (config.weather.location or config.weather.place_id):
        raise ValueError("[weather] needs location or place_id when enabled")


def from_dict(data):
    unknown = set(data) - set(SECTIONS) - {"hosts"}
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(sorted(unknown))}")
    if "device" not in data:
        raise ValueError("[device] with host = \"...\" is required")
    built = {name: _build(cls, data.get(name, {}), name) for name, cls in SECTIONS.items() if name != "device"}
    built["device"] = _build(Device, data["device"], "device")
    config = Config(**built, hosts=[_build(Host, h, f"hosts.{i}") for i, h in enumerate(data.get("hosts", []))])
    _check(config)
    return config


def read_toml(path):
    with open(Path(path), "rb") as f:
        return tomllib.load(f)


def load(path):
    return from_dict(read_toml(path))


def merge(base, changes):
    """Deep-merge two dicts of sections; values in changes win."""
    result = copy.deepcopy(base)
    for section, values in changes.items():
        if isinstance(values, dict):
            result.setdefault(section, {}).update(copy.deepcopy(values))
        else:
            result[section] = copy.deepcopy(values)
    return result


def editable(config):
    """The settings the web dashboard shows and may change."""
    return {section: {k: v for k, v in asdict(getattr(config, section)).items() if k in keys}
            for section, keys in EDITABLE.items()}


def check_editable(changes):
    if not isinstance(changes, dict):
        raise ValueError("settings must be an object")
    for section, values in changes.items():
        if section not in EDITABLE or not isinstance(values, dict):
            raise ValueError(f"{section} cannot be changed from the dashboard")
        blocked = set(values) - EDITABLE[section]
        if blocked:
            raise ValueError(f"[{section}] {', '.join(sorted(blocked))} cannot be changed from the dashboard")


class Settings:
    """config.toml plus the dashboard's overrides in settings.json."""

    def __init__(self, config_path, overrides_path=None):
        self.base = read_toml(config_path)
        default = Path(config_path).resolve().parent / "settings.json"
        self.path = Path(overrides_path or os.environ.get("SMALLTV_SETTINGS") or default)
        self.overrides = {}
        if self.path.is_file():
            self.overrides = json.loads(self.path.read_text())

    def current(self):
        return from_dict(merge(self.base, self.overrides))

    def update(self, changes):
        check_editable(changes)
        overrides = merge(self.overrides, changes)
        config = from_dict(merge(self.base, overrides))  # validate before saving
        self._save(overrides)
        return config

    def reset(self):
        self._save({})
        return self.current()

    def _save(self, overrides):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=self.path.parent, delete=False, suffix=".tmp") as f:
            json.dump(overrides, f, indent=2)
        os.replace(f.name, self.path)
        self.overrides = overrides
