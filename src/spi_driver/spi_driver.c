/* GDEM0397T81P SPI driver implementation.
 *
 * The panel's controller is the Solomon Systech SSD1677 (confirmed from the
 * GoodDisplay product page / datasheet). This is a Solomon-style controller,
 * NOT the UC8253/IL0398 family an earlier stub assumed -- the command set is
 * entirely different (write RAM 0x24, display-update-control 0x22, master
 * activation 0x20), which is why the old sequence "succeeded" over SPI while
 * the panel never refreshed. Sequence below follows the SSD1677 datasheet and
 * GoodDisplay's reference init for 800x480.
 *
 * GPIO (DC/RST/BUSY) is driven through libgpiod's 2.x API (one bulk line
 * request on the chip's character device). SPI goes through spidev.
 *
 * Bring-up knobs (env, no rebuild): EINKY_GPIOCHIP, EINKY_SPI_HZ,
 * EINKY_INVERT_FRAME, EINKY_SPI_DEBUG. See einky_open / push_frame.
 */

/* nanosleep() and ioctl() are POSIX/misc, hidden under strict -std=c11 unless we
 * opt back into the default glibc feature set. Must precede every #include. */
#define _DEFAULT_SOURCE

#include "spi_driver.h"

#include <errno.h>
#include <fcntl.h>
#include <gpiod.h>
#include <linux/spi/spidev.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define EINKY_SPI_HZ_DEFAULT 10000000
#define EINKY_SPI_BITS 8

/* Max bytes per SPI_IOC_MESSAGE. spidev rejects a transfer whose total exceeds
 * its `bufsiz` module parameter (default 4096) with -EMSGSIZE, so a full 48000-
 * byte frame must be split. DC stays asserted across the chunks; only hardware
 * CS toggles between them, which the panel tolerates during a data burst. */
#define EINKY_SPI_CHUNK 4096

/* GPIO chip that owns the BCM lines. On the Pi Zero 2 W vendor kernel the SoC
 * bank is gpiochip0 (BCM number == line offset). Override with $EINKY_GPIOCHIP. */
#define EINKY_GPIOCHIP_DEFAULT "/dev/gpiochip0"
#define EINKY_GPIO_CONSUMER "einky-panel"

/* Bytes per packed row (800 px / 8) and gate line count. Derived from the
 * contract geometry so a panel-size change in the contract flows through. */
#define EINKY_BYTES_PER_ROW (EINKY_FRAME_BYTES / EINKY_PANEL_H)
#define EINKY_GATE_LINES EINKY_PANEL_H

/* ── SSD1677 command set (datasheet) ─────────────────────────────────────── */
#define SSD1677_SW_RESET 0x12
#define SSD1677_TEMP_SENSOR 0x18
#define SSD1677_BOOSTER_SOFT_START 0x0C
#define SSD1677_DRIVER_OUTPUT 0x01
#define SSD1677_BORDER_WAVEFORM 0x3C
#define SSD1677_DATA_ENTRY 0x11
#define SSD1677_SET_RAM_X 0x44
#define SSD1677_SET_RAM_Y 0x45
#define SSD1677_SET_RAM_X_COUNT 0x4E
#define SSD1677_SET_RAM_Y_COUNT 0x4F
#define SSD1677_WRITE_RAM_BW 0x24
#define SSD1677_WRITE_RAM_RED 0x26
#define SSD1677_UPDATE_CTRL1 0x21
#define SSD1677_UPDATE_CTRL2 0x22
#define SSD1677_MASTER_ACTIVATE 0x20
#define SSD1677_DEEP_SLEEP 0x10

/* Full-update sequence for 0x22: enable clock + analog, load temp value, load
 * LUT (display mode 1), display, then disable analog + clock. */
#define SSD1677_UPDATE_FULL 0xF7
/* Partial-update sequence: display mode 2, a no-flash differential against the
 * previous image held in the RED RAM (0x26). */
#define SSD1677_UPDATE_PART 0xFC

struct einky_panel {
    int spi_fd;
    uint32_t spi_hz;
    int invert; /* XOR the frame before writing RAM (photo-negative fix). */
    int debug;  /* extra stderr logging for bring-up. */
    /* Set once a full refresh has stocked the previous-image RAM (0x26); a
     * partial refresh before that would differential against power-on noise,
     * so it is promoted to a full refresh. Cleared by einky_init. */
    int did_full;
    /* libgpiod v2 handles: one bulk request owns DC/RST/BUSY together, so
     * releasing it in einky_close frees all three lines at once. */
    struct gpiod_chip *chip;
    struct gpiod_line_request *lines;
    /* Scratch TX buffer for the (optionally inverted) frame, sized to one full
     * frame so push_frame never allocates on the hot path. */
    uint8_t txbuf[EINKY_FRAME_BYTES];
};

static void einky_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
}

static int env_int(const char *name, int fallback) {
    const char *v = getenv(name);
    if (!v || !*v)
        return fallback;
    return (int)strtol(v, NULL, 0);
}

static int spi_xfer(einky_panel_t *p, const uint8_t *tx, size_t len, uint8_t cs_change) {
    struct spi_ioc_transfer tr = {
        .tx_buf = (uintptr_t)tx,
        .rx_buf = 0,
        .len = (uint32_t)len,
        .speed_hz = p->spi_hz,
        .bits_per_word = EINKY_SPI_BITS,
        .cs_change = cs_change,
    };
    return ioctl(p->spi_fd, SPI_IOC_MESSAGE(1), &tr) < 0 ? -1 : 0;
}

static int gpio_set(struct gpiod_line_request *lines, unsigned int offset, int value) {
    return gpiod_line_request_set_value(
        lines, offset, value ? GPIOD_LINE_VALUE_ACTIVE : GPIOD_LINE_VALUE_INACTIVE);
}

static int gpio_get(struct gpiod_line_request *lines, unsigned int offset) {
    /* GPIOD_LINE_VALUE_ERROR/INACTIVE/ACTIVE map to -1/0/1. */
    return (int)gpiod_line_request_get_value(lines, offset);
}

static void msleep(int ms) {
    struct timespec ts = {ms / 1000, (ms % 1000) * 1000000L};
    nanosleep(&ts, NULL);
}

/* SSD1677 BUSY is active-HIGH: the controller holds it high during an update
 * and releases it low when idle. Wait up to 30s (a full refresh is ~1.5s, but
 * a cold panel is slower). A stuck-high BUSY errors out rather than hanging. */
static int wait_busy(einky_panel_t *p) {
    for (int i = 0; i < 3000; i++) {
        int v = gpio_get(p->lines, EINKY_PIN_BUSY);
        if (v < 0)
            return -1;
        if (v == 0) {
            if (p->debug && i)
                einky_log("einky: BUSY cleared after ~%d ms", i * 10);
            return 0;
        }
        msleep(10);
    }
    einky_log("einky: ERROR BUSY stuck high >30s (check wiring/polarity)");
    return -1;
}

static int send_cmd(einky_panel_t *p, uint8_t cmd) {
    if (gpio_set(p->lines, EINKY_PIN_DC, 0) < 0)
        return -1;
    return spi_xfer(p, &cmd, 1, 0);
}

static int send_data(einky_panel_t *p, const uint8_t *data, size_t len) {
    if (gpio_set(p->lines, EINKY_PIN_DC, 1) < 0)
        return -1;
    for (size_t off = 0; off < len; off += EINKY_SPI_CHUNK) {
        size_t n = len - off;
        if (n > EINKY_SPI_CHUNK)
            n = EINKY_SPI_CHUNK;

        uint8_t cs_change = (off + n < len) ? 1 : 0;
        if (spi_xfer(p, data + off, n, cs_change) < 0)
            return -1;
    }
    return 0;
}

/* Command followed by a short inline data payload (init registers). */
static int send_cmd_data(einky_panel_t *p, uint8_t cmd, const uint8_t *data, size_t len) {
    if (send_cmd(p, cmd) < 0)
        return -1;
    return len ? send_data(p, data, len) : 0;
}

/* One bulk v2 request for the three control lines. DC idles low (command
 * mode); RST idles high (reset is active-low, so idle de-asserted); BUSY is a
 * panel-driven input. The settings/config objects are copied into the request,
 * so they are freed here regardless of success. */
static struct gpiod_line_request *request_control_lines(struct gpiod_chip *chip) {
    struct gpiod_line_settings *dc_out = gpiod_line_settings_new();
    struct gpiod_line_settings *rst_out = gpiod_line_settings_new();
    struct gpiod_line_settings *busy_in = gpiod_line_settings_new();
    struct gpiod_line_config *line_cfg = gpiod_line_config_new();
    struct gpiod_request_config *req_cfg = gpiod_request_config_new();
    struct gpiod_line_request *req = NULL;
    unsigned int dc = EINKY_PIN_DC, rst = EINKY_PIN_RST, busy = EINKY_PIN_BUSY;

    if (!dc_out || !rst_out || !busy_in || !line_cfg || !req_cfg)
        goto out;

    if (gpiod_line_settings_set_direction(dc_out, GPIOD_LINE_DIRECTION_OUTPUT) < 0 ||
        gpiod_line_settings_set_output_value(dc_out, GPIOD_LINE_VALUE_INACTIVE) < 0 ||
        gpiod_line_settings_set_direction(rst_out, GPIOD_LINE_DIRECTION_OUTPUT) < 0 ||
        gpiod_line_settings_set_output_value(rst_out, GPIOD_LINE_VALUE_ACTIVE) < 0 ||
        gpiod_line_settings_set_direction(busy_in, GPIOD_LINE_DIRECTION_INPUT) < 0)
        goto out;

    if (gpiod_line_config_add_line_settings(line_cfg, &dc, 1, dc_out) < 0 ||
        gpiod_line_config_add_line_settings(line_cfg, &rst, 1, rst_out) < 0 ||
        gpiod_line_config_add_line_settings(line_cfg, &busy, 1, busy_in) < 0)
        goto out;

    gpiod_request_config_set_consumer(req_cfg, EINKY_GPIO_CONSUMER);
    req = gpiod_chip_request_lines(chip, req_cfg, line_cfg);

out:
    gpiod_request_config_free(req_cfg);
    gpiod_line_config_free(line_cfg);
    gpiod_line_settings_free(busy_in);
    gpiod_line_settings_free(rst_out);
    gpiod_line_settings_free(dc_out);
    return req;
}

einky_panel_t *einky_open(const char *spi_dev) {
    einky_panel_t *p = calloc(1, sizeof(*p));
    if (!p)
        return NULL;

    p->spi_hz = (uint32_t)env_int("EINKY_SPI_HZ", EINKY_SPI_HZ_DEFAULT);
    p->invert = env_int("EINKY_INVERT_FRAME", 0);
    p->debug = env_int("EINKY_SPI_DEBUG", 0);

    p->spi_fd = open(spi_dev, O_RDWR);
    if (p->spi_fd < 0) {
        einky_log("einky: open(%s) failed: %s", spi_dev, strerror(errno));
        free(p);
        return NULL;
    }

    uint32_t mode = SPI_MODE_0;
    uint8_t bits = EINKY_SPI_BITS;
    uint32_t hz = p->spi_hz;
    if (ioctl(p->spi_fd, SPI_IOC_WR_MODE32, &mode) < 0 ||
        ioctl(p->spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 ||
        ioctl(p->spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &hz) < 0) {
        einky_log("einky: spidev ioctl failed: %s", strerror(errno));
        close(p->spi_fd);
        free(p);
        return NULL;
    }

    const char *chip_path = getenv("EINKY_GPIOCHIP");
    if (!chip_path || !*chip_path)
        chip_path = EINKY_GPIOCHIP_DEFAULT;

    p->chip = gpiod_chip_open(chip_path);
    if (!p->chip) {
        einky_log("einky: gpiod_chip_open(%s) failed: %s", chip_path, strerror(errno));
        goto fail;
    }

    p->lines = request_control_lines(p->chip);
    if (!p->lines) {
        einky_log("einky: request DC/RST/BUSY on %s failed: %s (line already in use?)", chip_path,
                  strerror(errno));
        goto fail;
    }

    einky_log("einky: SSD1677 open ok (spi=%s %u Hz, invert=%d, chip=%s)", spi_dev, p->spi_hz,
              p->invert, chip_path);
    return p;

fail:
    if (p->chip)
        gpiod_chip_close(p->chip);
    if (p->spi_fd >= 0)
        close(p->spi_fd);
    free(p);
    return NULL;
}

/* Set the RAM address window to the full panel and home the address counter.
 * Two SSD1677 traps here (both confirmed by the GxEPD2 reference driver for
 * this exact panel, GxEPD2_397_GDEM0397T81::_setPartialRamArea, and the
 * SSD1677 datasheet):
 *
 *  - X addresses are 10-bit and in PIXELS: 0x44 takes 4 data bytes (start
 *    lo/hi, end lo/hi) and 0x4E takes 2. The SSD168x-style single-byte,
 *    byte-indexed form under-feeds the commands, leaving a garbage window that
 *    wraps every write -- the whole panel renders as static.
 *  - The panel's gates are reversed relative to our top-down frame and the
 *    controller has no gate-reverse scan, so data entry is X-increment /
 *    Y-DECREMENT (0x01) with the Y window and counter starting at the bottom
 *    gate line (H-1). */
static int set_ram_window(einky_panel_t *p) {
    const uint8_t entry[] = {0x01};
    const uint8_t xr[] = {0x00, 0x00, (EINKY_PANEL_W - 1) & 0xFF, (EINKY_PANEL_W - 1) >> 8};
    const uint8_t yr[] = {(EINKY_GATE_LINES - 1) & 0xFF, (EINKY_GATE_LINES - 1) >> 8, 0x00, 0x00};
    const uint8_t xc[] = {0x00, 0x00};
    const uint8_t yc[] = {(EINKY_GATE_LINES - 1) & 0xFF, (EINKY_GATE_LINES - 1) >> 8};

    if (send_cmd_data(p, SSD1677_DATA_ENTRY, entry, sizeof(entry)) < 0)
        return -1;
    if (send_cmd_data(p, SSD1677_SET_RAM_X, xr, sizeof(xr)) < 0)
        return -1;
    if (send_cmd_data(p, SSD1677_SET_RAM_Y, yr, sizeof(yr)) < 0)
        return -1;
    if (send_cmd_data(p, SSD1677_SET_RAM_X_COUNT, xc, sizeof(xc)) < 0)
        return -1;
    if (send_cmd_data(p, SSD1677_SET_RAM_Y_COUNT, yc, sizeof(yc)) < 0)
        return -1;
    return 0;
}

int einky_init(einky_panel_t *p) {
    if (!p)
        return -EINVAL;
    p->did_full = 0;

    /* Hardware reset (RST active-low: idle high, pulse low >=10ms, release). */
    gpio_set(p->lines, EINKY_PIN_RST, 1);
    msleep(10);
    gpio_set(p->lines, EINKY_PIN_RST, 0);
    msleep(10);
    gpio_set(p->lines, EINKY_PIN_RST, 1);
    msleep(10);
    if (wait_busy(p) < 0)
        return -1;

    /* Software reset re-loads the OTP defaults; wait for it to settle. */
    if (send_cmd(p, SSD1677_SW_RESET) < 0)
        return -1;
    if (wait_busy(p) < 0)
        return -1;

    /* Internal temperature sensor (controller picks the waveform from OTP). */
    const uint8_t temp[] = {0x80};
    if (send_cmd_data(p, SSD1677_TEMP_SENSOR, temp, sizeof(temp)) < 0)
        return -1;

    /* Booster soft-start (GoodDisplay SSD1677 reference values). */
    const uint8_t booster[] = {0xAE, 0xC7, 0xC3, 0xC0, 0x80};
    if (send_cmd_data(p, SSD1677_BOOSTER_SOFT_START, booster, sizeof(booster)) < 0)
        return -1;

    /* Driver output control: number of gate lines (H-1), scan settings 0x02
     * (gate scan order for this glass -- GxEPD2 reference value; 0x00
     * interleaves the gate lines). */
    const uint8_t drv[] = {(EINKY_GATE_LINES - 1) & 0xFF, (EINKY_GATE_LINES - 1) >> 8, 0x02};
    if (send_cmd_data(p, SSD1677_DRIVER_OUTPUT, drv, sizeof(drv)) < 0)
        return -1;

    /* Border: follow the LUT for a clean white border on full refresh. */
    const uint8_t border[] = {0x01};
    if (send_cmd_data(p, SSD1677_BORDER_WAVEFORM, border, sizeof(border)) < 0)
        return -1;

    if (set_ram_window(p) < 0)
        return -1;
    if (wait_busy(p) < 0)
        return -1;

    einky_log("einky: init ok (%dx%d, %d gate lines, %d bytes/row)", EINKY_PANEL_W, EINKY_PANEL_H,
              EINKY_GATE_LINES, EINKY_BYTES_PER_ROW);
    return 0;
}

/* Re-arm the RAM window / address counter and stream the frame into one RAM
 * plane (BW 0x24 or RED-as-previous 0x26). SSD1677 BW RAM: bit 1 = white,
 * matching the contract's packed_white_is_one, so we write the packed frame
 * as-is by default (EINKY_INVERT_FRAME=1 flips it if the panel comes up as a
 * photo-negative). */
static int write_ram(einky_panel_t *p, uint8_t ram_cmd, const uint8_t *frame, size_t len) {
    const uint8_t *src = frame;
    if (set_ram_window(p) < 0)
        return -1;
    if (send_cmd(p, ram_cmd) < 0)
        return -1;
    if (p->invert) {
        for (size_t i = 0; i < len; i++)
            p->txbuf[i] = (uint8_t)~frame[i];
        src = p->txbuf;
    }
    return send_data(p, src, len);
}

static int push_frame(einky_panel_t *p, const uint8_t *frame, size_t len, int full) {
    if (!p || !frame)
        return -EINVAL;
    if (len != EINKY_FRAME_BYTES)
        return -EINVAL;

    /* Display mode 2 (partial) is a differential against the previous-image
     * RAM, which holds power-on noise until a full refresh has stocked it. */
    if (!p->did_full)
        full = 1;

    /* A full refresh also stocks the previous-image RAM so the next partial
     * differentials against what is actually on the glass; partials only
     * rewrite the BW RAM (the mode-2 update sequence ping-pongs it into the
     * previous-image RAM itself). */
    if (full && write_ram(p, SSD1677_WRITE_RAM_RED, frame, len) < 0)
        return -1;
    if (write_ram(p, SSD1677_WRITE_RAM_BW, frame, len) < 0)
        return -1;

    /* Display update control 1: full bypasses the RED RAM as 0 (plain BW
     * refresh); partial reads it as the previous image. */
    const uint8_t ctrl1[] = {full ? 0x40 : 0x00, 0x00};
    if (send_cmd_data(p, SSD1677_UPDATE_CTRL1, ctrl1, sizeof(ctrl1)) < 0)
        return -1;

    /* Trigger the update: load the refresh sequence, then activate. */
    const uint8_t upd[] = {full ? SSD1677_UPDATE_FULL : SSD1677_UPDATE_PART};
    if (send_cmd_data(p, SSD1677_UPDATE_CTRL2, upd, sizeof(upd)) < 0)
        return -1;
    if (send_cmd(p, SSD1677_MASTER_ACTIVATE) < 0)
        return -1;
    if (p->debug)
        einky_log("einky: %s refresh activated, waiting BUSY", full ? "full" : "partial");
    if (wait_busy(p) < 0)
        return -1;
    if (full)
        p->did_full = 1;
    return 0;
}

int einky_full_refresh(einky_panel_t *p, const uint8_t *frame, size_t len) {
    return push_frame(p, frame, len, 1);
}

int einky_partial_refresh(einky_panel_t *p, const uint8_t *frame, size_t len) {
    return push_frame(p, frame, len, 0);
}

int einky_sleep(einky_panel_t *p) {
    if (!p)
        return -EINVAL;
    /* Deep sleep mode 1: lowest power, retains the displayed image. A hardware
     * reset + einky_init is required to wake (the launcher re-inits on wake). */
    const uint8_t deep[] = {0x01};
    return send_cmd_data(p, SSD1677_DEEP_SLEEP, deep, sizeof(deep));
}

void einky_close(einky_panel_t *p) {
    if (!p)
        return;
    /* Releasing the request frees DC/RST/BUSY; do it before dropping the SPI fd. */
    if (p->lines)
        gpiod_line_request_release(p->lines);
    if (p->chip)
        gpiod_chip_close(p->chip);
    if (p->spi_fd >= 0)
        close(p->spi_fd);
    free(p);
}
