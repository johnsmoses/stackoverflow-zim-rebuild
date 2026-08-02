SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help config-check check-lock bootstrap restore-baseline redis update recover-images finalize-placeholders assemble verify

help: ## Print this help
	@echo "stackoverflow-zim-rebuild targets:"
	@echo "  help                  Show this help"
	@echo "  config-check          Validate .env / WORK_ROOT configuration"
	@echo "  check-lock            Verify sotoki.lock: base 157ca9a, 9 patches, tree hash (exits nonzero on mismatch)"
	@echo "  bootstrap             Tool checks, WORK_ROOT skeleton + restore marker, patched sotoki"
	@echo "  restore-baseline      Restore July 2026 baseline bundle (set BASELINE_BUNDLE)"
	@echo "  redis                 Manage the isolated redis instance (start|stop|status|restart)"
	@echo "  update                Prerequisite guard: fails fast until baseline + dump + SNAPSHOT_ID are ready (see docs/update-runbook.md)"
	@echo "  recover-images        Prerequisite guard: fails fast with a pointer to the recovery pipeline (see docs/recovery-runbook.md)"
	@echo "  finalize-placeholders Prerequisite guard: fails fast with a pointer to the finalize step (see docs/recovery-runbook.md)"
	@echo "  assemble              Build ZIM"
	@echo "  verify                zimcheck/zimdump + baseline comparison"

config-check: ## Validate configuration (functional stub)
	@bash bin/common.sh config-check

check-lock: ## Verify sotoki.lock is complete and current (base 157ca9a, 9 patches, tree hash)
	@test -f sotoki.lock || { echo "ERROR: check-lock: sotoki.lock not found" >&2; exit 1; }
	@python3 -c "import sys,yaml; d=yaml.safe_load(open('sotoki.lock')); u=(d.get('upstream') or {}) if isinstance(d,dict) else {}; p=(d.get('patch_series') or {}) if isinstance(d,dict) else {}; errs=[]; errs.append('upstream.base_commit=%r, expected 157ca9a' % (u.get('base_commit'),)) if u.get('base_commit')!='157ca9a' else None; errs.append('patch_series.commit_count=%r, expected 9' % (p.get('commit_count'),)) if p.get('commit_count')!=9 else None; th=p.get('tree_hash'); errs.append('patch_series.tree_hash=%r, expected 40-hex SHA-1' % (th,)) if not (isinstance(th,str) and len(th)==40 and all(c in '0123456789abcdef' for c in th)) else None; (print('OK: sotoki.lock valid (base 157ca9a, 9 patches, tree_hash %s)' % th) or sys.exit(0)) if not errs else (print('ERROR: check-lock: sotoki.lock validation failed:', file=sys.stderr) or print('\n'.join('  - '+e for e in errs), file=sys.stderr) or sys.exit(1))"

bootstrap:
	@bash bin/bootstrap

restore-baseline:
	@bash bin/restore-baseline --bundle "${BASELINE_BUNDLE:?set BASELINE_BUNDLE}" $$ARGS

redis:
	@bash bin/redis ${ARGS}

update:
	@echo "ERROR: update requires a baseline bundle, a fresh dump archive, and a SNAPSHOT_ID; the patched-sotoki --incremental run is documented in docs/update-runbook.md" >&2; exit 1

recover-images:
	@echo "ERROR: recover-images requires a completed update run and the recovery inputs; the recovery pipeline (inventory -> classify -> IA manifest -> XML scan -> edge resolver -> sync) is documented in docs/recovery-runbook.md" >&2; exit 1

finalize-placeholders:
	@echo "ERROR: finalize-placeholders requires a completed recovery run and verified placeholder candidates; the finalize step (recovery/finalize_unavailable.py) is documented in docs/recovery-runbook.md" >&2; exit 1

assemble:
	@bash bin/assemble --snapshot-id "${SNAPSHOT_ID:-current}" --flavour "${FLAVOUR:-full}" $$ARGS

verify:
	@scripts/audit_stage.py --stage-dir $${STAGE_DIR:?set STAGE_DIR} --placeholder-spec data/placeholder-spec.json \
		&& scripts/audit_zim.py --zim $${ZIM:?set ZIM} \
		&& scripts/compare_baseline.py --zim $${ZIM} --baseline data/baseline-2026-07.json