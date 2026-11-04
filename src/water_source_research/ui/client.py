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

    def relationships(self, run_id: str, build: bool = True) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/relationships", build=str(build).lower())

    def review_pending(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/review/pending")

    def review_progress(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/review/progress")

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

    # --- records and export ---------------------------------------------------------------

    def records(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/records")

    def trace(self, run_id: str, record_key: str, field_key: str) -> dict[str, Any]:
        return self._get(f"/research/runs/{run_id}/records/{record_key}/trace",
                         field_key=field_key)

    def export(self, run_id: str, fmt: str, kind: str = "standardized",
               requested_by: str | None = None) -> tuple[bytes, str, str]:
        """(content, filename, media type) of an export file."""
        params = {"format": fmt, "kind": kind}
        if requested_by:
            params["requested_by"] = requested_by
        r = self.http.get(f"/research/runs/{run_id}/export", params=params)
        if r.status_code >= 400:
            raise ApiError(f"export -> {r.status_code}: {r.text[:300]}")
        disposition = r.headers.get("content-disposition", "")
        filename = disposition.split("filename=")[-1].strip('"') or f"export.{fmt}"
        return r.content, filename, r.headers.get("content-type", "application/octet-stream")

    # --- evaluation (separate from research review) ---------------------------------------

    def eval_references(self) -> list[dict[str, Any]]:
        return self._get("/evaluation/references")

    def eval_overview(self) -> list[dict[str, Any]]:
        return self._get("/evaluation/overview")

    def eval_runs(self, research_run_id: str | None = None) -> list[dict[str, Any]]:
        return self._get("/evaluation/runs", research_run_id=research_run_id)

    def eval_run(self, eval_id: str) -> dict[str, Any]:
        return self._get(f"/evaluation/runs/{eval_id}")

    def evaluate(self, research_run_id: str, dataset_id: str | None = None) -> dict[str, Any]:
        return self._post("/evaluation/runs", {"research_run_id": research_run_id,
                                               "dataset_id": dataset_id})

    def eval_fields(self, eval_id: str) -> list[dict[str, Any]]:
        return self._get(f"/evaluation/runs/{eval_id}/fields")

    def eval_field(self, eval_id: str, field_key: str) -> dict[str, Any]:
        return self._get(f"/evaluation/runs/{eval_id}/fields/{field_key}")

    def eval_diagnoses(self, eval_id: str) -> list[dict[str, Any]]:
        return self._get(f"/evaluation/runs/{eval_id}/diagnoses")

    def adjudicate(self, eval_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._post(f"/evaluation/runs/{eval_id}/adjudications", body)

    def eval_cities(self) -> list[dict[str, Any]]:
        return self._get("/evaluation/cities")

    def eval_comparison(self, eval_id: str, stage: str) -> dict[str, Any]:
        return self._get(f"/evaluation/runs/{eval_id}/comparison", stage=stage)

    def eval_performance(self) -> dict[str, Any]:
        return self._get("/evaluation/performance")

    def eval_report(self, eval_id: str, kind: str, fmt: str) -> tuple[bytes, str, str]:
        r = self.http.get(f"/evaluation/runs/{eval_id}/report",
                          params={"kind": kind, "format": fmt})
        if r.status_code >= 400:
            raise ApiError(f"report -> {r.status_code}: {r.text[:300]}")
        disposition = r.headers.get("content-disposition", "")
        filename = disposition.split("filename=")[-1].strip('"') or f"report.{fmt}"
        return r.content, filename, r.headers.get("content-type", "application/octet-stream")

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

    # --- monitoring -----------------------------------------------------------------------

    def mon_overview(self, hours: float = 24) -> dict[str, Any]:
        return self._get("/monitoring/overview", hours=hours)

    def mon_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._get("/monitoring/runs", limit=limit)

    def mon_run(self, run_id: str) -> dict[str, Any]:
        return self._get(f"/monitoring/runs/{run_id}")

    def mon_workers(self) -> list[dict[str, Any]]:
        return self._get("/monitoring/workers")

    def mon_crashes(self, hours: float = 24 * 7) -> list[dict[str, Any]]:
        return self._get("/monitoring/crashes", hours=hours)

    def mon_recovery(self, hours: float = 24 * 7) -> list[dict[str, Any]]:
        return self._get("/monitoring/recovery", hours=hours)

    def mon_usage(self, hours: float | None = 24,
                  group_by: str = "provider_model") -> dict[str, Any]:
        return self._get("/monitoring/usage", hours=hours or 0, group_by=group_by)

    def mon_alerts(self) -> dict[str, Any]:
        return self._get("/monitoring/alerts")
