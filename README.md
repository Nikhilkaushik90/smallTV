# SmallTV Dashboard

Turn a GeekMagic **SmallTV Ultra** (the small ESP8266 desk display sold on AliExpress) into a home lab and weather dashboard you control end to end.

| Folder | What it is |
|---|---|
| [`firmware/`](firmware/) | Replacement firmware for the display. It shows whatever image a server pushes to it, has a Wi-Fi setup hotspot, over-the-air updates and a crash-safe recovery mode. |
| [`service/`](service/) | A small Python service, packaged for Docker, that renders the screens and pushes them to the display. Includes a stats collector to run on each server you want to monitor. |

## What it shows

Screens rotate every 15 seconds by default, and every part is configurable:

- **Weather now:** temperature, feels-like, wind, gusts, humidity and rain chance, from the Australian Bureau of Meteorology (BOM)
- **Forecast:** the next 5 days
- **Clock:** 12- or 24-hour, with the date
- **One screen per server:** CPU, memory, disk, NVIDIA GPU, container counts, CPU temperature and uptime
- **Alert screen:** appears between the others when a disk is nearly full, a machine runs hot, a server stops reporting, or BOM issues a severe warning

## How it fits together

```
 each server                         any always-on machine                the display
┌──────────────────────┐  HTTP     ┌──────────────────────────┐  HTTP    ┌──────────────────┐
│ smalltv-stats        │ ───────▶  │ smalltv-dashboard        │ ───────▶ │ custom firmware   │
│  (no network)        │ :8766     │  renders 240x240 screens │ /frame   │  draws the frames │
│ smalltv-stats-web    │ host.json │  + BOM weather           │          │  on its ST7789    │
└──────────────────────┘           └──────────────────────────┘          └──────────────────┘
```

The display does no rendering itself. That keeps the firmware tiny, and every new screen is a few lines of Python with no reflashing. Frames go straight to the screen's memory, so frequent updates cause no flash wear.

## Quick start

1. **Flash the firmware.** This replaces the stock firmware, so read [`firmware/README.md`](firmware/README.md) first.
2. **Connect the display to Wi-Fi** through its `SmallTV-Setup` hotspot.
3. **Run the collector** on each server: [`service/README.md`](service/README.md#2-run-the-collector-on-each-server).
4. **Configure and start the dashboard:** [`service/README.md`](service/README.md#3-configure-and-run-the-dashboard).

## Important notes

- **No USB recovery on the SmallTV Ultra:** its USB-C port only supplies power. If a flash goes wrong, recovery means soldering a 3.3 V USB-serial adapter to pads on the board. The firmware is built to avoid that: it keeps a Wi-Fi update page and has a safe mode after repeated crashes.
- **Stock firmware can be restored** through the same Wi-Fi update page. Download the build that matches your model from [GeekMagic's repository](https://github.com/GeekMagicClock/smalltv-ultra) before you start.
- **Weather is Australia-only (BOM).** It uses the JSON API behind the BOM website, which is not a published public API. Read the [BOM notes](service/README.md#bom-weather-data) before you enable it.
- **Your LAN is the security boundary:** the firmware's update and frame endpoints have no authentication, like the stock firmware. Keep the display on a trusted network.

## Credits

- Display wiring from the [ESPHome device page](https://devices.esphome.io/devices/geekmagic-ultra/) and the [Home Assistant community thread](https://community.home-assistant.io/t/installing-esphome-on-geekmagic-smart-weather-clock-smalltv-pro/618029), confirmed on real hardware.
- The BOM client and weather icons come from an earlier BOM weather page by Nikhil Kaushik.

## License

MIT. See [LICENSE](LICENSE). GeekMagic and SmallTV are trademarks of their owners; this project is not affiliated with them.
