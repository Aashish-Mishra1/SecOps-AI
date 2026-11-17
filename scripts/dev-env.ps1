# Dot-source from the repository root: . .\scripts\dev-env.ps1
$root = Split-Path $PSScriptRoot -Parent
foreach ($line in Get-Content -LiteralPath (Join-Path $root '.env')) {
    if ($line -match '^([A-Z_]+)=(.*)$' -and $Matches[2] -ne '') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
$env:DATABASE_URL = "postgresql+psycopg://secops:$($env:POSTGRES_PASSWORD)@127.0.0.1:55432/secops"
Write-Output 'Loaded local development configuration without printing secrets.'
