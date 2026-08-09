package com.opsagent.admin.web;

import java.util.Map;
import java.util.Optional;

/**
 * 将可审计的写请求（method + 归一化 path）映射为审计 action 码与目标类型。
 * 归一化：去掉 /api 前缀，数字路径段统一替换为 {id}；未命中则回退为 method:资源根。
 */
public final class ActionMapper {

    private record ActionMeta(String action, String targetType) {}

    private static final Map<String, ActionMeta> MAP = Map.ofEntries(
            Map.entry("POST:/datasets", new ActionMeta("dataset:create", "dataset")),
            Map.entry("PUT:/datasets/{id}", new ActionMeta("dataset:update", "dataset")),
            Map.entry("DELETE:/datasets/{id}", new ActionMeta("dataset:delete", "dataset")),
            Map.entry("POST:/datasets/{id}/collect", new ActionMeta("dataset:collect", "dataset")),

            Map.entry("DELETE:/models/{id}", new ActionMeta("model:delete", "model_version")),

            Map.entry("POST:/training/jobs", new ActionMeta("training:create", "training_job")),
            Map.entry("DELETE:/training/jobs/{id}", new ActionMeta("training:delete", "training_job")),

            Map.entry("POST:/serving/endpoints/deploy", new ActionMeta("serving:deploy", "serving_endpoint")),
            Map.entry("POST:/serving/endpoints/{id}/undeploy", new ActionMeta("serving:undeploy", "serving_endpoint")),
            Map.entry("DELETE:/serving/endpoints/{id}", new ActionMeta("serving:delete", "serving_endpoint")),

            Map.entry("POST:/users", new ActionMeta("user:create", "user")),
            Map.entry("PUT:/users/{id}", new ActionMeta("user:update", "user")),
            Map.entry("DELETE:/users/{id}", new ActionMeta("user:delete", "user")),
            Map.entry("POST:/users/{id}/reset-password", new ActionMeta("user:reset_password", "user")),

            Map.entry("POST:/roles", new ActionMeta("role:create", "role")),
            Map.entry("PUT:/roles/{id}", new ActionMeta("role:update", "role")),
            Map.entry("DELETE:/roles/{id}", new ActionMeta("role:delete", "role")),

            Map.entry("POST:/permissions", new ActionMeta("permission:create", "permission")),
            Map.entry("PUT:/permissions/{id}", new ActionMeta("permission:update", "permission")),
            Map.entry("DELETE:/permissions/{id}", new ActionMeta("permission:delete", "permission")),

            Map.entry("POST:/agent/tasks", new ActionMeta("agent:dispatch", "agent_task")),
            Map.entry("POST:/agent/suggestions/{id}/approve", new ActionMeta("agent:suggestion_approve", "agent_suggestion")),
            Map.entry("POST:/agent/suggestions/{id}/reject", new ActionMeta("agent:suggestion_reject", "agent_suggestion")),
            Map.entry("POST:/agent/conversations", new ActionMeta("agent:conversation_create", "conversation")),
            Map.entry("DELETE:/agent/conversations/{id}", new ActionMeta("agent:conversation_delete", "conversation")),
            Map.entry("POST:/agent/conversations/{id}/messages", new ActionMeta("agent:message", "conversation")),
            Map.entry("DELETE:/agent/tools/{id}", new ActionMeta("agent_tool:delete", "agent_tool"))
    );

    public static String resolve(String method, String uri) {
        return Optional.ofNullable(MAP.get(key(method, uri)))
                .map(ActionMeta::action)
                .orElseGet(() -> method.toLowerCase() + ":" + resourceRoot(uri));
    }

    public static String targetTypeOf(String method, String uri) {
        return Optional.ofNullable(MAP.get(key(method, uri)))
                .map(ActionMeta::targetType)
                .orElseGet(() -> singular(resourceRoot(uri)));
    }

    private static String key(String method, String uri) {
        return method.toUpperCase() + ":" + normalize(uri);
    }

    private static String normalize(String uri) {
        String p = uri.startsWith("/api/") ? uri.substring("/api".length()) : uri;
        String[] parts = p.split("/");
        StringBuilder sb = new StringBuilder();
        for (String part : parts) {
            if (part.isBlank()) continue;
            sb.append('/');
            sb.append(part.matches("\\d+") ? "{id}" : part);
        }
        return sb.toString();
    }

    private static String resourceRoot(String uri) {
        String p = uri.startsWith("/api/") ? uri.substring("/api".length()) : uri;
        String[] parts = p.split("/");
        for (String part : parts) {
            if (!part.isBlank()) return part;
        }
        return "unknown";
    }

    private static String singular(String root) {
        if (root.endsWith("s") && !root.endsWith("ss")) {
            return root.substring(0, root.length() - 1);
        }
        return root;
    }

    private ActionMapper() {}
}
