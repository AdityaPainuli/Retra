# Retra — Your Day, In Focus
# Run `make setup` once, then `make start` to begin tracking.

PYTHON     := python3
VENV       := venv
PIP        := $(VENV)/bin/pip
PYTHON_BIN := $(VENV)/bin/python
DASHBOARD  := ui/dashboard
NODE_MOD   := $(DASHBOARD)/node_modules

.PHONY: setup start stop status dashboard capture menubar journal build-dashboard clean help

## ── First-Time Setup ───────────────────────────────────────────

setup: $(VENV)/bin/activate .env config/settings.toml ## One-command setup: venv + deps + config
	@echo ""
	@echo "  ✅ Setup complete!"
	@echo ""
	@echo "  Next steps:"
	@echo "    1. Grant macOS permissions (Accessibility + Screen Recording)"
	@echo "       System Settings → Privacy & Security → add your terminal app"
	@echo "    2. (Optional) Add your Anthropic API key to .env for AI summaries"
	@echo "    3. Run: make start"
	@echo ""

$(VENV)/bin/activate: requirements.txt
	@echo "  Creating virtual environment..."
	@$(PYTHON) -m venv $(VENV)
	@echo "  Installing Python dependencies..."
	@$(PIP) install --upgrade pip -q
	@$(PIP) install -r requirements.txt -q
	@touch $(VENV)/bin/activate

.env:
	@cp .env.example .env
	@echo "  Created .env from .env.example"

config/settings.toml:
	@if [ -f config/settings.example.toml ]; then \
		cp config/settings.example.toml config/settings.toml; \
		echo "  Created config/settings.toml from example"; \
	fi

## ── Run ────────────────────────────────────────────────────────

start: $(VENV)/bin/activate ## Start everything (capture + dashboard + menubar)
	@$(PYTHON_BIN) main.py start

stop: ## Stop all Retra processes
	@$(PYTHON_BIN) main.py stop

status: $(VENV)/bin/activate ## Show recording health and today's stats
	@$(PYTHON_BIN) main.py status

## ── Individual Services ────────────────────────────────────────

capture: $(VENV)/bin/activate ## Start capture daemon (foreground)
	@$(PYTHON_BIN) main.py capture

dashboard: $(VENV)/bin/activate ## Start web dashboard (foreground)
	@$(PYTHON_BIN) main.py dashboard

menubar: $(VENV)/bin/activate ## Start menubar app
	@$(PYTHON_BIN) main.py menubar

## ── Utilities ──────────────────────────────────────────────────

journal: $(VENV)/bin/activate ## Generate today's Obsidian journal
	@$(PYTHON_BIN) main.py journal

install: $(VENV)/bin/activate ## Install as macOS Launch Agent (auto-start on login)
	@$(PYTHON_BIN) main.py install

uninstall: $(VENV)/bin/activate ## Remove macOS Launch Agent
	@$(PYTHON_BIN) main.py uninstall

## ── Dashboard Development ──────────────────────────────────────

build-dashboard: $(NODE_MOD) ## Rebuild the React dashboard (requires Node.js)
	@echo "  Building dashboard..."
	@cd $(DASHBOARD) && npm run build
	@echo "  Dashboard built → $(DASHBOARD)/dist/"

dev-dashboard: $(NODE_MOD) $(VENV)/bin/activate ## Run dashboard in dev mode (hot reload)
	@echo "  Starting API server (background)..."
	@$(PYTHON_BIN) main.py dashboard &
	@echo "  Starting Vite dev server..."
	@cd $(DASHBOARD) && npm run dev

$(NODE_MOD): $(DASHBOARD)/package.json
	@echo "  Installing Node.js dependencies..."
	@cd $(DASHBOARD) && npm install
	@touch $(NODE_MOD)

## ── Cleanup ────────────────────────────────────────────────────

clean: ## Remove venv and cached files
	@rm -rf $(VENV) __pycache__ capture/__pycache__ storage/__pycache__ \
		config/__pycache__ export/__pycache__ ui/__pycache__
	@echo "  Cleaned build artifacts. Data in data/ is preserved."

clean-all: clean ## Remove everything including runtime data
	@echo ""
	@echo "  ⚠️  This will delete ALL captured data (database + screenshots)."
	@read -p "  Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	@rm -rf data/
	@echo "  All data removed."

## ── Help ───────────────────────────────────────────────────────

help: ## Show this help
	@echo ""
	@echo "  Retra — Your Day, In Focus"
	@echo ""
	@echo "  Usage: make <target>"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*## "}; {printf "    \033[36m%-20s\033[0m %s\n", $$1, $$2}'
	@echo ""

.DEFAULT_GOAL := help
