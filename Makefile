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
	$(CARGO) fmt --all

.PHONY: lint
lint: ##H Run Cargo clippy lints
	$(CARGO) clippy --workspace --all-targets --all-features -- -D warnings

.PHONY: test
test: ##H Run Cargo tests
	$(CARGO) test --workspace --all-targets --all-features

.PHONY: doc
doc: ##H Build Rust documentation
	$(CARGO) doc --workspace --no-deps

.PHONY: macros
macros: ##H Show Rust macro expansion statistics
	$(CARGO) +nightly rustc --workspace --all-targets -- -Zmacro-stats

.PHONY: extras/cloc
extras/cloc: ##H Count lines of code for the HEAD revision
	cloc --git HEAD --fmt=2
