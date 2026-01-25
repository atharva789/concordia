.PHONY: install help clean

install:
	@bash install.sh

help:
	@echo "Concordia - Multi-user prompt party"
	@echo ""
	@echo "Available commands:"
	@echo "  make install    - Install concordia and run initial setup"
	@echo "  make host       - Start a party (host)"
	@echo "  make help       - Show this help"
	@echo ""
	@echo "For client commands, use concordia_client directly"

host:
	concordia_host

.DEFAULT_GOAL := help
