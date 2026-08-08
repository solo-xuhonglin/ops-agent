package com.opsagent.admin.init;

import com.opsagent.admin.entity.Permission;
import com.opsagent.admin.entity.Role;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.PermissionRepository;
import com.opsagent.admin.repository.RoleRepository;
import com.opsagent.admin.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.Statement;
import java.util.List;
import java.util.Set;

@Component
@RequiredArgsConstructor
public class DataInitializer implements CommandLineRunner {

    private final PermissionRepository permissionRepository;
    private final RoleRepository roleRepository;
    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final DataSource dataSource;

    /** 初始化开关，默认开启；设为 false 时不写入种子数据（权限/角色/用户） */
    @Value("${app.init.enabled:true}")
    private boolean initEnabled;

    /** 确保 pgvector 扩展存在（建表用到 vector 类型） */
    private void ensurePgVectorExtension() {
        try (Connection conn = dataSource.getConnection();
             Statement stmt = conn.createStatement()) {
            stmt.execute("CREATE EXTENSION IF NOT EXISTS vector");
        } catch (Exception e) {
            // 扩展创建失败不应阻断启动（例如无超级权限时），记录后继续
            System.err.println("pgvector extension init skipped: " + e.getMessage());
        }
    }


    /** 权限码 -> 中文描述（缺省与更新均以此为准） */
    private static final java.util.Map<String, String> PERMISSION_DESCRIPTIONS = java.util.Map.ofEntries(
            java.util.Map.entry("user:read", "查看用户"),
            java.util.Map.entry("user:write", "管理用户"),
            java.util.Map.entry("role:read", "查看角色"),
            java.util.Map.entry("role:write", "管理角色"),
            java.util.Map.entry("permission:read", "查看权限"),
            java.util.Map.entry("permission:write", "管理权限"),
            java.util.Map.entry("dataset:read", "查看数据集"),
            java.util.Map.entry("dataset:write", "管理数据集"),
            java.util.Map.entry("model:read", "查看模型"),
            java.util.Map.entry("model:write", "管理模型"),
            java.util.Map.entry("training:read", "查看训练任务"),
            java.util.Map.entry("training:write", "管理训练任务"),
            java.util.Map.entry("serving:read", "查看模型服务"),
            java.util.Map.entry("serving:write", "管理模型服务"));

    private static final List<String> PERMISSION_CODES = List.copyOf(PERMISSION_DESCRIPTIONS.keySet());

    /** 业务读写权限（运营人员：不含 user/role/permission 后台管理） */
    private static final List<String> BUSINESS_READ_WRITE_CODES = List.of(
            "dataset:read", "dataset:write",
            "model:read", "model:write",
            "training:read", "training:write",
            "serving:read", "serving:write");

    /** 业务只读权限（只读用户 / 未来 agent 继承用户权限） */
    private static final List<String> BUSINESS_READ_CODES = List.of(
            "dataset:read", "model:read", "training:read", "serving:read");

    @Override
    @Transactional
    public void run(String... args) {
        ensurePgVectorExtension();
        if (!initEnabled) {
            // 开关关闭：不写入种子数据（权限/角色/用户）
            return;
        }

        // 1. 权限：缺失插入 + 描述对齐（新增权限码补全，既有权限的描述收敛到中文定义）
        for (java.util.Map.Entry<String, String> e : PERMISSION_DESCRIPTIONS.entrySet()) {
            Permission p = permissionRepository.findByCode(e.getKey()).orElse(null);
            if (p == null) {
                p = new Permission();
                p.setCode(e.getKey());
                p.setDescription(e.getValue());
                permissionRepository.save(p);
            } else if (!e.getValue().equals(p.getDescription())) {
                p.setDescription(e.getValue());
                permissionRepository.save(p);
            }
        }

        // 2. 角色：按名判断，缺失才插入；已存在则收敛权限到目标集合
        //    （既补全新增权限码，也移除不再属于该角色的权限，保证与常量定义一致）。
        Role admin = roleRepository.findByName("ADMIN").orElseGet(() -> {
            Role r = new Role();
            r.setName("ADMIN");
            r.setDescription("超级管理员");
            r.setPermissions(Set.copyOf(permissionRepository.findAll()));
            return roleRepository.save(r);
        });
        syncPermissions(admin, PERMISSION_CODES);

        Role operator = roleRepository.findByName("OPERATOR").orElseGet(() -> {
            Role r = new Role();
            r.setName("OPERATOR");
            r.setDescription("运营人员（业务读写，无后台管理）");
            r.setPermissions(Set.copyOf(permissionRepository.findAll()));
            return roleRepository.save(r);
        });
        syncPermissions(operator, BUSINESS_READ_WRITE_CODES);

        Role readOnly = roleRepository.findByName("READONLY").orElseGet(() -> {
            Role r = new Role();
            r.setName("READONLY");
            r.setDescription("只读用户（业务只读，供 agent 继承权限）");
            r.setPermissions(Set.copyOf(permissionRepository.findAll()));
            return roleRepository.save(r);
        });
        syncPermissions(readOnly, BUSINESS_READ_CODES);

        // 3. 初始管理员
        if (userRepository.findByUsername("admin").isEmpty()) {
            User adminUser = new User();
            adminUser.setUsername("admin");
            adminUser.setPasswordHash(passwordEncoder.encode("admin123"));
            adminUser.setDisplayName("管理员");
            adminUser.setEmail("admin@opsagent.local");
            adminUser.setStatus("ACTIVE");
            adminUser.setRoles(Set.of(admin));
            userRepository.save(adminUser);
        }

        // 4. 演示运营人员（业务读写，无后台管理）
        if (userRepository.findByUsername("user").isEmpty()) {
            User demoUser = new User();
            demoUser.setUsername("user");
            demoUser.setPasswordHash(passwordEncoder.encode("user123"));
            demoUser.setDisplayName("运营人员");
            demoUser.setEmail("user@opsagent.local");
            demoUser.setStatus("ACTIVE");
            demoUser.setRoles(Set.of(operator));
            userRepository.save(demoUser);
        }
    }

    /** 将角色权限收敛为目标权限码集合（仅当集合不一致时写库，幂等）。 */
    private void syncPermissions(Role role, List<String> expectedCodes) {
        Set<String> expectedSet = Set.copyOf(expectedCodes);
        Set<Long> expectedIds = permissionRepository.findAll().stream()
                .filter(p -> expectedSet.contains(p.getCode()))
                .map(Permission::getId)
                .collect(java.util.stream.Collectors.toSet());
        Set<Long> currentIds = role.getPermissions().stream()
                .map(Permission::getId)
                .collect(java.util.stream.Collectors.toSet());
        if (!currentIds.equals(expectedIds)) {
            role.setPermissions(Set.copyOf(permissionRepository.findAll()).stream()
                    .filter(p -> expectedSet.contains(p.getCode()))
                    .collect(java.util.stream.Collectors.toSet()));
            roleRepository.save(role);
        }
    }
}
