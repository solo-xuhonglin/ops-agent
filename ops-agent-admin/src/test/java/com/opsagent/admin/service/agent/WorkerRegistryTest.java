package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.AgentInfo;
import com.opsagent.admin.agent.proto.ServerMessage;
import io.grpc.stub.StreamObserver;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class WorkerRegistryTest {

    private WorkerRegistry registry;

    @BeforeEach
    void setUp() {
        registry = new WorkerRegistry();
    }

    private StreamObserver<ServerMessage> observer(String tag) {
        return new StreamObserver<>() {
            @Override public void onNext(ServerMessage value) { }
            @Override public void onError(Throwable t) { }
            @Override public void onCompleted() { }
        };
    }

    @Test
    void registerAddsWorkerWithAgents() {
        registry.register("w1", List.of(AgentInfo.newBuilder().setAgentId("ops-core").build()),
                observer("w1"));
        assertThat(registry.get("w1")).isPresent();
        assertThat(registry.get("w1").get().getAgents().get(0).getAgentId()).isEqualTo("ops-core");
        assertThat(registry.all()).hasSize(1);
    }

    @Test
    void reRegisterOverwritesSameWorkerId() {
        StreamObserver<ServerMessage> first = observer("a");
        StreamObserver<ServerMessage> second = observer("b");
        registry.register("w1", List.of(), first);
        registry.register("w1", List.of(), second);
        assertThat(registry.all()).hasSize(1);
        assertThat(registry.get("w1").get().getResponseObserver()).isSameAs(second);
    }

    @Test
    void touchUpdatesLastSeen() throws InterruptedException {
        registry.register("w1", List.of(), observer("w1"));
        WorkerRegistry.WorkerEntry entry = registry.get("w1").get();
        long before = entry.getLastSeenMs();
        Thread.sleep(5);
        registry.touch("w1");
        assertThat(entry.getLastSeenMs()).isGreaterThan(before);
    }

    @Test
    void evictStaleRemovesTimedOutWorkers() {
        registry.register("w1", List.of(), observer("w1"));
        registry.register("w2", List.of(), observer("w2"));
        registry.get("w1").get().setLastSeenMs(System.currentTimeMillis()
                - WorkerRegistry.HEARTBEAT_TIMEOUT_MS - 1_000);
        registry.evictStale();
        assertThat(registry.get("w1")).isEmpty();
        assertThat(registry.get("w2")).isPresent();
    }

    @Test
    void unregisterRemovesWorker() {
        registry.register("w1", List.of(), observer("w1"));
        registry.unregister("w1");
        assertThat(registry.get("w1")).isEmpty();
        assertThat(registry.all()).isEmpty();
    }
}
