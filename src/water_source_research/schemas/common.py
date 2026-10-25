from enum import StrEnum

class ValueStatus(StrEnum):
    KNOWN = "KNOWN"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"

class ValueOrigin(StrEnum):
    REPORTED = "REPORTED"
    CALCULATED = "CALCULATED"
    ESTIMATED = "ESTIMATED"
    ALLOCATED = "ALLOCATED"

class FieldResolutionStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    CANDIDATE = "CANDIDATE"
    CONFLICT = "CONFLICT"
    HITL_REQUIRED = "HITL_REQUIRED"
    RESOLVED = "RESOLVED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    # Applicable, researched, but no reliable value: exported as "Unknown". Only an explicit
    # decision (reviewer or documented policy) sets it; missing evidence alone never does.
    UNAVAILABLE = "UNAVAILABLE"
    # A reviewer rejected every candidate and chose no value, Unknown or N/A: the field needs
    # further research (a later run). Exported as "Unknown" (the standardized format has no
    # other gap value) but recorded as NEEDS_RESEARCH, never as a reviewer's Unknown.
    NEEDS_RESEARCH = "NEEDS_RESEARCH"

# Statuses that need no further research or review in this run.
FINAL_STATUSES = frozenset({FieldResolutionStatus.RESOLVED, FieldResolutionStatus.NOT_APPLICABLE,
                            FieldResolutionStatus.UNAVAILABLE,
                            FieldResolutionStatus.NEEDS_RESEARCH})

class ValidationOutcome(StrEnum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INVALID = "INVALID"

class CheckResult(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
