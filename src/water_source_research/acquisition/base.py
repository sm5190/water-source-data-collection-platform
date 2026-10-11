from dataclasses import dataclass

@dataclass(slots=True)
class AcquiredDocument:
    url: str
    title: str | None
    media_type: str
    text: str
    acquisition_method: str
