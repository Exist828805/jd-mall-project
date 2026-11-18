package com.macro.mall.service.impl;

import com.macro.mall.service.UmsAdminCacheService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.sql.PreparedStatement;
import java.sql.Timestamp;
import java.util.List;
import java.util.Objects;

/** 仅补偿管理员资料 update 路径的缓存删除失败；任务成功后删除。 */
@Service
public class AdminCacheInvalidationService {
    private static final Logger LOGGER = LoggerFactory.getLogger(AdminCacheInvalidationService.class);
    private final JdbcTemplate jdbc;
    private final ObjectProvider<UmsAdminCacheService> cacheService;
    private final TransactionTemplate newTransaction;
    private final String keyPrefix;

    public AdminCacheInvalidationService(JdbcTemplate jdbc,
            ObjectProvider<UmsAdminCacheService> cacheService,
            PlatformTransactionManager transactionManager,
            @Value("${redis.database}") String database,
            @Value("${redis.key.admin}") String adminKey) {
        this.jdbc = jdbc;
        this.cacheService = cacheService;
        this.keyPrefix = database + ":" + adminKey + ":";
        this.newTransaction = new TransactionTemplate(transactionManager);
        this.newTransaction.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    /** 必须在资料更新事务内调用，防止数据库提交但未留下失效任务。 */
    public void enqueue(String username) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException("缓存失效任务必须与管理员更新处于同一事务");
        }
        GeneratedKeyHolder keyHolder = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    "INSERT INTO ums_admin_cache_invalidation(cache_key) VALUES (?)",
                    new String[]{"id"});
            statement.setString(1, keyPrefix + username);
            return statement;
        }, keyHolder);
        long taskId = Objects.requireNonNull(keyHolder.getKey()).longValue();
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                // afterCommit 中原事务资源可能仍绑定，显式使用新事务处理任务。
                processSafely(taskId);
            }
        });
    }

    @Scheduled(fixedDelayString = "${cache.admin-invalidation.poll-delay-ms:5000}")
    public void retryPending() {
        try {
            List<Long> ids = jdbc.queryForList(
                    "SELECT id FROM ums_admin_cache_invalidation WHERE next_retry_at <= CURRENT_TIMESTAMP ORDER BY id LIMIT 20",
                    Long.class);
            for (Long id : ids) {
                processSafely(id);
            }
        } catch (Exception exception) {
            LOGGER.error("读取管理员缓存失效任务失败，下轮继续处理", exception);
        }
    }

    private void processSafely(long taskId) {
        try {
            newTransaction.executeWithoutResult(status -> {
                List<String> keys = jdbc.queryForList(
                        "SELECT cache_key FROM ums_admin_cache_invalidation WHERE id = ? FOR UPDATE",
                        String.class, taskId);
                if (keys.isEmpty()) {
                    return;
                }
                try {
                    // 独立代理方法会向外抛出删除异常，补偿逻辑才能发现失败。
                    cacheService.getObject().deleteAdminCacheKey(keys.get(0));
                } catch (Exception exception) {
                    String error = exception.getClass().getSimpleName() + ": " + exception.getMessage();
                    jdbc.update("UPDATE ums_admin_cache_invalidation SET attempts = attempts + 1, next_retry_at = ?, last_error = ? WHERE id = ?",
                            new Timestamp(System.currentTimeMillis() + 10000),
                            error.substring(0, Math.min(error.length(), 500)), taskId);
                    LOGGER.warn("管理员缓存删除失败，保留补偿任务 taskId:{} key:{}", taskId, keys.get(0));
                    return;
                }
                jdbc.update("DELETE FROM ums_admin_cache_invalidation WHERE id = ?", taskId);
                LOGGER.info("管理员缓存失效完成 taskId:{} key:{}", taskId, keys.get(0));
            });
        } catch (Exception exception) {
            // 包括删除成功但任务提交失败：任务保留，再次 DEL 同一键是幂等操作。
            LOGGER.error("管理员缓存补偿任务执行失败 taskId:{}，保留任务等待重试", taskId, exception);
        }
    }
}
