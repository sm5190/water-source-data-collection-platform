"""Termination classification: facts in, (class, confidence, rationale) out. Memory exhaustion
is CONFIRMED only with evidence tying it to this process's termination."""

import pytest
from water_source_research.observability.termination import (
    CLEAN_SHUTDOWN, CONFIRMED, CONFIRMED_MEMORY_EXHAUSTION, CONNECTION_FAILURE,
    EXTERNAL_TERMINATION, HEARTBEAT_TIMEOUT, NATIVE_CRASH, POSSIBLE, POSSIBLE_MEMORY_EXHAUSTION,
    PROBABLE, UNHANDLED_EXCEPTION, UNKNOWN, UNKNOWN_TERMINATION, classify,
)

def signed(status: int) -> int:  # how Windows reports an NTSTATUS exit to Python
    return status - 2**32 if status >= 2**31 else status

@pytest.mark.parametrize("facts, expected", [
    ({"exit_code": 0, "stop_requested": True}, (CLEAN_SHUTDOWN, CONFIRMED)),
    ({"exit_code": 0, "stopped_marker": True}, (CLEAN_SHUTDOWN, PROBABLE)),
    ({"exit_code": 0}, (CLEAN_SHUTDOWN, POSSIBLE)),
    ({"exit_code": 1, "killed_by_supervisor": "stall"}, (HEARTBEAT_TIMEOUT, CONFIRMED)),
    ({"exit_code": 1, "killed_by_supervisor": "stop_timeout"},
     (EXTERNAL_TERMINATION, CONFIRMED)),
    ({"exit_code": signed(0xC0000017)}, (CONFIRMED_MEMORY_EXHAUSTION, CONFIRMED)),
    ({"exit_code": 137, "oom_kill_record": "container OOMKilled=true"},
     (CONFIRMED_MEMORY_EXHAUSTION, CONFIRMED)),
    ({"exit_code": signed(0xC0000005)}, (NATIVE_CRASH, CONFIRMED)),
    ({"exit_code": 1, "fatal_error": True}, (NATIVE_CRASH, PROBABLE)),
    ({"exit_code": 1, "terminating_exception": "OperationalError"},
     (CONNECTION_FAILURE, CONFIRMED)),
    ({"exit_code": 1, "terminating_exception": "KeyError"}, (UNHANDLED_EXCEPTION, CONFIRMED)),
    ({"exit_code": signed(0xC000013A)}, (EXTERNAL_TERMINATION, PROBABLE)),
    ({"exit_code": 127}, (UNKNOWN_TERMINATION, UNKNOWN)),
    ({"exit_code": 1}, (UNKNOWN_TERMINATION, UNKNOWN)),
])
def test_classification_table(facts, expected):
    cls, confidence, rationale = classify(facts)
    assert (cls, confidence) == expected
    assert rationale

# --- memory: single signals are never CONFIRMED ------------------------------------------

def test_memory_error_alone_is_only_probable():
    cls, conf, why = classify({"exit_code": 1, "terminating_exception": "MemoryError"})
    assert (cls, conf) == (POSSIBLE_MEMORY_EXHAUSTION, PROBABLE)
    assert "no independent memory evidence" in why

def test_low_available_memory_alone_is_only_possible():
    cls, conf, _ = classify({"exit_code": 1,
                             "last_sample": {"system_available_ratio": 0.02}})
    assert (cls, conf) == (POSSIBLE_MEMORY_EXHAUSTION, POSSIBLE)

def test_resource_exhaustion_event_alone_is_not_confirmed():
    cls, conf, _ = classify({"exit_code": 1, "resource_exhaustion_events": [
        {"time": "t", "names_process": False}]})
    assert (cls, conf) == (POSSIBLE_MEMORY_EXHAUSTION, POSSIBLE)
    cls, conf, _ = classify({"exit_code": 1, "resource_exhaustion_events": [
        {"time": "t", "names_process": True}]})
    assert (cls, conf) == (POSSIBLE_MEMORY_EXHAUSTION, PROBABLE)  # still not linked to exit

def test_memory_error_with_independent_corroboration_is_confirmed():
    cls, conf, why = classify({"exit_code": 1, "terminating_exception": "MemoryError",
                               "last_sample": {"commit_used_ratio": 0.97}})
    assert (cls, conf) == (CONFIRMED_MEMORY_EXHAUSTION, CONFIRMED)
    assert "corroborated" in why
    # a system-wide signal is not corroboration
    cls, conf, _ = classify({"exit_code": 1, "terminating_exception": "MemoryError",
                             "last_sample": {"system_available_ratio": 0.01}})
    assert (cls, conf) == (POSSIBLE_MEMORY_EXHAUSTION, PROBABLE)

def test_logged_memory_error_that_did_not_end_the_process():
    cls, _, why = classify({"exit_code": 1, "memory_errors_logged": True})
    assert cls == POSSIBLE_MEMORY_EXHAUSTION and "not linked directly" in why

def test_exit_127_is_never_memory_and_says_so():
    cls, conf, why = classify({"exit_code": 127})
    assert (cls, conf) == (UNKNOWN_TERMINATION, UNKNOWN)
    assert "not an out-of-memory code" in why

def test_power_events_are_context_not_cause():
    cls, conf, why = classify({"exit_code": 127, "power_events": [
        {"kind": "resume", "time": "2026-10-25T21:31:00Z"}]})
    assert cls == UNKNOWN_TERMINATION and conf == UNKNOWN
    assert "context only, not a cause" in why
