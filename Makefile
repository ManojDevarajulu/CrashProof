ifeq ($(OS),Windows_NT)
SHELL := cmd.exe
.SHELLFLAGS := /c
PYTHON ?= py -3.12
VENV := if not exist .venv $(PYTHON) -m venv .venv
PYTHON_BIN := .venv\Scripts\python
PYTEST_BIN := .venv\Scripts\pytest
RUFF_BIN := .venv\Scripts\ruff
MYPY_BIN := .venv\Scripts\mypy
else
PYTHON ?= python3.12
VENV := test -d .venv || $(PYTHON) -m venv .venv
PYTHON_BIN := .venv/bin/python
PYTEST_BIN := .venv/bin/pytest
RUFF_BIN := .venv/bin/ruff
MYPY_BIN := .venv/bin/mypy
endif

SEEDS ?= 200
RUNTIMES ?= naive,keyed,hardened
SEED ?= 1
RUNTIME ?= hardened

.PHONY: setup test lint eval replay report readme

setup:
	$(VENV)
	$(PYTHON_BIN) -m pip install -e ".[dev]"

test:
	$(PYTEST_BIN) -q

lint:
	$(RUFF_BIN) check .
	$(RUFF_BIN) format --check .
	$(MYPY_BIN) --strict src

eval:
	$(PYTHON_BIN) -m crashproof.cli eval --seeds $(SEEDS) --runtimes $(RUNTIMES)

replay:
	$(PYTHON_BIN) -m crashproof.cli replay --seed $(SEED) --runtime $(RUNTIME)

report:
	$(PYTHON_BIN) -m crashproof.cli report

readme: report
	$(PYTHON_BIN) -m crashproof.cli readme
