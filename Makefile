PYTHON ?= .venv/bin/python
SCRIPT ?= scripts/example.json

.PHONY: test validate build

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
