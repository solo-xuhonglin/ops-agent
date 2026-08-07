package com.opsagent.admin.init;

import com.opsagent.admin.entity.Permission;
import com.opsagent.admin.entity.Role;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.PermissionRepository;
import com.opsagent.admin.repository.RoleRepository;
import com.opsagent.admin.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.boot.CommandLineRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Set;

@Component
@RequiredArgsConstructor
public class DataInitializer implements CommandLineRunner {

    private final PermissionRepository permissionRepository;
    private final RoleRepository roleRepository;
    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

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
        if (permissionRepository.count() > 0) return; // 已初始化则跳过

        // 1. 权限
        for (String code : PERMISSION_CODES) {
            Permission p = new Permission();
            p.setCode(code);
            p.setDescription(code);
            permissionRepository.save(p);
        }

        // 2. 角色
        Role admin = new Role();
        admin.setName("ADMIN");
        admin.setDescription("超级管理员");
        admin.setPermissions(Set.copyOf(permissionRepository.findAll()));
        roleRepository.save(admin);

        Role operator = new Role();
        operator.setName("OPERATOR");
        operator.setDescription("运营人员");
        operator.setPermissions(Set.copyOf(permissionRepository.findAll()));
        roleRepository.save(operator);

        Role user = new Role();
        user.setName("USER");
        user.setDescription("普通用户（仅对话）");
        user.setPermissions(Set.of(
                permissionRepository.findByCode("dataset:read").orElseThrow(),
                permissionRepository.findByCode("model:read").orElseThrow()));
        roleRepository.save(user);

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
    }
}
