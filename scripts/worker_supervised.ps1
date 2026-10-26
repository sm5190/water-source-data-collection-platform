# Run the research worker and record how it ends.
#
#   powershell -ExecutionPolicy Bypass -File scripts/worker_supervised.ps1 [-Concurrency 2]
#       [-MaxRestarts 5] [-LogDir logs]
#
# The worker writes its own log (logs/worker-<start>-<pid>.log). A process that is killed
# can't log its own exit code, so this supervisor runs it directly (no shell or uv layer in
# between), waits for it, and appends to logs/worker-supervisor.log: start/end time, PID, the
# exit code in decimal and as an NTSTATUS hex value (e.g. 0xC0000005 access violation,
# 0xC0000017 no memory, 0x40010004 killed by the debugger / console close), and the system's
# free memory at exit. It then restarts the worker with a growing delay, up to MaxRestarts.
# A clean exit (code 0) ends the loop.
param(
    [int]$Concurrency = 2,
    [int]$MaxRestarts = 5,
    [string]$LogDir = "logs"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$supervisorLog = Join-Path $LogDir "worker-supervisor.log"
$python = Join-Path $root ".venv\Scripts\python.exe"

function Write-Supervisor([string]$message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ss.fffK"), $message
    Add-Content -Path $supervisorLog -Value $line -Encoding utf8
    Write-Host $line
}

$restarts = 0
$delay = 5
while ($true) {
    $proc = Start-Process -FilePath $python -PassThru -NoNewWindow `
        -ArgumentList @("scripts/run_research_worker.py", "--concurrency", $Concurrency,
                        "--log-dir", $LogDir)
    Write-Supervisor ("started worker pid {0} (concurrency {1})" -f $proc.Id, $Concurrency)
    $started = Get-Date
    $proc.WaitForExit()
    $code = $proc.ExitCode
    $hex = "0x{0:X8}" -f ([int64]$code -band 0xFFFFFFFF)
    $os = Get-CimInstance Win32_OperatingSystem
    Write-Supervisor ("worker pid {0} exited after {1:N0}s: code {2} ({3}); free RAM {4:N0} MB, free commit {5:N0} MB" -f `
        $proc.Id, ((Get-Date) - $started).TotalSeconds, $code, $hex,
        ($os.FreePhysicalMemory / 1KB), ($os.FreeVirtualMemory / 1KB))
    if ($code -eq 0) { break }
    $restarts++
    if ($restarts -gt $MaxRestarts) {
        Write-Supervisor "giving up after $MaxRestarts restarts"
        exit 1
    }
    Write-Supervisor "restarting in $delay s ($restarts/$MaxRestarts)"
    Start-Sleep -Seconds $delay
    $delay = [Math]::Min($delay * 2, 300)
}
