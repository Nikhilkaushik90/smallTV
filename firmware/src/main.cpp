// Display firmware for the GeekMagic SmallTV Ultra and similar ESP8266 + ST7789 240x240 boards.
//
// The device shows frames that a server renders and pushes to it. It never mounts or formats
// the flash filesystem, so a stock firmware's files survive and the stock firmware can be
// restored through /update.
//
// Endpoints:  GET /  /status  /test  /lcd?brightness=&inv=&madctl=  /lcd/init  /reboot
//             POST /frame   multipart upload of LCD_SIZE*LCD_SIZE*2 bytes, big-endian RGB565
//             TCP FRAME_PORT  fast path: 18-byte header, then raw or zlib RGB565 (see README)
//             /update       firmware upload (browser form or curl -F firmware=@file.bin)
//             POST /wifi    save Wi-Fi details, only while the setup hotspot is active
//
// Every board-specific value below can be overridden with build_flags in platformio.ini.

#include <ESP8266HTTPUpdateServer.h>
#include <ESP8266WebServer.h>
#include <ESP8266WiFi.h>
#include <SPI.h>
#include <user_interface.h>

#include "frame_codec.h"

#ifndef FW_VERSION
#define FW_VERSION "dev"
#endif
#ifndef BOARD_NAME
#define BOARD_NAME "SmallTV-Ultra"
#endif

// Wiring. Defaults are the SmallTV Ultra: SPI clock GPIO14, data GPIO13, no chip select.
#ifndef PIN_DC
#define PIN_DC 0
#endif
#ifndef PIN_RST
#define PIN_RST 2
#endif
#ifndef PIN_BACKLIGHT
#define PIN_BACKLIGHT 5
#endif
#ifndef BACKLIGHT_ACTIVE_LOW
#define BACKLIGHT_ACTIVE_LOW 1  // SmallTV Ultra drives the backlight through a P-MOSFET.
#endif
#ifndef LCD_SIZE
#define LCD_SIZE 240
#endif
#ifndef SPI_HZ
#define SPI_HZ 20000000  // Speed measured on the stock firmware.
#endif

// Panel appearance. MADCTL 0x00 is RGB order, 0x08 is BGR. Both can also be changed at runtime.
#ifndef DEFAULT_MADCTL
#define DEFAULT_MADCTL 0x00
#endif
#ifndef DEFAULT_INVERT
#define DEFAULT_INVERT 1
#endif
#ifndef DEFAULT_BRIGHTNESS
#define DEFAULT_BRIGHTNESS 60
#endif

// Network.
#ifndef SETUP_SSID
#define SETUP_SSID "SmallTV-Setup"
#endif
#ifndef SETUP_AP_IP
#define SETUP_AP_IP "10.42.0.1"  // Not the ESP8266 default, which often overlaps home networks.
#endif
#ifndef DEVICE_HOSTNAME
#define DEVICE_HOSTNAME "smalltv"
#endif
#ifndef FRAME_PORT
#define FRAME_PORT 7777
#endif

static const uint32_t FRAME_BYTES = uint32_t(LCD_SIZE) * LCD_SIZE * 2;
static const uint32_t POWER_ON_SETTLE_MS = 600;      // Panel needs time after a cold power-up.
static const uint32_t LCD_REFRESH_LATEST_MS = 10000; // Re-init deadline if Wi-Fi never connects.
static const uint32_t STA_WAIT_MS = 45000;           // Boot wait before opening the setup hotspot.
static const uint32_t STA_LOST_MS = 300000;          // Link-loss wait before opening it again.
static const uint32_t STABLE_MS = 60000;             // Uptime that clears the crash counter.
static const uint8_t CRASH_LIMIT = 3;                // Consecutive crashes before safe mode.
static const uint32_t RTC_MAGIC = 0x53545631;        // RTC memory marker.
static const uint32_t RTC_SLOT = 100;                // Clear of the block reserved for OTA.

static ESP8266WebServer server(80);
static ESP8266HTTPUpdateServer updater;
static WiFiServer frameServer(FRAME_PORT);
static uint8_t inflateDict[4096];  // zlib window; the sender must use wbits <= 12
static const uint32_t FRAME_READ_TIMEOUT_MS = 3000;
static const uint32_t MAX_COMPRESSED_BYTES = 262144;

struct RtcState {
  uint32_t magic;
  uint32_t crashes;
};

static bool safeMode = false;
static bool apActive = false;
static bool wasConnected = false;
static bool bootCleared = false;
static bool showingStatus = true;
static bool lcdRefreshed = false;
static uint32_t crashCount = 0;
static uint32_t lastLinkMs = 0;
static uint32_t connectedSinceMs = 0;
static uint32_t lastTickMs = 0;
static uint32_t frameBytes = 0;
static uint32_t framesShown = 0;
static uint8_t brightness = DEFAULT_BRIGHTNESS;
static uint8_t madctl = DEFAULT_MADCTL;
static bool invertColors = DEFAULT_INVERT;

// Classic 5x7 font for ASCII 0x20 to 0x5A.
static const uint8_t FONT[][5] PROGMEM = {
    {0x00, 0x00, 0x00, 0x00, 0x00}, {0x00, 0x00, 0x5F, 0x00, 0x00}, {0x00, 0x07, 0x00, 0x07, 0x00},
    {0x14, 0x7F, 0x14, 0x7F, 0x14}, {0x24, 0x2A, 0x7F, 0x2A, 0x12}, {0x23, 0x13, 0x08, 0x64, 0x62},
    {0x36, 0x49, 0x56, 0x20, 0x50}, {0x00, 0x08, 0x07, 0x03, 0x00}, {0x00, 0x1C, 0x22, 0x41, 0x00},
    {0x00, 0x41, 0x22, 0x1C, 0x00}, {0x2A, 0x1C, 0x7F, 0x1C, 0x2A}, {0x08, 0x08, 0x3E, 0x08, 0x08},
    {0x00, 0x80, 0x70, 0x30, 0x00}, {0x08, 0x08, 0x08, 0x08, 0x08}, {0x00, 0x00, 0x60, 0x60, 0x00},
    {0x20, 0x10, 0x08, 0x04, 0x02}, {0x3E, 0x51, 0x49, 0x45, 0x3E}, {0x00, 0x42, 0x7F, 0x40, 0x00},
    {0x72, 0x49, 0x49, 0x49, 0x46}, {0x21, 0x41, 0x49, 0x4D, 0x33}, {0x18, 0x14, 0x12, 0x7F, 0x10},
    {0x27, 0x45, 0x45, 0x45, 0x39}, {0x3C, 0x4A, 0x49, 0x49, 0x31}, {0x41, 0x21, 0x11, 0x09, 0x07},
    {0x36, 0x49, 0x49, 0x49, 0x36}, {0x46, 0x49, 0x49, 0x29, 0x1E}, {0x00, 0x00, 0x14, 0x00, 0x00},
    {0x00, 0x40, 0x34, 0x00, 0x00}, {0x00, 0x08, 0x14, 0x22, 0x41}, {0x14, 0x14, 0x14, 0x14, 0x14},
    {0x00, 0x41, 0x22, 0x14, 0x08}, {0x02, 0x01, 0x59, 0x09, 0x06}, {0x3E, 0x41, 0x5D, 0x59, 0x4E},
    {0x7C, 0x12, 0x11, 0x12, 0x7C}, {0x7F, 0x49, 0x49, 0x49, 0x36}, {0x3E, 0x41, 0x41, 0x41, 0x22},
    {0x7F, 0x41, 0x41, 0x41, 0x3E}, {0x7F, 0x49, 0x49, 0x49, 0x41}, {0x7F, 0x09, 0x09, 0x09, 0x01},
    {0x3E, 0x41, 0x41, 0x51, 0x73}, {0x7F, 0x08, 0x08, 0x08, 0x7F}, {0x00, 0x41, 0x7F, 0x41, 0x00},
    {0x20, 0x40, 0x41, 0x3F, 0x01}, {0x7F, 0x08, 0x14, 0x22, 0x41}, {0x7F, 0x40, 0x40, 0x40, 0x40},
    {0x7F, 0x02, 0x1C, 0x02, 0x7F}, {0x7F, 0x04, 0x08, 0x10, 0x7F}, {0x3E, 0x41, 0x41, 0x41, 0x3E},
    {0x7F, 0x09, 0x09, 0x09, 0x06}, {0x3E, 0x41, 0x51, 0x21, 0x5E}, {0x7F, 0x09, 0x19, 0x29, 0x46},
    {0x26, 0x49, 0x49, 0x49, 0x32}, {0x03, 0x01, 0x7F, 0x01, 0x03}, {0x3F, 0x40, 0x40, 0x40, 0x3F},
    {0x1F, 0x20, 0x40, 0x20, 0x1F}, {0x3F, 0x40, 0x38, 0x40, 0x3F}, {0x63, 0x14, 0x08, 0x14, 0x63},
    {0x03, 0x04, 0x78, 0x04, 0x03}, {0x61, 0x59, 0x49, 0x4D, 0x43},
};

// ---- LCD ----

static void lcdCommand(uint8_t command) {
  digitalWrite(PIN_DC, LOW);
  SPI.write(command);
  digitalWrite(PIN_DC, HIGH);
}

static void lcdCommand1(uint8_t command, uint8_t value) {
  lcdCommand(command);
  SPI.write(value);
}

static void lcdWindow(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1) {
  lcdCommand(0x2A);
  SPI.write16(x0);
  SPI.write16(x1);
  lcdCommand(0x2B);
  SPI.write16(y0);
  SPI.write16(y1);
  lcdCommand(0x2C);
}

static void lcdFill(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint16_t color) {
  lcdWindow(x, y, x + w - 1, y + h - 1);
  for (uint32_t i = 0, n = uint32_t(w) * h; i < n; i++) {
    SPI.write16(color);
    if ((i & 0x3FFF) == 0) yield();
  }
}

static void lcdApplyAppearance() {
  lcdCommand1(0x36, madctl);
  lcdCommand(invertColors ? 0x21 : 0x20);
}

static void setBrightness(uint8_t percent) {
  brightness = min<uint8_t>(percent, 100);
  analogWrite(PIN_BACKLIGHT, BACKLIGHT_ACTIVE_LOW ? 100 - brightness : brightness);
}

static void lcdInit() {
  pinMode(PIN_DC, OUTPUT);
  digitalWrite(PIN_DC, HIGH);
  pinMode(PIN_RST, OUTPUT);
  digitalWrite(PIN_RST, LOW);
  delay(50);
  digitalWrite(PIN_RST, HIGH);
  delay(150);

  SPI.begin();
  SPI.setFrequency(SPI_HZ);
  SPI.setDataMode(SPI_MODE3);  // Required when the panel's chip select is tied low.
  SPI.setBitOrder(MSBFIRST);

  lcdCommand(0x01);  // Software reset.
  delay(150);
  lcdCommand(0x11);  // Sleep out.
  delay(120);
  lcdCommand1(0x3A, 0x55);  // 16 bits per pixel.
  lcdApplyAppearance();
  lcdCommand(0x13);  // Normal display mode.
  delay(10);
  lcdCommand(0x29);  // Display on.
  delay(20);
}

static void drawText(uint16_t x, uint16_t y, const String &text, uint16_t color, uint8_t scale) {
  for (size_t n = 0; n < text.length(); n++) {
    char c = text[n];
    if (c >= 'a' && c <= 'z') c -= 32;
    if (c < 0x20 || c > 0x5A) c = '?';
    uint16_t glyphX = x + n * 6 * scale;
    if (glyphX + 5 * scale > LCD_SIZE) return;
    for (uint8_t col = 0; col < 5; col++) {
      uint8_t bits = pgm_read_byte(&FONT[c - 0x20][col]);
      for (uint8_t row = 0; row < 7; row++) {
        if (bits & (1 << row)) lcdFill(glyphX + col * scale, y + row * scale, scale, scale, color);
      }
    }
    yield();
  }
}

static void drawStatus(const String &line1, const String &line2, const String &line3) {
  if (safeMode) return;
  lcdFill(0, 0, LCD_SIZE, LCD_SIZE, 0x0000);
  drawText(12, 16, F("SMALLTV"), 0xFFFF, 4);
  drawText(12, 62, String(F("FW ")) + FW_VERSION, 0x8410, 2);
  drawText(12, 110, line1, 0x07FF, 2);
  drawText(12, 142, line2, 0xFFFF, 2);
  drawText(12, 186, line3, 0xFFE0, 2);
  showingStatus = true;
}

static void drawTestBars() {
  static const uint16_t colors[] = {0xF800, 0x07E0, 0x001F, 0xFFFF};
  uint16_t band = LCD_SIZE / 4;
  for (uint8_t i = 0; i < 4; i++) lcdFill(0, i * band, LCD_SIZE, band, colors[i]);
  showingStatus = false;
}

// ---- Boot guard ----

static void bootGuard() {
  RtcState state = {0, 0};
  ESP.rtcUserMemoryRead(RTC_SLOT, reinterpret_cast<uint32_t *>(&state), sizeof(state));
  if (state.magic != RTC_MAGIC) state = {RTC_MAGIC, 0};
  uint32_t reason = ESP.getResetInfoPtr()->reason;
  bool crashed = reason == REASON_EXCEPTION_RST || reason == REASON_SOFT_WDT_RST || reason == REASON_WDT_RST;
  state.crashes = crashed ? state.crashes + 1 : 0;
  crashCount = state.crashes;
  safeMode = crashCount >= CRASH_LIMIT;
  ESP.rtcUserMemoryWrite(RTC_SLOT, reinterpret_cast<uint32_t *>(&state), sizeof(state));
}

static void clearBootGuard() {
  RtcState state = {RTC_MAGIC, 0};
  ESP.rtcUserMemoryWrite(RTC_SLOT, reinterpret_cast<uint32_t *>(&state), sizeof(state));
  crashCount = 0;
  bootCleared = true;
}

// ---- Wi-Fi ----

static void startSetupHotspot() {
  if (apActive) return;
  WiFi.mode(WIFI_AP_STA);
  IPAddress ip, mask(255, 255, 255, 0);
  ip.fromString(SETUP_AP_IP);
  WiFi.softAPConfig(ip, ip, mask);
  WiFi.softAP(SETUP_SSID);
  apActive = true;
  drawStatus(F("JOIN WIFI"), F(SETUP_SSID), String(F("THEN ")) + SETUP_AP_IP);
}

static void stopSetupHotspot() {
  if (!apActive) return;
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  apActive = false;
}

static void wifiTick(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED) {
    if (!wasConnected) {
      wasConnected = true;
      connectedSinceMs = now;
      // Only the status screen is redrawn, so a brief Wi-Fi drop leaves the last frame up.
      if (showingStatus) drawStatus(F("WAITING FOR DATA"), WiFi.localIP().toString(), WiFi.SSID());
    }
    lastLinkMs = now;
    if (apActive && now - connectedSinceMs > 60000) stopSetupHotspot();
    return;
  }
  wasConnected = false;
  uint32_t limit = lastLinkMs == 0 ? STA_WAIT_MS : STA_LOST_MS;
  if (now - lastLinkMs > limit || WiFi.SSID().length() == 0) startSetupHotspot();
}

// ---- Web ----

static String htmlEscape(String text) {
  text.replace("&", "&amp;");
  text.replace("<", "&lt;");
  text.replace(">", "&gt;");
  text.replace("\"", "&quot;");
  text.replace("'", "&#39;");
  return text;
}

static void handleRoot() {
  String page = F("<!doctype html><meta name=viewport content='width=device-width'><title>SmallTV</title>"
                  "<body style='font:16px sans-serif;margin:20px'><h2>SmallTV</h2><p>Firmware ");
  page += FW_VERSION;
  if (safeMode) page += F("<p><b>Safe mode after repeated crashes. Upload firmware below.</b>");
  page += F("<p><a href=/status>Status</a> &middot; <a href=/test>Colour test</a> &middot; "
            "<a href=/update>Firmware update</a>");
  if (apActive) {
    page += F("<h3>Connect to Wi-Fi</h3><form method=post action=/wifi>"
              "<p><input name=ssid placeholder='Wi-Fi name' value='");
    page += htmlEscape(WiFi.SSID());
    page += F("'><p><input name=pass type=password placeholder=Password><p><button>Save</button></form>");
  }
  server.send(200, "text/html", page);
}

static void handleStatus() {
  String json = F("{\"model\":\"" BOARD_NAME "\",\"firmware\":\"custom-");
  json += FW_VERSION;
  json += F("\",\"uptime_s\":");
  json += millis() / 1000;
  json += F(",\"heap\":");
  json += ESP.getFreeHeap();
  json += F(",\"rssi\":");
  json += WiFi.RSSI();
  json += F(",\"ip\":\"");
  json += WiFi.localIP().toString();
  json += F("\",\"frame_port\":");
  json += FRAME_PORT;
  json += F(",\"codecs\":\"raw,zlib\",\"lcd_size\":");
  json += LCD_SIZE;
  json += F(",\"frames\":");
  json += framesShown;
  json += F(",\"has_frame\":");
  json += showingStatus ? F("false") : F("true");
  json += F(",\"brightness\":");
  json += brightness;
  json += F(",\"madctl\":");
  json += madctl;
  json += F(",\"invert\":");
  json += invertColors ? F("true") : F("false");
  json += F(",\"safe_mode\":");
  json += safeMode ? F("true") : F("false");
  json += F(",\"crashes\":");
  json += crashCount;
  json += F(",\"reset_reason\":\"");
  json += ESP.getResetReason();
  json += F("\"}");
  server.send(200, "application/json", json);
}

static void handleLcd() {
  if (server.hasArg("brightness")) setBrightness(constrain(server.arg("brightness").toInt(), 0, 100));
  if (!safeMode) {
    if (server.hasArg("inv")) invertColors = server.arg("inv").toInt() != 0;
    if (server.hasArg("madctl")) madctl = strtoul(server.arg("madctl").c_str(), nullptr, 0);
    if (server.hasArg("inv") || server.hasArg("madctl")) lcdApplyAppearance();
  }
  handleStatus();
}

static void handleInit() {
  if (!safeMode) {
    lcdInit();
    drawStatus(F("SCREEN RESET"), WiFi.localIP().toString(), F(""));
  }
  handleStatus();
}

static void handleTest() {
  if (!safeMode) drawTestBars();
  server.send(200, "text/plain", safeMode ? "safe mode" : "OK");
}

static void handleFrameDone() {
  bool ok = frameBytes == FRAME_BYTES;
  if (ok) {
    framesShown++;
    showingStatus = false;
  }
  server.send(ok ? 200 : 400, "text/plain", ok ? "OK" : "wrong frame size");
}

static void handleFrameUpload() {
  if (safeMode) return;
  HTTPUpload &upload = server.upload();
  if (upload.status == UPLOAD_FILE_START) {
    frameBytes = 0;
    lcdWindow(0, 0, LCD_SIZE - 1, LCD_SIZE - 1);
  } else if (upload.status == UPLOAD_FILE_WRITE) {
    uint32_t room = frameBytes < FRAME_BYTES ? FRAME_BYTES - frameBytes : 0;
    uint32_t count = min<uint32_t>(upload.currentSize, room);
    if (count) SPI.writeBytes(upload.buf, count);
    frameBytes += upload.currentSize;
  }
}

// ---- Fast frame port ----
// Header (18 bytes, big-endian): "STV1", encoding (0 raw, 1 zlib), reserved, x, y, w, h (uint16 each),
// payload length (uint32). Then the payload. The device replies "OK\n" or "ERR <reason>\n" and closes.

struct NetReader {
  WiFiClient *client;
};

static int netRead(void *ctx, uint8_t *buf, int max) {
  WiFiClient *client = static_cast<NetReader *>(ctx)->client;
  uint32_t start = millis();
  while (!client->available()) {
    if (!client->connected() || millis() - start > FRAME_READ_TIMEOUT_MS) return 0;
    delay(1);
  }
  return client->read(buf, min<int>(max, client->available()));
}

static void lcdWriteBytes(void *, const uint8_t *buf, int len) { SPI.writeBytes(buf, len); }

static bool readExact(NetReader &net, uint8_t *buf, int len) {
  while (len > 0) {
    int n = netRead(&net, buf, len);
    if (n <= 0) return false;
    buf += n;
    len -= n;
  }
  return true;
}

static void handleFrameClient(WiFiClient &client) {
  client.setNoDelay(true);
  NetReader net = {&client};
  uint8_t h[18];
  if (!readExact(net, h, sizeof(h))) {
    client.stop();
    return;
  }
  uint8_t encoding = h[4];
  uint16_t x = h[6] << 8 | h[7], y = h[8] << 8 | h[9], w = h[10] << 8 | h[11], ht = h[12] << 8 | h[13];
  uint32_t length = uint32_t(h[14]) << 24 | uint32_t(h[15]) << 16 | uint32_t(h[16]) << 8 | h[17];
  uint32_t pixelBytes = uint32_t(w) * ht * 2;

  const char *error = nullptr;
  if (memcmp(h, "STV1", 4) != 0) error = "bad header";
  else if (safeMode) error = "safe mode";
  else if (w == 0 || ht == 0 || x + w > LCD_SIZE || y + ht > LCD_SIZE) error = "bad region";
  else if (encoding == 0 && length != pixelBytes) error = "bad length";
  else if (encoding == 1 && (length == 0 || length > MAX_COMPRESSED_BYTES)) error = "bad length";
  else if (encoding > 1) error = "bad encoding";

  if (!error) {
    lcdWindow(x, y, x + w - 1, y + ht - 1);
    int rc = encoding == 0
                 ? fc_copy_stream(netRead, &net, length, lcdWriteBytes, nullptr)
                 : fc_inflate_stream(netRead, &net, length, lcdWriteBytes, nullptr, pixelBytes,
                                     inflateDict, sizeof(inflateDict));
    if (rc == FC_ERR_READ) error = "timeout";
    else if (rc == FC_ERR_WINDOW) error = "window too large";
    else if (rc != FC_OK) error = "decode failed";
  }
  if (!error) {
    framesShown++;
    if (x == 0 && y == 0 && w == LCD_SIZE && ht == LCD_SIZE) showingStatus = false;
  }
  client.print(error ? String(F("ERR ")) + error + "\n" : String(F("OK\n")));
  client.flush();
  client.stop();
}

static void handleWifiSave() {
  if (!apActive) {
    server.send(403, "text/plain", "Only available on the setup hotspot");
    return;
  }
  String ssid = server.arg("ssid");
  String pass = server.arg("pass");
  server.send(200, "text/plain", "Saved. Connecting.");
  delay(300);
  WiFi.persistent(true);
  WiFi.begin(ssid.c_str(), pass.c_str());
  WiFi.persistent(false);
  lastLinkMs = millis();
}

static void handleReboot() {
  server.send(200, "text/plain", "Rebooting");
  delay(300);
  ESP.restart();
}

void setup() {
  // Core 3.x starts with Wi-Fi off; this restores use of the saved network at boot.
  enableWiFiAtBootTime();
  bootGuard();
  analogWriteRange(100);
  analogWriteFreq(1000);
  pinMode(PIN_BACKLIGHT, OUTPUT);
  setBrightness(brightness);
  if (!safeMode) {
    if (ESP.getResetInfoPtr()->reason == REASON_DEFAULT_RST) delay(POWER_ON_SETTLE_MS);
    lcdInit();
    drawStatus(F("CONNECTING"), F("WI-FI..."), F(""));
  }

  // Keep the stored Wi-Fi details intact unless the setup page changes them.
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.hostname(DEVICE_HOSTNAME);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.setAutoReconnect(true);
  if (WiFi.SSID().length()) WiFi.begin();
  if (safeMode) startSetupHotspot();

  server.on("/", HTTP_GET, handleRoot);
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/lcd", HTTP_GET, handleLcd);
  server.on("/lcd/init", HTTP_GET, handleInit);
  server.on("/test", HTTP_GET, handleTest);
  server.on("/reboot", HTTP_GET, handleReboot);
  server.on("/wifi", HTTP_POST, handleWifiSave);
  server.on("/frame", HTTP_POST, handleFrameDone, handleFrameUpload);
  updater.setup(&server, "/update");
  server.begin();
  frameServer.begin();
  frameServer.setNoDelay(true);
}

void loop() {
  server.handleClient();
  WiFiClient frameClient = frameServer.accept();
  if (frameClient) handleFrameClient(frameClient);
  uint32_t now = millis();
  if (now - lastTickMs >= 1000) {
    lastTickMs = now;
    wifiTick(now);
    // After a cold power-on the first init can leave the panel dark, because Wi-Fi start-up draws a
    // burst of current at the same time. A second init once Wi-Fi has settled fixes it.
    if (!safeMode && !lcdRefreshed && (wasConnected || now > LCD_REFRESH_LATEST_MS)) {
      lcdRefreshed = true;
      lcdInit();
      if (wasConnected) drawStatus(F("WAITING FOR DATA"), WiFi.localIP().toString(), WiFi.SSID());
      else drawStatus(F("CONNECTING"), F("WI-FI..."), F(""));
    }
    if (!bootCleared && now > STABLE_MS) clearBootGuard();
  }
}
