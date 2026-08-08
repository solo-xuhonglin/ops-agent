# M1 AI Agent 通信层 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 打通 admin（gRPC server）↔ ops-agent-core（gRPC client）双向流通信闭环：agent 出站拨号注册、心跳保活、断线重连、任务下发 → 事件流式回推 → 结果返回（M1 固定结论，不接 LLM）。

**Architecture:** admin 作为 gRPC server（内网 :9090，Spring Boot 3.3.2 + net.devh starter），agent 作为 Python (grpcio) client 出站拨号，一条双向流承载 worker 内多 agent；消息用 Envelope+oneof 路由。M1 落库 `agent_tasks` / `agent_events` 两张表支撑闭环可观测；管理面 API `POST/GET /api/agent/tasks` 提供手动派发/查看入口（后续 Poller、前端复用）。M1 不引入 Redis/DeepSeek/grantKey（M2/M3）。

**Tech Stack:** Java 17 / Spring Boot 3.3.2 / grpc-server-spring-boot-starter 3.1.0.RELEASE / protobuf 3.25.x / xolstice protobuf-maven-plugin；Python 3.11 / grpcio+grpcio-tools / pytest-asyncio。

---

## 任务总览

| # | 组件 | 端 |
|---|------|----|
| 1 | `proto/agent.proto` 协议定义 | 共享 |
| 2 | admin pom 依赖 + protobuf 生成插件 | admin |
| 3 | gRPC 端口配置 + 实体 `AgentTask`/`AgentEvent` + Repository | admin |
| 4 | `WorkerRegistry`（在线 worker 注册表 + 心跳过期清理） | admin |
| 5 | `AgentGrpcService`（Connect 双向流：注册/事件/结果/Pong 处理） | admin |
| 6 | `AgentTaskService`（任务状态机 + 派发 + 事件落库 + TaskDispatch 发送） | admin |
| 7 | `AgentTaskController` 管理面 API + 权限码 seed（agent:read/write） | admin |
| 8 | Python 骨架（requirements/Dockerfile/proto 生成）+ `transport/grpc_client` | agent |
| 9 | Python `agent/core` M1 固定结论循环 + main 入口 | agent |
| 10 | Python pytest（协议/重连退避/循环） | agent |
| 11 | Java 单元测试（WorkerRegistry）+ 编译验证 | admin |
| 12 | compose 加 agent 服务 + 部署验证 E2E | 部署 |

---

## Task 1: proto/agent.proto（共享协议）

**Files:**
- Create: `ops-agent-core/proto/agent.proto`（源，agent 侧）
- Create: `ops-agent-admin/src/main/proto/agent.proto`（复制同一份，admin 构建用）

**Step 1: 定义协议**

```proto
syntax = "proto3";

package opsagent.agent;

option java_multiple_files = true;
option java_package = "com.opsagent.admin.agent.proto";
option java_outer_classname = "AgentProto";

// admin 为 server，agent 为 client 出站拨号；一条双向流承载 worker 内多 agent
service AgentService {
  rpc Connect(stream ClientMessage) returns (stream ServerMessage);
}

message ClientMessage {
  oneof msg {
    Register register = 1;
    TaskEvent task_event = 2;
    TaskResult task_result = 3;
    AgentUpdate agent_update = 4;
    Pong pong = 5;
  }
}

message ServerMessage {
  oneof msg {
    RegisterAck register_ack = 1;
    TaskDispatch task_dispatch = 2;
    CancelTask cancel_task = 3;
    Ping ping = 4;
  }
}

message AgentInfo {
  string agent_id = 1;
  repeated string capabilities = 2;
}

message Register {
  string worker_id = 1;
  repeated AgentInfo agents = 2;
}

message RegisterAck {
  bool ok = 1;
  string message = 2;
}

message TaskEvent {
  string task_id = 1;
  int32 seq = 2;
  string event_type = 3;   // progress / tool_call / error
  string content = 4;
}

message Suggestion {
  string action_type = 1;
  string target_type = 2;
  int64 target_id = 3;
  string params = 4;       // JSON 字符串
  string reason = 5;
  string priority = 6;     // HIGH / NORMAL / LOW
}

message TaskResult {
  string task_id = 1;
  bool ok = 2;
  string conclusion = 3;
  repeated Suggestion suggestions = 4;
  string error = 5;
}

message TaskDispatch {
  string task_id = 1;
  string task_type = 2;    // diagnose_training / diagnose_serving / diagnose_dataset / model_review / question
  string target_type = 3;
  int64 target_id = 4;
  string query = 5;
  string task_token = 6;   // M2 起使用（scoped JWT）
}

message CancelTask {
  string task_id = 1;
  string reason = 2;
}

message AgentUpdate {
  repeated AgentInfo agents = 1;
}

message Ping {
  int64 ts = 1;
}

message Pong {
  int64 ts = 1;
}
```

**Step 2: Commit**

```bash
git add ops-agent-core/proto/agent.proto ops-agent-admin/src/main/proto/agent.proto
git commit -m "feat(agent): shared gRPC protocol definition"
```

---

## Task 2: admin pom 依赖 + protobuf 生成插件

**Files:**
- Modify: `ops-agent-admin/pom.xml`

**Step 1: 追加依赖与插件**（依赖块追加 grpc starter + protobuf；build/plugins 追加 os-maven + protobuf-maven-plugin）：

```xml
<properties>
  ...
  <grpc-starter.version>3.1.0.RELEASE</grpc-starter.version>
</properties>

<dependencies>
  ...
  <!-- gRPC server（agent 双向流） -->
  <dependency>
    <groupId>net.devh</groupId>
    <artifactId>grpc-server-spring-boot-starter</artifactId>
    <version>${grpc-starter.version}</version>
  </dependency>
</dependencies>

<build>
  <extensions>
    <extension>
      <groupId>kr.motd.maven</groupId>
      <artifactId>os-maven-plugin</artifactId>
      <version>1.7.1</version>
    </extension>
  </extensions>
  <plugins>
    <plugin>
      <groupId>org.xolstice.maven.plugins</groupId>
      <artifactId>protobuf-maven-plugin</artifactId>
      <version>0.6.1</version>
      <configuration>
        <protocArtifact>com.google.protobuf:protoc:3.25.3:exe:${os.detected.classifier}</protocArtifact>
        <pluginId>grpc-java</pluginId>
        <pluginArtifact>io.grpc:protoc-gen-grpc-java:1.63.0:exe:${os.detected.classifier}</pluginArtifact>
      </configuration>
      <executions>
        <execution>
          <goals><goal>compile</goal><goal>compile-custom</goal></goals>
        </execution>
      </executions>
    </plugin>
  </plugins>
</build>
```

**Step 2: 验证生成**

Run: `mvn -q compile`（本地仅编译验证，产物在 `target/generated-sources/protobuf/`）
Expected: 编译通过，生成 `com/opsagent/admin/agent/proto/AgentServiceGrpc.java` 等

**Step 3: Commit**

```bash
git add ops-agent-admin/pom.xml
git commit -m "chore(agent): add gRPC server deps and protobuf codegen"
```

---

## Task 3: gRPC 端口配置 + 实体 + Repository

**Files:**
- Modify: `ops-agent-admin/src/main/resources/application.yml`（追加 `grpc.server.port`）
- Create: `entity/AgentTask.java`、`entity/AgentEvent.java`
- Create: `repository/AgentTaskRepository.java`、`repository/AgentEventRepository.java`

**Step 1: application.yml**

```yaml
grpc:
  server:
    port: 9090
```

**Step 2: AgentTask 实体**（表 `agent_tasks`，风格同 TrainingJob：Lombok + OffsetDateTime + snake_case）：

```java
@Entity
@Table(name = "agent_tasks")
@Getter @Setter @NoArgsConstructor
public class AgentTask {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", length = 64, nullable = false, unique = true)
    private String taskId;

    @Column(name = "task_type", length = 32, nullable = false)
    private String taskType;

    @Column(name = "target_type", length = 32)
    private String targetType;

    @Column(name = "target_id")
    private Long targetId;

    @Column(columnDefinition = "TEXT")
    private String query;

    @Column(length = 16, nullable = false)
    private String status;   // DISPATCHED / RUNNING / SUCCEEDED / FAILED / CANCELLED

    @Column(name = "dispatched_by")
    private Long dispatchedBy;

    @Column(name = "worker_id", length = 64)
    private String workerId;

    @Column(columnDefinition = "TEXT")
    private String conclusion;

    @Column(name = "started_at")
    private OffsetDateTime startedAt;

    @Column(name = "finished_at")
    private OffsetDateTime finishedAt;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist void onCreate() { createdAt = OffsetDateTime.now(); }
}
```

**Step 3: AgentEvent 实体**（表 `agent_events`，`seq` 任务内序号）：

```java
@Entity
@Table(name = "agent_events")
@Getter @Setter @NoArgsConstructor
public class AgentEvent {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "task_id", length = 64, nullable = false)
    private String taskId;

    @Column(nullable = false)
    private Integer seq;

    @Column(name = "event_type", length = 16, nullable = false)
    private String eventType;

    @Column(columnDefinition = "TEXT")
    private String content;

    @Column(name = "created_at")
    private OffsetDateTime createdAt;

    @PrePersist void onCreate() { createdAt = OffsetDateTime.now(); }
}
```

**Step 4: Repository**（标准 JpaRepository，`AgentEventRepository` 加 `List<AgentEvent> findByTaskIdOrderBySeqAsc(String taskId)`）。

**Step 5: Commit**

```bash
git add ops-agent-admin/src/main/resources/application.yml ops-agent-admin/src/main/java/com/opsagent/admin/entity ops-agent-admin/src/main/java/com/opsagent/admin/repository
git commit -m "feat(agent): agent task/event entities and repositories"
```

---

## Task 4: WorkerRegistry

**Files:**
- Create: `service/agent/WorkerRegistry.java`

**Step 1: 实现**（内存注册表，`ConcurrentHashMap<workerId, WorkerEntry>`；`WorkerEntry` 持有双向流两端的 StreamObserver 引用 + agents + lastSeen；心跳 90s 超时由 `@Scheduled` 清理；线程安全用 synchronized 或并发容器）：

```java
@Component
@Slf4j
public class WorkerRegistry {
    public static final long HEARTBEAT_TIMEOUT_MS = 90_000;

    @Getter @Setter
    public static class WorkerEntry {
        private final String workerId;
        private StreamObserver<ServerMessage> responseObserver;  // 下发任务/推送用
        private List<AgentInfo> agents = List.of();
        private volatile long lastSeenMs = System.currentTimeMillis();
        // + touch() 更新 lastSeenMs
    }

    private final Map<String, WorkerEntry> workers = new ConcurrentHashMap<>();

    public Optional<WorkerEntry> register(String workerId, List<AgentInfo> agents, StreamObserver<ServerMessage> obs);
    public Optional<WorkerEntry> get(String workerId);
    public Collection<WorkerEntry> all();
    public void touch(String workerId);                 // 心跳续期
    public void unregister(String workerId);
    public void evictStale();                            // 清理 lastSeen 超时条目（@Scheduled 30s）
}
```

**Step 2: 单元测试**（注册/覆盖注册/心跳续期/过期清理/注销；`junit` 断言）。

**Step 3: Commit**

```bash
git add ops-agent-admin/src/main/java/com/opsagent/admin/service/agent ops-agent-admin/src/test
git commit -m "feat(agent): worker registry with heartbeat eviction"
```

---

## Task 5: AgentGrpcService（Connect 双向流）

**Files:**
- Create: `service/agent/AgentGrpcService.java`（`@GrpcService` + extends `AgentServiceGrpc.AgentServiceImplBase`）

**Step 1: 实现要点**

```java
@GrpcService
@RequiredArgsConstructor
@Slf4j
public class AgentGrpcService extends AgentServiceGrpc.AgentServiceImplBase {

    private final WorkerRegistry registry;
    private final AgentTaskService taskService;

    @Override
    public StreamObserver<ClientMessage> connect(StreamObserver<ServerMessage> responseObserver) {
        // 返回一个 inbound handler：
        //  - Register: registry.register(workerId, agents, responseObserver) → 回 RegisterAck
        //  - TaskEvent: taskService.recordEvent(taskId, seq, type, content)
        //  - TaskResult: taskService.complete(taskId, ok, conclusion, suggestions, error)（RUNNING→SUCCEEDED/FAILED, 落 conclusion）
        //  - Pong: registry.touch(workerId)（Pong 不含 workerId → 用注册时绑定的 workerId，inbound handler 内维护局部变量）
        //  - AgentUpdate: 更新 registry 条目 agents
        //  - onError/onCompleted: registry.unregister(workerId)
    }
}
```

- inbound handler 内部维护 `AtomicReference<String> workerIdRef`，注册后才可处理后续消息；
- 未注册先收业务消息 → log.warn 忽略；
- 所有异常 try/catch + log.error，不抛出（流不能因单条消息崩溃）。

**Step 2: Commit**

```bash
git add ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentGrpcService.java
git commit -m "feat(agent): gRPC bidi stream handler (register/event/result/pong)"
```

---

## Task 6: AgentTaskService（任务状态机 + 派发）

**Files:**
- Create: `service/agent/AgentTaskService.java`

**Step 1: 实现要点**

```java
@Service
@RequiredArgsConstructor
@Slf4j
public class AgentTaskService {
    // 状态常量: DISPATCHED / RUNNING / SUCCEEDED / FAILED / CANCELLED

    // 派发：入库 DISPATCHED → 查 WorkerRegistry 找在线 worker（本期取 all() 第一个）
    //   → 无在线 worker：任务置 FAILED（reason 记录"no worker online"）返回
    //   → 有：发 TaskDispatch（task_id/task_type/target_type/target_id/query）
    //     → 发送失败 onError：置 FAILED
    public AgentTask dispatch(String taskType, String targetType, Long targetId, String query, Long dispatchedBy);

    public void recordEvent(String taskId, int seq, String type, String content);   // 落 agent_events；任务 RUNNING
    public void complete(String taskId, boolean ok, String conclusion, List<Suggestion> suggestions, String error);
    //   ok → SUCCEEDED + conclusion；!ok → FAILED + error（conclusion 可存错误说明）
    //   finishedAt = now；worker 侧已结束
    public List<AgentTask> list(int page, int size);        // 按 id 倒序
    public Optional<AgentTask> get(String taskId);
    public List<AgentEvent> events(String taskId);

    // 后续 M2/M3 扩展：cancel 超时、grantKey 推送
}
```

- `dispatch` 的 taskId 用 `UUID.randomUUID().toString()`；发送消息走 `WorkerEntry.responseObserver`（线程安全由 gRPC 保证）。

**Step 2: Commit**

```bash
git add ops-agent-admin/src/main/java/com/opsagent/admin/service/agent/AgentTaskService.java
git commit -m "feat(agent): task dispatch state machine and event persistence"
```

---

## Task 7: AgentTaskController + 权限 seed

**Files:**
- Create: `controller/AgentTaskController.java`
- Modify: `init/DataInitializer.java`（新增 `agent:read` / `agent:write` 权限码）

**Step 1: Controller**（基路径 `/api/agent/tasks`）：

```java
@RestController
@RequestMapping("/api/agent/tasks")
@RequiredArgsConstructor
public class AgentTaskController {
    // POST /            agent:write  body {taskType, targetType, targetId, query}
    //                    → {taskId, status}（派发）
    // GET  /            agent:read  分页列表
    // GET  /{taskId}    agent:read  详情 + events
}
```

- 复用现有 `ApiResponse<T>` 包装；`CurrentUser` 取当前 userId 作 `dispatchedBy`。

**Step 2: DataInitializer 加权限码**

在 `PERMISSION_DESCRIPTIONS`（Map<code,中文>）追加：
```java
put("agent:read", "查看Agent任务");   // ADMIN/OPERATOR/READONLY
put("agent:write", "派发Agent任务");  // ADMIN/OPERATOR
```
并同步角色权限分配逻辑（ADMIN 全量；OPERATOR + READONLY 增加 agent:read；OPERATOR 增加 agent:write）。

**Step 3: Commit**

```bash
git add ops-agent-admin/src/main/java/com/opsagent/admin/controller/AgentTaskController.java ops-agent-admin/src/main/java/com/opsagent/admin/init/DataInitializer.java
git commit -m "feat(agent): task management API and agent permission seeds"
```

---

## Task 8: Python 骨架 + grpc_client

**Files:**
- Create: `ops-agent-core/requirements.txt`、`Dockerfile`、`.dockerignore`、`app/__init__.py`
- Create: `ops-agent-core/app/config.py`、`app/transport/__init__.py`、`app/transport/grpc_client.py`
- Create: `ops-agent-core/gen.sh`（用 grpcio-tools 从 proto 生成 `app/transport/agent_pb2.py`、`agent_pb2_grpc.py`）

**Step 1: requirements.txt**

```
grpcio==1.64.1
grpcio-tools==1.64.1
pydantic==2.8.2
python-dotenv==1.0.1
pytest==8.2.2
pytest-asyncio==0.23.8
```

**Step 2: Dockerfile**（python:3.11-slim；`gen.sh` 生成代码；`CMD ["python","-m","app.main"]`）：

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proto/agent.proto proto/
COPY gen.sh .
RUN chmod +x gen.sh && ./gen.sh          # 生成 pb2 到 app/transport
COPY app/ app/
CMD ["python", "-m", "app.main"]
```

**Step 3: config.py**（全部环境变量，带默认值）：

```python
from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Config:
    worker_id: str
    admin_grpc_addr: str      # "admin:9090"
    reconnect_min_s: float = 1.0
    reconnect_max_s: float = 30.0
    ping_interval_s: float = 30.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            worker_id=os.getenv("WORKER_ID", "ops-agent-core-1"),
            admin_grpc_addr=os.getenv("ADMIN_GRPC_ADDR", "localhost:9090"),
        )
```

**Step 4: grpc_client.py**（核心：出站拨号 + 双向流收发 + 指数退避重连 + Ping 响应）

```python
import asyncio, logging
import grpc
from app.transport import agent_pb2, agent_pb2_grpc

log = logging.getLogger("grpc_client")

class GrpcClient:
    def __init__(self, config: Config, agents: list[tuple[str, list[str]]]):
        self.cfg, self.agents = config, agents
        self._channel: grpc.aio.Channel | None = None
        self._stream: grpc.aio.StreamStreamCall | None = None
        self._callbacks = {}      # 消息类型 -> 处理函数（由 main 注入）
        self._seq = 0

    async def run(self):
        """主循环：拨号 → 注册 → 收消息；断开后指数退避重连，永不退出"""
        backoff = self.cfg.reconnect_min_s
        while True:
            try:
                await self.connect_once()
                backoff = self.cfg.reconnect_min_s
                log.info("connected to admin %s", self.cfg.admin_grpc_addr)
            except Exception as e:      # 连接失败或流中断
                log.warning("connection lost: %s; retry in %.1fs", e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.cfg.reconnect_max_s)

    async def connect_once(self):
        self._channel = grpc.aio.insecure_channel(self.cfg.admin_grpc_addr)
        stub = agent_pb2_grpc.AgentServiceStub(self._channel)
        self._stream = stub.Connect()   # bidi
        await self._send(agent_pb2.ClientMessage(register=agent_pb2.Register(
            worker_id=self.cfg.worker_id,
            agents=[agent_pb2.AgentInfo(agent_id=a, capabilities=c) for a, c in self.agents])))
        async for msg in self._stream:  # 收 server 消息
            self._route(msg)

    async def _send(self, msg: agent_pb2.ClientMessage):
        await self._stream.write(msg)

    def _route(self, msg: agent_pb2.ServerMessage):
        kind = msg.WhichOneof("msg")
        if kind == "ping" and self._callbacks.get("ping"):
            # 直接回 Pong（事件循环内即可，write 是 async → 用 ensure_future）
        elif cb := self._callbacks.get(kind):
            cb(msg)

    # 供 agent 层调用
    async def send_event(self, task_id: str, event_type: str, content: str):
        self._seq += 1
        await self._send(agent_pb2.ClientMessage(task_event=agent_pb2.TaskEvent(
            task_id=task_id, seq=self._seq, event_type=event_type, content=content)))

    async def send_result(self, task_id: str, ok: bool, conclusion: str,
                          suggestions: list | None = None, error: str = ""):
        await self._send(agent_pb2.ClientMessage(task_result=agent_pb2.TaskResult(
            task_id=task_id, ok=ok, conclusion=conclusion, error=error)))
```

**Step 5: Commit**

```bash
git add ops-agent-core
git commit -m "feat(agent): python project skeleton and gRPC client with reconnect"
```

---

## Task 9: Python agent 循环（M1 固定结论）

**Files:**
- Create: `ops-agent-core/app/agent/__init__.py`、`app/agent/core.py`、`app/events.py`
- Create: `ops-agent-core/app/main.py`

**Step 1: core.py（M1：收到 TaskDispatch → 发进度事件 → 返回固定结论）**

```python
import asyncio, logging
from app.transport.grpc_client import GrpcClient

log = logging.getLogger("agent.core")

async def handle_dispatch(client: GrpcClient, msg):
    d = msg.task_dispatch
    task_id = d.task_id
    log.info("dispatch received: task=%s type=%s", task_id, d.task_type)
    await client.send_event(task_id, "progress", f"received task {d.task_type}")
    await asyncio.sleep(0.2)                     # 模拟处理
    await client.send_event(task_id, "progress", "M1 stub: no LLM yet")
    await client.send_result(task_id, ok=True,
        conclusion=f"M1 connectivity OK: task {task_id} ({d.task_type}) processed by stub")
```

**Step 2: main.py（装配：注册回调 + 启动重连循环）**

```python
import asyncio, logging, signal
from app.config import Config
from app.transport.grpc_client import GrpcClient
from app.agent import core

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

async def amain():
    cfg = Config.from_env()
    client = GrpcClient(cfg, agents=[("ops-core", ["diagnose_training", "diagnose_serving",
                                                   "diagnose_dataset", "model_review", "question"])])
    client.on("task_dispatch", lambda m: asyncio.create_task(core.handle_dispatch(client, m)))
    await client.run()

if __name__ == "__main__":
    asyncio.run(amain())
```

**Step 3: Commit**

```bash
git add ops-agent-core/app
git commit -m "feat(agent): M1 stub agent loop (fixed conclusion)"
```

---

## Task 10: Python pytest

**Files:**
- Create: `ops-agent-core/tests/__init__.py`、`tests/test_config.py`、`tests/test_grpc_client.py`、`tests/test_agent_core.py`

**Step 1: 测试点**
- `test_config`：`Config.from_env()` 默认值 + 环境变量覆盖；
- `test_grpc_client`：退避序列（`min→max` 封顶、翻倍、重置）；消息路由分发（mock stream，`_route` 按 oneof 调用对应回调）；
- `test_agent_core`：`handle_dispatch` 用 mock client 断言：2 个事件 + 1 个结果、task_id 正确、结论含 task_id。

**Step 2: 运行**

Run: `cd ops-agent-core && python -m pytest tests -v`
Expected: 全部 PASS

**Step 3: Commit**

```bash
git add ops-agent-core/tests
git commit -m "test(agent): python unit tests for client and stub loop"
```

---

## Task 11: Java 单元测试 + 编译验证

**Files:**
- Create: `ops-agent-admin/src/test/java/com/opsagent/admin/service/agent/WorkerRegistryTest.java`

**Step 1: WorkerRegistry 测试**（注册/心跳 touch 续期/90s 过期被 evict/注销；用 mocked StreamObserver）。

**Step 2: 编译验证**

Run: `mvn -q compile`
Expected: BUILD SUCCESS（含 gRPC 生成代码）

**Step 3: Commit**

```bash
git add ops-agent-admin/src/test ops-agent-admin/pom.xml
git commit -m "test(agent): worker registry unit tests"
```

---

## Task 12: compose + 部署验证 E2E

**Files:**
- Modify: `docker-compose.yml`（新增 agent 服务）
- Modify: `deploy-remote.env.example`（加 `DEEPSEEK_API_KEY=` 占位，M2 用）

**Step 1: compose 新增 agent**

```yaml
  # ===== AI Agent（常驻，零端口暴露；出站拨号 admin gRPC）=====
  agent:
    build:
      context: ./ops-agent-core
      dockerfile: Dockerfile
    image: ops-agent-core:latest
    container_name: ops-agent-agent
    restart: unless-stopped
    depends_on:
      - admin
    environment:
      WORKER_ID: ops-agent-core-1
      ADMIN_GRPC_ADDR: admin:9090
      DEEPSEEK_API_KEY: ${DEEPSEEK_API_KEY:-}
      DEEPSEEK_BASE_URL: https://api.deepseek.com
      DEEPSEEK_MODEL: deepseek-chat
    networks:
      - opsnet
```

**Step 2: 部署 + E2E 验证**

1. `./deploy.sh admin agent`（或 `scripts/ssh_deploy.py admin agent`）服务器构建部署；
2. `docker logs ops-agent-agent`：看到 `connected to admin` + 注册日志；
3. `curl -X POST http://<host>:8080/api/agent/tasks -H "Authorization: Bearer <admin jwt>" -H "Content-Type: application/json" -d '{"taskType":"question","query":"hello"}'` → 返回 taskId；
4. `curl GET /api/agent/tasks/<taskId>` → status SUCCEEDED + conclusion 含 `M1 connectivity OK`；events 两条。

**Step 3: Commit**

```bash
git add docker-compose.yml deploy-remote.env.example
git commit -m "feat(agent): compose agent service and E2E verification"
```

---

## 验收标准（M1 Done）

- [ ] admin 编译通过，gRPC server :9090 启动（内网不映射宿主端口）
- [ ] agent 出站拨号 → 注册 → 心跳 → 断线自动重连（日志可见）
- [ ] 派发任务 → agent 回 2 个进度事件 → 返回固定结论 → 落库（agent_tasks SUCCEEDED + agent_events 2 条）
- [ ] worker 离线时派发 → 任务 FAILED（不悬挂）
- [ ] Python pytest 全绿；Java WorkerRegistry 单测绿
- [ ] E2E 通过（见 Task 12 Step 2）
