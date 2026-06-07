PYTHON ?= .venv/bin/python
SCRIPT ?= scripts/example.json

.PHONY: check test validate build

# Readiness check (Russian, beginner-friendly). Runs on a bare clone with system
# python3 — no venv needed. Reports what's installed, what media is present, and
# what can be built. See also: НАЧНИ_ЗДЕСЬ.md
check:
	python3 check_setup.py

# Run the test suite (integration tests needing local media auto-skip).
test:
	$(PYTHON) -m pytest

# Validate a VideoScript JSON against the schema.
#   make validate SCRIPT=scripts/example.json
validate:
	$(PYTHON) -m src.cli validate $(SCRIPT)

# Build a reel from a VideoScript JSON (standard / library pipeline).
#   make build SCRIPT=scripts/example.json
build:
	$(PYTHON) -m src.cli build $(SCRIPT)
