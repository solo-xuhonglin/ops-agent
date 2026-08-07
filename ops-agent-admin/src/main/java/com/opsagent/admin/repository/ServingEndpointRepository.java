package com.opsagent.admin.repository;

import com.opsagent.admin.entity.ServingEndpoint;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ServingEndpointRepository extends JpaRepository<ServingEndpoint, Long> {
}
