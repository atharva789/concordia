.PHONY: install help clean build release

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
	@perl -0pi -e 's/^__version__ = \"[^\"]+\"/__version__ = \"$(VERSION)\"/m' concordia/__init__.py
	@git add concordia/__init__.py
	@git commit -m "Release v$(VERSION)"
	@git tag -a v$(VERSION) -m "v$(VERSION)"
	@git push origin main --tags

.DEFAULT_GOAL := help
