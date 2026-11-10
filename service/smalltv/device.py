"""Client for the companion firmware running on the display."""
import json
import socket
import struct
import urllib.request
import uuid
import zlib

from PIL import Image, ImageChops

# Lookup tables for RGB565: high byte RRRRRGGG, low byte GGGBBBBB. The channel bits never
# overlap, so adding the two parts equals OR-ing them, and Pillow does it all in C.
_HIGH_R = [v & 0xF8 for v in range(256)]
_HIGH_G = [v >> 5 for v in range(256)]
_LOW_G = [(v & 0x1C) << 3 for v in range(256)]
_LOW_B = [v >> 3 for v in range(256)]


def rgb565(image):
    """Convert a Pillow image to the big-endian RGB565 bytes the firmware expects."""
    r, g, b = image.convert("RGB").split()
    high = ImageChops.add(r.point(_HIGH_R), g.point(_HIGH_G))
    low = ImageChops.add(g.point(_LOW_G), b.point(_LOW_B))
    return Image.merge("LA", (high, low)).tobytes()


# The firmware's inflater keeps a 4 KB window, so frames must be compressed with wbits <= 12.
ZLIB_WBITS = 12
ZLIB_LEVEL = 6


def encode_frame(pixels, width, height, x=0, y=0, compress=True):
    """Build a fast-port message: 18-byte header ("STV1", encoding, x, y, w, h, length) plus payload."""
    if compress:
        packer = zlib.compressobj(ZLIB_LEVEL, zlib.DEFLATED, ZLIB_WBITS)
        payload = packer.compress(pixels) + packer.flush()
    else:
        payload = pixels
    header = b"STV1" + struct.pack(">BBHHHHI", 1 if compress else 0, 0, x, y, width, height, len(payload))
    return header + payload


class Device:
    def __init__(self, host, timeout=15):
        self.host = host
        self.base = f"http://{host}"
        self.timeout = timeout
        self.frame_port = None  # Learned from /status; firmware 0.3.0 and later

    def get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=self.timeout) as response:
            return response.read()

    def status(self):
        status = json.loads(self.get("/status"))
        self.frame_port = status.get("frame_port") if "zlib" in status.get("codecs", "") else None
        return status

    def set_lcd(self, **values):
        query = "&".join(f"{key}={value}" for key, value in values.items())
        return json.loads(self.get(f"/lcd?{query}"))

    def push(self, image):
        """Send a full-screen image, compressed over the fast port when the firmware supports it."""
        if self.frame_port:
            self.push_fast(rgb565(image), *image.size)
        else:
            self.push_http(image)

    def push_fast(self, pixels, width, height):
        message = encode_frame(pixels, width, height)
        with socket.create_connection((self.host, self.frame_port), timeout=self.timeout) as conn:
            conn.sendall(message)
            reply = conn.recv(64)
        if not reply.startswith(b"OK"):
            raise RuntimeError(f"device rejected the frame: {reply.decode(errors='replace').strip() or 'no reply'}")

    def push_http(self, image):
        """Multipart upload understood by every firmware version."""
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="frame"; filename="frame.rgb565"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n").encode() + rgb565(image) + \
            f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(f"{self.base}/frame", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.read().strip() != b"OK":
                raise RuntimeError("device rejected the frame")
