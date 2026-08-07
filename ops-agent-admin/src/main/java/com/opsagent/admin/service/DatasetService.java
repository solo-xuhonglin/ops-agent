package com.opsagent.admin.service;

import com.opsagent.admin.common.ResourceNotFoundException;
import com.opsagent.admin.dto.DatasetDto;
import com.opsagent.admin.entity.Dataset;
import com.opsagent.admin.entity.User;
import com.opsagent.admin.repository.DatasetRepository;
import com.opsagent.admin.repository.UserRepository;
import com.opsagent.admin.security.CurrentUser;
import lombok.RequiredArgsConstructor;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class DatasetService {

    private final DatasetRepository datasetRepository;
    private final CurrentUser currentUser;
    private final UserRepository userRepository;

    @Transactional(readOnly = true)
    public Page<DatasetDto.Response> list(Pageable pageable) {
        return datasetRepository.findAll(pageable).map(this::toResponse);
    }

    @Transactional(readOnly = true)
    public DatasetDto.Response get(Long id) {
        return toResponse(find(id));
    }

    @Transactional
    public DatasetDto.Response create(DatasetDto.CreateRequest req) {
        Dataset d = new Dataset();
        d.setName(req.name());
        d.setDescription(req.description());
        d.setObjectKey(req.objectKey());
        d.setRegion(req.region());
        d.setSource(req.source());
        d.setFileFormat(req.fileFormat());
        d.setRowCount(req.rowCount());
        d.setDateStart(req.dateStart());
        d.setDateEnd(req.dateEnd());
        d.setStatus("READY");
        d.setCreatedBy(currentUserId());
        return toResponse(datasetRepository.save(d));
    }

    @Transactional
    public DatasetDto.Response update(Long id, DatasetDto.UpdateRequest req) {
        Dataset d = find(id);
        d.setName(req.name());
        d.setDescription(req.description());
        d.setRegion(req.region());
        d.setSource(req.source());
        d.setFileFormat(req.fileFormat());
        d.setRowCount(req.rowCount());
        d.setDateStart(req.dateStart());
        d.setDateEnd(req.dateEnd());
        d.setStatus(req.status());
        return toResponse(datasetRepository.save(d));
    }

    @Transactional
    public void delete(Long id) {
        datasetRepository.delete(find(id));
    }

    private Dataset find(Long id) {
        return datasetRepository.findById(id)
                .orElseThrow(() -> new ResourceNotFoundException("数据集不存在: " + id));
    }

    private Long currentUserId() {
        String username = currentUser.username();
        if (username == null) return null;
        return userRepository.findByUsername(username).map(User::getId).orElse(null);
    }

    private DatasetDto.Response toResponse(Dataset d) {
        return new DatasetDto.Response(d.getId(), d.getName(), d.getDescription(), d.getObjectKey(),
                d.getRegion(), d.getSource(), d.getFileFormat(), d.getRowCount(),
                d.getDateStart(), d.getDateEnd(), d.getStatus(), d.getCreatedBy());
    }
}
