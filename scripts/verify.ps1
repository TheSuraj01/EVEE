# The full quality gate. Run before every commit; CI runs the same thing.
# $ErrorActionPreference does not see a native command's exit code, so each one is checked.
$ErrorActionPreference = "Stop"
function Assert-Passed([string]$step) {
    if ($LASTEXITCODE -ne 0) { throw "$step failed (exit $LASTEXITCODE)" }
}

Write-Host "== pytest ==" -ForegroundColor Cyan
python -m pytest -q; Assert-Passed "pytest"
Write-Host "== ruff ==" -ForegroundColor Cyan
ruff check .; Assert-Passed "ruff check"
ruff format --check .; Assert-Passed "ruff format"
Write-Host "== mypy ==" -ForegroundColor Cyan
mypy src; Assert-Passed "mypy"
Write-Host "`nGate green." -ForegroundColor Green
