package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.ServerMessage;
import com.opsagent.admin.agent.proto.Suggestion;
import com.opsagent.admin.agent.proto.TaskDispatch;
import com.opsagent.admin.entity.AgentEvent;
import com.opsagent.admin.entity.AgentSuggestion;
import com.opsagent.admin.entity.AgentTask;
import com.opsagent.admin.repository.AgentEventRepository;
import com.opsagent.admin.repository.AgentSuggestionRepository;
import com.opsagent.admin.repository.AgentTaskRepository;
import com.opsagent.admin.security.JwtUtil;
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

    /** 任务级 scoped token 有效期：5 分钟（诊断任务足够，过期即失效） */
    public static final long SCOPED_TOKEN_TTL_MS = 5 * 60_000;

    /** scoped token 裁剪出的只读权限（诊断/问询仅需业务只读） */
    private static final List<String> SCOPED_READ_PERMISSIONS = List.of(
            "dataset:read", "model:read", "training:read", "serving:read");

    private final AgentTaskRepository taskRepository;
    private final AgentEventRepository eventRepository;
    private final AgentSuggestionRepository suggestionRepository;
    private final WorkerRegistry workerRegistry;
    private final JwtUtil jwtUtil;

    /** 派发任务：入库 DISPATCHED → 找在线 worker → 发 TaskDispatch（无 worker 直接 FAILED） */
    @Transactional
    public AgentTask dispatch(String taskType, String targetType, Long targetId, String query, Long dispatchedBy) {
        String effectiveType = (taskType == null || taskType.isBlank()) ? "question" : taskType;
        AgentTask task = new AgentTask();
        task.setTaskId(UUID.randomUUID().toString());
        task.setTaskType(effectiveType);
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
        String taskToken = jwtUtil.generateScopedToken(task.getDispatchedBy(),
                SCOPED_READ_PERMISSIONS, task.getTaskId(), SCOPED_TOKEN_TTL_MS);
        try {
            worker.getResponseObserver().onNext(ServerMessage.newBuilder()
                    .setTaskDispatch(TaskDispatch.newBuilder()
                            .setTaskId(task.getTaskId())
                            .setTaskType(effectiveType)
                            .setTargetType(targetType == null ? "" : targetType)
                            .setTargetId(targetId == null ? 0 : targetId)
                            .setQuery(query == null ? "" : query)
                            .setTaskToken(taskToken))
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

    /** 任务完成（TaskResult 到达）：落结论；写操作建议落 agent_suggestions（PENDING，待人工确认） */
    @Transactional
    public void complete(String taskId, boolean ok, String conclusion, List<Suggestion> suggestions, String error) {
        taskRepository.findByTaskId(taskId).ifPresent(task -> {
            task.setFinishedAt(OffsetDateTime.now());
            if (ok) {
                task.setStatus(STATUS_SUCCEEDED);
                task.setConclusion(conclusion);
                if (suggestions != null && !suggestions.isEmpty()) {
                    persistSuggestions(taskId, suggestions);
                }
            } else {
                task.setStatus(STATUS_FAILED);
                task.setConclusion(error == null || error.isBlank() ? conclusion : error);
            }
            taskRepository.save(task);
            log.info("task finished: taskId={}, ok={}", taskId, ok);
        });
    }

    private void persistSuggestions(String taskId, List<Suggestion> suggestions) {
        for (Suggestion s : suggestions) {
            AgentSuggestion suggestion = new AgentSuggestion();
            suggestion.setTaskId(taskId);
            suggestion.setActionType(s.getActionType());
            suggestion.setTargetType(s.getTargetType());
            suggestion.setTargetId(s.getTargetId());
            suggestion.setParams(s.getParams());
            suggestion.setReason(s.getReason());
            suggestion.setPriority(s.getPriority().isBlank() ? "NORMAL" : s.getPriority());
            suggestionRepository.save(suggestion);
        }
        log.info("task suggestions persisted: taskId={}, count={}", taskId, suggestions.size());
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
