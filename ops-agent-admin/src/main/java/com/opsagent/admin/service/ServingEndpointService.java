package com.opsagent.admin.service;

import com.opsagent.admin.entity.ServingEndpoint;
import com.opsagent.admin.repository.ServingEndpointRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class ServingEndpointService {

    private final ServingEndpointRepository servingEndpointRepository;

    @Transactional(readOnly = true)
    public Page<ServingEndpoint> list(Pageable pageable) {
        return servingEndpointRepository.findAll(pageable);
    }

    @Transactional(readOnly = true)
    public ServingEndpoint get(Long id) {
        return servingEndpointRepository.findById(id)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("部署端点不存在: " + id));
    }

    @Transactional
    public ServingEndpoint save(ServingEndpoint ep) {
        return servingEndpointRepository.save(ep);
    }

    @Transactional
    public void delete(Long id) {
        servingEndpointRepository.deleteById(id);
    }
}
