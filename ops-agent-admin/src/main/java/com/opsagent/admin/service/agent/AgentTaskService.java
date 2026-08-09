package com.opsagent.admin.service.agent;

import com.opsagent.admin.agent.proto.CancelTask;
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
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

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

    /** 任务超时：DISPATCHED/RUNNING 超过该时长未结束 → 发 CancelTask 置 CANCELLED（双兜底：agent 侧也 await 不退出则靠此收口） */
    @Value("${agent.task.timeout-seconds:300}")
    private long taskTimeoutSeconds;

    /** 定时扫描卡住的任务（DISPATCHED/RUNNING 超时）→ 发 CancelTask + 置 CANCELLED。 */
    @Scheduled(fixedDelay = 30_000)
    @Transactional
    public void timeoutScan() {
        OffsetDateTime cutoff = OffsetDateTime.now().minusSeconds(taskTimeoutSeconds);
        List<AgentTask> stuck = taskRepository.findByStatusInAndCreatedAtBefore(
                List.of(STATUS_DISPATCHED, STATUS_RUNNING), cutoff);
        for (AgentTask task : stuck) {
            cancel(task);
        }
    }

    private void cancel(AgentTask task) {
        if (task.getWorkerId() != null) {
            workerRegistry.get(task.getWorkerId()).ifPresent(worker -> {
                try {
                    worker.getResponseObserver().onNext(ServerMessage.newBuilder()
                            .setCancelTask(CancelTask.newBuilder()
                                    .setTaskId(task.getTaskId())
                                    .setReason("timeout after " + taskTimeoutSeconds + "s"))
                            .build());
                } catch (Exception e) {
                    log.warn("cancel send failed: taskId={}", task.getTaskId());
                }
            });
        }
        task.setStatus(STATUS_CANCELLED);
        task.setFinishedAt(OffsetDateTime.now());
        task.setConclusion("cancelled by timeout (" + taskTimeoutSeconds + "s)");
        taskRepository.save(task);
        log.info("task cancelled by timeout: taskId={}", task.getTaskId());
    }

    /** 派发任务：入库 DISPATCHED → 找在线 worker → 事务提交后发 TaskDispatch（无 worker 直接 FAILED）。 */
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
        ServerMessage dispatchMsg = ServerMessage.newBuilder()
                .setTaskDispatch(TaskDispatch.newBuilder()
                        .setTaskId(task.getTaskId())
                        .setTaskType(effectiveType)
                        .setTargetType(targetType == null ? "" : targetType)
                        .setTargetId(targetId == null ? 0 : targetId)
                        .setQuery(query == null ? "" : query)
                        .setTaskToken(taskToken))
                .build();
        // 推送放到事务提交后：worker 秒回 TaskResult/事件时，complete()/recordEvent()
        // 的事务必须能看到本任务（否则 findByTaskId 查不到、结果被静默丢弃，
        // 任务会卡到 timeoutScan 才被 CANCELLED）。approve() 内联派发的
        // execute_suggestion 任务尤其容易命中该竞态（同线程毫秒级回包）。
        pushAfterCommit(worker, task, dispatchMsg);
        return task;
    }

    private void pushAfterCommit(WorkerRegistry.WorkerEntry worker, AgentTask task, ServerMessage msg) {
        if (!TransactionSynchronizationManager.isSynchronizationActive()) {
            push(worker, task, msg);
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                push(worker, task, msg);
            }
        });
    }

    private void push(WorkerRegistry.WorkerEntry worker, AgentTask task, ServerMessage msg) {
        try {
            worker.getResponseObserver().onNext(msg);
            log.info("task dispatched: taskId={}, type={}, worker={}", task.getTaskId(),
                    task.getTaskType(), worker.getWorkerId());
        } catch (Exception e) {
            // 事务已提交，任务已落库；只能把状态收敛为 FAILED
            log.error("dispatch send failed: taskId={}", task.getTaskId(), e);
            task.setStatus(STATUS_FAILED);
            task.setConclusion("dispatch send failed: " + e.getMessage());
            task.setFinishedAt(OffsetDateTime.now());
            taskRepository.save(task);
        }
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
            if (STATUS_CANCELLED.equals(task.getStatus())) {
                log.info("task already cancelled, ignore late result: taskId={}", taskId);
                return;
            }
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
            if ("execute_suggestion".equals(task.getTaskType()) && task.getQuery() != null) {
                updateSuggestionFromExecuteTask(task, ok, error);
            }
            log.info("task finished: taskId={}, ok={}", taskId, ok);
        });
    }

    /** execute_suggestion 任务结束：把执行结果回写到对应处置建议（EXECUTED/FAILED + result）。 */
    private void updateSuggestionFromExecuteTask(AgentTask task, boolean ok, String error) {
        Long suggestionId = extractSuggestionId(task.getQuery());
        if (suggestionId == null) {
            return;
        }
        suggestionRepository.findById(suggestionId).ifPresent(s -> {
            s.setStatus(ok ? "EXECUTED" : "FAILED");
            s.setExecutedAt(OffsetDateTime.now());
            s.setResult(ok ? task.getConclusion() : (error != null && !error.isBlank() ? error : task.getConclusion()));
            suggestionRepository.save(s);
            log.info("suggestion updated from execute task: id={} status={}", suggestionId, s.getStatus());
        });
    }

    private Long extractSuggestionId(String query) {
        var matcher = java.util.regex.Pattern.compile("\"suggestionId\"\\s*:\\s*(\\d+)").matcher(query);
        return matcher.find() ? Long.parseLong(matcher.group(1)) : null;
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
