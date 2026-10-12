import math
import httpx2
import pytest
from llm_helpers import Server, Sleeps, error, openai_settings, vt_settings
from procedure_helpers import FakeEmbedder, make_engine, make_worker, recipe, seed_version
from procedure_helpers import settings as pm_settings
from water_source_research.core.config import Settings
from water_source_research.core.versions import EMBEDDING_DIMENSIONS
from water_source_research.llm.base import LLMError, LLMErrorKind
from water_source_research.llm.embeddings import OpenAICompatibleEmbeddingClient
from water_source_research.llm.router import build_embedding_client
from water_source_research.procedures.dedupe import (
    MergeDecision, MergeEvaluator, procedure_key,
)
from water_source_research.procedures.repository import ProcedureRepository

def vectors(*widths: int) -> httpx2.Response:
    return httpx2.Response(200, json={
        "object": "list", "model": "m",
        "data": [{"object": "embedding", "index": i, "embedding": [1.0] * w}
                 for i, w in enumerate(widths)],
        "usage": {"prompt_tokens": 3, "total_tokens": 3}})

def vt_client(server, **kw):
    return build_embedding_client(vt_settings(), http_client=server.clients()["http_client"],
                                  **kw)

def test_vt_embeddings_are_truncated_to_the_column_width_and_normalised():
    server = Server(vectors(2560, 2560))
    out = vt_client(server).embed(["a", "b"])
    assert [len(v) for v in out] == [EMBEDDING_DIMENSIONS] * 2
    assert math.isclose(math.sqrt(sum(x * x for x in out[0])), 1.0)
    [body] = server.bodies()
    assert body["model"] == "Qwen3-Embedding-4B" and "dimensions" not in body
    assert server.requests[0].url.host == "vt.test"

def test_vt_query_gets_instruction_prefix_documents_do_not():
    server = Server(vectors(2560))
    client = vt_client(server)
    client.embed(["recipe text"])
    client.embed_query("withdrawal research")
    first, second = server.bodies()
    assert first["input"] == ["recipe text"]
    assert second["input"][0].startswith("Instruct: ")
    assert second["input"][0].endswith("\nQuery: withdrawal research")

def test_openai_embeddings_request_dimensions_directly():
    server = Server(vectors(EMBEDDING_DIMENSIONS))
    client = build_embedding_client(openai_settings(),
                                    http_client=server.clients()["http_client"])
    assert client.model == "text-embedding-3-small"
    client.embed_query("q")
    [body] = server.bodies()
    assert body["dimensions"] == EMBEDDING_DIMENSIONS and body["input"] == ["q"]

def test_short_vectors_are_rejected():
    with pytest.raises(LLMError, match="dimensions"):
        vt_client(Server(vectors(768))).embed(["a"])

def test_rate_limited_embeddings_wait_for_vt_hint():
    sleeps = Sleeps()
    server = Server(error(429, {"error": {"reason": "tokens", "retry_after_s": 12}}),
                    vectors(2560))
    client = OpenAICompatibleEmbeddingClient(
        "sk", "Qwen3-Embedding-4B", provider="vt", base_url="http://vt.test/api/v1",
        http_client=server.clients()["http_client"], sleep=sleeps)
    assert len(client.embed(["a"])[0]) == EMBEDDING_DIMENSIONS
    assert sleeps.delays == [12.0]

def test_embedding_client_needs_selected_provider_credentials():
    with pytest.raises(LLMError) as exc:
        build_embedding_client(Settings(_env_file=None, vt_llm_base_url="",
                                        vt_llm_api_key="", vt_llm_model=""))
    assert exc.value.kind is LLMErrorKind.UNCONFIGURED

# --- vectors from different models are never compared -----------------------------------

class OtherEmbedder(FakeEmbedder):
    model = "other-embedding"

def test_similarity_and_reembedding_are_scoped_to_the_embedding_model(tmp_path):
    repo = ProcedureRepository(make_engine(tmp_path))
    vid = seed_version(repo, recipe(), embedder=FakeEmbedder())  # stored as fake-embedding
    query = FakeEmbedder().embed_query("withdrawal annual report")
    assert vid in repo.similarities([vid], query, "fake-embedding")
    assert repo.similarities([vid], query, "other-embedding") == {}
    assert repo.pending_embeddings(10, "fake-embedding") == []

    # Switching models re-embeds stored versions, after which they are comparable again.
    assert [v for v, _ in repo.pending_embeddings(10, "other-embedding")] == [vid]
    assert make_worker(repo, embedder=OtherEmbedder()).embed_pending() == 1
    assert vid in repo.similarities([vid], query, "other-embedding")
    assert repo.pending_embeddings(10, "other-embedding") == []

def test_merge_ignores_cosine_across_embedding_models(tmp_path):
    repo = ProcedureRepository(make_engine(tmp_path))
    r = recipe()
    seed_version(repo, r, embedder=FakeEmbedder(), procedure_tag="t")
    pid = repo.get_or_create_procedure(procedure_key(r, "t", None), r, "t", None)
    existing = repo.versions_for_procedure(pid)
    assert existing[0].embedding_model == "fake-embedding"
    candidate = r.model_copy(update={"summary": r.summary + " Updated."})
    opposite = [-x for x in existing[0].embedding]
    merge = MergeEvaluator(pm_settings())
    # Same model: the (opposite) vector blocks the merge; another model's vector is ignored.
    assert merge.evaluate(candidate, opposite, existing, "fake-embedding").decision \
        is MergeDecision.NEW_VERSION
    assert merge.evaluate(candidate, opposite, existing, "other-embedding").decision \
        is MergeDecision.MERGE
