"""Drawing helpers shared by every screen: fonts, text fitting, gradients and the SVG weather icons."""
import functools
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 240
SAFE_BOTTOM = 222  # The SmallTV Ultra case hides the lowest rows of the panel.
ICONS = Path(__file__).resolve().parent / "icons.svg"
SVG = "{http://www.w3.org/2000/svg}"

WHITE = (255, 255, 255, 255)
SOFT = (255, 255, 255, 190)
GREEN, AMBER, RED = (67, 180, 96, 255), (255, 179, 0, 255), (229, 57, 53, 255)

THEMES = ("gradient", "dark")
_theme = {"name": "gradient"}

_fonts = {"regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
          "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"}

EXTRA_ART = """
<symbol xmlns="http://www.w3.org/2000/svg" id="moon" viewBox="0 0 100 100">
  <path d="M62 12 Q20 16 20 50 Q20 84 62 88 Q40 70 40 50 Q40 30 62 12Z" fill="#FFE082"/>
</symbol>
<symbol xmlns="http://www.w3.org/2000/svg" id="moon-cloud" viewBox="0 0 100 100">
  <path d="M46 10 Q14 14 14 40 Q14 66 46 70 Q30 56 30 40 Q30 24 46 10Z" fill="#FFE082"/>
  <path d="M30 75 Q30 64 40 64 Q40 54 52 56 Q64 54 64 65 Q70 65 70 73 Q70 82 61 82 L39 82 Q30 82 30 75Z" fill="#90A4AE"/>
</symbol>
<symbol xmlns="http://www.w3.org/2000/svg" id="wind" viewBox="0 0 100 100">
  <g stroke="#CFD8DC" stroke-width="6" stroke-linecap="round">
    <line x1="14" y1="34" x2="70" y2="34"/><line x1="22" y1="52" x2="86" y2="52"/><line x1="14" y1="70" x2="62" y2="70"/>
  </g>
</symbol>
"""


def set_theme(name):
    """"gradient" draws coloured skies; "dark" uses plain black, which also compresses far better."""
    if name not in THEMES:
        raise ValueError(f"unknown theme {name!r}, choose from {THEMES}")
    _theme["name"] = name


def set_fonts(regular, bold):
    for path in (regular, bold):
        if not Path(path).is_file():
            raise FileNotFoundError(f"font not found: {path} (set font_regular / font_bold in [display])")
    _fonts.update(regular=regular, bold=bold)
    font.cache_clear()


@functools.lru_cache(maxsize=64)
def font(size, bold=False):
    return ImageFont.truetype(_fonts["bold" if bold else "regular"], size)


def fit(draw, text, size, width, bold=False, minimum=11):
    """Shrink text to fit a width, then trim it with an ellipsis if it is still too long."""
    while size > minimum and draw.textlength(text, font=font(size, bold)) > width:
        size -= 1
    f = font(size, bold)
    if draw.textlength(text, font=f) <= width:
        return text, f
    while text and draw.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text.rstrip() + "…", f


def whole(value, suffix=""):
    return "--" if value is None else f"{int(float(value) + 0.5) if value >= 0 else round(value)}{suffix}"


def sky(top, bottom):
    gradient = Image.linear_gradient("L").resize((SIZE, SIZE))
    return Image.composite(Image.new("RGB", (SIZE, SIZE), bottom), Image.new("RGB", (SIZE, SIZE), top), gradient)


def canvas(top, bottom, keep_colour=False):
    if _theme["name"] == "dark" and not keep_colour:
        card = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255))
    else:
        card = sky(top, bottom).convert("RGBA")
    layer = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return card, layer, ImageDraw.Draw(layer)


def finish(card, layer):
    card.alpha_composite(layer)
    return card.convert("RGB")


# ---- SVG icons: a tiny rasterizer for the shapes used in icons.svg ----

@functools.lru_cache(maxsize=1)
def symbols():
    root = ET.fromstring(ICONS.read_text(encoding="utf-8"))
    found = {s.get("id"): s for s in root.iter(f"{SVG}symbol")}
    found.update({s.get("id"): s for s in ET.fromstring(f"<x>{EXTRA_ART}</x>").iter(f"{SVG}symbol")})
    return found


def _rgba(hex_color, opacity):
    value = hex_color.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4)) + (round(255 * opacity),)


def _path_points(d):
    tokens = re.findall(r"[MQLZ]|-?\d+\.?\d*", d)
    points, i, command = [], 0, None
    while i < len(tokens):
        if tokens[i] in "MQLZ":
            command = tokens[i]
            i += 1
            continue
        if command in ("M", "L"):
            points.append((float(tokens[i]), float(tokens[i + 1])))
            i += 2
        elif command == "Q":
            x0, y0 = points[-1]
            cx, cy, x1, y1 = map(float, tokens[i:i + 4])
            for n in range(1, 13):
                t = n / 12
                points.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * cx + t * t * x1,
                               (1 - t) ** 2 * y0 + 2 * (1 - t) * t * cy + t * t * y1))
            i += 4
        else:
            i += 1
    return points


def _draw_element(el, draw, k, inherited):
    style = {**inherited, **{a: el.get(a) for a in ("fill", "stroke", "stroke-width", "opacity") if el.get(a)}}
    tag = el.tag.replace(SVG, "")
    opacity = float(style.get("opacity", 1))
    if tag == "g":
        for child in el:
            _draw_element(child, draw, k, style)
    elif tag == "circle":
        cx, cy, r = (float(el.get(a)) * k for a in ("cx", "cy", "r"))
        fill = _rgba(style["fill"], opacity) if style.get("fill") else None
        outline = _rgba(style["stroke"], opacity) if style.get("stroke") else None
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=outline,
                     width=round(float(style.get("stroke-width", 0)) * k))
    elif tag == "line":
        x1, y1, x2, y2 = (float(el.get(a)) * k for a in ("x1", "y1", "x2", "y2"))
        color, width = _rgba(style["stroke"], opacity), float(style.get("stroke-width", 1)) * k
        draw.line((x1, y1, x2, y2), fill=color, width=round(width))
        for x, y in ((x1, y1), (x2, y2)):
            draw.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=color)
    elif tag == "path":
        draw.polygon([(x * k, y * k) for x, y in _path_points(el.get("d"))], fill=_rgba(style["fill"], opacity))
    elif tag == "polygon":
        nums = list(map(float, re.findall(r"-?\d+\.?\d*", el.get("points"))))
        draw.polygon([(x * k, y * k) for x, y in zip(nums[::2], nums[1::2])], fill=_rgba(style["fill"], opacity))


@functools.lru_cache(maxsize=64)
def icon(name, size):
    """Render a symbol from icons.svg at 4x and scale down for smooth edges."""
    canvas_ = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas_)
    for child in symbols()[name]:
        _draw_element(child, draw, size * 4 / 100, {})
    return canvas_.resize((size, size), Image.LANCZOS)
