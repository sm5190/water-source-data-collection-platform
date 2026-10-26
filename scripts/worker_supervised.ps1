# Run the research worker under the supervisor (scripts/worker_supervisor.py).
#
#   powershell -ExecutionPolicy Bypass -File scripts/worker_supervised.ps1 [-Concurrency 2]
#   powershell -ExecutionPolicy Bypass -File scripts/worker_supervised.ps1 -Stop
#
# The supervisor is a separate Python process: it starts the worker with the venv's
# python.exe directly, records how it ends (exit code, NTSTATUS, heartbeat, active runs,
# evidence-based classification) as worker events in logs/events/*.jsonl and Postgres, and
# restarts it with backoff. -Stop asks for a graceful stop. This wrapper only forwards its
# arguments and returns the supervisor's own exit code.
param(
    [int]$Concurrency = 2,
    [string]$LogDir = "logs",
    [switch]$Stop
)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "scripts\worker_supervisor.py"
if ($Stop) {
    & $python $script stop --log-dir $LogDir
} else {
    & $python $script run --concurrency $Concurrency --log-dir $LogDir
}
exit $LASTEXITCODE
