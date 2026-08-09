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
    # deepseek-v4-flash：原生支持 thinking + function calling（bind_tools）。
    # 思考模式按任务开关（TaskDispatch.reasoning_enabled），强度取 deepseek_reasoning_effort。
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_reasoning_effort: str = "max"  # thinking 强度：high | max（fast 非思考模式忽略）
    llm_timeout_s: float = 60.0
    max_tool_rounds: int = 10
    # agent 自治写库：直连 PostgreSQL（asyncpg），DDL 归 admin JPA
    # 优先用独立 PG* 变量（避免密码特殊字符进 DSN URL 解析出错）；否则用 DATABASE_URL
    database_url: str = ""
    pg_host: str = ""
    pg_port: int = 5432
    pg_user: str = ""
    pg_password: str = ""
    pg_database: str = ""
    db_pool_min: int = 1
    db_pool_max: int = 5

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
            deepseek_model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            deepseek_reasoning_effort=os.getenv("DEEPSEEK_REASONING_EFFORT", "max"),
            llm_timeout_s=float(os.getenv("DEEPSEEK_TIMEOUT_S", "60")),
            max_tool_rounds=int(os.getenv("AGENT_MAX_TOOL_ROUNDS", "10")),
            database_url=os.getenv("DATABASE_URL", ""),
            pg_host=os.getenv("PGHOST", ""),
            pg_port=int(os.getenv("PGPORT", "5432")),
            pg_user=os.getenv("PGUSER", ""),
            pg_password=os.getenv("PGPASSWORD", ""),
            pg_database=os.getenv("PGDATABASE", ""),
            db_pool_min=int(os.getenv("DB_POOL_MIN", "1")),
            db_pool_max=int(os.getenv("DB_POOL_MAX", "5")),
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
