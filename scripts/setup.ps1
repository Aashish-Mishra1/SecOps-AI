$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$target = Join-Path $root '.env'
if (Test-Path -LiteralPath $target) { Write-Output '.env already exists; preserving it.'; exit 0 }
function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
$content = Get-Content -LiteralPath (Join-Path $root '.env.example') -Raw
$content = $content.Replace('POSTGRES_PASSWORD=', ('POSTGRES_PASSWORD=' + (New-RandomSecret)))
$content = $content.Replace('JWT_SECRET=', ('JWT_SECRET=' + (New-RandomSecret)))
$content = $content.Replace('DEMO_PASSWORD=', ('DEMO_PASSWORD=' + (New-RandomSecret)))
[System.IO.File]::WriteAllText($target, $content)
Write-Output 'Created .env with random local secrets. Read DEMO_PASSWORD locally to sign in.'
