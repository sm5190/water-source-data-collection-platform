import httpx
from water_source_research.core.config import get_settings

class VTLLMClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def complete(self, system: str, user: str) -> str:
        if not self.settings.vt_llm_base_url:
            raise RuntimeError("VT LLM is not configured")
        # TODO: replace with official VT endpoint request/response schema.
        payload = {
            "model": self.settings.vt_llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                self.settings.vt_llm_base_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.vt_llm_api_key}"},
            )
            r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
