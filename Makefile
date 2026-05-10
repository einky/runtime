.PHONY: setup build-c compile-c-only lint format test run-dev run-dev-tcp run-input-net run-prod clean

PYTHON ?= python3
VENV   ?= .venv
PIP    := $(VENV)/bin/pip
PY     := $(VENV)/bin/python

# Pick venv python if present, else fall back to system (used by CI).
PY_RUN := $(shell test -x $(VENV)/bin/python && echo $(VENV)/bin/python || echo $(PYTHON))

setup: $(VENV)/bin/activate
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	$(VENV)/bin/pre-commit install

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

# Build the CFFI extension (requires cffi installed for the chosen python).
build-c:
	$(PY_RUN) -c "import cffi" 2>/dev/null || $(PYTHON) -m pip install --user cffi
	$(PY_RUN) src/spi_driver/build.py

# Plain C compile-check, no python involved. Used by the C CI job.
compile-c-only:
	$(CC) -std=c11 -Wall -Wextra -Werror -c src/spi_driver/spi_driver.c -o /tmp/spi_driver.o
	@rm -f /tmp/spi_driver.o

lint:
	$(VENV)/bin/ruff check src tests
	$(VENV)/bin/ruff format --check src tests
	$(VENV)/bin/mypy src tests
	@command -v clang-format >/dev/null && \
	  find src/spi_driver -type f \( -name '*.c' -o -name '*.h' \) -print0 | \
	  xargs -0 clang-format --dry-run --Werror || \
	  echo "clang-format not installed, skipping C format check"

format:
	$(VENV)/bin/ruff check --fix src tests
	$(VENV)/bin/ruff format src tests
	@command -v clang-format >/dev/null && \
	  find src/spi_driver -type f \( -name '*.c' -o -name '*.h' \) -print0 | \
	  xargs -0 clang-format -i || true

test:
	$(VENV)/bin/pytest

run-dev:
	EINKY_BACKEND=socket EINKY_SOCKET_PATH=/tmp/einky-preview.sock \
	  $(PY) -m frame_processor

# WSL + ESP32 bridge demo path (ADR 0006).
run-dev-tcp:
	EINKY_BACKEND=tcp EINKY_TCP_HOST=0.0.0.0 EINKY_TCP_PORT=5333 \
	  $(PY) -m frame_processor

run-input-net:
	EINKY_INPUT_BACKEND=net EINKY_INPUT_HOST=0.0.0.0 EINKY_INPUT_PORT=5334 \
	  $(PY) -m input

run-prod:
	EINKY_BACKEND=spi $(PY) -m frame_processor

clean:
	rm -rf build/ src/spi_driver/build/ *.egg-info
	find . -name __pycache__ -type d -exec rm -rf {} +
	find . -name '*.so' -delete
	find . -name '*.o' -delete
