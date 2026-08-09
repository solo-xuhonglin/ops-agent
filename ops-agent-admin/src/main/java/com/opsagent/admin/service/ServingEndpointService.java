package com.opsagent.admin.service;

import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.entity.ModelVersion;
import com.opsagent.admin.entity.ServingEndpoint;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.ModelVersionRepository;
import com.opsagent.admin.repository.ServingEndpointRepository;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.List;

import jakarta.persistence.criteria.Predicate;

@Service
@RequiredArgsConstructor
@Slf4j
public class ServingEndpointService {

    private static final int SERVING_PORT = 8000;

    private final ServingEndpointRepository servingEndpointRepository;
    private final ModelVersionRepository modelVersionRepository;
    private final UserRepository userRepository;
    private final CurrentUser currentUser;
    private final ServingLauncher servingLauncher;

    @Transactional(readOnly = true)
    public Page<ServingEndpoint> list(Pageable pageable, String status, Long modelVersionId) {
        Specification<ServingEndpoint> spec = (root, query, cb) -> {
            List<Predicate> ps = new ArrayList<>();
            if (status != null && !status.isBlank()) {
                ps.add(cb.equal(root.get("status"), status));
            }
            if (modelVersionId != null) {
                ps.add(cb.equal(root.get("modelVersionId"), modelVersionId));
            }
            return cb.and(ps.toArray(new Predicate[0]));
        };
        return servingEndpointRepository.findAll(spec, pageable);
    }

    @Transactional(readOnly = true)
    public ServingEndpoint get(Long id) {
        return servingEndpointRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("部署端点不存在: " + id));
    }

    @Transactional
    public ServingEndpoint save(ServingEndpoint ep) {
        return servingEndpointRepository.save(ep);
    }

    /**
     * 部署一个模型版本：校验模型 READY → 建 CREATING 端点 → 起 serving 容器 → 回填容器信息。
     * 就绪/失败由 ServingHealthPoller 轮询判定，本方法立即返回。
     */
    @Transactional
    public ServingEndpoint deploy(Long modelVersionId) {
        ModelVersion mv = modelVersionRepository.findById(modelVersionId)
                .orElseThrow(() -> new ResourceNotFoundException("模型版本不存在: " + modelVersionId));
        if (!"READY".equals(mv.getStatus())) {
            throw new IllegalArgumentException("仅 READY 状态的模型可以部署（当前: " + mv.getStatus() + "）");
        }

        ServingEndpoint ep = new ServingEndpoint();
        ep.setModelVersionId(modelVersionId);
        ep.setStatus("CREATING");
        ep.setHost(null);
        ep.setPort(null);
        ep.setUrl(null);
        ep.setDeployedBy(currentUserId());
        ServingEndpoint saved = servingEndpointRepository.save(ep);

        String containerId = servingLauncher.launch(saved.getId(), modelVersionId);
        String host = servingLauncher.containerName(saved.getId());
        saved.setContainerId(containerId);
        saved.setHost(host);
        saved.setPort(SERVING_PORT);
        saved.setUrl("http://" + host + ":" + SERVING_PORT);
        saved.setUnhealthyCount(0);
        log.info("Serving deploy submitted endpointId={} modelVersionId={} containerId={}",
                saved.getId(), modelVersionId, containerId);
        return servingEndpointRepository.save(saved);
    }

    /**
     * 下线：停删容器 → 置 STOPPED。容器已不存在时幂等。
     */
    @Transactional
    public ServingEndpoint undeploy(Long id) {
        ServingEndpoint ep = get(id);
        if ("STOPPED".equals(ep.getStatus()) || "FAILED".equals(ep.getStatus())) {
            return ep; // 已是终态，幂等返回
        }
        ep.setStatus("STOPPING");
        servingEndpointRepository.save(ep);
        servingLauncher.stopAndRemove(id);
        ep.setStatus("STOPPED");
        ep.setStoppedAt(OffsetDateTime.now());
        ep.setUnhealthyCount(0);
        log.info("Serving endpoint undeployed endpointId={} modelVersionId={}", id, ep.getModelVersionId());
        return servingEndpointRepository.save(ep);
    }

    @Transactional
    public void delete(Long id) {
        ServingEndpoint ep = servingEndpointRepository.findById(id).orElse(null);
        // 删除记录前先确保容器已停删（无论当前状态，防遗留孤儿容器）
        if (ep != null) {
            try {
                servingLauncher.stopAndRemove(id);
            } catch (Exception e) {
                log.warn("Failed to stop serving container while deleting endpointId={} error={}",
                        id, e.getMessage());
            }
        }
        servingEndpointRepository.deleteById(id);
        log.info("Serving endpoint record deleted endpointId={}", id);
    }

    private Long currentUserId() {
        String username = currentUser.username();
        if (username == null) return null;
        return userRepository.findByUsername(username).map(User::getId).orElse(null);
    }
}
