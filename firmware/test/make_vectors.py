"""Write test frames for host_test: raw RGB565 plus zlib streams at 4 KB and 32 KB windows."""
import sys
import zlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "service"))
sys.path.insert(0, str(ROOT / "service" / "tests"))

import test_service as sample  # noqa: E402
from smalltv import screens  # noqa: E402
from smalltv.device import rgb565  # noqa: E402

out = Path(sys.argv[1])
data = sample.build_weather_data(sample.PLACE, sample.DETAILS, sample.DAILY, None, sample.OBS,
                                 sample.WARNINGS, "Australia/Sydney")
now = datetime(2026, 1, 15, 14, 5, tzinfo=ZoneInfo("Australia/Sydney"))
frames = {
    "weather": screens.weather_card(data, now, None),
    "forecast": screens.forecast_card(data),
    "clock": screens.clock_card(now, data),
    "host": screens.host_card("Server", "label", sample.STATS),
    "alert": screens.alert_card(["Server disk 93%"]),
}
for name, image in frames.items():
    raw = rgb565(image)
    (out / f"{name}.raw").write_bytes(raw)
    for wbits in (12, 15):
        packer = zlib.compressobj(9, zlib.DEFLATED, wbits)
        (out / f"{name}.w{wbits}").write_bytes(packer.compress(raw) + packer.flush())
