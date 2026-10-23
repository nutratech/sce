SHELL := /bin/bash
.DEFAULT_GOAL := _help

MAKEFLAGS += --no-print-directory

CARGO ?= cargo

.PHONY: _help
_help:
	@grep -hE '^[a-zA-Z0-9_/-]+:[[:space:]]*##H .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":[[:space:]]*##H "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

.PHONY: help
help: _help

.PHONY: format
format: ##H Run pre-commit hooks and format Rust code
	pre-commit run --all-files
	prettier --prose-wrap always --print-width 80 --write $$(git ls-files '*.md' '*.y*ml')
	markdownlint $$(git ls-files '*.md')
	# $(CARGO) fmt --all
	black $$(git ls-files '*.py')
	isort $$(git ls-files '*.py')
	ruff format $$(git ls-files '*.py')
	ruff check --fix $$(git ls-files '*.py')

.PHONY: lint
lint: ##H Run lints
	# $(CARGO) clippy --workspace --all-targets --all-features -- -D warnings
	flake8 --max-line-length 88 $$(git ls-files '*.py')
	ruff check $$(git ls-files '*.py')
	mypy $$(git ls-files '*.py')

# .PHONY: test
# test: ##H Run tests
# 	$(CARGO) test --workspace --all-targets --all-features

# .PHONY: doc
# doc: ##H Build Rust documentation
# 	$(CARGO) doc --workspace --no-deps

# .PHONY: macros
# macros: ##H Show Rust macro expansion statistics
# 	$(CARGO) +nightly rustc --workspace --all-targets -- -Zmacro-stats

.PHONY: extras/cloc
extras/cloc: ##H Count lines of code for the HEAD revision
	cloc --git HEAD --fmt=2
