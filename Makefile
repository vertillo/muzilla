SHELL := /bin/bash

SANDBOX_COMPOSE := docker compose -f docker-compose.sandbox.yml
SANDBOX_EXPORT_DIR ?= .pi/sandbox-exports
SANDBOX_ARCHIVE ?= $(SANDBOX_EXPORT_DIR)/pi-home.tar.gz
SANDBOX_ALPINE_IMAGE ?= alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d

.PHONY: sandbox sandbox-rebuild sandbox-sync sandbox-clean sandbox-logs \
	sandbox-import sandbox-test sandbox-gates sandbox-verify

sandbox: ## Lancia pi in sandbox (auto-verifica integrità, --force per bypass dirty)
	./scripts/pi-sandbox $(ARGS)

sandbox-verify: ## Verifica integrità sandbox senza avviare Pi (Q4/Q5/Q6/Q8)
	./scripts/pi-sandbox --verify

sandbox-rebuild: ## Ricostruisce immagine sandbox (verifica inclusa, usa ARGS="--force" per bypass dirty)
	./scripts/pi-sandbox --rebuild $(ARGS)

sandbox-sync: ## Verifica sync di estensioni e gate nel container
	$(SANDBOX_COMPOSE) run --rm sandbox true

sandbox-clean: ## Rimuove volumi sandbox (con conferma)
	read -r -p "Rimuovere tutti i volumi della sandbox Muzilla? [y/N] "; \
	case "$$REPLY" in [yY]|[yY][eE][sS]) $(SANDBOX_COMPOSE) down -v --remove-orphans ;; *) echo "Annullato." ;; esac

sandbox-logs: ## Esporta la HOME Pi persistente
	mkdir -p "$(SANDBOX_EXPORT_DIR)"
	docker run --rm \
		--mount type=volume,src=muzilla-sandbox-pi-home,dst=/vol,readonly \
		--mount type=bind,src="$(CURDIR)/$(SANDBOX_EXPORT_DIR)",dst=/out \
		"$(SANDBOX_ALPINE_IMAGE)" sh -ec \
		'tar -C /vol -czf /out/pi-home.tar.gz . && sha256sum /out/pi-home.tar.gz \
		  | sed "s#/out/##" > /out/pi-home.tar.gz.sha256'

sandbox-import: ## Importa una HOME Pi esportata (con conferma)
	if test ! -f "$(SANDBOX_ARCHIVE)"; then \
		echo "Archivio mancante: $(SANDBOX_ARCHIVE)" >&2; exit 1; \
	fi
	read -r -p "Importare la HOME Pi e sovrascrivere il volume sandbox? [y/N] "; \
	case "$$REPLY" in [yY]|[yY][eE][sS]) \
		docker run --rm \
			--mount type=volume,src=muzilla-sandbox-pi-home,dst=/vol \
			--mount type=bind,src="$(CURDIR)/$(SANDBOX_EXPORT_DIR)",dst=/in,readonly \
			"$(SANDBOX_ALPINE_IMAGE)" sh -ec \
			'cd /in && sha256sum -c pi-home.tar.gz.sha256 && \
			test -z "$$(find /vol -mindepth 1 -print -quit)" && \
			tar -C /vol -xzf /in/pi-home.tar.gz' ;; \
		*) echo "Annullato." ;; esac

sandbox-test: ## Esegue il test di regressione della sandbox
	./scripts/test-sandbox.sh

sandbox-gates: ## Esegue tutti i gate dentro la sandbox (unset dei default app)
	$(SANDBOX_COMPOSE) run --rm sandbox bash scripts/sandbox-gates.sh
