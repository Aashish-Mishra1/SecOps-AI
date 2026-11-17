$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    . ./scripts/dev-env.ps1
    docker compose -f compose.yaml -f compose.dev.yaml up -d --wait db
    if ($LASTEXITCODE -ne 0) { throw 'Database startup failed' }
    Push-Location backend
    try {
        python -m uv sync --frozen
        if ($LASTEXITCODE -ne 0) { throw 'Dependency sync failed' }
        python -m uv run pytest -q
        if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed' }
        python -m uv run ruff check .
        if ($LASTEXITCODE -ne 0) { throw 'Backend lint failed' }
    } finally { Pop-Location }
    Push-Location frontend
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed' }
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
} finally { Pop-Location }
