"""Vendor-independent research capabilities.

Procedures recommend capabilities, never vendors. A gateway (not yet built) maps a capability
to whichever provider is currently healthy; ``PROVIDER_CAPABILITIES`` records which existing
adapters implement which capability.
"""

import importlib.util
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from water_source_research.core.config import Settings

class Capability(StrEnum):
    SOURCE_DISCOVERY = "source_discovery"
    SCHOLARLY_DISCOVERY = "scholarly_discovery"
    STATIC_DOCUMENT_FETCH = "static_document_fetch"
    DOCUMENT_PARSING = "document_parsing"
    DYNAMIC_DOCUMENT_ACQUISITION = "dynamic_document_acquisition"
    INTERACTIVE_PORTAL_ACQUISITION = "interactive_portal_acquisition"
    RESOLVE_DEPENDENCY = "resolve_dependency"
    CLAIM_EXTRACTION = "claim_extraction"
    CLAIM_VALIDATION = "claim_validation"

DISCOVERY_CAPABILITIES = frozenset({Capability.SOURCE_DISCOVERY, Capability.SCHOLARLY_DISCOVERY})

PROVIDER_CAPABILITIES: dict[str, Capability] = {
    "searxng": Capability.SOURCE_DISCOVERY,
    "openalex": Capability.SCHOLARLY_DISCOVERY,
    "crossref": Capability.SCHOLARLY_DISCOVERY,
    "semantic_scholar": Capability.SCHOLARLY_DISCOVERY,
    "direct_http": Capability.STATIC_DOCUMENT_FETCH,
    "docling": Capability.DOCUMENT_PARSING,
    "firecrawl": Capability.DYNAMIC_DOCUMENT_ACQUISITION,
    "apify": Capability.DYNAMIC_DOCUMENT_ACQUISITION,
    "computer_use": Capability.INTERACTIVE_PORTAL_ACQUISITION,
}

# Capabilities each workflow version can execute. A procedure requiring a capability its
# workflow doesn't know is incompatible.
WORKFLOW_CAPABILITIES: dict[int, frozenset[Capability]] = {
    2: frozenset(Capability),
}

def capabilities_for_workflow(workflow_version: int) -> frozenset[Capability]:
    return WORKFLOW_CAPABILITIES.get(workflow_version, frozenset())

def available_capabilities(settings: "Settings") -> frozenset[Capability]:
    """Capabilities with a configured provider right now.

    A stand-in for real provider health / circuit-breaker state until a tool gateway exists.
    """
    available = {
        Capability.STATIC_DOCUMENT_FETCH,
        Capability.RESOLVE_DEPENDENCY,
        Capability.CLAIM_EXTRACTION,
        Capability.CLAIM_VALIDATION,
    }
    discovery = settings.discovery
    if discovery.enabled:
        if "searxng" in discovery.providers and settings.searxng_url:
            available.add(Capability.SOURCE_DISCOVERY)
        if {"openalex", "semantic_scholar", "crossref"} & set(discovery.providers):
            available.add(Capability.SCHOLARLY_DISCOVERY)
    if importlib.util.find_spec("docling") is not None:
        available.add(Capability.DOCUMENT_PARSING)
    if settings.firecrawl_api_key or settings.apify_api_token:
        available.add(Capability.DYNAMIC_DOCUMENT_ACQUISITION)
    return frozenset(available)
