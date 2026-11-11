param([switch]$Live, [switch]$Demo, [switch]$Production)
$ErrorActionPreference = 'Stop'
if ($Live -and $Demo) { throw 'Choose Live or Demo, not both.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot 'web'
$nextBin = Join-Path $webRoot 'node_modules/next/dist/bin/next'
if (!(Test-Path -LiteralPath $nextBin)) { throw 'Run npm ci inside web first.' }
if ($Production -and !(Test-Path -LiteralPath (Join-Path $webRoot '.next/BUILD_ID'))) { throw 'Run npm run build inside web first.' }
$listeners = Get-NetTCPConnection -LocalPort 3000,8000 -State Listen -ErrorAction SilentlyContinue
if ($listeners) { throw 'Port 3000 or 8000 is occupied. Check the existing service; no process was stopped.' }
$privateDir = Join-Path $projectRoot 'artifacts/web'
New-Item -ItemType Directory -Path $privateDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$previousToken = $env:TRAVEL_WEB_TOKEN
$previousLive = $env:TRAVEL_WEB_LIVE
$previousDemo = $env:TRAVEL_DEMO_MODE
$previousExternal = $env:TRAVEL_TASK_WORKER_EXTERNAL
$backend = $null
try {
    $bytes = New-Object byte[] 32
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $random.GetBytes($bytes)
    $random.Dispose()
    $env:TRAVEL_WEB_TOKEN = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    $env:TRAVEL_WEB_LIVE = if ($Live) { '1' } else { '0' }
    $env:TRAVEL_DEMO_MODE = if ($Live) { '0' } else { '1' }
    $env:TRAVEL_TASK_WORKER_EXTERNAL = '1'
    $envFile = Join-Path $projectRoot '.env'
    $backendArgs = @('run','--project', ('"' + $projectRoot.Replace('\', '/') + '"'), '--no-sync')
    if ($Live -and (Test-Path -LiteralPath $envFile)) { $backendArgs += @('--env-file', ('"' + $envFile.Replace('\', '/') + '"')) }
    $workerArgs = $backendArgs + @('python','-m','travel_agent.worker')
    $backendArgs += @('uvicorn','travel_agent.api:app','--host','127.0.0.1','--port','8000')
    $backend = Start-Process -FilePath (Get-Command uv).Source -ArgumentList $backendArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $privateDir "$stamp-backend.log") -RedirectStandardError (Join-Path $privateDir "$stamp-backend-error.log")
    $worker = Start-Process -FilePath (Get-Command uv).Source -ArgumentList $workerArgs -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $privateDir "$stamp-worker.log") -RedirectStandardError (Join-Path $privateDir "$stamp-worker-error.log")
    $mode = if ($Production) { 'start' } else { 'dev' }
    $frontend = Start-Process -FilePath (Get-Command node).Source -ArgumentList @(('"' + $nextBin + '"'),$mode,'--hostname','127.0.0.1','--port','3000') -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $privateDir "$stamp-frontend.log") -RedirectStandardError (Join-Path $privateDir "$stamp-frontend-error.log")
    @{backend_launcher_pid=$backend.Id; worker_launcher_pid=$worker.Id; frontend_pid=$frontend.Id; started=$stamp; live=[bool]$Live} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $privateDir 'processes.json') -Encoding utf8
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        try {
            $config = Invoke-RestMethod -Uri 'http://127.0.0.1:3000/api/config' -TimeoutSec 2
            $ready = ($config.live_enabled -eq [bool]$Live -and $config.demo_mode -eq (!$Live))
            if ($ready) { break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (!$ready) { throw 'Web service did not become ready. Check artifacts/web logs; no credentials were printed.' }
    Write-Output 'Website: http://127.0.0.1:3000'
    Write-Output ('Live planning: ' + [bool]$Live + '; loopback only, no public deployment.')
    if (!$Live) { Write-Output 'Synthetic demo: no real model, map or search requests.' }
} finally {
    $env:TRAVEL_WEB_TOKEN = $previousToken
    $env:TRAVEL_WEB_LIVE = $previousLive
    $env:TRAVEL_DEMO_MODE = $previousDemo
    $env:TRAVEL_TASK_WORKER_EXTERNAL = $previousExternal
}
