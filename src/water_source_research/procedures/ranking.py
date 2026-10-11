"""Reliability ranking for procedure candidates. Similarity is one signal, not truth.

score = w_sim * similarity
      + w_rel * wilson_lower_bound(successes, outcomes)   # small samples can't win on rate
      + w_recent * recent_success_rate (EWMA; falls back to the Wilson bound)
      + w_recency * 0.5 ** (age_days / half_life)
      - w_correction * human_corrections / outcomes
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from .config import ProcedureMemorySettings

@dataclass(frozen=True, slots=True)
class RankInput:
    procedure_id: str
    version_id: str
    version: int
    success_count: int
    failure_count: int
    usage_count: int
    recent_success_rate: float | None
    human_correction_count: int
    last_success_at: datetime | None
    created_at: datetime

@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: RankInput
    score: float
    reliability: float
    similarity: float | None
    reasons: tuple[str, ...]

def wilson_lower_bound(successes: int, n: int, z: float) -> float:
    if n <= 0:
        return 0.0
    p = successes / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)

class RankingPolicy:
    def __init__(self, settings: ProcedureMemorySettings) -> None:
        self.s = settings

    def score(self, c: RankInput, similarity: float | None, now: datetime) -> RankedCandidate:
        s = self.s
        outcomes = c.success_count + c.failure_count
        reliability = wilson_lower_bound(c.success_count, outcomes, s.wilson_z)
        recent = c.recent_success_rate if c.recent_success_rate is not None else reliability
        anchor = _aware(c.last_success_at or c.created_at)
        age_days = max(0.0, (_aware(now) - anchor).total_seconds() / 86400)
        recency = 0.5 ** (age_days / s.recency_half_life_days)
        correction_rate = c.human_correction_count / outcomes if outcomes else 0.0

        score = (
            s.weight_similarity * (similarity or 0.0)
            + s.weight_reliability * reliability
            + s.weight_recent_success * recent
            + s.weight_recency * recency
            - s.weight_correction * correction_rate
        )

        reasons = ["task and field compatible", "workflow compatible"]
        if similarity is not None:
            reasons.append(f"semantic similarity {similarity:.2f}")
        if outcomes:
            reasons.append(f"{c.success_count}/{outcomes} successful outcomes")
        else:
            reasons.append("no outcome history yet")
        if c.recent_success_rate is not None and c.recent_success_rate >= 0.7:
            reasons.append("recently successful")
        if correction_rate > s.promotion_max_correction_rate:
            reasons.append(f"high human-correction rate {correction_rate:.0%}")
        return RankedCandidate(c, round(score, 6), round(reliability, 6), similarity,
                               tuple(reasons))

    def rank(
        self, candidates: list[RankInput], similarities: dict[str, float], now: datetime
    ) -> list[RankedCandidate]:
        ranked = [self.score(c, similarities.get(c.version_id), now) for c in candidates]
        # Deterministic: score desc, then stable identity.
        return sorted(ranked, key=lambda r: (-r.score, r.candidate.procedure_id,
                                             r.candidate.version))

def _aware(dt: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
