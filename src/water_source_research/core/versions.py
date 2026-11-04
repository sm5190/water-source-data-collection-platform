"""Version identifiers that gate reuse of learned or persisted artifacts.

Bump the relevant constant when behaviour changes in a way stored data doesn't capture.
"""

# Graph/workflow shape. Integer so procedures can declare a minimum supported version.
WORKFLOW_VERSION = 3

# Procedural memory.
DISTILLER_VERSION = "deterministic-1"
# Prompt/response-schema version of LLMDistiller; its full version also names the model.
LLM_DISTILLER_VERSION = "llm-1"
RECIPE_SCHEMA_VERSION = 1
# Prompt/response-schema version of the extraction gateway (llm/extraction.py). Part of the LLM
# cache key: bump it whenever the extraction prompt or claim schema changes.
EXTRACTION_PROMPT_VERSION = "extract-6"  # 6: identity header + passage place notes
PROCEDURE_RETRIEVAL_POLICY_VERSION = "1"
EMBEDDING_VERSION = "1"
# Fixed by the pgvector column type; changing it requires a migration and re-embedding.
EMBEDDING_DIMENSIONS = 1536
# Deterministic claim normalization/validation (services/normalization.py) and resolution
# rules (services/resolution.py). Stored with each claim's validation / field resolution.
# 5: CITY_NAMED on the city identity (geo_match); 6: utility authority from the registry
NORMALIZATION_VERSION = "normalize-8"  # 7: geo subject vs publisher; 8: feature location
RESOLUTION_POLICY_VERSION = "resolve-5"  # 5: official sources = verified utility roles
# Source classification rules (discovery/normalize.py::classify). Stored with each document and
# discovery hit; NULL in older rows means classify-1.
CLASSIFICATION_VERSION = "classify-2"
# Discovery planning (discovery/queries.py DiscoveryPlan, agents/nodes.py): verified utility
# hosts and names, verification and fallback searches, identity place terms, host suppression.
DISCOVERY_POLICY_VERSION = "discovery-4"  # 3: verification slot, place variants, topic misses
# 4: search-result scope from title/snippet, publisher apart; reserved utility slot
