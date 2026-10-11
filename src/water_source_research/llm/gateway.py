from typing import Protocol
from water_source_research.schemas.research import FieldClaim
from water_source_research.context_manager import ContextBundle

class LLMGateway(Protocol):
    """Boundary between the graph and model calls.

    Receives a READY ContextBundle and returns claims for ``field_key``, each citing evidence
    via ``bundle.evidence_ref(...)``. Model selection, routing (OpenAI vs VT LLM), and LLM
    response caching (keyed by ``bundle.fingerprint``) live behind this interface, never in
    the Context Manager.
    """

    async def extract(
        self, bundle: ContextBundle, *, run_id: str, field_key: str
    ) -> list[FieldClaim]: ...

class UnconfiguredGateway:
    async def extract(
        self, bundle: ContextBundle, *, run_id: str, field_key: str
    ) -> list[FieldClaim]:
        raise NotImplementedError(
            "No LLM gateway configured. Pass build_graph(gateway=...) with an LLMGateway."
        )
