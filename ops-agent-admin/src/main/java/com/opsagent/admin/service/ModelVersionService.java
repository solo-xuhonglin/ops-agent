package com.opsagent.admin.service;

import com.opsagent.admin.entity.ModelVersion;
import com.opsagent.admin.repository.ModelVersionRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class ModelVersionService {

    private final ModelVersionRepository modelVersionRepository;

    @Transactional(readOnly = true)
    public Page<ModelVersion> list(Pageable pageable) {
        return modelVersionRepository.findAll(pageable);
    }

    @Transactional(readOnly = true)
    public ModelVersion get(Long id) {
        return modelVersionRepository.findById(id)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("模型版本不存在: " + id));
    }

    @Transactional
    public ModelVersion save(ModelVersion mv) {
        return modelVersionRepository.save(mv);
    }

    @Transactional
    public void delete(Long id) {
        modelVersionRepository.deleteById(id);
    }
}
