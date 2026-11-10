"""The rotation loop: refresh data, pick the next screen, push it to the device."""
import json
import logging
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import draw, screens
from .bom import BomClient
from .device import Device

log = logging.getLogger(__name__)


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
        self.config = config
        self.tz = ZoneInfo(config.display.timezone)
        draw.set_fonts(config.display.font_regular, config.display.font_bold)
        draw.set_theme(config.display.theme)
        self.device = Device(config.device.host)
        self.bom = BomClient(config.weather, config.display.timezone) if config.weather.enabled else None
        self.alert_warnings = re.compile(config.weather.alert_warnings, re.I) if config.weather.alert_warnings else None
        self.ignore_warnings = re.compile(config.weather.ignore_warnings, re.I) if config.weather.ignore_warnings else None
        self.weather = None
        self.last_weather_slot = None
        self.index = 0
        self.showing_alert = False
        self.on_screen = None  # Last image the device confirmed, for changed-region updates

    def now(self):
        return datetime.now(self.tz)

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
        limits, items = self.config.alerts, []
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
        for warning in (self.weather or {}).get("warnings", []):
            title = warning.get("title", "")
            if self.alert_warnings and self.alert_warnings.search(title) and \
                    not (self.ignore_warnings and self.ignore_warnings.search(title)):
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

    def cycle(self):
        self.refresh_weather()
        stats = self.host_stats()
        alerts = self.alerts(stats)
        cards = self.screens(stats)
        if alerts and not self.showing_alert:
            name, image, self.showing_alert = "alert", screens.alert_card(alerts), True
        elif cards:
            self.showing_alert = False
            name, build = cards[self.index % len(cards)]
            self.index += 1
            image = build()
        else:
            return

        status = self.device.status()
        level = self.brightness()
        if status.get("madctl") != self.config.device.madctl or status.get("brightness") != level:
            self.device.set_lcd(madctl=self.config.device.madctl, brightness=level)
        # After a restart the device shows its own status screen, so send everything again.
        previous = self.on_screen if status.get("has_frame") else None
        self.on_screen = None
        started = time.perf_counter()
        sent = self.device.push(image, previous)
        self.on_screen = image
        log.info("%s: %s in %.0f ms", name, sent, (time.perf_counter() - started) * 1000)

    def run(self):
        log.info("pushing to %s every %ss", self.config.device.host, self.config.device.dwell_seconds)
        while True:
            try:
                self.cycle()
            except Exception as error:
                log.warning("cycle failed: %s", error)
            time.sleep(self.config.device.dwell_seconds)

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
