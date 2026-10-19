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

# Statuses that need no further research or review.
FINAL_STATUSES = frozenset({FieldResolutionStatus.RESOLVED, FieldResolutionStatus.NOT_APPLICABLE,
                            FieldResolutionStatus.UNAVAILABLE})

class ValidationOutcome(StrEnum):
    VALID = "VALID"
    VALID_WITH_WARNINGS = "VALID_WITH_WARNINGS"
    INVALID = "INVALID"

class CheckResult(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
