.PHONY: install help clean build release
 .PHONY: publish

install:
	@bash install.sh

help:
	@echo "Concordia - Multi-user prompt party"
	@echo ""
	@echo "Available commands:"
	@echo "  make install    - Install concordia and run initial setup"
	@echo "  make host       - Start a party (host)"
	@echo "  make build      - Build source/wheel distributions"
	@echo "  make release    - Bump version, tag, and push (VERSION=x.y.z)"
	@echo "  make publish    - Trigger GitHub Actions publish (requires gh auth)"
	@echo "  make help       - Show this help"
	@echo ""
	@echo "For client commands, use concordia_client directly"

host:
	concordia_host

build:
	python -m build

release:
	@if [ -z "$(VERSION)" ]; then echo "VERSION is required: make release VERSION=x.y.z"; exit 1; fi
	@git diff --quiet || (echo "Working tree not clean"; exit 1)
	@python - <<'PY'
from pathlib import Path
import re
path = Path("concordia/__init__.py")
text = path.read_text(encoding="utf-8")
new = re.sub(r'^__version__\\s*=\\s*\"[^\"]+\"', f'__version__ = \"{VERSION}\"', text, flags=re.M)
path.write_text(new, encoding="utf-8")
PY
	@git add concordia/__init__.py
	@git commit -m "Release v$(VERSION)"
	@git tag -a v$(VERSION) -m "v$(VERSION)"
	@git push origin main --tags

publish:
	@gh workflow run workflow.yml -f publish=true

.DEFAULT_GOAL := help
