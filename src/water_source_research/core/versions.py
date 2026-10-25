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
EXTRACTION_PROMPT_VERSION = "extract-2"
PROCEDURE_RETRIEVAL_POLICY_VERSION = "1"
EMBEDDING_VERSION = "1"
# Fixed by the pgvector column type; changing it requires a migration and re-embedding.
EMBEDDING_DIMENSIONS = 1536
# Deterministic claim normalization/validation (services/normalization.py) and resolution
# rules (services/resolution.py). Stored with each claim's validation / field resolution.
NORMALIZATION_VERSION = "normalize-2"
RESOLUTION_POLICY_VERSION = "resolve-2"
