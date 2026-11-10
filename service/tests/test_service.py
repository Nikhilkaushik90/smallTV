"""Offline tests: config loading, every screen rendering, and the frame format. No network needed.

Run from the service folder: python -m unittest discover -s tests
"""
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE))

from smalltv import config, draw, screens  # noqa: E402
from smalltv.bom import build_weather_data  # noqa: E402
from smalltv.device import rgb565  # noqa: E402

FONTS = Path(draw._fonts["regular"]).is_file() and Path(draw._fonts["bold"]).is_file()
NOW = datetime(2026, 1, 15, 14, 5, tzinfo=ZoneInfo("Australia/Sydney"))

PLACE = {"id": "x1", "name": "Sampletown", "state": "NSW"}
DETAILS = {"place": {"name": "Sampletown", "coordinate": {"latitude": -33.8, "longitude": 151.0}}}
DAILY = {"fcst": {"daily": [
    {"date_utc": f"2026-01-{14 + i}T13:00:00Z",
     "atm": {"surf_air": {"temp_max_cel": 28 + i, "temp_min_cel": 18, "weather": {"icon_code": code},
                          "precip": {"any_probability_percent": 10 * i}}}}
    for i, code in enumerate([3, 1, 11, 16, 4, 17])]}}
OBS = {"stn": {"identity": {"bom_stn_name": "Sample Airport"}},
       "obs": {"datetime_utc": "2026-01-15T03:00:00Z",
               "temp": {"dry_bulb_1min_cel": 26.4, "apparent_1min_cel": 25.1, "rel_hum_percent": 48},
               "wind": {"speed_10m_mps": 5.5, "gust_speed_10m_mps": 9.0, "dirn_10m_ord": "NE"}}}
WARNINGS = {"warnings": [{"title": "Severe Thunderstorm Warning for Sampletown", "type": "X"},
                         {"title": "Marine Wind Warning", "type": "Y"}]}
STATS = {"ts": 0, "cpu": 35.0, "temp": 48, "uptime_s": 345600, "mem_percent": 51.2, "mem_used_gb": 8.2,
         "mem_total_gb": 16.0, "disk_percent": 48.0, "disk_used_gb": 120, "disk_total_gb": 250,
         "running": 12, "stopped": 2, "failing": 0, "gpu": None}


class ConfigTest(unittest.TestCase):
    def test_example_loads(self):
        cfg = config.load(SERVICE / "config.example.toml")
        self.assertEqual(cfg.device.madctl, 0)
        self.assertEqual(len(cfg.hosts), 2)

    def test_unknown_setting_is_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[device]\nhost = "display.test"\ncolour = "blue"\n')
        with self.assertRaisesRegex(ValueError, "colour"):
            config.load(f.name)

    def test_weather_needs_location(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write('[device]\nhost = "display.test"\n[weather]\nenabled = true\n')
        with self.assertRaisesRegex(ValueError, "location"):
            config.load(f.name)


@unittest.skipUnless(FONTS, "DejaVu fonts not installed")
class ScreensTest(unittest.TestCase):
    def setUp(self):
        self.data = build_weather_data(PLACE, DETAILS, DAILY, None, OBS, WARNINGS, "Australia/Sydney")

    def test_weather_data(self):
        self.assertEqual(self.data["current"]["temp"], 26.4)
        self.assertEqual(self.data["current"]["wind_kmh"], 19.8)
        self.assertEqual(len(self.data["daily"]), 6)

    def test_every_screen_renders_240(self):
        gpu = dict(STATS, gpu={"name": "GPU", "util": 25.0, "mem_used_mb": 512, "mem_total_mb": 8192, "temp": 50})
        images = [
            screens.weather_card(self.data, NOW, "cancel|marine"),
            screens.weather_card(self.data, NOW.replace(hour=23), None),
            screens.forecast_card(self.data),
            screens.clock_card(NOW, self.data, clock_24h=False),
            screens.clock_card(NOW, None, clock_24h=True),
            screens.host_card("Server", "label", STATS),
            screens.host_card("Server", "label", gpu),
            screens.host_card("Server", "label", dict(STATS, running=None)),
            screens.host_card("Server", "label", None),
            screens.alert_card(["Server disk 93%", "A very long alert " * 6]),
        ]
        for image in images:
            self.assertEqual(image.size, (240, 240))

    def test_frame_is_big_endian_rgb565(self):
        frame = rgb565(screens.alert_card(["x"]))
        self.assertEqual(len(frame), 240 * 240 * 2)
        white = rgb565(draw.sky("#ffffff", "#ffffff"))
        self.assertEqual(white[:2], b"\xff\xff")


if __name__ == "__main__":
    unittest.main()
