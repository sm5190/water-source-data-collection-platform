from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

@dataclass(slots=True)
class SearchResult:
    """A discovery candidate: a pointer to a source, never evidence by itself.

    ``provider`` is the provider that produced this record; after merging duplicates,
    ``providers`` lists every provider that returned it and ``provider_ids`` their native ids.
    """

    title: str
    url: str
    snippet: str | None = None
    provider: str = ""
    rank: int | None = None
    doi: str | None = None  # normalized, e.g. "10.1016/j.watres.2020.1"
    published_date: str | None = None  # ISO 8601 (YYYY, YYYY-MM or YYYY-MM-DD)
    authors: list[str] = field(default_factory=list)
    venue: str | None = None
    source_type: str | None = None  # UTILITY, GOVERNMENT, REGULATORY, TECHNICAL, ACADEMIC, OTHER
    providers: list[str] = field(default_factory=list)
    provider_ids: dict[str, str] = field(default_factory=dict)
    query: str | None = None
    score: float | None = None
    # Crossref check: True = registered with Crossref (metadata verified); False = not a
    # Crossref DOI (may still be valid, e.g. DataCite for datasets/theses); None = not checked.
    doi_verified: bool | None = None

    def __post_init__(self) -> None:
        if self.provider and self.provider not in self.providers:
            self.providers.insert(0, self.provider)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SearchResult":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

class DiscoveryProvider(Protocol):
    name: str

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]: ...
