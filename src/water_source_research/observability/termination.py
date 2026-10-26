"""Evidence-based classification of how a worker process ended.

``classify(facts)`` reads only observed facts (collected by the supervisor) and returns an
interpretation: class, confidence and a rationale naming the evidence used. The facts are
stored separately from the result, so a later reading can disagree.

Confidence: CONFIRMED (direct evidence of this cause for this process), PROBABLE (strong but
indirect), POSSIBLE (one weak signal), UNKNOWN (nothing points anywhere).

Memory exhaustion is CONFIRMED only with evidence that links memory to *this* process's
termination: an OS/runtime kill record naming it for memory, the process's own out-of-memory
exit status, or a MemoryError that ended the process corroborated by an independent memory
signal from the same window. A MemoryError, low free memory or a Resource-Exhaustion event
alone is at most POSSIBLE/PROBABLE. Exit code 127 is not a memory code.
"""

from collections.abc import Mapping
from typing import Any

CLEAN_SHUTDOWN = "CLEAN_SHUTDOWN"
UNHANDLED_EXCEPTION = "UNHANDLED_EXCEPTION"
NATIVE_CRASH = "NATIVE_CRASH"
POSSIBLE_MEMORY_EXHAUSTION = "POSSIBLE_MEMORY_EXHAUSTION"
CONFIRMED_MEMORY_EXHAUSTION = "CONFIRMED_MEMORY_EXHAUSTION"
EXTERNAL_TERMINATION = "EXTERNAL_TERMINATION"
HEARTBEAT_TIMEOUT = "HEARTBEAT_TIMEOUT"
CONNECTION_FAILURE = "CONNECTION_FAILURE"
UNKNOWN_TERMINATION = "UNKNOWN_TERMINATION"

CONFIRMED, PROBABLE, POSSIBLE, UNKNOWN = "CONFIRMED", "PROBABLE", "POSSIBLE", "UNKNOWN"

# NTSTATUS values a Windows process can end with (unsigned 32-bit).
STATUS_NO_MEMORY = 0xC0000017
STATUS_COMMITMENT_LIMIT = 0xC000012D
STATUS_ACCESS_VIOLATION = 0xC0000005
STATUS_STACK_OVERFLOW = 0xC00000FD
STATUS_HEAP_CORRUPTION = 0xC0000374
STATUS_STACK_BUFFER_OVERRUN = 0xC0000409
STATUS_CONTROL_C_EXIT = 0xC000013A
DBG_TERMINATE_PROCESS = 0x40010004
MEMORY_STATUSES = {STATUS_NO_MEMORY: "STATUS_NO_MEMORY",
                   STATUS_COMMITMENT_LIMIT: "STATUS_COMMITMENT_LIMIT"}
NATIVE_STATUSES = {STATUS_ACCESS_VIOLATION: "STATUS_ACCESS_VIOLATION",
                   STATUS_STACK_OVERFLOW: "STATUS_STACK_OVERFLOW",
                   STATUS_HEAP_CORRUPTION: "STATUS_HEAP_CORRUPTION",
                   STATUS_STACK_BUFFER_OVERRUN: "STATUS_STACK_BUFFER_OVERRUN"}
CONSOLE_STATUSES = {STATUS_CONTROL_C_EXIT: "STATUS_CONTROL_C_EXIT",
                    DBG_TERMINATE_PROCESS: "DBG_TERMINATE_PROCESS"}
CONNECTION_EXCEPTIONS = ("OperationalError", "InterfaceError", "ConnectionError",
                         "ConnectionRefusedError", "ConnectionResetError", "PoolTimeout",
                         "DisconnectionError", "ConnectionDoesNotExistError")
COMMIT_CORROBORATION = 0.95  # last sample's commit use as a share of the commit limit

def unsigned(code: int | None) -> int | None:
    return None if code is None else code & 0xFFFFFFFF

def exit_hex(code: int | None) -> str | None:
    return None if code is None else f"0x{code & 0xFFFFFFFF:08X}"

def classify(facts: Mapping[str, Any]) -> tuple[str, str, str]:
    """(termination class, confidence, rationale) from observed facts.

    Facts used (all optional): exit_code, stop_requested, killed_by_supervisor ("stall" |
    "stop_timeout"), terminating_exception (type name recorded by the worker's excepthook),
    fatal_error (faulthandler "Fatal Python error" seen), stopped_marker (the worker logged a
    normal stop), memory_errors_logged (MemoryError seen in the log, not terminating),
    oom_kill_record (an OS/runtime record naming this process killed for memory),
    resource_exhaustion_events (Windows 2004 events in the window; ``names_process`` when the
    event lists this PID), last_sample (commit_used_ratio, system_available_ratio),
    power_events (standby/resume; context only)."""
    code = facts.get("exit_code")
    status = unsigned(code)
    notes: list[str] = []
    if facts.get("power_events"):
        notes.append(f"power transitions in the window: {_power(facts['power_events'])} "
                     "(context only, not a cause)")

    def done(cls: str, conf: str, why: str) -> tuple[str, str, str]:
        return cls, conf, "; ".join([why, *notes])

    exc = facts.get("terminating_exception")
    signals = _memory_signals(facts)

    if code == 0 and facts.get("stop_requested"):
        return done(CLEAN_SHUTDOWN, CONFIRMED, "exit 0 after a requested stop")
    if facts.get("killed_by_supervisor") == "stall":
        return done(HEARTBEAT_TIMEOUT, CONFIRMED,
                    "the supervisor killed the worker after its heartbeat stopped")
    if facts.get("killed_by_supervisor") == "stop_timeout":
        return done(EXTERNAL_TERMINATION, CONFIRMED,
                    "the supervisor killed the worker after the stop grace period")
    if facts.get("oom_kill_record"):
        return done(CONFIRMED_MEMORY_EXHAUSTION, CONFIRMED,
                    f"OS/runtime record of an out-of-memory kill of this process: "
                    f"{facts['oom_kill_record']}")
    if status in MEMORY_STATUSES:
        return done(CONFIRMED_MEMORY_EXHAUSTION, CONFIRMED,
                    f"the process exited with {MEMORY_STATUSES[status]} ({exit_hex(code)})")
    if exc == "MemoryError":
        corroboration = [s for s in signals if s[0] == "strong"]
        if corroboration:
            return done(CONFIRMED_MEMORY_EXHAUSTION, CONFIRMED,
                        "a MemoryError ended the process, corroborated by "
                        + ", ".join(s[1] for s in corroboration))
        return done(POSSIBLE_MEMORY_EXHAUSTION, PROBABLE,
                    "a MemoryError ended the process, with no independent memory evidence "
                    "from the same window (a single allocation can fail without the system "
                    "being exhausted)")
    if code == 0:
        if facts.get("stopped_marker"):
            return done(CLEAN_SHUTDOWN, PROBABLE, "exit 0 and the worker logged a normal stop")
        return done(CLEAN_SHUTDOWN, POSSIBLE, "exit 0 without a stop request or stop marker")
    if status in NATIVE_STATUSES:
        return done(NATIVE_CRASH, CONFIRMED,
                    f"the process exited with {NATIVE_STATUSES[status]} ({exit_hex(code)})")
    if facts.get("fatal_error"):
        return done(NATIVE_CRASH, PROBABLE,
                    "faulthandler logged a fatal error before the exit")
    if exc:
        if any(name in exc for name in CONNECTION_EXCEPTIONS):
            return done(CONNECTION_FAILURE, CONFIRMED,
                        f"the process ended on a connection error ({exc})")
        return done(UNHANDLED_EXCEPTION, CONFIRMED, f"the process ended on {exc}")
    if status in CONSOLE_STATUSES:
        return done(EXTERNAL_TERMINATION, PROBABLE,
                    f"exit {CONSOLE_STATUSES[status]} ({exit_hex(code)}): console Ctrl+C/close "
                    "or a debugger ended the process")
    if signals:
        strength = PROBABLE if any(s[0] == "strong" for s in signals) else POSSIBLE
        return done(POSSIBLE_MEMORY_EXHAUSTION, strength,
                    f"exit {code} ({exit_hex(code)}) with no recorded cause; memory signals: "
                    + ", ".join(s[1] for s in signals)
                    + " (not linked directly to this termination)")
    extra = (" 127 is not an out-of-memory code (on POSIX shells it usually means "
             "'command not found'; on Windows it has no fixed meaning)") if code == 127 else ""
    return done(UNKNOWN_TERMINATION, UNKNOWN,
                f"exit {code} ({exit_hex(code)}) with no exception, crash dump, kill record or "
                f"memory signal in the evidence.{extra}")

def _memory_signals(facts: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(strength, description) of memory evidence; 'strong' ones are specific to this
    process and window, 'weak' ones are system-wide or circumstantial."""
    out: list[tuple[str, str]] = []
    sample = facts.get("last_sample") or {}
    ratio = sample.get("commit_used_ratio")
    if isinstance(ratio, int | float) and ratio >= COMMIT_CORROBORATION:
        out.append(("strong", f"system commit at {ratio:.0%} of its limit in the last sample"))
    for ev in facts.get("resource_exhaustion_events") or []:
        if ev.get("names_process"):
            out.append(("strong", "a Resource-Exhaustion event naming this process"))
        else:
            out.append(("weak", "a Resource-Exhaustion event (not naming this process)"))
    avail = sample.get("system_available_ratio")
    if isinstance(avail, int | float) and avail < 0.05:
        out.append(("weak", f"system available memory {avail:.0%} in the last sample"))
    if facts.get("memory_errors_logged"):
        out.append(("weak", "MemoryError logged earlier (not the terminating exception)"))
    return out

def _power(events: list[Mapping[str, Any]]) -> str:
    return ", ".join(f"{e.get('kind', e.get('id'))}@{e.get('time')}" for e in events[:5])
