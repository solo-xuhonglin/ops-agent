package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.ServerMessage;
import com.opsagent.admin.agent.proto.Suggestion;
import com.opsagent.admin.agent.proto.TaskDispatch;
import com.opsagent.admin.entity.AgentEvent;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.repository.AgentEventRepository;
import com.opsagent.admin.repository.AgentTaskRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

/**
 * AI Agent 任务状态机与派发。
 * 状态流转：DISPATCHED → RUNNING（收到首事件）→ SUCCEEDED / FAILED（收到 TaskResult）；无在线 worker 时派发即 FAILED。
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class AgentTaskService {

    public static final String STATUS_DISPATCHED = "DISPATCHED";
    public static final String STATUS_RUNNING = "RUNNING";
    public static final String STATUS_SUCCEEDED = "SUCCEEDED";
    public static final String STATUS_FAILED = "FAILED";
    public static final String STATUS_CANCELLED = "CANCELLED";

    private final AgentTaskRepository taskRepository;
    private final AgentEventRepository eventRepository;
    private final WorkerRegistry workerRegistry;

    /** 派发任务：入库 DISPATCHED → 找在线 worker → 发 TaskDispatch（无 worker 直接 FAILED） */
    @Transactional
    public AgentTask dispatch(String taskType, String targetType, Long targetId, String query, Long dispatchedBy) {
        AgentTask task = new AgentTask();
        task.setTaskId(UUID.randomUUID().toString());
        task.setTaskType(taskType);
        task.setTargetType(targetType);
        task.setTargetId(targetId);
        task.setQuery(query);
        task.setStatus(STATUS_DISPATCHED);
        task.setDispatchedBy(dispatchedBy);
        taskRepository.save(task);

        WorkerRegistry.WorkerEntry worker = workerRegistry.all().stream().findFirst().orElse(null);
        if (worker == null) {
            fail(task, "no agent worker online");
            log.warn("dispatch skipped, no online worker: taskId={}", task.getTaskId());
            return task;
        }

        task.setWorkerId(worker.getWorkerId());
        taskRepository.save(task);
        try {
            worker.getResponseObserver().onNext(ServerMessage.newBuilder()
                    .setTaskDispatch(TaskDispatch.newBuilder()
                            .setTaskId(task.getTaskId())
                            .setTaskType(taskType)
                            .setTargetType(targetType == null ? "" : targetType)
                            .setTargetId(targetId == null ? 0 : targetId)
                            .setQuery(query == null ? "" : query))
                    .build());
            log.info("task dispatched: taskId={}, type={}, worker={}", task.getTaskId(), taskType, worker.getWorkerId());
        } catch (Exception e) {
            log.error("dispatch send failed: taskId={}", task.getTaskId(), e);
            fail(task, "dispatch send failed: " + e.getMessage());
        }
        return task;
    }

    /** 记录事件流；首个事件将任务置 RUNNING */
    @Transactional
    public void recordEvent(String taskId, int seq, String type, String content) {
        eventRepository.save(buildEvent(taskId, seq, type, content));
        taskRepository.findByTaskId(taskId).ifPresent(task -> {
            if (STATUS_DISPATCHED.equals(task.getStatus())) {
                task.setStatus(STATUS_RUNNING);
                task.setStartedAt(OffsetDateTime.now());
                taskRepository.save(task);
            }
        });
    }

    /** 任务完成（TaskResult 到达） */
    @Transactional
    public void complete(String taskId, boolean ok, String conclusion, List<Suggestion> suggestions, String error) {
        taskRepository.findByTaskId(taskId).ifPresent(task -> {
            task.setFinishedAt(OffsetDateTime.now());
            if (ok) {
                task.setStatus(STATUS_SUCCEEDED);
                task.setConclusion(conclusion);
                if (suggestions != null && !suggestions.isEmpty()) {
                    // M1 阶段建议不落库（agent_suggestions 表 M3 引入），仅记录
                    log.info("task suggestions (persisted in M3): taskId={}, count={}", taskId, suggestions.size());
                }
            } else {
                task.setStatus(STATUS_FAILED);
                task.setConclusion(error == null || error.isBlank() ? conclusion : error);
            }
            taskRepository.save(task);
            log.info("task finished: taskId={}, ok={}", taskId, ok);
        });
    }

    public Page<AgentTask> list(int page, int size) {
        return taskRepository.findAllByOrderByIdDesc(PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "id")));
    }

    public Optional<AgentTask> get(String taskId) {
        return taskRepository.findByTaskId(taskId);
    }

    public List<AgentEvent> events(String taskId) {
        return eventRepository.findByTaskIdOrderBySeqAsc(taskId);
    }

    private void fail(AgentTask task, String reason) {
        task.setStatus(STATUS_FAILED);
        task.setConclusion(reason);
        task.setFinishedAt(OffsetDateTime.now());
        taskRepository.save(task);
    }

    private AgentEvent buildEvent(String taskId, int seq, String type, String content) {
        AgentEvent event = new AgentEvent();
        event.setTaskId(taskId);
        event.setSeq(seq);
        event.setEventType(type);
        event.setContent(content);
        return event;
    }
}
