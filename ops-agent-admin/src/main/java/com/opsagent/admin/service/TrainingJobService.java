package com.opsagent.admin.service;

import com.opsagent.admin.entity.TrainingJob;
import com.opsagent.admin.repository.TrainingJobRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class TrainingJobService {

    private final TrainingJobRepository trainingJobRepository;

    @Transactional(readOnly = true)
    public Page<TrainingJob> list(Pageable pageable) {
        return trainingJobRepository.findAll(pageable);
    }

    @Transactional(readOnly = true)
    public TrainingJob get(Long id) {
        return trainingJobRepository.findById(id)
                .orElseThrow(() -> new com.opsagent.admin.common.ResourceNotFoundException("训练任务不存在: " + id));
    }

    @Transactional
    public TrainingJob save(TrainingJob job) {
        return trainingJobRepository.save(job);
    }

    @Transactional
    public void delete(Long id) {
        trainingJobRepository.deleteById(id);
    }
}
