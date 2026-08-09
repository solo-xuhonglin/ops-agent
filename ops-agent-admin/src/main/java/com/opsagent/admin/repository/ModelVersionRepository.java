package com.opsagent.admin.repository;

import com.opsagent.admin.entity.ModelVersion;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

public interface ModelVersionRepository extends JpaRepository<ModelVersion, Long>, JpaSpecificationExecutor<ModelVersion> {
}
