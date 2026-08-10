"""build_openai_tools 工具列表构建：内置四件套（plan_create/plan_update/wait_until/sleep）必须
全部出现在模型可见的工具列表里——否则模型在异步对象未到达终态时无法等待。

回归：之前 build_openai_tools 只 append 了 plan_create / plan_update，漏注册 wait_until / sleep。
导致模型反复调 dataset_get 触发重复检测才发现目标工具不在列表里（截图：模型原话"There's
no wait_until, no sleep, ..."）。
"""
from __future__ import annotations

from app.tools.registry import ToolRegistry
from app.transport import agent_pb2
from app.agent.graph import build_openai_tools


def make_tool(name, is_write=False, parameters='{"type":"object","properties":{}}'):
    return agent_pb2.ToolSchema(
        name=name, description=f"desc-{name}", parameters=parameters,
        is_write=is_write, http_method="GET", path_template="/x")


def make_registry():
    registry = ToolRegistry()
    registry.load([
        make_tool("dataset_list", is_write=False),
        make_tool("dataset_get", is_write=False),
        make_tool("training_create", is_write=True),  # 只走 approve_training_create
    ])
    return registry


def test_build_openai_tools_includes_all_builtin_tools():
    """内置四件套必须齐全：plan_create / plan_update / wait_until / sleep。"""
    tools = build_openai_tools(make_registry())
    names = {t["function"]["name"] for t in tools}
    for required in ("plan_create", "plan_update", "wait_until", "sleep"):
        assert required in names, f"内置工具 {required} 缺失, 当前工具: {sorted(names)}"


def test_build_openai_tools_does_not_leak_write_tool_body():
    """写工具本体（training_create）不能出现在工具列表——只能走 approve_*.（安全边界）。"""
    tools = build_openai_tools(make_registry())
    names = {t["function"]["name"] for t in tools}
    assert "training_create" not in names, "写工具本体不应进入模型可见列表"
    assert "approve_training_create" in names, "审批工具必须进入模型可见列表"


def test_wait_until_and_sleep_have_proper_schema():
    """wait_until / sleep 的 schema 描述要清晰引导模型使用（之前看不到是 bug 的一半）。"""
    tools = build_openai_tools(make_registry())
    by_name = {t["function"]["name"]: t for t in tools}
    for name in ("wait_until", "sleep"):
        tool = by_name[name]
        assert tool["type"] == "function"
        params = tool["function"]["parameters"]
        assert params["type"] == "object"
        props = params["properties"]
        assert name in props or len(props) > 0, f"{name} 应有可用参数"
