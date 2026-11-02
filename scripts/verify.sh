#!/usr/bin/env bash
# The full quality gate. Identical to scripts/verify.ps1 and to CI.
set -euo pipefail
echo "== pytest =="   && python -m pytest -q
echo "== ruff =="     && ruff check . && ruff format --check .
echo "== mypy =="     && mypy src
echo && echo "Gate green."
