INVENTORY ?= inventory/hosts.yml
PLAYBOOK  := ansible-playbook -i $(INVENTORY)
LIMIT     ?=
ARGS      ?=
LIMIT_ARG := $(if $(LIMIT),--limit $(LIMIT),)

.PHONY: help deps app lint syntax test fuzz webui-test webui-browser image smoke ui site build-installer provision baseline keys

help: ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

deps: ## Install Python deps (including the web UI's) and Ansible collections
	pip install -r requirements.txt -r requirements-dev.txt -r requirements-webui.in
	ansible-galaxy collection install -r requirements.yml

app: ## Install the web UI app's packages (npm ci) and build it into webui/usas_web/static/app
	cd webui/frontend && npm ci --ignore-scripts && npm run build

lint: ## Run yamllint, ansible-lint and ruff
	yamllint .
	ansible-lint
	ruff check

syntax: ## Syntax-check every playbook against the example inventory
	ansible-playbook -i inventory/hosts.example.yml --syntax-check playbooks/site.yml

test: ## Integration tests against containers and a mock Proxmox API (needs Docker)
	tests/run.sh

fuzz: ## Atheris fuzz targets, FUZZ_SECONDS each (Linux x86-64; needs requirements-fuzz.txt)
	tests/fuzz/run.sh

webui-test: ## The web UI's unit tests (server and app), with coverage
	cd webui && python -m pytest
	cd webui/frontend && npm run check && npm test

webui-browser: ## The web UI in Chromium: keyboard, focus, live regions, axe-core (after make app and the collections)
	tests/webui-browser.sh

image: ## Build the container image as ubuntu-server-auto-setup:dev
	docker build -t ubuntu-server-auto-setup:dev .

smoke: ## Build the image and smoke-test it against the mock Proxmox API (needs Docker)
	tests/webui-smoke.sh

ui: app ## Build the app, then run the web UI from this checkout on http://localhost:8080, data in .webui-data/
	USAS_HOST=127.0.0.1 USAS_PROJECT_DIR=$(CURDIR) USAS_DATA_DIR=$(CURDIR)/.webui-data PYTHONPATH=webui python -m usas_web

site: ## Build installer, provision missing VMs, apply baseline
	$(PLAYBOOK) playbooks/site.yml $(LIMIT_ARG) $(ARGS)

build-installer: ## Download and remaster the Ubuntu ISO on the Proxmox nodes the servers use
	$(PLAYBOOK) playbooks/build-installer.yml $(LIMIT_ARG) $(ARGS)

provision: ## Create and auto-install VMs that don't exist yet
	$(PLAYBOOK) playbooks/provision-vms.yml $(LIMIT_ARG) $(ARGS)

baseline: ## Apply the server baseline (users, SSH, UFW, Fail2Ban, updates)
	$(PLAYBOOK) playbooks/baseline.yml $(LIMIT_ARG) $(ARGS)

keys: ## Re-sync GitHub SSH keys only
	$(PLAYBOOK) playbooks/baseline.yml --tags ssh_keys $(LIMIT_ARG) $(ARGS)
