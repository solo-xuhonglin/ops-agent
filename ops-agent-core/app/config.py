"""AI Agent worker (ops-agent-core) 配置：全部来自环境变量。"""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Config:
    worker_id: str
    admin_grpc_addr: str
    admin_http_base: str = "http://admin:8080"
    reconnect_min_s: float = 1.0
    reconnect_max_s: float = 30.0
    ping_interval_s: float = 30.0
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # 固定 deepseek-reasoner：输出含推理链(reasoning_content)，且不支持 tools/temperature 参数
    # （工具走 prompt 注入 + JSON 契约，见 graph.py）；如需更换模型改此环境变量即可
    deepseek_model: str = "deepseek-reasoner"
    llm_timeout_s: float = 60.0
    max_tool_rounds: int = 10

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            worker_id=os.getenv("WORKER_ID", "ops-agent-core-1"),
            admin_grpc_addr=os.getenv("ADMIN_GRPC_ADDR", "localhost:9090"),
            admin_http_base=os.getenv("ADMIN_HTTP_BASE", "http://admin:8080"),
            reconnect_min_s=float(os.getenv("AGENT_RECONNECT_MIN_S", "1.0")),
            reconnect_max_s=float(os.getenv("AGENT_RECONNECT_MAX_S", "30.0")),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            deepseek_base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-reasoner"),
            llm_timeout_s=float(os.getenv("DEEPSEEK_TIMEOUT_S", "60")),
            max_tool_rounds=int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "10")),
        )


def backoff_sequence(min_s: float, max_s: float) -> list[float]:
    """指数退避序列：从 min 翻倍递增，序列以 max 收尾（封顶），不越过 max。"""
    seq: list[float] = []
    v = min_s
    while v < max_s:
        seq.append(round(v, 1))
        v *= 2
    seq.append(max_s)
    return seq
