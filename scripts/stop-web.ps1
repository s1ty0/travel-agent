$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$metadata = Join-Path $projectRoot 'artifacts/web/processes.json'
if (!(Test-Path -LiteralPath $metadata)) { Write-Output 'No owned web processes recorded.'; exit 0 }
$owned = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json
$inventory = @(Get-CimInstance Win32_Process)
function Stop-OwnedTree([int]$rootTaskPid) {
    foreach ($child in $inventory | Where-Object { $_.ParentProcessId -eq $rootTaskPid }) {
        Stop-OwnedTree ([int]$child.ProcessId)
    }
    Stop-Process -Id $rootTaskPid -ErrorAction SilentlyContinue
}
foreach ($taskPid in @($owned.frontend_pid, $owned.backend_launcher_pid, $owned.worker_launcher_pid)) {
    if (!$taskPid) { continue }
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int]$taskPid)
    if (!$process) { continue }
    $command = ([string]$process.CommandLine).Replace('/', '\')
    if ($command.IndexOf($projectRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
        throw 'A recorded process no longer belongs to this checkout. No unrelated process was stopped.'
    }
    Stop-OwnedTree ([int]$taskPid)
}
Remove-Item -LiteralPath $metadata
Write-Output 'Stopped the web processes recorded for this checkout.'
