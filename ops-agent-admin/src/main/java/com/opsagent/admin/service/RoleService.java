package com.opsagent.admin.service;

import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.dto.PermissionDto;
import com.opsagent.admin.dto.RoleDto;
import com.opsagent.admin.entity.Permission;
import com.opsagent.admin.entity.Role;
import com.opsagent.admin.repository.PermissionRepository;
import com.opsagent.admin.repository.RoleRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class RoleService {

    private final RoleRepository roleRepository;
    private final PermissionRepository permissionRepository;

    @Transactional(readOnly = true)
    public Page<RoleDto.Response> list(Pageable pageable) {
        return roleRepository.findAll(pageable).map(this::toResponse);
    }

    @Transactional(readOnly = true)
    public RoleDto.Response get(Long id) {
        return toResponse(find(id));
    }

    @Transactional
    public RoleDto.Response create(RoleDto.CreateRequest req) {
        if (roleRepository.findByName(req.name()).isPresent()) {
            throw new IllegalArgumentException("角色名已存在");
        }
        Role role = new Role();
        role.setName(req.name());
        role.setDescription(req.description());
        role.setPermissions(resolvePermissions(req.permissionIds()));
        return toResponse(roleRepository.save(role));
    }

    @Transactional
    public RoleDto.Response update(Long id, RoleDto.UpdateRequest req) {
        Role role = find(id);
        role.setDescription(req.description());
        if (req.permissionIds() != null) {
            role.setPermissions(resolvePermissions(req.permissionIds()));
        }
        return toResponse(roleRepository.save(role));
    }

    @Transactional
    public void delete(Long id) {
        roleRepository.delete(find(id));
    }

    private Role find(Long id) {
        return roleRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("角色不存在: " + id));
    }

    private Set<Permission> resolvePermissions(List<Long> permissionIds) {
        if (permissionIds == null || permissionIds.isEmpty()) return new HashSet<>();
        List<Permission> perms = permissionRepository.findAllById(permissionIds);
        if (perms.size() != permissionIds.size()) {
            throw new IllegalArgumentException("存在无效的权限 ID");
        }
        return new HashSet<>(perms);
    }

    private RoleDto.Response toResponse(Role role) {
        List<PermissionDto.Response> perms = role.getPermissions().stream()
                .map(p -> new PermissionDto.Response(p.getId(), p.getCode(), p.getDescription()))
                .collect(Collectors.toList());
        return new RoleDto.Response(role.getId(), role.getName(), role.getDescription(), perms);
    }
}
