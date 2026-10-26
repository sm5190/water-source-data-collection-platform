"""Facts about a finished worker process, gathered by the supervisor (never by the worker,
which may be gone): its log tail, the system's memory, and Windows power / resource-exhaustion
events in the process's lifetime. Every collector is soft: a missing source yields no fact.
"""

import ctypes
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import psutil

log = logging.getLogger(__name__)

TAIL_BYTES = 128 * 1024
# Written by research_queue/diagnostics.py's excepthook.
TERMINATING = re.compile(r"worker exiting on an uncaught exception: ([A-Za-z_][\w.]*)")
FATAL = re.compile(r"Fatal Python error")
STOPPED = re.compile(r"worker \S+ stopped|worker process exiting normally")
MEMORY_ERROR = re.compile(r"\bMemoryError\b")

def worker_log(log_dir: Path, pid: int) -> Path | None:
    """The worker's own log file (``worker-<start>-<pid>.log``)."""
    matches = sorted(log_dir.glob(f"worker-*-{pid}.log"), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None

def log_facts(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"worker_log": None}
    with open(path, "rb") as f:
        f.seek(max(0, path.stat().st_size - TAIL_BYTES))
        tail = f.read().decode("utf-8", errors="replace")
    facts: dict[str, Any] = {"worker_log": str(path)}
    if m := list(TERMINATING.finditer(tail)):
        facts["terminating_exception"] = m[-1].group(1).rsplit(".", 1)[-1]
    if FATAL.search(tail):
        facts["fatal_error"] = True
    if STOPPED.search(tail):
        facts["stopped_marker"] = True
    if MEMORY_ERROR.search(tail) and facts.get("terminating_exception") != "MemoryError":
        facts["memory_errors_logged"] = True
    facts["last_lines"] = [line[:300] for line in tail.splitlines()[-5:]]
    return facts

def memory_facts() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    out: dict[str, Any] = {"system_available_mb": round(vm.available / 2**20),
                           "system_available_ratio": round(vm.available / vm.total, 4)}
    commit = commit_memory()
    if commit:
        out.update(commit)
    return out

def commit_memory() -> dict[str, Any] | None:
    """Windows commit charge (what out-of-memory means there), via GlobalMemoryStatusEx."""
    if sys.platform != "win32":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return None
    limit, avail = status.ullTotalPageFile, status.ullAvailPageFile
    return {"commit_limit_mb": round(limit / 2**20), "commit_available_mb": round(avail / 2**20),
            "commit_used_ratio": round(1 - avail / limit, 4) if limit else None}

# System-log events worth knowing around a termination (Windows).
POWER_IDS = {42: "sleep", 107: "resume", 506: "standby_enter", 507: "standby_exit",
             1: "resume_power_troubleshooter"}
EXHAUSTION_ID = 2004  # Microsoft-Windows-Resource-Exhaustion-Detector

def windows_events(start: datetime, end: datetime, pid: int,
                   timeout: float = 20.0) -> dict[str, Any]:
    """Power transitions and resource-exhaustion events between ``start`` and ``end``."""
    if sys.platform != "win32":
        return {}
    ids = ",".join(str(i) for i in [*POWER_IDS, EXHAUSTION_ID])
    fmt = "%Y-%m-%dT%H:%M:%S"
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$s=[datetime]::Parse('{(start - timedelta(seconds=60)).astimezone().strftime(fmt)}');"
        f"$e=[datetime]::Parse('{(end + timedelta(seconds=60)).astimezone().strftime(fmt)}');"
        f"Get-WinEvent -FilterHashtable @{{LogName='System';StartTime=$s;EndTime=$e;"
        f"Id={ids}}} -MaxEvents 50 | ForEach-Object {{ [pscustomobject]@{{id=$_.Id;"
        "provider=$_.ProviderName;time=$_.TimeCreated.ToUniversalTime().ToString('o');"
        "message=($_.Message -replace '\\s+',' ').Substring(0,[Math]::Min(600,"
        "($_.Message -replace '\\s+',' ').Length))}} } | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"windows_events_error": type(exc).__name__}
    if not out.strip():
        return {}
    try:
        rows = json.loads(out)
    except ValueError:
        return {"windows_events_error": "unparseable"}
    rows = rows if isinstance(rows, list) else [rows]
    power, exhaustion = [], []
    for r in rows:
        if r.get("id") == EXHAUSTION_ID and "Resource-Exhaustion" in (r.get("provider") or ""):
            exhaustion.append({"time": r.get("time"), "message": r.get("message"),
                               "names_process": bool(re.search(rf"\({pid}\)",
                                                               r.get("message") or ""))})
        elif r.get("id") in POWER_IDS:
            power.append({"id": r.get("id"), "kind": POWER_IDS[r["id"]], "time": r.get("time"),
                          "provider": r.get("provider")})
    out_facts: dict[str, Any] = {}
    if power:
        out_facts["power_events"] = power
    if exhaustion:
        out_facts["resource_exhaustion_events"] = exhaustion
    return out_facts
