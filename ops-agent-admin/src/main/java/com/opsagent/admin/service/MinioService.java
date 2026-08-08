package com.opsagent.admin.service;

import io.minio.BucketExistsArgs;
import io.minio.GetObjectArgs;
import io.minio.GetPresignedObjectUrlArgs;
import io.minio.MakeBucketArgs;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import io.minio.RemoveObjectArgs;
import io.minio.http.Method;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import jakarta.annotation.PostConstruct;
import java.io.InputStream;

@Slf4j
@Service
@RequiredArgsConstructor
@ConditionalOnProperty(prefix = "minio", name = "enabled", havingValue = "true")
public class MinioService {

    private final MinioClient minioClient;
    private final com.opsagent.admin.config.MinioConfig minioConfig;

    @PostConstruct
    public void init() {
        ensureBucketExists(minioConfig.getBucket());
        ensureBucketExists(minioConfig.getModelBucket());
        ensureBucketExists(minioConfig.getLogBucket());
    }

    public void ensureBucketExists(String bucket) {
        try {
            boolean exists = minioClient.bucketExists(BucketExistsArgs.builder().bucket(bucket).build());
            if (!exists) {
                minioClient.makeBucket(MakeBucketArgs.builder().bucket(bucket).build());
                log.info("MinIO bucket created: {}", bucket);
            }
        } catch (Exception e) {
            log.warn("Failed to ensure MinIO bucket {} exists: {}", bucket, e.getMessage());
        }
    }

    public String upload(String objectKey, MultipartFile file) throws Exception {
        return upload(minioConfig.getBucket(), objectKey, file);
    }

    public String upload(String bucket, String objectKey, MultipartFile file) throws Exception {
        try (InputStream is = file.getInputStream()) {
            minioClient.putObject(PutObjectArgs.builder()
                    .bucket(bucket)
                    .object(objectKey)
                    .stream(is, file.getSize(), -1)
                    .contentType(file.getContentType())
                    .build());
        }
        return objectKey;
    }

    /** 从任意输入流上传对象（采集的 CSV、训练日志等不走 MultipartFile 的场景）。 */
    public String upload(String objectKey, InputStream is, long size, String contentType) throws Exception {
        return upload(minioConfig.getBucket(), objectKey, is, size, contentType);
    }

    public String upload(String bucket, String objectKey, InputStream is, long size, String contentType) throws Exception {
        minioClient.putObject(PutObjectArgs.builder()
                .bucket(bucket)
                .object(objectKey)
                .stream(is, size, -1)
                .contentType(contentType)
                .build());
        return objectKey;
    }

    public InputStream download(String objectKey) throws Exception {
        return download(minioConfig.getBucket(), objectKey);
    }

    public InputStream download(String bucket, String objectKey) throws Exception {
        return minioClient.getObject(GetObjectArgs.builder()
                .bucket(bucket)
                .object(objectKey)
                .build());
    }

    public String presignedUrl(String objectKey, int expiryMinutes) throws Exception {
        return presignedUrl(minioConfig.getBucket(), objectKey, expiryMinutes);
    }

    public String presignedUrl(String bucket, String objectKey, int expiryMinutes) throws Exception {
        return minioClient.getPresignedObjectUrl(GetPresignedObjectUrlArgs.builder()
                .bucket(bucket)
                .object(objectKey)
                .method(Method.GET)
                .expiry(expiryMinutes * 60)
                .build());
    }

    public void delete(String objectKey) throws Exception {
        delete(minioConfig.getBucket(), objectKey);
    }

    public void delete(String bucket, String objectKey) throws Exception {
        minioClient.removeObject(RemoveObjectArgs.builder()
                .bucket(bucket)
                .object(objectKey)
                .build());
    }
}
