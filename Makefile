SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help config-check check-lock bootstrap restore-baseline redis update recover-images finalize-placeholders assemble verify

help: ## Print this help
	@echo "stackoverflow-zim-rebuild targets:"
	@echo "  help                  Show this help"
	@echo "  config-check          Validate .env / WORK_ROOT configuration"
	@echo "  check-lock            Verify sotoki.lock is complete and current"
	@echo "  bootstrap             Tool checks, WORK_ROOT skeleton + restore marker, patched sotoki"
	@echo "  restore-baseline      Restore July 2026 baseline bundle (set BASELINE_BUNDLE)"
	@echo "  redis                 Manage the isolated redis instance (start|stop|status|restart)"
	@echo "  update                Incremental update from fresh dump"
	@echo "  recover-images        Recover missing images"
	@echo "  finalize-placeholders Replace placeholder bytes"
	@echo "  assemble              Build ZIM"
	@echo "  verify                zimcheck/zimdump + baseline comparison"

config-check: ## Validate configuration (functional stub)
	@bash bin/common.sh config-check

check-lock:
	@echo "check-lock: not yet implemented"

bootstrap:
	@bash bin/bootstrap

restore-baseline:
	@bash bin/restore-baseline --bundle "${BASELINE_BUNDLE:?set BASELINE_BUNDLE}" $$ARGS

redis:
	@bash bin/redis ${ARGS}

update:
	@echo "update: not yet implemented"

recover-images:
	@echo "recover-images: not yet implemented"

finalize-placeholders:
	@echo "finalize-placeholders: not yet implemented"

assemble:
	@echo "assemble: not yet implemented"

verify:
	@echo "verify: not yet implemented"