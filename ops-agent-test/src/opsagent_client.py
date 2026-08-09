"""Thin HTTP client for the ops-agent backend API.

Wraps the real endpoints (verified against DatasetController / DatasetService):
  GET    /api/datasets              list (paginated, id desc)
  GET    /api/datasets/{id}         detail
  POST   /api/datasets              create (JSON, auto-triggers weather collection)
  PUT    /api/datasets/{id}         update metadata ONLY (no re-collection)
  POST   /api/datasets/{id}/collect explicit weather re-collection
  DELETE /api/datasets/{id}         delete
  GET    /api/datasets/{id}/file/url  presigned URL

Backend envelope: { code, message, data, timestamp }  (null fields omitted).
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = os.getenv("BASE_URL", "http://localhost:8080/api")
DEFAULT_USER = os.getenv("ADMIN_USERNAME", "admin")
DEFAULT_PASS = os.getenv("ADMIN_PASSWORD", "admin123")


class OpsAgentError(Exception):
    def __init__(self, message: str, status_code: int | None = None, envelope: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.envelope = envelope


class OpsAgentClient:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: float = 30.0,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        # Normalize: BASE_URL may be provided WITH or WITHOUT the `/api` suffix
        # (e.g. "http://host:8080/api" or "http://host:8080"). The helper methods
        # below already prefix paths with `/api`, so strip a trailing `/api` here
        # to avoid building ".../api/api/..." (which the security whitelist would
        # reject with 401, making login look like "bad credentials").
        if self.base_url.endswith("/api"):
            self.base_url = self.base_url[: -len("/api")]
        self.username = username or DEFAULT_USER
        self.password = password or DEFAULT_PASS
        self.timeout = timeout
        self.token: str | None = None
        self.http = httpx.Client(base_url=self.base_url, timeout=timeout)

    # ---- auth ----
    def login(self) -> dict:
        resp = self.http.post(
            "/api/auth/login",
            json={"username": self.username, "password": self.password},
        )
        if resp.status_code >= 400:
            raise OpsAgentError(f"login failed {resp.status_code}: {resp.text}", resp.status_code)
        data = resp.json().get("data") or {}
        self.token = data.get("token")
        if not self.token:
            raise OpsAgentError("login returned no token", resp.status_code, data)
        self.http.headers["Authorization"] = f"Bearer {self.token}"
        return data

    # ---- low-level request with envelope handling ----
    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = self.http.request(method, path, **kwargs)
        try:
            body = resp.json()
        except Exception:
            body = None
        if resp.status_code >= 400:
            raise OpsAgentError(
                f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}",
                resp.status_code,
                body,
            )
        return body  # envelope { code, message, data, timestamp }

    def get(self, path: str, **kw):
        return self._request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self._request("POST", path, **kw)

    def put(self, path: str, **kw):
        return self._request("PUT", path, **kw)

    def delete(self, path: str, **kw):
        return self._request("DELETE", path, **kw)

    # ---- dataset helpers (operate on the envelope's data field) ----
    def create_dataset(self, payload: dict) -> dict:
        return self.post("/api/datasets", json=payload)["data"]

    def get_dataset(self, ds_id: int) -> dict:
        return self.get(f"/api/datasets/{ds_id}")["data"]

    def list_datasets(self, page: int = 0, size: int = 20) -> dict:
        return self.get("/api/datasets", params={"page": page, "size": size})["data"]

    def update_dataset(self, ds_id: int, payload: dict) -> dict:
        """PUT /api/datasets/{id} — metadata only, does NOT re-collect weather."""
        return self.put(f"/api/datasets/{ds_id}", json=payload)["data"]

    def collect_dataset(self, ds_id: int) -> dict:
        """POST /api/datasets/{id}/collect — explicit weather re-collection
        (overwrites weather.csv by current regions/date range, returns updated dataset)."""
        return self.post(f"/api/datasets/{ds_id}/collect")["data"]

    def delete_dataset(self, ds_id: int) -> dict:
        return self.delete(f"/api/datasets/{ds_id}")

    def file_url(self, ds_id: int, expiry_minutes: int = 30) -> dict:
        return self.get(
            f"/api/datasets/{ds_id}/file/url",
            params={"expiryMinutes": expiry_minutes},
        )["data"]

    # ---- model version helpers (verified against ModelController) ----
    def create_model(self, payload: dict) -> dict:
        return self.post("/api/models", json=payload)["data"]

    def get_model(self, mv_id: int) -> dict:
        return self.get(f"/api/models/{mv_id}")["data"]

    def list_models(self, page: int = 0, size: int = 20) -> dict:
        return self.get("/api/models", params={"page": page, "size": size})["data"]

    def delete_model(self, mv_id: int) -> dict:
        return self.delete(f"/api/models/{mv_id}")

    def model_download_url(self, mv_id: int, expiry_minutes: int = 30) -> dict:
        return self.get(
            f"/api/models/{mv_id}/download",
            params={"expiryMinutes": expiry_minutes},
        )["data"]

    # ---- training job helpers (verified against TrainingController) ----
    def create_training_job(self, req: dict) -> dict:
        return self.post("/api/training/jobs", json=req)["data"]

    def get_training_job(self, job_id: int) -> dict:
        return self.get(f"/api/training/jobs/{job_id}")["data"]

    def list_training_jobs(self, page: int = 0, size: int = 20) -> dict:
        return self.get("/api/training/jobs", params={"page": page, "size": size})["data"]

    def delete_training_job(self, job_id: int) -> dict:
        return self.delete(f"/api/training/jobs/{job_id}")

    def training_logs_url(self, job_id: int, expiry_minutes: int = 30) -> dict:
        return self.get(
            f"/api/training/jobs/{job_id}/logs",
            params={"expiryMinutes": expiry_minutes},
        )["data"]

    # ---- serving endpoint helpers (verified against ServingController) ----
    def list_serving_endpoints(self, page: int = 0, size: int = 20, status: str | None = None) -> dict:
        params = {"page": page, "size": size}
        if status:
            params["status"] = status
        return self.get("/api/serving/endpoints", params=params)["data"]

    def get_serving_endpoint(self, ep_id: int) -> dict:
        return self.get(f"/api/serving/endpoints/{ep_id}")["data"]

    def deploy_serving(self, model_version_id: int) -> dict:
        return self.post("/api/serving/endpoints/deploy", json={"modelVersionId": model_version_id})["data"]

    def undeploy_serving(self, ep_id: int) -> dict:
        return self.post(f"/api/serving/endpoints/{ep_id}/undeploy")["data"]

    def delete_serving_endpoint(self, ep_id: int) -> dict:
        return self.delete(f"/api/serving/endpoints/{ep_id}")

    def serving_predict(self, ep_id: int, values: list, horizon: int = 1) -> dict:
        return self.post(
            f"/api/serving/endpoints/{ep_id}/predict",
            json={"values": values, "horizon": horizon},
        )["data"]

    # ---- agent module helpers (verified against AgentTask/Suggestion/ToolController) ----
    def dispatch_agent_task(self, payload: dict) -> dict:
        """POST /api/agent/tasks -> {taskId, status}"""
        return self.post("/api/agent/tasks", json=payload)["data"]

    def get_agent_task(self, task_id: str) -> dict:
        """GET /api/agent/tasks/{taskId} -> {task: {...}, events: [...]}"""
        return self.get(f"/api/agent/tasks/{task_id}")["data"]

    def list_agent_tasks(self, page: int = 0, size: int = 20) -> dict:
        return self.get("/api/agent/tasks", params={"page": page, "size": size})["data"]

    def list_agent_suggestions(self, page: int = 0, size: int = 20) -> dict:
        return self.get("/api/agent/suggestions", params={"page": page, "size": size})["data"]

    def approve_agent_suggestion(self, sug_id: int) -> dict:
        """POST /api/agent/suggestions/{id}/approve -> {id, status, grantKey}"""
        return self.post(f"/api/agent/suggestions/{sug_id}/approve")["data"]

    def reject_agent_suggestion(self, sug_id: int) -> dict:
        """POST /api/agent/suggestions/{id}/reject -> {id, status}"""
        return self.post(f"/api/agent/suggestions/{sug_id}/reject")["data"]

    def list_agent_tools(self) -> list:
        return self.get("/api/agent/tools")["data"]

    def set_agent_tool_enabled(self, tool_id: int, enabled: bool) -> dict:
        return self.put(f"/api/agent/tools/{tool_id}/enabled", json={"enabled": enabled})["data"]

    # ---- RBAC helpers (roles / users) ----
    def list_roles(self, page: int = 0, size: int = 50) -> dict:
        return self.get("/api/roles", params={"page": page, "size": size})["data"]

    def create_user(self, payload: dict) -> dict:
        return self.post("/api/users", json=payload)["data"]

    def delete_user(self, user_id: int) -> dict:
        return self.delete(f"/api/users/{user_id}")

    def close(self):
        self.http.close()
