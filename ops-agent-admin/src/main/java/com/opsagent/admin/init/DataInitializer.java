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


    private static final List<String> PERMISSION_CODES = List.of(
            "user:read", "user:write",
            "role:read", "role:write",
            "permission:read", "permission:write",
            "dataset:read", "dataset:write",
            "model:read", "model:write",
            "training:read", "training:write",
            "serving:read", "serving:write");

    @Override
    @Transactional
    public void run(String... args) {
        ensurePgVectorExtension();
        if (!initEnabled) {
            // 开关关闭：不写入种子数据（权限/角色/用户）
            return;
        }

        // 1. 权限：逐条判断，缺失才插入（支持新增权限码补全）
        for (String code : PERMISSION_CODES) {
            if (permissionRepository.findByCode(code).isEmpty()) {
                Permission p = new Permission();
                p.setCode(code);
                p.setDescription(code);
                permissionRepository.save(p);
            }
        }

        // 2. 角色：按名判断，缺失才插入；已存在则取出用于用户绑定
        Role admin = roleRepository.findByName("ADMIN").orElseGet(() -> {
            Role r = new Role();
            r.setName("ADMIN");
            r.setDescription("超级管理员");
            r.setPermissions(Set.copyOf(permissionRepository.findAll()));
            return roleRepository.save(r);
        });

        roleRepository.findByName("OPERATOR").orElseGet(() -> {
            Role r = new Role();
            r.setName("OPERATOR");
            r.setDescription("运营人员");
            r.setPermissions(Set.copyOf(permissionRepository.findAll()));
            return roleRepository.save(r);
        });

        Role user = roleRepository.findByName("USER").orElseGet(() -> {
            Role r = new Role();
            r.setName("USER");
            r.setDescription("普通用户（仅对话）");
            r.setPermissions(Set.of(
                    permissionRepository.findByCode("dataset:read").orElseThrow(),
                    permissionRepository.findByCode("model:read").orElseThrow()));
            return roleRepository.save(r);
        });

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

        // 4. 演示普通用户
        if (userRepository.findByUsername("user").isEmpty()) {
            User demoUser = new User();
            demoUser.setUsername("user");
            demoUser.setPasswordHash(passwordEncoder.encode("user123"));
            demoUser.setDisplayName("演示用户");
            demoUser.setEmail("user@opsagent.local");
            demoUser.setStatus("ACTIVE");
            demoUser.setRoles(Set.of(user));
            userRepository.save(demoUser);
        }
    }
}
