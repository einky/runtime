// ESP32 dev bridge: TCP -> e-paper, buttons -> TCP. See ADR 0006.
//
// Wire protocol (matches runtime/src/frame_processor/dispatch.py):
//   | "EINK" | u32 width LE | u32 height LE | width/8 * height bytes |
// Bits are MSB-first; bit=1 means white, bit=0 means black (inherited from
// numpy.packbits + the (>=128) threshold in dither.pack_1bit).

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>

#include <GxEPD2_BW.h>

#include "config.h"

// Panel geometry, ports, magic, and button pins all come from contract.h
// (pulled in via config.h), generated from meta/shared/hardware.toml.
static constexpr uint16_t PANEL_W = EINKY_PANEL_W;
static constexpr uint16_t PANEL_H = EINKY_PANEL_H;
static constexpr size_t FRAME_BYTES = EINKY_FRAME_BYTES; // 48000

// Paged buffer (HEIGHT/8 ≈ 6 KB) so GxEPD2's internal buffer doesn't collide
// with our own 48 KB frameBuf in DRAM. firstPage()/nextPage() iterates 8x.
GxEPD2_BW<GxEPD2_750_T7, GxEPD2_750_T7::HEIGHT / 8>
    display(GxEPD2_750_T7(EPD_CS_PIN, EPD_DC_PIN, EPD_RST_PIN, EPD_BUSY_PIN));

static uint8_t frameBuf[FRAME_BYTES];
static uint32_t framesDrawn = 0;
static constexpr uint32_t FULL_REFRESH_EVERY = EINKY_FULL_REFRESH_EVERY;

struct Button {
    const char *name;
    uint8_t pin;
    bool lastState; // INPUT_PULLUP idle is HIGH
    uint32_t lastChangeMs;
};

static Button buttons[] = {
    {"up", BTN_UP_PIN, HIGH, 0},       {"down", BTN_DOWN_PIN, HIGH, 0},
    {"left", BTN_LEFT_PIN, HIGH, 0},   {"right", BTN_RIGHT_PIN, HIGH, 0},
    {"a", BTN_A_PIN, HIGH, 0},         {"b", BTN_B_PIN, HIGH, 0},
    {"start", BTN_START_PIN, HIGH, 0},
};
static constexpr size_t NUM_BUTTONS = sizeof(buttons) / sizeof(buttons[0]);
static constexpr uint32_t DEBOUNCE_MS = EINKY_DEBOUNCE_MS; // shared debounce_ms

static WiFiClient frameClient;
static WiFiClient inputClient;

static void connectWiFi() {
    Serial.printf("WiFi: joining %s...\n", EINKY_WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(EINKY_WIFI_SSID, EINKY_WIFI_PASSWORD);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print('.');
    }
    Serial.printf("\nWiFi: %s\n", WiFi.localIP().toString().c_str());
}

// Read exactly `n` bytes from the client into `dst`. Returns false on disconnect.
static bool readExact(WiFiClient &c, uint8_t *dst, size_t n, uint32_t timeoutMs) {
    size_t got = 0;
    uint32_t deadline = millis() + timeoutMs;
    while (got < n) {
        if (!c.connected())
            return false;
        int available = c.available();
        if (available <= 0) {
            if ((int32_t)(millis() - deadline) > 0)
                return false;
            delay(1);
            continue;
        }
        int r = c.read(dst + got, n - got);
        if (r <= 0)
            return false;
        got += r;
        deadline = millis() + timeoutMs; // sliding window: any progress resets it
    }
    return true;
}

static bool receiveOneFrame() {
    uint8_t header[12];
    if (!readExact(frameClient, header, sizeof(header), 5000))
        return false;
    if (memcmp(header, EINKY_FRAME_MAGIC, 4) != 0) {
        Serial.println("frame: bad magic, dropping connection");
        return false;
    }
    uint32_t w = (uint32_t)header[4] | ((uint32_t)header[5] << 8) | ((uint32_t)header[6] << 16) |
                 ((uint32_t)header[7] << 24);
    uint32_t h = (uint32_t)header[8] | ((uint32_t)header[9] << 8) | ((uint32_t)header[10] << 16) |
                 ((uint32_t)header[11] << 24);
    if (w != PANEL_W || h != PANEL_H) {
        Serial.printf("frame: bad size %ux%u, expected %ux%u\n", w, h, PANEL_W, PANEL_H);
        return false;
    }
    return readExact(frameClient, frameBuf, FRAME_BYTES, 10000);
}

static void renderFrame() {
    // dither.pack_1bit produces bit=1 for white. drawBitmap with color=BLACK
    // treats bit=1 as foreground, so invert before drawing — bit=0 (black ink)
    // becomes the foreground.
    for (size_t i = 0; i < FRAME_BYTES; i++)
        frameBuf[i] = ~frameBuf[i];

    bool partial = (framesDrawn % FULL_REFRESH_EVERY) != 0;
    display.setFullWindow();
    display.firstPage();
    do {
        display.fillScreen(GxEPD_WHITE);
        display.drawBitmap(0, 0, frameBuf, PANEL_W, PANEL_H, GxEPD_BLACK);
    } while (display.nextPage());
    if (!partial)
        display.refresh(false); // full clear+redraw to kill ghosting
    framesDrawn++;
}

static void pollButtonsAndSend() {
    uint32_t now = millis();
    for (size_t i = 0; i < NUM_BUTTONS; i++) {
        bool state = digitalRead(buttons[i].pin);
        if (state == buttons[i].lastState)
            continue;
        if (now - buttons[i].lastChangeMs < DEBOUNCE_MS)
            continue;
        buttons[i].lastChangeMs = now;
        buttons[i].lastState = state;
        if (state == LOW) {
            Serial.printf("btn: %s\n", buttons[i].name);
            if (inputClient.connected()) {
                inputClient.printf("%s\n", buttons[i].name);
                inputClient.flush();
            }
        }
    }
}

static void ensureClients() {
    if (!frameClient.connected()) {
        Serial.printf("frame: connecting %s:%d\n", EINKY_SERVER_HOST, EINKY_FRAME_PORT);
        if (frameClient.connect(EINKY_SERVER_HOST, EINKY_FRAME_PORT)) {
            frameClient.setNoDelay(true);
            Serial.println("frame: connected");
        } else {
            delay(1000);
        }
    }
    if (!inputClient.connected()) {
        Serial.printf("input: connecting %s:%d\n", EINKY_SERVER_HOST, EINKY_INPUT_PORT);
        if (inputClient.connect(EINKY_SERVER_HOST, EINKY_INPUT_PORT)) {
            inputClient.setNoDelay(true);
            Serial.println("input: connected");
        }
    }
}

void setup() {
    Serial.begin(115200);
    delay(200);
    Serial.println("\neinky-esp32 bridge starting");

    for (size_t i = 0; i < NUM_BUTTONS; i++) {
        pinMode(buttons[i].pin, INPUT_PULLUP);
    }

    pinMode(EPD_PWR_PIN, OUTPUT);
    digitalWrite(EPD_PWR_PIN, HIGH);
    delay(10);

    display.init(115200);
    display.setRotation(0);
    display.setFullWindow();
    display.firstPage();
    do {
        display.fillScreen(GxEPD_WHITE);
    } while (display.nextPage());

    connectWiFi();
}

void loop() {
    if (WiFi.status() != WL_CONNECTED) {
        connectWiFi();
        return;
    }
    ensureClients();
    pollButtonsAndSend();

    if (frameClient.connected() && frameClient.available() >= 12) {
        if (receiveOneFrame()) {
            renderFrame();
        } else {
            Serial.println("frame: read failed, reconnecting");
            frameClient.stop();
        }
    }
}
