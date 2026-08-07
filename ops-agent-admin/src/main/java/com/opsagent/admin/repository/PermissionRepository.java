package com.opsagent.admin.repository;

import com.opsagent.admin.entity.Permission;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PermissionRepository extends JpaRepository<Permission, Long> {
    java.util.Optional<Permission> findByCode(String code);
}
