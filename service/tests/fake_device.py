"""A stand-in for the display firmware, for tests and UI work without real hardware.

It answers /status, /lcd, /lcd/init, /test and /reboot, and accepts frames on the fast port,
decoding them into an in-memory 240x240 RGB565 screen.

    python tests/fake_device.py 18080      # HTTP on 18080, fast port on 18081
"""
import json
import socket
import struct
import sys
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIZE = 240


class FakeDevice:
    def __init__(self, http_port=0, frame_port=0):
        self.screen = bytearray(SIZE * SIZE * 2)
        self.state = {"brightness": 54, "madctl": 0, "frames": 0, "has_frame": False}
        self.regions = []
        self.lock = threading.Lock()
        self.frames = socket.socket()
        self.frames.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.frames.bind(("127.0.0.1", frame_port))
        self.frames.listen(4)
        self.frame_port = self.frames.getsockname()[1]
        self.http = ThreadingHTTPServer(("127.0.0.1", http_port), self._handler())
        self.http_port = self.http.server_address[1]

    def start(self):
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        threading.Thread(target=self._accept, daemon=True).start()
        return self

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.frames.close()

    def status(self):
        return {"model": "Fake", "firmware": "custom-fake", "uptime_s": 120, "heap": 42000, "rssi": -48,
                "ip": "127.0.0.1", "frame_port": self.frame_port, "codecs": "raw,zlib", "lcd_size": SIZE,
                "invert": True, "safe_mode": False, "crashes": 0, "reset_reason": "Fake", **self.state}

    def _handler(self):
        device = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path, _, query = self.path.partition("?")
                if path == "/lcd":
                    for pair in filter(None, query.split("&")):
                        key, _, value = pair.partition("=")
                        if key in ("brightness", "madctl"):
                            device.state[key] = int(value)
                elif path in ("/lcd/init", "/reboot", "/test"):
                    device.state["has_frame"] = False
                elif path != "/status":
                    self.send_error(404)
                    return
                body = json.dumps(device.status()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def _accept(self):
        while True:
            try:
                conn, _ = self.frames.accept()
            except OSError:
                return
            with conn:
                self._frame(conn)

    def _frame(self, conn):
        data = b""
        while len(data) < 18:
            chunk = conn.recv(65536)
            if not chunk:
                return
            data += chunk
        magic, encoding, _, x, y, w, h, length = struct.unpack(">4sBBHHHHI", data[:18])
        payload = data[18:]
        while len(payload) < length:
            chunk = conn.recv(65536)
            if not chunk:
                return
            payload += chunk
        pixels = zlib.decompress(payload) if encoding == 1 else payload
        if magic != b"STV1" or len(pixels) != w * h * 2 or x + w > SIZE or y + h > SIZE:
            conn.sendall(b"ERR bad frame\n")
            return
        with self.lock:
            for row in range(h):
                start = ((y + row) * SIZE + x) * 2
                self.screen[start:start + w * 2] = pixels[row * w * 2:(row + 1) * w * 2]
            self.regions.append((x, y, w, h))
            self.state["frames"] += 1
            if (x, y, w, h) == (0, 0, SIZE, SIZE):
                self.state["has_frame"] = True
        conn.sendall(b"OK\n")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    fake = FakeDevice(port, port + 1).start()
    print(f"fake display: http://127.0.0.1:{fake.http_port}  fast port {fake.frame_port}", flush=True)
    threading.Event().wait()
