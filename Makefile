UV ?= uv
PDF_DIR ?= papers
OUT ?= outputs/predictions.jsonl

.PHONY: help sync test check extract-check extract-dry extract-smoke extract extract-agent extract-one-shot validate

help:
	@echo "MKEA extraction targets (uv-managed):"
	@echo "  make sync              - uv sync"
	@echo "  make test              - run extraction unit tests"
	@echo "  make check             - verify LLM endpoint/key/model"
	@echo "  make extract-dry       - parse PDFs and show context stats (no LLM calls)"
	@echo "  make extract-smoke     - extract the first 5 papers"
	@echo "  make extract           - extract all papers with anchored agent mode"
	@echo "  make extract-agent     - extract all papers with pure tool-agent mode"
	@echo "  make extract-one-shot  - extract all papers with the one-shot fallback"
	@echo "  make validate          - validate the output JSONL"

sync:
	$(UV) sync

test:
	$(UV) run python -m unittest discover -s tests -v

check:
	$(UV) run mkea-extract check

extract-check: check

extract-dry:
	$(UV) run mkea-extract extract --pdf-dir $(PDF_DIR) --dry-run --limit 10

extract-smoke:
	$(UV) run mkea-extract extract --pdf-dir $(PDF_DIR) --limit 5 --out outputs/smoke.jsonl

extract:
	$(UV) run mkea-extract extract --pdf-dir $(PDF_DIR) --out $(OUT) --resume

extract-agent:
	$(UV) run mkea-extract extract --pdf-dir $(PDF_DIR) --out outputs/predictions-agent.jsonl --resume --mode agent

extract-one-shot:
	$(UV) run mkea-extract extract --pdf-dir $(PDF_DIR) --out outputs/predictions-one-shot.jsonl --resume --no-agent

validate:
	$(UV) run mkea-extract validate --pred $(OUT)
