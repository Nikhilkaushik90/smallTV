# Dashboard service

Renders 240×240 screens and pushes them to a display running the [companion firmware](../firmware/). It has two parts:

| Part | Runs on | Does |
|---|---|---|
| **`smalltv-dashboard`** (this folder) | One always-on machine on the same LAN as the display | Fetches weather, reads server stats, renders the screens, pushes one every few seconds |
| **`collector/`** | Every server you want on the display | Publishes that machine's CPU, memory, disk, GPU and container counts as JSON on port 8766 |

Requirements: Docker with Compose v2. To run without Docker you need Python 3.11+, Pillow and the DejaVu fonts.

## 1. Flash the display

Follow [`firmware/README.md`](../firmware/README.md). When the display shows **WAITING FOR DATA** with its IP address, it's ready.

## 2. Run the collector on each server

```bash
cd service/collector
cp .env.example .env
getent group docker | cut -d: -f3     # put this number in .env as DOCKER_GID
docker compose up -d --build
curl http://localhost:8766/host.json   # should print JSON within ~20 seconds
```

**NVIDIA GPU stats:** the host needs the NVIDIA driver and CDI specs (`nvidia-ctk cdi generate`). Then start with both files:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

**Disk:** by default the root filesystem `/` is reported. On systems where `/` is read-only or tiny (Fedora Atomic desktops, some NAS systems), set `HOST_DISK_PATH` in `.env` to your data disk, for example `/var/home`.

**What it exposes:** the collector container has **no network** and reads `/proc`, the disk and a read-only Docker socket. A second container with no host access publishes only `/host.json`, which holds numbers and the GPU model name, with no container or service names. Set `STATS_BIND` in `.env` to one LAN address to keep it off other interfaces.

## 3. Configure and run the dashboard

```bash
cd service
cp config.example.toml config.toml
```

Edit `config.toml`. At minimum, set `[device] host` and one `[[hosts]]` entry per collector. Every option is explained in [`config.example.toml`](config.example.toml).

Check how the screens look before touching the display. This renders PNGs and doesn't contact the device:

```bash
pip install -r requirements.txt
python -m smalltv --config config.toml preview ./preview
```

Then run it:

```bash
docker compose up -d --build
docker logs -f smalltv-dashboard
```

Other commands:

```bash
python -m smalltv --config config.toml push-once        # push a single screen and exit
python -m smalltv find-location "Parramatta"           # list BOM place names and ids
python -m unittest discover -s tests                   # offline tests
```

## Configuration reference

| Section | Key settings |
|---|---|
| `[device]` | `host` (required), `madctl` (0 for RGB, 8 for BGR), `dwell_seconds` |
| `[display]` | `timezone`, `clock_24h`, `screens` (order of `weather`, `forecast`, `clock`, `hosts`), font paths |
| `[brightness]` | `day`, `night`, `night_start`, `night_end` (hours) |
| `[weather]` | `enabled`, `location`, `state`, optional `place_id` and `station_id`, `refresh_minutes`, `alert_warnings`, `ignore_warnings` |
| `[alerts]` | `disk_percent`, `memory_percent`, `cpu_temp`, `gpu_temp`, `stale_seconds` |
| `[[hosts]]` | `name`, `label`, `url`: `http://host:8766/host.json`, or `file:///path/host.json` if the dashboard can read the file directly |

Unknown settings are rejected at startup with a clear message, so typos don't silently do nothing.

## BOM weather data

Weather comes from the Australian **Bureau of Meteorology**, through the JSON API behind the BOM website (`api.bom.gov.au`). Before you enable it:

- **It isn't a published public API.** BOM documents no terms for it, and it can change or disappear without notice. A related BOM endpoint (`api.weather.bom.gov.au`) returns the notice *"This application programming interface (API) is owned by the Bureau of Meteorology. You must not use, copy or share it."* Check BOM's [copyright](https://www.bom.gov.au/copyright) and [data services](https://www.bom.gov.au/resources/data-services) pages yourself and decide whether your use is acceptable.
- **Keep it personal and gentle.** The default fetches twice an hour (about 5 requests each time) and caches place details for a day. Don't lower `refresh_minutes` to poll aggressively.
- **Alternative:** BOM publishes public forecast and observation products on its anonymous FTP server (`ftp://ftp.bom.gov.au/anon/gen/fwo/`), for example state town forecasts (`IDx10044`-style files) and state observations (`IDx60920`). They're coarser (town level), but a supported way to get the data. A provider for them would slot into `smalltv/bom.py`.
- **Australia only.** Elsewhere, set `[weather] enabled = false` and the weather screens are skipped.

Forecast numbers come from BOM's grid for your place. Current conditions come from the nearest BOM station (`station_id` overrides it). Text forecasts are BOM's for the surrounding district.

## Adding a screen

Screens are plain functions in [`smalltv/screens.py`](smalltv/screens.py) that return a 240×240 Pillow image. Add one, register its name in `SCREENS` in `config.py` and in `Dashboard.screens()` in `app.py`, then run `preview` to see it. Keep content above row 222, because the SmallTV Ultra case hides the bottom rows.

## Troubleshooting

| Symptom | Check |
|---|---|
| Logs show `cycle failed: ... No route to host` | The display is off or unreachable: power, Wi-Fi, or a wrong `[device] host` |
| A server screen shows **No data** and an alert | `curl http://<server>:8766/host.json` from the dashboard machine. Check the collector logs and firewall |
| GPU row missing | Start the collector with `docker-compose.gpu.yml`; check `nvidia-smi` works on the host |
| `weather update skipped` | BOM unreachable or changed. The last good data stays on screen. Check with `find-location` |
| Wrong colours | Change `[device] madctl` between `0` and `8` |
