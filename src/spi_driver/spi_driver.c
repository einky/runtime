/* GDEM0397T81P SPI driver implementation.
 *
 * GPIO (DC/RST/BUSY) is driven through libgpiod's 2.x API (one bulk line
 * request on the chip's character device). The image ships libgpiod2 -- the
 * v1 library can't coexist with it (or with the python-gpiod button reader),
 * so this file must stay on the v2 API. SPI goes through spidev. The init
 * sequence comes from the GoodDisplay datasheet for the UC8253-class controller
 * used by this panel. Tweak EINKY_SPI_HZ if writes come back garbled.
 */

/* nanosleep() and ioctl() are POSIX/misc, hidden under strict -std=c11 unless we
 * opt back into the default glibc feature set. Must precede every #include. */
#define _DEFAULT_SOURCE

#include "spi_driver.h"

#include <errno.h>
#include <fcntl.h>
#include <gpiod.h>
#include <linux/spi/spidev.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define EINKY_SPI_HZ 10000000
#define EINKY_SPI_BITS 8

/* Max bytes per SPI_IOC_MESSAGE. spidev rejects a transfer whose total exceeds
 * its `bufsiz` module parameter (default 4096) with -EMSGSIZE, so a full 48000-
 * byte frame must be split. DC stays asserted across the chunks; only hardware
 * CS toggles between them, which the panel tolerates during a data burst. */
#define EINKY_SPI_CHUNK 4096

/* GPIO chip that owns the BCM lines. On the Pi Zero 2 W vendor kernel the SoC
 * bank is gpiochip0 (54 lines, BCM number == line offset). Override with
 * $EINKY_GPIOCHIP if a kernel enumerates the bank elsewhere. */
#define EINKY_GPIOCHIP_DEFAULT "/dev/gpiochip0"
#define EINKY_GPIO_CONSUMER "einky-panel"

/* Frame polarity. dither/pack emits bit=1 for WHITE (meta/shared/hardware.toml:
 * packed_white_is_one=true); this panel's RAM takes bit=1 as black ink, so we
 * invert before pushing (contract: "the SPI driver ... INVERT before drawing").
 * Set to 0 during bring-up if the panel shows a photo negative. */
#define EINKY_INVERT_FRAME 1

/* EINKY_PANEL_*, EINKY_FRAME_BYTES, and the EINKY_PIN_* pins come from contract.h
 * (included via spi_driver.h), which is generated from meta/shared/hardware.toml. */

struct einky_panel {
    int spi_fd;
    /* libgpiod v2 handles: one bulk request owns DC/RST/BUSY together, so
     * releasing it in einky_close frees all three lines at once. */
    struct gpiod_chip *chip;
    struct gpiod_line_request *lines;
    /* Scratch TX buffer for the inverted frame (see EINKY_INVERT_FRAME), sized
     * to one full frame so push_frame never allocates on the hot path. */
    uint8_t txbuf[EINKY_FRAME_BYTES];
};

static int spi_xfer(int fd, const uint8_t *tx, size_t len) {
    struct spi_ioc_transfer tr = {
        .tx_buf = (uintptr_t)tx,
        .rx_buf = 0,
        .len = (uint32_t)len,
        .speed_hz = EINKY_SPI_HZ,
        .bits_per_word = EINKY_SPI_BITS,
    };
    return ioctl(fd, SPI_IOC_MESSAGE(1), &tr) < 0 ? -1 : 0;
}

static int gpio_set(struct gpiod_line_request *lines, unsigned int offset, int value) {
    return gpiod_line_request_set_value(
        lines, offset, value ? GPIOD_LINE_VALUE_ACTIVE : GPIOD_LINE_VALUE_INACTIVE);
}

static int gpio_get(struct gpiod_line_request *lines, unsigned int offset) {
    /* GPIOD_LINE_VALUE_ERROR/INACTIVE/ACTIVE map to -1/0/1, same contract as
     * the v1 gpiod_line_get_value this replaced. */
    return (int)gpiod_line_request_get_value(lines, offset);
}

static void msleep(int ms) {
    struct timespec ts = {ms / 1000, (ms % 1000) * 1000000L};
    nanosleep(&ts, NULL);
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

static int wait_busy(einky_panel_t *p) {
    /* BUSY is active-high on this panel; wait up to 5s. */
    for (int i = 0; i < 500; i++) {
        int v = gpio_get(p->lines, EINKY_PIN_BUSY);
        if (v < 0)
            return -1;
        if (v == 0)
            return 0;
        msleep(10);
    }
    return -1;
}

static int send_cmd(einky_panel_t *p, uint8_t cmd) {
    if (gpio_set(p->lines, EINKY_PIN_DC, 0) < 0)
        return -1;
    return spi_xfer(p->spi_fd, &cmd, 1);
}

static int send_data(einky_panel_t *p, const uint8_t *data, size_t len) {
    if (gpio_set(p->lines, EINKY_PIN_DC, 1) < 0)
        return -1;
    for (size_t off = 0; off < len; off += EINKY_SPI_CHUNK) {
        size_t n = len - off;
        if (n > EINKY_SPI_CHUNK)
            n = EINKY_SPI_CHUNK;
        if (spi_xfer(p->spi_fd, data + off, n) < 0)
            return -1;
    }
    return 0;
}

einky_panel_t *einky_open(const char *spi_dev) {
    einky_panel_t *p = calloc(1, sizeof(*p));
    if (!p)
        return NULL;

    p->spi_fd = open(spi_dev, O_RDWR);
    if (p->spi_fd < 0) {
        free(p);
        return NULL;
    }

    uint32_t mode = SPI_MODE_0;
    uint8_t bits = EINKY_SPI_BITS;
    uint32_t hz = EINKY_SPI_HZ;
    if (ioctl(p->spi_fd, SPI_IOC_WR_MODE32, &mode) < 0 ||
        ioctl(p->spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits) < 0 ||
        ioctl(p->spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &hz) < 0) {
        close(p->spi_fd);
        free(p);
        return NULL;
    }

    const char *chip_path = getenv("EINKY_GPIOCHIP");
    if (!chip_path || !*chip_path)
        chip_path = EINKY_GPIOCHIP_DEFAULT;

    p->chip = gpiod_chip_open(chip_path);
    if (!p->chip)
        goto fail;

    p->lines = request_control_lines(p->chip);
    if (!p->lines)
        goto fail;

    return p;

fail:
    if (p->chip)
        gpiod_chip_close(p->chip);
    if (p->spi_fd >= 0)
        close(p->spi_fd);
    free(p);
    return NULL;
}

int einky_init(einky_panel_t *p) {
    if (!p)
        return -EINVAL;

    /* Hardware reset (RST is active-low: pulse low, then release). */
    gpio_set(p->lines, EINKY_PIN_RST, 1);
    msleep(10);
    gpio_set(p->lines, EINKY_PIN_RST, 0);
    msleep(10);
    gpio_set(p->lines, EINKY_PIN_RST, 1);
    msleep(10);

    /* Power settings (from GoodDisplay reference init for UC8253). */
    static const uint8_t pwr[] = {0x03, 0x00, 0x2b, 0x2b, 0x09};
    static const uint8_t booster[] = {0x17, 0x17, 0x17};

    if (send_cmd(p, 0x01) < 0)
        return -1; /* POWER_SETTING */
    if (send_data(p, pwr, sizeof(pwr)) < 0)
        return -1;
    if (send_cmd(p, 0x06) < 0)
        return -1; /* BOOSTER_SOFT_START */
    if (send_data(p, booster, sizeof(booster)) < 0)
        return -1;
    if (send_cmd(p, 0x04) < 0)
        return -1; /* POWER_ON */
    if (wait_busy(p) < 0)
        return -1;

    return 0;
}

static int push_frame(einky_panel_t *p, uint8_t cmd, const uint8_t *frame, size_t len) {
    if (!p || !frame)
        return -EINVAL;
    if (len != EINKY_FRAME_BYTES)
        return -EINVAL;
    if (send_cmd(p, cmd) < 0)
        return -1;
    if (EINKY_INVERT_FRAME) {
        for (size_t i = 0; i < len; i++)
            p->txbuf[i] = (uint8_t)~frame[i];
        if (send_data(p, p->txbuf, len) < 0)
            return -1;
    } else {
        if (send_data(p, frame, len) < 0)
            return -1;
    }
    if (send_cmd(p, 0x12) < 0)
        return -1; /* DISPLAY_REFRESH */
    return wait_busy(p);
}

int einky_full_refresh(einky_panel_t *p, const uint8_t *frame, size_t len) {
    return push_frame(p, 0x13, frame, len); /* DATA_START_TRANSMISSION_2 (new image) */
}

int einky_partial_refresh(einky_panel_t *p, const uint8_t *frame, size_t len) {
    /* TODO: switch to partial LUT before pushing. For now we re-use the full
     * path so the surface compiles and the python tests can drive it. */
    return push_frame(p, 0x13, frame, len);
}

int einky_sleep(einky_panel_t *p) {
    if (!p)
        return -EINVAL;
    if (send_cmd(p, 0x02) < 0)
        return -1; /* POWER_OFF */
    if (wait_busy(p) < 0)
        return -1;
    static const uint8_t deep[] = {0xa5};
    if (send_cmd(p, 0x07) < 0)
        return -1; /* DEEP_SLEEP */
    return send_data(p, deep, sizeof(deep));
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
