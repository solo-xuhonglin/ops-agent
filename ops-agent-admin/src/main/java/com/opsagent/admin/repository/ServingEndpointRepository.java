package com.opsagent.admin.repository;

import com.opsagent.admin.entity.ServingEndpoint;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

public interface ServingEndpointRepository extends JpaRepository<ServingEndpoint, Long>, JpaSpecificationExecutor<ServingEndpoint> {
}
