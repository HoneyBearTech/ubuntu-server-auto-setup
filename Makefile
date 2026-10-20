INVENTORY ?= inventory/hosts.yml
PLAYBOOK  := ansible-playbook -i $(INVENTORY)
LIMIT     ?=
ARGS      ?=
LIMIT_ARG := $(if $(LIMIT),--limit $(LIMIT),)

.PHONY: help deps lint syntax site build-installer provision baseline keys

help: ## Show targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

deps: ## Install Python deps and Ansible collections
	pip install -r requirements.txt -r requirements-dev.txt
	ansible-galaxy collection install -r requirements.yml

lint: ## Run yamllint and ansible-lint
	yamllint .
	ansible-lint

syntax: ## Syntax-check every playbook against the example inventory
	ansible-playbook -i inventory/hosts.example.yml --syntax-check playbooks/site.yml

site: ## Build installer, provision missing VMs, apply baseline
	$(PLAYBOOK) playbooks/site.yml $(LIMIT_ARG) $(ARGS)

build-installer: ## Download and remaster the Ubuntu ISO on the Proxmox node(s)
	$(PLAYBOOK) playbooks/build-installer.yml $(ARGS)

provision: ## Create and auto-install VMs that don't exist yet
	$(PLAYBOOK) playbooks/provision-vms.yml $(LIMIT_ARG) $(ARGS)

baseline: ## Apply the server baseline (users, SSH, UFW, Fail2Ban, updates)
	$(PLAYBOOK) playbooks/baseline.yml $(LIMIT_ARG) $(ARGS)

keys: ## Re-sync GitHub SSH keys only
	$(PLAYBOOK) playbooks/baseline.yml --tags ssh_keys $(LIMIT_ARG) $(ARGS)
