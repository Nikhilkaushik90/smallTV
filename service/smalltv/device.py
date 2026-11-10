"""HTTP client for the companion firmware running on the display."""
import json
import urllib.request
import uuid


def rgb565(image):
    """Convert a Pillow image to the big-endian RGB565 bytes the firmware expects."""
    raw = image.convert("RGB").tobytes()
    frame = bytearray(len(raw) // 3 * 2)
    for i in range(len(raw) // 3):
        r, g, b = raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]
        value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        frame[i * 2], frame[i * 2 + 1] = value >> 8, value & 0xFF
    return bytes(frame)


class Device:
    def __init__(self, host, timeout=15):
        self.base = f"http://{host}"
        self.timeout = timeout

    def get(self, path):
        with urllib.request.urlopen(f"{self.base}{path}", timeout=self.timeout) as response:
            return response.read()

    def status(self):
        return json.loads(self.get("/status"))

    def set_lcd(self, **values):
        query = "&".join(f"{key}={value}" for key, value in values.items())
        return json.loads(self.get(f"/lcd?{query}"))

    def push(self, image):
        boundary = uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="frame"; filename="frame.rgb565"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n").encode() + rgb565(image) + \
            f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(f"{self.base}/frame", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.read().strip() != b"OK":
                raise RuntimeError("device rejected the frame")
