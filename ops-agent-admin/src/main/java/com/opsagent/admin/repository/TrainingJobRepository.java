package com.opsagent.admin.repository;

import com.opsagent.admin.entity.TrainingJob;
import org.springframework.data.jpa.repository.JpaRepository;

public interface TrainingJobRepository extends JpaRepository<TrainingJob, Long> {
}
