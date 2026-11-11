param([switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot
try {
    foreach ($tool in @('uv', 'node', 'npm')) { Get-Command $tool -ErrorAction Stop | Out-Null }
    & uv sync --locked --cache-dir .cache/uv
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if (!$SkipBuild) {
        Push-Location (Join-Path $projectRoot 'web')
        try {
            & npm ci --registry=https://registry.npmjs.org
            if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
            & npm run build
            if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
        } finally { Pop-Location }
    }
    & (Join-Path $PSScriptRoot 'start-web.ps1') -Demo -Production
} finally { Pop-Location }
