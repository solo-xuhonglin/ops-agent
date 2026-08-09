package com.opsagent.admin.controller;

import com.opsagent.admin.common.ApiResponse;
import com.opsagent.admin.service.AuditLogService;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;

import java.time.OffsetDateTime;

@RestController
@RequestMapping("/api/audit")
@RequiredArgsConstructor
public class AuditLogController {

    private final AuditLogService auditLogService;

    /**
     * 审计日志列表（仅 ADMIN）。支持按写操作 / 执行人类型 / 执行人 / 审批人 / 对象类型 / 时间范围过滤。
     */
    @GetMapping("/logs")
    @PreAuthorize("hasAuthority('audit:read')")
    public ApiResponse<?> list(@RequestParam(defaultValue = "0") int page,
                               @RequestParam(defaultValue = "20") int size,
                               @RequestParam(required = false) String action,
                               @RequestParam(required = false) String actorType,
                               @RequestParam(required = false) String actorName,
                               @RequestParam(required = false) String approverName,
                               @RequestParam(required = false) String targetType,
                               @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) OffsetDateTime from,
                               @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) OffsetDateTime to) {
        Pageable pageable = PageRequest.of(page, size, Sort.by("id").descending());
        return ApiResponse.ok(auditLogService.search(action, actorType, actorName,
                approverName, targetType, from, to, pageable));
    }
}
