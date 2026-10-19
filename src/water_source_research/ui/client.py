"""Thin HTTP client for the research API, used by the Streamlit dashboard."""

from typing import Any
import httpx

class ApiError(RuntimeError):
    pass

class ResearchApiClient:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout)

    def _get(self, path: str, **params: Any) -> Any:
        r = self.http.get(path, params={k: v for k, v in params.items() if v is not None})
        if r.status_code >= 400:
            raise ApiError(f"GET {path} -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def health(self) -> bool:
        try:
            return self._get("/health").get("status") == "ok"
        except (httpx.HTTPError, ApiError):
            return False

    def cities(self) -> list[dict[str, Any]]:
        return self._get("/research/cities")

    def submit(self, city_name: str, country: str | None, priority: int = 0) -> dict[str, Any]:
        r = self.http.post("/research/city", json={"city_name": city_name, "country": country,
                                                   "priority": priority})
        if r.status_code >= 400:
            raise ApiError(f"submit -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def runs(self, city: str | None = None, country: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        return self._get("/research/runs", city=city, country=country, limit=limit)

    def steps(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/steps")

    def review(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/review")

    def fields(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/fields")

    # --- human review ---------------------------------------------------------------------

    def review_pending(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/review/pending")

    def review_item(self, run_id: str, item_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/review/items/{item_id}")

    def review_history(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/review/history")

    def submit_decisions(self, run_id: str, reviewer: str,
                         decisions: list[dict[str, Any]]) -> dict[str, Any]:
        return self._post(f"/research/runs/{run_id}/review/decisions",
                          {"reviewer": reviewer, "decisions": decisions})

    def resume_review(self, run_id: str, reviewer: str) -> dict[str, Any]:
        return self._post(f"/research/runs/{run_id}/review/resume", {"reviewer": reviewer})

    def _post(self, path: str, body: dict[str, Any]) -> Any:
        r = self.http.post(path, json=body)
        if r.status_code >= 400:
            raise ApiError(f"POST {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def cancel(self, run_id: str) -> dict[str, Any]:
        r = self.http.post(f"/research/runs/{run_id}/cancel")
        if r.status_code >= 400:
            raise ApiError(f"cancel -> {r.status_code}: {r.text[:200]}")
        return r.json()

    def queue_stats(self) -> dict[str, Any]:
        return self._get("/research/queue/stats")
