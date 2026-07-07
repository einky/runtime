/* GDEM0397T81P SPI driver: minimal init / refresh / sleep API.
 *
 * The panel is a 3.97" 800x480 1-bit e-paper module from GoodDisplay using a
 * Solomon Systech SSD1677 controller. Panel geometry and the SPI / control pins come from
 * the shared contract via contract.h (generated from meta/shared/hardware.toml);
 * do not hard-code them here.
 */
#ifndef EINKY_SPI_DRIVER_H
#define EINKY_SPI_DRIVER_H

#include <stddef.h>
#include <stdint.h>

#include "contract.h"

typedef struct einky_panel einky_panel_t;

/* Open the SPI device and initialise GPIO. Returns NULL on failure. */
einky_panel_t *einky_open(const char *spi_dev);

/* Run the panel power-on sequence. Must be called before any refresh. */
int einky_init(einky_panel_t *p);

/* Push a full frame and trigger a full LUT refresh. `frame` length must equal
 * EINKY_FRAME_BYTES, MSB-first packed. Blocks until BUSY deasserts. */
int einky_full_refresh(einky_panel_t *p, const uint8_t *frame, size_t len);

/* Same as full_refresh but uses the partial LUT (faster, accumulates ghosting). */
int einky_partial_refresh(einky_panel_t *p, const uint8_t *frame, size_t len);

/* Send the deep-sleep command. Call before einky_close to avoid panel damage. */
int einky_sleep(einky_panel_t *p);

/* Close the SPI device and release GPIO. */
void einky_close(einky_panel_t *p);

#endif /* EINKY_SPI_DRIVER_H */
