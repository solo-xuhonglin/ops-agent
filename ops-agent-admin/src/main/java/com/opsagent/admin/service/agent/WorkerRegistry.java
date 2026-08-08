package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.AgentInfo;
import com.opsagent.admin.agent.proto.ServerMessage;
import io.grpc.stub.StreamObserver;
import lombok.Getter;
import lombok.Setter;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.EnableScheduling;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 在线 agent worker 注册表（内存）。
 * 一条 gRPC 双向流 = 一个 worker；注册后持有其响应引用，供下发任务/推送使用。
 * 心跳（Ping/Pong）续期 lastSeen，超时（90s）由调度任务清理。
 */
@Component
@EnableScheduling
@Slf4j
public class WorkerRegistry {

    /** 心跳超时：超过此时长未收到任何消息视为失联 */
    public static final long HEARTBEAT_TIMEOUT_MS = 90_000;

    private final Map<String, WorkerEntry> workers = new ConcurrentHashMap<>();

    @Getter
    @Setter
    public static class WorkerEntry {
        private final String workerId;
        private volatile StreamObserver<ServerMessage> responseObserver;
        private volatile List<AgentInfo> agents = List.of();
        private volatile long lastSeenMs = System.currentTimeMillis();

        public WorkerEntry(String workerId, StreamObserver<ServerMessage> responseObserver) {
            this.workerId = workerId;
            this.responseObserver = responseObserver;
        }

        public void touch() {
            this.lastSeenMs = System.currentTimeMillis();
        }
    }

    /** 注册（同 workerId 重复注册覆盖旧条目，返回旧条目（可能为 null）） */
    public Optional<WorkerEntry> register(String workerId, List<AgentInfo> agents,
                                          StreamObserver<ServerMessage> responseObserver) {
        WorkerEntry entry = new WorkerEntry(workerId, responseObserver);
        entry.setAgents(agents);
        WorkerEntry previous = workers.put(workerId, entry);
        if (previous != null) {
            log.info("agent worker re-registered: workerId={}", workerId);
        } else {
            log.info("agent worker registered: workerId={}, agents={}", workerId,
                    agents.stream().map(AgentInfo::getAgentId).toList());
        }
        return Optional.ofNullable(previous);
    }

    public Optional<WorkerEntry> get(String workerId) {
        return Optional.ofNullable(workers.get(workerId));
    }

    public Collection<WorkerEntry> all() {
        return workers.values();
    }

    /** 心跳/任何活跃消息续期 */
    public void touch(String workerId) {
        WorkerEntry entry = workers.get(workerId);
        if (entry != null) {
            entry.touch();
        }
    }

    public void unregister(String workerId) {
        WorkerEntry removed = workers.remove(workerId);
        if (removed != null) {
            log.info("agent worker unregistered: workerId={}", workerId);
        }
    }

    /** 清理失联 worker（调度任务，30s 一次） */
    @Scheduled(fixedDelay = 30_000)
    public void evictStale() {
        long now = System.currentTimeMillis();
        workers.forEach((workerId, entry) -> {
            if (now - entry.getLastSeenMs() > HEARTBEAT_TIMEOUT_MS) {
                log.warn("agent worker heartbeat timeout, evicting: workerId={}", workerId);
                workers.remove(workerId, entry);
            }
        });
    }
}
