package com.opsagent.admin.repository;

import com.opsagent.admin.entity.ModelVersion;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ModelVersionRepository extends JpaRepository<ModelVersion, Long> {
}
