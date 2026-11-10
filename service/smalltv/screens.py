"""Every 240x240 screen. Content stays above draw.SAFE_BOTTOM because the case hides the lowest rows."""
import math
import re
from zoneinfo import ZoneInfo

from .draw import AMBER, GREEN, RED, SIZE, SOFT, WHITE, canvas, finish, fit, font, icon, whole

# BOM forecast icon code -> art in icons.svg. Codes 1, 2, 3, 4, 9, 11, 16 and 17 were checked
# against real BOM forecast text; the rest follow the commonly documented BOM codes.
ART_DAY = {1: "icon-1", 2: "icon-1", 3: "icon-3", 4: "icon-5", 6: "icon-6", 8: "icon-10",
           9: "wind", 10: "icon-7", 11: "icon-11", 12: "icon-12", 13: "icon-13",
           14: "icon-14", 15: "icon-15", 16: "icon-16", 17: "icon-9", 18: "icon-12", 19: "icon-16"}
ART_NIGHT = {1: "moon", 2: "moon", 3: "moon-cloud"}

SKY = {"sunny": ("#01579b", "#0288d1"), "part": ("#1565c0", "#455a64"), "cloud": ("#37474f", "#546e7a"),
       "rain": ("#1a237e", "#263238"), "storm": ("#0d0d1a", "#1a1a2e"), "fog": ("#546e7a", "#78909c"),
       "snow": ("#1a237e", "#4fc3f7"), "dust": ("#4e342e", "#bf360c"), "night": ("#0b1a33", "#1b2a41")}
SKY_BY_CODE = {1: "sunny", 2: "sunny", 3: "part", 4: "cloud", 6: "sunny", 8: "rain", 9: "part", 10: "fog",
               11: "rain", 12: "rain", 13: "dust", 14: "snow", 15: "snow", 16: "storm", 17: "rain",
               18: "rain", 19: "storm"}
SLATE = ("#10202f", "#22303f")


def is_night(lat, lon, moment):
    """True when the sun is below the horizon at this location."""
    if lat is None or lon is None:
        return moment.hour >= 19 or moment.hour < 6
    utc = moment.astimezone(ZoneInfo("UTC"))
    day = utc.timetuple().tm_yday
    b = 2 * math.pi * (day - 81) / 364
    equation_of_time = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    solar_hours = utc.hour + utc.minute / 60 + lon / 15 + equation_of_time / 60
    hour_angle = math.radians(15 * (solar_hours - 12))
    declination = math.radians(-23.44 * math.cos(2 * math.pi * (day + 10) / 365))
    latitude = math.radians(lat)
    elevation = math.asin(math.sin(latitude) * math.sin(declination)
                          + math.cos(latitude) * math.cos(declination) * math.cos(hour_angle))
    return math.degrees(elevation) < -0.83


def _stat(d, x, y, width, label, value):
    d.text((x, y), label, font=font(11, True), fill=(255, 255, 255, 170))
    text, f = fit(d, value, 19, width, bold=True)
    d.text((x, y + 13), text, font=f, fill="white")


def weather_card(data, now, ignore_warnings=None):
    cur, today = data.get("current") or {}, (data.get("daily") or [{}])[0]
    code = today.get("icon_code")
    night = is_night(data.get("lat"), data.get("lon"), now)
    art = (ART_NIGHT.get(code) if night else None) or ART_DAY.get(code)
    card, layer, d = canvas(*(SKY["night"] if night else SKY[SKY_BY_CODE.get(code, "part")]))

    label, f = fit(d, data.get("location_name", ""), 15, 158, bold=True)
    d.text((12, 8), label, font=f, fill=WHITE)
    d.text((228, 9), (cur.get("obs_time") or "").replace(" ", "").lower(), font=font(13), fill=SOFT, anchor="ra")
    source, f = fit(d, f"{cur.get('station_name') or 'no observations'} · BOM", 11, 95)
    d.text((12, 25), source, font=f, fill=SOFT)
    if art:
        layer.alpha_composite(icon(art, 80), (10, 40))

    d.text((230, 26), whole(cur.get("temp"), "°"), font=font(68, True), fill=WHITE, anchor="ra")
    d.text((230, 104), f"feels {whole(cur.get('feels_like'), '°')}", font=font(16), fill=SOFT, anchor="ra")
    d.text((228, 127), f"Max {whole(today.get('temp_max'), '°')}", font=font(16, True), fill=WHITE, anchor="ra")
    precis, f = fit(d, (today.get("precis") or "").rstrip("."), 18, 138, bold=True, minimum=12)
    d.text((12, 126), precis, font=f, fill=WHITE)
    d.line((12, 151, 228, 151), fill=(255, 255, 255, 80), width=1)

    wind = "--" if cur.get("wind_kmh") is None else f"{cur.get('wind_dir', '')} {whole(cur['wind_kmh'])}".strip()
    _stat(d, 12, 154, 108, "WIND KM/H", wind)
    _stat(d, 126, 154, 102, "GUST KM/H", whole(cur.get("wind_gust_kmh")))

    ignored = re.compile(ignore_warnings, re.I) if ignore_warnings else None
    alerts = [w["title"] for w in data.get("warnings") or [] if not (ignored and ignored.search(w.get("title", "")))]
    if alerts:
        d.rectangle((0, 190, SIZE, 222), fill=AMBER)
        text, f = fit(d, "⚠ " + alerts[0], 14, 228, bold=True, minimum=10)
        d.text((6, 198), text, font=f, fill=(20, 20, 20, 255))
    else:
        _stat(d, 12, 187, 108, "HUMIDITY", whole(cur.get("humidity"), "%"))
        _stat(d, 126, 187, 102, "RAIN CHANCE", whole(today.get("rain_prob"), "%"))
    return finish(card, layer)


def forecast_card(data):
    card, layer, d = canvas(*SLATE)
    d.text((12, 8), "Forecast", font=font(17, True), fill=WHITE)
    label, f = fit(d, data.get("location_name", ""), 12, 120)
    d.text((228, 12), label, font=f, fill=SOFT, anchor="ra")
    for i, day in enumerate((data.get("daily") or [])[1:6]):
        y = 34 + i * 37
        d.line((12, y - 3, 228, y - 3), fill=(255, 255, 255, 45), width=1)
        d.text((12, y + 5), day.get("day_name", ""), font=font(17, True), fill=WHITE)
        art = ART_DAY.get(day.get("icon_code"))
        if art:
            layer.alpha_composite(icon(art, 32), (56, y))
        d.text((96, y + 8), whole(day.get("rain_prob"), "%"), font=font(14), fill=(144, 202, 249, 255))
        d.text((186, y + 4), whole(day.get("temp_max"), "°"), font=font(20, True), fill=WHITE, anchor="ra")
        d.text((228, y + 7), whole(day.get("temp_min"), "°"), font=font(16), fill=SOFT, anchor="ra")
    return finish(card, layer)


def clock_card(now, data=None, clock_24h=False):
    card, layer, d = canvas("#0b1a33", "#16324f")
    text = now.strftime("%H:%M") if clock_24h else now.strftime("%I:%M").lstrip("0")
    text, f = fit(d, text, 74, 210 if clock_24h else 170, bold=True, minimum=40)
    d.text((12, 46), text, font=f, fill=WHITE)
    if not clock_24h:
        d.text((228, 94), now.strftime("%p").lower(), font=font(26, True), fill=SOFT, anchor="ra")
    d.text((12, 136), now.strftime("%A"), font=font(22, True), fill=WHITE)
    d.text((12, 166), f"{now.day} {now.strftime('%B %Y')}", font=font(18), fill=SOFT)
    if data and (data.get("current") or {}).get("temp") is not None:
        line, f = fit(d, f"{whole(data['current']['temp'], '°')} {data.get('location_name', '')}", 16, 216, bold=True)
        d.text((12, 198), line, font=f, fill=WHITE)
    return finish(card, layer)


def _level(value, warn, bad):
    return RED if value >= bad else AMBER if value >= warn else GREEN


def _meter(d, y, label, value, detail, warn=70, bad=90):
    d.text((12, y), label, font=font(14, True), fill=SOFT)
    d.text((228, y - 3), f"{whole(value)}%", font=font(19, True), fill=WHITE, anchor="ra")
    if detail:
        d.text((58, y + 2), detail, font=font(12), fill=SOFT)
    d.rounded_rectangle((12, y + 21, 228, y + 29), radius=4, fill=(255, 255, 255, 40))
    width = max(8, round(216 * min(value, 100) / 100))
    d.rounded_rectangle((12, y + 21, 12 + width, y + 29), radius=4, fill=_level(value, warn, bad))


def _uptime(seconds):
    days, hours = seconds // 86400, seconds % 86400 // 3600
    return f"{days}d {hours}h" if days else f"{hours}h"


def host_card(name, label, stats):
    card, layer, d = canvas(*SLATE)
    title, f = fit(d, name, 18, 150, bold=True)
    d.text((12, 8), title, font=f, fill=WHITE)
    d.text((228, 11), label, font=font(13), fill=SOFT, anchor="ra")
    if not stats:
        d.text((12, 96), "No data", font=font(30, True), fill=AMBER)
        d.text((12, 136), "Stats collector not reporting", font=font(13), fill=SOFT)
        return finish(card, layer)

    gpu = stats.get("gpu")
    top, step = (33, 32) if gpu else (36, 40)
    _meter(d, top, "CPU", stats["cpu"], "")
    _meter(d, top + step, "MEM", stats["mem_percent"], f"{stats['mem_used_gb']:.0f}/{stats['mem_total_gb']:.0f} GB")
    _meter(d, top + step * 2, "DISK", stats["disk_percent"], f"{stats['disk_used_gb']}/{stats['disk_total_gb']} GB", 80, 90)
    if gpu:
        _meter(d, top + step * 3, "GPU", gpu["util"], f"{gpu['temp']}°  {gpu['mem_used_mb']}/{gpu['mem_total_mb']} MB")
    base = 162 if gpu else 154
    d.line((12, base, 228, base), fill=(255, 255, 255, 60), width=1)

    if stats.get("running") is None:
        d.text((12, base + 12), "Docker not available", font=font(14), fill=SOFT)
    else:
        failing = stats.get("failing", 0)
        d.text((12, base + 5), "CONTAINERS", font=font(11, True), fill=SOFT)
        d.text((12, base + 18), f"{stats['running']} up", font=font(22, True), fill=WHITE)
        x = 12 + d.textlength(f"{stats['running']} up", font=font(22, True)) + 10
        note, color = (f"{failing} failing", RED) if failing else (f"{stats.get('stopped', 0)} stopped", SOFT)
        d.text((x, base + 26), note, font=font(14, bool(failing)), fill=color)
    temp = f"{stats['temp']}°" if stats.get("temp") is not None else "--"
    d.text((12, base + 45), f"{temp}  ·  up {_uptime(stats['uptime_s'])}", font=font(13), fill=SOFT)
    return finish(card, layer)


def _wrap(d, text, f, width):
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if d.textlength(candidate, font=f) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    return lines + ([current] if current else [])


def alert_card(alerts):
    card, layer, d = canvas("#8e0000", "#c62828")
    d.text((12, 10), "⚠ ALERT", font=font(26, True), fill=WHITE)
    if len(alerts) > 1:
        d.text((228, 19), f"{len(alerts)} active", font=font(13), fill=SOFT, anchor="ra")
    y, f = 50, font(19, True)
    for alert in alerts[:3]:
        for line in _wrap(d, alert, f, 216)[:3]:
            if y > 196:
                return finish(card, layer)
            d.text((12, y), line, font=f, fill=WHITE)
            y += 25
        y += 8
    return finish(card, layer)
