import pytest

from app.tools.http_client import AdminHttpClient, TaskContext
from app.tools.registry import ToolRegistry
from app.transport import agent_pb2


def make_tool(name, method, path, params='{"type":"object","properties":{}}'):
    return agent_pb2.ToolSchema(name=name, description="d", parameters=params,
                                is_write=False, http_method=method, path_template=path)


def make_http():
    return AdminHttpClient(base_url="http://admin:8080", worker_id="w1")


def test_render_get_path_and_query():
    http = make_http()
    tool = make_tool("training_get", "GET", "/api/training/jobs/{jobId}")
    path, query, body = http._render(tool, {"jobId": 5, "page": 1})
    assert path == "/api/training/jobs/5"
    assert query == {"page": 1}
    assert body is None


def test_render_post_uses_body():
    http = make_http()
    tool = make_tool("serving_deploy", "POST", "/api/serving/deploy")
    path, query, body = http._render(tool, {"modelVersionId": 2})
    assert path == "/api/serving/deploy"
    assert query == {}
    assert body == {"modelVersionId": 2}


def test_registry_schemas_openai_format():
    registry = ToolRegistry()
    registry.load([make_tool("training_list", "GET", "/api/training/jobs",
                             '{"type":"object","properties":{"page":{"type":"integer"}},"required":[]}')])
    schemas = registry.schemas()
    assert schemas[0]["type"] == "function"
    fn = schemas[0]["function"]
    assert fn["name"] == "training_list"
    assert "page" in fn["parameters"]["properties"]


def test_http_call_injects_system_headers():
    """验证系统参数注入（Authorization/X-Agent-Worker/X-Agent-Task），LLM 不可见不可改。"""
    http = make_http()
    captured = {}

    class FakeResp:
        status_code = 200
        text = '{"content":[]}'

    async def fake_get(url, headers=None, params=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        return FakeResp()

    http._http.get = fake_get  # type: ignore[assignment]
    tool = make_tool("training_list", "GET", "/api/training/jobs")

    import asyncio
    result = asyncio.run(http.call(tool, {}, TaskContext(task_id="t9", task_token="tok9")))

    assert result["status"] == 200
    assert captured["url"] == "http://admin:8080/api/training/jobs"
    assert captured["headers"]["Authorization"] == "Bearer tok9"
    assert captured["headers"]["X-Agent-Worker"] == "w1"
    assert captured["headers"]["X-Agent-Task"] == "t9"


@pytest.mark.asyncio
async def test_write_tool_without_grant_skipped():
    """写工具无授权：不发起请求，返回 403 提示待人工确认。"""
    http = make_http()
    called = []

    async def fake_request(method, url, headers=None, json=None, params=None):
        called.append(url)

    http._http.request = fake_request  # type: ignore[assignment]
    tool = make_tool("serving_undeploy", "POST", "/api/serving/endpoints/{endpointId}")
    tool.is_write = True
    result = await http.call(tool, {"endpointId": 3},
                             TaskContext(task_id="t1", task_token="tok1"))
    assert result["status"] == 403
    assert "approval" in result["body"]
    assert called == []  # 未发请求


@pytest.mark.asyncio
async def test_write_tool_with_grant_injects_key():
    """写工具带 grant_key（TaskDispatch 下发，v3）：注入 X-Grant-Key。"""
    http = make_http()
    captured = {}

    class FakeResp:
        status_code = 200
        text = "{}"

    async def fake_request(method, url, headers=None, json=None, params=None):
        captured["headers"] = headers
        return FakeResp()

    http._http.request = fake_request  # type: ignore[assignment]
    tool = make_tool("serving_undeploy", "POST", "/api/serving/endpoints/{endpointId}")
    tool.is_write = True
    result = await http.call(tool, {"endpointId": 3},
                             TaskContext(task_id="t1", task_token="tok1",
                                         grant_key="agent:grant:test-key"))
    assert result["status"] == 200
    assert captured["headers"]["X-Grant-Key"] == "agent:grant:test-key"


@pytest.mark.asyncio
async def test_write_tool_grant_key_from_context_only():
    """v3：grant_key 只能来自 TaskContext（TaskDispatch 下发），不再有独立 GrantStore。"""
    http = make_http()
    assert not hasattr(http, "grants")
