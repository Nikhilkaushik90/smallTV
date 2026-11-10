"""The rotation loop, plus the controls the web dashboard uses.

Only the loop thread talks to the display. The dashboard queues requests with request(), and the
loop wakes up and applies them straight away.
"""
import io
import json
import logging
import re
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, draw, screens
from .bom import BomClient
from .device import Device

log = logging.getLogger(__name__)
DEVICE_ACTIONS = {"init": "/lcd/init", "test": "/test", "reboot": "/reboot"}


def read_stats(url, max_age):
    """Fetch a collector's JSON from http(s):// or file:// and drop it if it is stale."""
    try:
        if url.startswith("file://"):
            stats = json.loads(Path(url[len("file://"):]).read_text())
        else:
            with urllib.request.urlopen(url, timeout=5) as response:
                stats = json.load(response)
    except (OSError, ValueError):
        return None
    return stats if time.time() - stats.get("ts", 0) <= max_age else None


class Dashboard:
    def __init__(self, config):
        self.device = Device(config.device.host)
        self.config = None
        self.bom = None
        self.weather = None
        self.last_weather_slot = None
        self.apply_config(config)

        self.index = 0
        self.showing_alert = False
        self.paused = False
        self.on_screen = None      # Last image the device confirmed, for changed-region updates
        self.current = None        # Name of the screen on display
        self.preview_png = None
        self.last_push = None
        self.last_error = None
        self.last_status = None
        self.last_hosts = {}
        self.last_alerts = []
        self.rotation = []

        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._pending = {}

    # ---- configuration ----

    def apply_config(self, config):
        old = self.config
        self.config = config
        draw.set_fonts(config.display.font_regular, config.display.font_bold)
        draw.set_theme(config.display.theme)
        weather_keys = ("enabled", "location", "state", "place_id", "station_id")
        if old is None or any(getattr(old.weather, k) != getattr(config.weather, k) for k in weather_keys):
            self.bom = BomClient(config.weather, config.display.timezone) if config.weather.enabled else None
            self.weather = None
            self.last_weather_slot = None

    def request(self, **changes):
        """Queue work for the loop: config=, jump=, next=True, pause=bool, refresh_weather=True, device=."""
        with self._lock:
            self._pending.update(changes)
        self._wake.set()

    # ---- data ----

    def now(self):
        return datetime.now(ZoneInfo(self.config.display.timezone))

    def refresh_weather(self, force=False):
        if not self.bom:
            return
        now = self.now()
        slot = (now.date(), now.hour, now.minute)
        due = now.minute in self.config.weather.refresh_minutes and slot != self.last_weather_slot
        if not (force or self.weather is None or due):
            return
        self.last_weather_slot = slot
        try:
            self.weather = self.bom.fetch()
            cur = self.weather["current"]
            log.info("weather %sC at %s %s", cur["temp"], cur["station_name"], cur["obs_time"])
        except Exception as error:
            log.warning("weather update skipped, keeping last data: %s", error)

    def host_stats(self):
        return {h.name: read_stats(h.url, self.config.alerts.stale_seconds) for h in self.config.hosts}

    def alerts(self, stats_by_host):
        limits, weather, items = self.config.alerts, self.config.weather, []
        for name, stats in stats_by_host.items():
            if stats is None:
                items.append(f"{name} stats not reporting")
                continue
            if stats["disk_percent"] >= limits.disk_percent:
                items.append(f"{name} disk {round(stats['disk_percent'])}%")
            if stats["mem_percent"] >= limits.memory_percent:
                items.append(f"{name} memory {round(stats['mem_percent'])}%")
            if (stats.get("temp") or 0) >= limits.cpu_temp:
                items.append(f"{name} CPU {stats['temp']}°")
            if ((stats.get("gpu") or {}).get("temp") or 0) >= limits.gpu_temp:
                items.append(f"{name} GPU {stats['gpu']['temp']}°")
        alert_re = re.compile(weather.alert_warnings, re.I) if weather.alert_warnings else None
        ignore_re = re.compile(weather.ignore_warnings, re.I) if weather.ignore_warnings else None
        for warning in (self.weather or {}).get("warnings", []):
            title = warning.get("title", "")
            if alert_re and alert_re.search(title) and not (ignore_re and ignore_re.search(title)):
                items.append(title)
        return items

    def screens(self, stats_by_host):
        cards, cfg = [], self.config
        for name in cfg.display.screens:
            if name == "weather" and self.weather:
                cards.append(("weather", lambda: screens.weather_card(
                    self.weather, self.now(), cfg.weather.ignore_warnings)))
            elif name == "forecast" and self.weather:
                cards.append(("forecast", lambda: screens.forecast_card(self.weather)))
            elif name == "clock":
                cards.append(("clock", lambda: screens.clock_card(self.now(), self.weather, cfg.display.clock_24h)))
            elif name == "hosts":
                for host in cfg.hosts:
                    cards.append((f"host-{host.name}", lambda h=host: screens.host_card(
                        h.name, h.label, stats_by_host.get(h.name))))
        return cards

    def brightness(self):
        b, hour = self.config.brightness, self.now().hour
        night = hour >= b.night_start or hour < b.night_end if b.night_start > b.night_end \
            else b.night_start <= hour < b.night_end
        return b.night if night else b.day

    # ---- one screen ----

    def cycle(self, jump=None, advance=True):
        self.refresh_weather()
        stats = self.host_stats()
        alerts = self.alerts(stats)
        cards = self.screens(stats)
        self.last_hosts, self.last_alerts = stats, alerts
        self.rotation = [name for name, _ in cards]

        names = self.rotation
        if jump in names:
            i = names.index(jump)
            name, image = cards[i][0], cards[i][1]()
            self.index, self.showing_alert = i + 1, False
        elif jump == "alert" or (alerts and not self.showing_alert and advance):
            name, image, self.showing_alert = "alert", screens.alert_card(alerts or ["No active alerts"]), True
        elif cards:
            self.showing_alert = False
            i = self.index % len(cards) if advance else (self.index - 1) % len(cards)
            self.index = i + 1
            name, image = cards[i][0], cards[i][1]()
        else:
            return

        status = self.device.status()
        self.last_status = status
        level = self.brightness()
        if status.get("madctl") != self.config.device.madctl or status.get("brightness") != level:
            self.last_status = self.device.set_lcd(madctl=self.config.device.madctl, brightness=level)
        # After a restart the device shows its own status screen, so send everything again.
        previous = self.on_screen if status.get("has_frame") else None
        self.on_screen = None
        started = time.perf_counter()
        sent = self.device.push(image, previous)
        took = (time.perf_counter() - started) * 1000
        self.on_screen, self.current = image, name
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        self.preview_png = buffer.getvalue()
        self.last_push = {"screen": name, "sent": sent, "ms": round(took), "at": self.now().isoformat(),
                          "count": (self.last_push or {}).get("count", 0) + 1}
        self.last_error = None
        log.info("%s: %s in %.0f ms", name, sent, took)

    def device_action(self, action):
        self.device.get(DEVICE_ACTIONS[action])
        self.on_screen = None  # the panel no longer shows our last frame
        log.info("device action %s", action)

    # ---- loop ----

    def run(self):
        log.info("pushing to %s", self.config.device.host)
        next_push = 0.0
        while True:
            with self._lock:
                pending, self._pending = self._pending, {}
            try:
                if "config" in pending:
                    self.apply_config(pending["config"])
                if "pause" in pending:
                    self.paused = pending["pause"]
                if pending.get("refresh_weather"):
                    self.refresh_weather(force=True)
                if "device" in pending:
                    self.device_action(pending["device"])
                held = pending.get("device") == "test"
                forced = "jump" in pending or pending.get("next") or "config" in pending \
                    or pending.get("refresh_weather") or pending.get("device") in ("init", "reboot")
                if pending.get("device") == "reboot":
                    next_push = time.monotonic() + 20  # give the display time to restart
                elif held:
                    next_push = time.monotonic() + 5   # leave the colour bars up briefly
                elif forced or time.monotonic() >= next_push:
                    # A settings change redraws the current screen so its effect shows at once.
                    advance = bool(pending.get("next")) or (not self.paused and "config" not in pending)
                    self.cycle(jump=pending.get("jump"), advance=advance)
                    next_push = time.monotonic() + self.config.device.dwell_seconds
            except Exception as error:
                self.last_error = f"{datetime.now():%H:%M:%S} {error}"
                log.warning("cycle failed: %s", error)
                next_push = time.monotonic() + self.config.device.dwell_seconds
            self._wake.wait(max(0.1, next_push - time.monotonic()))
            self._wake.clear()

    # ---- dashboard view ----

    def snapshot(self):
        hosts = []
        for host in self.config.hosts:
            stats = self.last_hosts.get(host.name)
            hosts.append({"name": host.name, "label": host.label, "online": stats is not None,
                          **({k: stats.get(k) for k in ("cpu", "mem_percent", "disk_percent", "temp")} if stats else {}),
                          "gpu": (stats or {}).get("gpu")})
        cur = (self.weather or {}).get("current", {})
        return {
            "version": __version__,
            "current": self.current,
            "paused": self.paused,
            "rotation": self.rotation,
            "brightness_now": self.brightness(),
            "last_push": self.last_push,
            "last_error": self.last_error,
            "alerts": self.last_alerts,
            "hosts": hosts,
            "weather": {"location": (self.weather or {}).get("location_name"), "temp": cur.get("temp"),
                        "station": cur.get("station_name"), "observed": cur.get("obs_time")} if self.weather else None,
            "device": self.last_status,
        }

    def preview(self, out):
        """Render every screen to PNG files without touching the device."""
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        self.refresh_weather(force=True)
        stats = self.host_stats()
        written = []
        for name, build in self.screens(stats):
            path = out / f"{name}.png"
            build().save(path)
            written.append(path)
        alerts = self.alerts(stats)
        if alerts:
            path = out / "alert.png"
            screens.alert_card(alerts).save(path)
            written.append(path)
        return written
