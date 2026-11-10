# Firmware

Minimal replacement firmware for the **GeekMagic SmallTV Ultra** (ESP8266, ST7789V 240x240). It joins your Wi-Fi, shows a status screen, and then displays whatever frames the [dashboard service](../service/) pushes to it.

- **No display library:** frames arrive as raw pixels, so the firmware only needs the ESP8266 Arduino core.
- **Never touches the filesystem:** the stock firmware's files stay on flash, so restoring stock gives you its pages and photos back.
- **Wi-Fi setup hotspot:** opens when no saved network can be joined.
- **Wi-Fi firmware updates** at `/update`, including in safe mode.
- **Safe mode:** after 3 crashes in a row the screen is skipped and only Wi-Fi plus the update page run.

## Before you flash

> **Risk:** the SmallTV Ultra's USB-C port carries power only. If the device stops booting, the only way back is soldering a 3.3 V USB-serial adapter to the board's TX, RX, GND, 3V3 and GPIO0 pads. Thousands of these devices are identical, but yours could be a revision with different wiring. Flash at your own risk.

1. **Note your stock firmware version** from the stock settings page (it looks like `Ultra-V9.x.xx`). Download the matching `.bin` from [GeekMagicClock/smalltv-ultra](https://github.com/GeekMagicClock/smalltv-ultra) and keep it. That's your way back to stock.
2. **Save your photos and GIFs** from the stock web page if you want copies off the device.
3. **Check your model.** These defaults are for the SmallTV **Ultra** (ESP8266). The SmallTV **Pro** uses an ESP32 and won't run this firmware.

## Build

The pinned toolchain is in [`platformio.ini`](platformio.ini).

**With PlatformIO installed:**

```bash
cd firmware
pio run -e smalltv-ultra
# Output: .pio/build/smalltv-ultra/firmware.bin
```

**With Docker only:** builds in a throwaway container that can see only this folder.

```bash
cd firmware
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/work/.home \
  -e PLATFORMIO_CORE_DIR=/work/.pio-core -v "$PWD:/work" -w /work python:3.12-slim \
  bash -c 'python -m venv .venv && .venv/bin/pip -q install platformio==6.1.18 && .venv/bin/pio run -e smalltv-ultra'
```

The first build downloads the toolchain into `.pio-core/`. Later builds can run with `--network none`.

Check the image header before flashing: `head -c 4 .pio/build/smalltv-ultra/firmware.bin | od -An -tx1` should print `e9 02 02 40`, matching the stock firmware (4 MB, DIO, 40 MHz).

## Flash over Wi-Fi

The stock firmware accepts uploads at `/update`. Use its web page (**Settings → Firmware update**), or:

```bash
curl -F "firmware=@.pio/build/smalltv-ultra/firmware.bin" http://<display-ip>/update
```

The display restarts after about 10 seconds. Later updates use the same command against this firmware.

## Connect to Wi-Fi

After the first flash the firmware may not find your Wi-Fi, because the stock firmware stores it differently. If it can't connect within 45 seconds, the screen shows **JOIN WIFI**.

1. On your phone, join the open network **`SmallTV-Setup`**.
2. Open **`http://10.42.0.1`**.
3. Enter your Wi-Fi name and password and press **Save**. Only 2.4 GHz networks work.

The screen then shows the IP address it received and **WAITING FOR DATA** until the dashboard service starts pushing. The hotspot closes a minute after it joins. If the Wi-Fi link is lost for 5 minutes, the hotspot opens again.

## HTTP API

| Method and path | What it does |
|---|---|
| `GET /` | Status page; Wi-Fi form while the hotspot is active |
| `GET /status` | JSON: firmware, uptime, heap, RSSI, IP, frames shown, `has_frame`, brightness, `madctl`, crashes, reset reason |
| `POST /frame` | Multipart upload of exactly 240 × 240 × 2 = 115,200 bytes of **big-endian RGB565**, drawn top to bottom as it arrives |
| `GET /lcd?brightness=0-100&madctl=0\|8&inv=0\|1` | Brightness and colour settings; returns status JSON |
| `GET /lcd/init` | Re-initialise the screen controller |
| `GET /test` | Red, green, blue and white bars |
| `GET /reboot` | Restart |
| `GET`/`POST /update` | Firmware upload |
| `POST /wifi` | Save Wi-Fi details; setup hotspot only |

`has_frame` is `false` after a restart or `/lcd/init`, so a pushing service can resend its latest frame straight away. The dashboard service does this every cycle.

## Build options

Override any of these with `build_flags` in your own `[env:...]` in `platformio.ini`. See the `custom` example there.

| Flag | Default | Meaning |
|---|---|---|
| `PIN_DC` | `0` | Data/command pin |
| `PIN_RST` | `2` | Panel reset pin |
| `PIN_BACKLIGHT` | `5` | Backlight PWM pin |
| `BACKLIGHT_ACTIVE_LOW` | `1` | `1` when low means on (P-MOSFET), as on the Ultra |
| `LCD_SIZE` | `240` | Square panel size in pixels |
| `SPI_HZ` | `20000000` | SPI clock; the stock firmware uses about 20 MHz |
| `DEFAULT_MADCTL` | `0x00` | `0x00` for RGB order, `0x08` for BGR |
| `DEFAULT_INVERT` | `1` | Colour inversion, required by the Ultra's panel |
| `DEFAULT_BRIGHTNESS` | `60` | Brightness at boot, 0–100 |
| `SETUP_SSID` | `"SmallTV-Setup"` | Setup hotspot name |
| `SETUP_AP_IP` | `"10.42.0.1"` | Hotspot address; change it if it overlaps your network |
| `DEVICE_HOSTNAME` | `"smalltv"` | DHCP hostname |
| `BOARD_NAME` | `"SmallTV-Ultra"` | Model string in `/status` |

The SmallTV Ultra wiring is: SPI clock GPIO14, SPI data GPIO13, data/command GPIO0, reset GPIO2, backlight GPIO5 (active low), no chip select (SPI mode 3).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Red and blue swapped (sun looks blue) | Set `madctl = 8` or `0` in the service config, or `curl "http://<ip>/lcd?madctl=8"` to test |
| Screen dark after power-on, but `/status` answers | `curl http://<ip>/lcd/init`. The firmware already re-initialises the panel once Wi-Fi connects, which fixed this on the original device; if yours still needs it, try a stronger USB power supply |
| Bottom few pixel rows hidden | That's the case bezel. The service keeps content above row 222 |
| Screen shows **JOIN WIFI** | Wi-Fi details are missing or wrong. See [Connect to Wi-Fi](#connect-to-wi-fi) |
| Screen dark and `/status` doesn't answer | Look for the `SmallTV-Setup` hotspot. If it's in safe mode, upload firmware at `http://10.42.0.1/update` |

## Restore the stock firmware

Unzip GeekMagic's file for your exact version and upload the `.bin`:

```bash
curl -F "firmware=@FW-Smalltv-Ultra-<your-version>.bin" http://<display-ip>/update
```

Your old photos and settings return, because this firmware never touched them.
