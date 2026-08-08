package com.opsagent.admin.repository;

import com.opsagent.admin.entity.TrainingJob;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface TrainingJobRepository extends JpaRepository<TrainingJob, Long> {

    List<TrainingJob> findByStatusIn(List<String> statuses);
}
