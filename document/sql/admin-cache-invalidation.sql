-- 管理员资料更新与缓存失效任务使用同一个本地事务。
CREATE TABLE IF NOT EXISTS ums_admin_cache_invalidation (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    cache_key VARCHAR(255) NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    next_retry_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_error VARCHAR(500) NULL,
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cache_invalidation_due (next_retry_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
