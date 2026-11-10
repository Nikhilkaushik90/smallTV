"""Dashboard tests against the fake display: settings layer, web API and partial updates."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE))
sys.path.insert(0, str(SERVICE / "tests"))

from fake_device import FakeDevice  # noqa: E402
from smalltv import config, draw, web  # noqa: E402
from smalltv.app import Dashboard  # noqa: E402
from smalltv.device import rgb565  # noqa: E402

FONTS = Path(draw._fonts["regular"]).is_file()


def write_config(folder, device_host):
    path = Path(folder) / "config.toml"
    path.write_text(f'[device]\nhost = "{device_host}"\ndwell_seconds = 5\n'
                    '[display]\ntheme = "dark"\nscreens = ["clock"]\n[web]\nport = 0\n')
    return path


class SettingsTest(unittest.TestCase):
    def test_overrides_are_layered_and_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = config.Settings(write_config(folder, "display.test"))
            cfg = settings.update({"brightness": {"day": 70}, "display": {"theme": "gradient"}})
            self.assertEqual((cfg.brightness.day, cfg.display.theme), (70, "gradient"))
            reloaded = config.Settings(Path(folder) / "config.toml").current()
            self.assertEqual(reloaded.brightness.day, 70)
            self.assertIn("day", (Path(folder) / "settings.json").read_text())
            self.assertEqual(settings.reset().display.theme, "dark")

    def test_rejects_unsafe_or_invalid_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = config.Settings(write_config(folder, "display.test"))
            for bad in ({"device": {"host": "elsewhere"}}, {"web": {"password": "x"}},
                        {"brightness": {"day": 150}}, {"display": {"screens": []}}):
                with self.assertRaises(ValueError):
                    settings.update(bad)
            self.assertFalse((Path(folder) / "settings.json").exists())


@unittest.skipUnless(FONTS, "DejaVu fonts not installed")
class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeDevice().start()
        self.folder = tempfile.TemporaryDirectory()
        self.settings = config.Settings(write_config(self.folder.name, f"127.0.0.1:{self.fake.http_port}"))
        self.dashboard = Dashboard(self.settings.current())
        self.server = web.start(self.dashboard, self.settings, self.settings.current().web)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.dashboard.run, daemon=True).start()
        self.wait(lambda: self.dashboard.last_push)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.fake.stop()
        self.folder.cleanup()
        draw.set_theme("gradient")

    def wait(self, check, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if check():
                return
            time.sleep(0.05)
        self.fail("timed out")

    def call(self, path, body=None):
        request = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_screen_matches_what_was_pushed(self):
        self.assertEqual(bytes(self.fake.screen), rgb565(self.dashboard.on_screen))

    def test_state_and_preview(self):
        state = self.call("/api/state")
        self.assertEqual(state["status"]["current"], "clock")
        self.assertEqual(state["settings"]["display"]["theme"], "dark")
        with urllib.request.urlopen(self.base + "/api/preview.png") as response:
            self.assertEqual(response.read(8), b"\x89PNG\r\n\x1a\n")
        with urllib.request.urlopen(self.base + "/") as response:
            self.assertIn(b"SmallTV", response.read())

    def test_brightness_change_reaches_device_quickly(self):
        self.call("/api/settings", {"brightness": {"day": 33, "night": 33}})
        self.wait(lambda: self.fake.state["brightness"] == 33, timeout=3)

    def test_partial_updates_keep_screen_exact(self):
        count = self.dashboard.last_push["count"]
        self.call("/api/settings", {"display": {"theme": "gradient"}})
        self.wait(lambda: self.dashboard.last_push["count"] > count, timeout=3)
        self.call("/api/action", {"action": "next"})
        self.wait(lambda: self.dashboard.last_push["count"] > count + 1, timeout=3)
        self.assertEqual(bytes(self.fake.screen), rgb565(self.dashboard.on_screen))

    def test_bad_requests_are_rejected(self):
        for path, body in (("/api/settings", {"device": {"host": "x"}}), ("/api/action", {"action": "format"})):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.call(path, body)
            self.assertEqual(caught.exception.code, 400)
        form = urllib.request.Request(self.base + "/api/action", data=b"action=reboot",
                                      headers={"Content-Type": "application/x-www-form-urlencoded"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(form)
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
