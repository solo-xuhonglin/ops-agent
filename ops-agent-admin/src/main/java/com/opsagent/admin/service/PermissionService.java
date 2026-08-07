package com.opsagent.admin.service;

import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.dto.PermissionDto;
import com.opsagent.admin.entity.Permission;
import com.opsagent.admin.repository.PermissionRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class PermissionService {

    private final PermissionRepository permissionRepository;

    @Transactional(readOnly = true)
    public Page<PermissionDto.Response> list(Pageable pageable) {
        return permissionRepository.findAll(pageable)
                .map(p -> new PermissionDto.Response(p.getId(), p.getCode(), p.getDescription()));
    }

    @Transactional(readOnly = true)
    public PermissionDto.Response get(Long id) {
        Permission p = find(id);
        return new PermissionDto.Response(p.getId(), p.getCode(), p.getDescription());
    }

    @Transactional
    public PermissionDto.Response create(PermissionDto.CreateRequest req) {
        Permission p = new Permission();
        p.setCode(req.code());
        p.setDescription(req.description());
        return toResponse(permissionRepository.save(p));
    }

    @Transactional
    public PermissionDto.Response update(Long id, PermissionDto.CreateRequest req) {
        Permission p = find(id);
        p.setCode(req.code());
        p.setDescription(req.description());
        return toResponse(permissionRepository.save(p));
    }

    @Transactional
    public void delete(Long id) {
        permissionRepository.delete(find(id));
    }

    private Permission find(Long id) {
        return permissionRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("权限不存在: " + id));
    }

    private PermissionDto.Response toResponse(Permission p) {
        return new PermissionDto.Response(p.getId(), p.getCode(), p.getDescription());
    }
}
