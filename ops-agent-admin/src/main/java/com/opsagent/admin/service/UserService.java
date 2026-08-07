package com.opsagent.admin.service;

import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.dto.UserDto;
import com.opsagent.admin.entity.Role;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.RoleRepository;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class UserService {

    private final UserRepository userRepository;
    private final RoleRepository roleRepository;
    private final PasswordEncoder passwordEncoder;
    private final CurrentUser currentUser;

    @Transactional(readOnly = true)
    public Page<UserDto.Response> list(Pageable pageable, String keyword) {
        Page<User> page = (keyword == null || keyword.isBlank())
                ? userRepository.findAll(pageable)
                : userRepository.findByUsernameContainingIgnoreCase(keyword, pageable);
        return page.map(this::toResponse);
    }

    @Transactional(readOnly = true)
    public UserDto.Response get(Long id) {
        return toResponse(find(id));
    }

    @Transactional
    public UserDto.Response create(UserDto.CreateRequest req) {
        if (userRepository.existsByUsername(req.username())) {
            throw new IllegalArgumentException("用户名已存在");
        }
        User user = new User();
        user.setUsername(req.username());
        user.setPasswordHash(passwordEncoder.encode(req.password()));
        user.setDisplayName(req.displayName());
        user.setEmail(req.email());
        user.setStatus("ACTIVE");
        user.setRoles(resolveRoles(req.roleIds()));
        user.setCreatedBy(currentUserId());
        return toResponse(userRepository.save(user));
    }

    @Transactional
    public UserDto.Response update(Long id, UserDto.UpdateRequest req) {
        User user = find(id);
        user.setDisplayName(req.displayName());
        user.setEmail(req.email());
        user.setStatus(req.status());
        if (req.roleIds() != null) {
            user.setRoles(resolveRoles(req.roleIds()));
        }
        return toResponse(userRepository.save(user));
    }

    @Transactional
    public void changePassword(Long id, String oldPassword, String newPassword) {
        User user = find(id);
        if (!passwordEncoder.matches(oldPassword, user.getPasswordHash())) {
            throw new IllegalArgumentException("原密码不正确");
        }
        user.setPasswordHash(passwordEncoder.encode(newPassword));
    }

    @Transactional
    public void delete(Long id) {
        userRepository.delete(find(id));
    }

    private User find(Long id) {
        return userRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("用户不存在: " + id));
    }

    private Set<Role> resolveRoles(List<Long> roleIds) {
        if (roleIds == null || roleIds.isEmpty()) return new HashSet<>();
        List<Role> roles = roleRepository.findAllById(roleIds);
        if (roles.size() != roleIds.size()) {
            throw new IllegalArgumentException("存在无效的角色 ID");
        }
        return new HashSet<>(roles);
    }

    private Long currentUserId() {
        String username = currentUser.username();
        if (username == null) return null;
        return userRepository.findByUsername(username).map(User::getId).orElse(null);
    }

    private UserDto.Response toResponse(User user) {
        List<String> roleNames = user.getRoles().stream().map(Role::getName).collect(Collectors.toList());
        return new UserDto.Response(user.getId(), user.getUsername(), user.getDisplayName(),
                user.getEmail(), user.getStatus(), roleNames);
    }
}
