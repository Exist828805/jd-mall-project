package com.macro.mall.service;

import com.macro.mall.common.service.RedisService;
import com.macro.mall.security.aspect.RedisCacheAspect;
import com.macro.mall.service.impl.AdminCacheInvalidationService;
import com.macro.mall.service.impl.UmsAdminCacheServiceImpl;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.EnableAspectJAutoProxy;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.junit.jupiter.SpringJUnitConfig;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import javax.sql.DataSource;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@SpringJUnitConfig(AdminCacheInvalidationServiceTest.Config.class)
@TestPropertySource(properties = {"redis.database=mall", "redis.key.admin=ums:admin",
        "redis.expire.common=86400", "redis.key.resourceList=ums:resourceList"})
class AdminCacheInvalidationServiceTest {
    @Configuration
    @EnableAspectJAutoProxy(proxyTargetClass = true)
    static class Config {
        @Bean DataSource dataSource() {
            return new DriverManagerDataSource("jdbc:h2:mem:cache_tasks;MODE=MySQL;DB_CLOSE_DELAY=-1", "sa", "");
        }
        @Bean JdbcTemplate jdbcTemplate(DataSource dataSource) { return new JdbcTemplate(dataSource); }
        @Bean PlatformTransactionManager transactionManager(DataSource dataSource) {
            return new DataSourceTransactionManager(dataSource);
        }
        @Bean RedisService redisService() { return mock(RedisService.class); }
        @Bean UmsAdminService adminService() { return mock(UmsAdminService.class); }
        @Bean com.macro.mall.mapper.UmsAdminRoleRelationMapper relationMapper() {
            return mock(com.macro.mall.mapper.UmsAdminRoleRelationMapper.class);
        }
        @Bean com.macro.mall.dao.UmsAdminRoleRelationDao relationDao() {
            return mock(com.macro.mall.dao.UmsAdminRoleRelationDao.class);
        }
        @Bean UmsAdminCacheService cacheService() { return new UmsAdminCacheServiceImpl(); }
        @Bean RedisCacheAspect cacheAspect() { return new RedisCacheAspect(); }
        @Bean AdminCacheInvalidationService invalidationService(JdbcTemplate jdbc,
                org.springframework.beans.factory.ObjectProvider<UmsAdminCacheService> cache,
                PlatformTransactionManager transactionManager) {
            return new AdminCacheInvalidationService(jdbc, cache, transactionManager, "mall", "ums:admin");
        }
    }

    @Autowired JdbcTemplate jdbc;
    @Autowired RedisService redis;
    @Autowired AdminCacheInvalidationService service;
    @Autowired PlatformTransactionManager transactionManager;
    private TransactionTemplate transaction;

    @BeforeEach
    void prepare() {
        reset(redis);
        transaction = new TransactionTemplate(transactionManager);
        jdbc.execute("DROP TABLE IF EXISTS ums_admin_cache_invalidation");
        jdbc.execute("CREATE TABLE ums_admin_cache_invalidation (id BIGINT AUTO_INCREMENT PRIMARY KEY, cache_key VARCHAR(255) NOT NULL, attempts INT DEFAULT 0 NOT NULL, next_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL, last_error VARCHAR(500))");
        jdbc.execute("CREATE TABLE IF NOT EXISTS sample_profile (id INT PRIMARY KEY, nickname VARCHAR(100))");
        jdbc.update("DELETE FROM sample_profile");
        jdbc.update("INSERT INTO sample_profile VALUES (1, 'old')");
    }

    @Test
    void rollbackKeepsProfileAndDoesNotInvalidateCache() {
        assertThrows(IllegalStateException.class, () -> transaction.executeWithoutResult(status -> {
            jdbc.update("UPDATE sample_profile SET nickname = 'new' WHERE id = 1");
            service.enqueue("testadmin");
            throw new IllegalStateException("rollback");
        }));
        assertEquals("old", jdbc.queryForObject("SELECT nickname FROM sample_profile WHERE id = 1", String.class));
        assertEquals(0, taskCount());
        verifyNoInteractions(redis);
    }

    @Test
    void failedDeleteSurvivesCommitAndIsRetriedAfterRecovery() {
        when(redis.del("mall:ums:admin:testadmin")).thenThrow(new IllegalStateException("Redis unavailable"));
        transaction.executeWithoutResult(status -> {
            jdbc.update("UPDATE sample_profile SET nickname = 'new' WHERE id = 1");
            service.enqueue("testadmin");
            verifyNoInteractions(redis); // 提交前不删除缓存
        });
        assertEquals("new", jdbc.queryForObject("SELECT nickname FROM sample_profile WHERE id = 1", String.class));
        assertEquals(1, taskCount());
        assertEquals(1, jdbc.queryForObject("SELECT attempts FROM ums_admin_cache_invalidation", Integer.class));
        reset(redis);
        when(redis.del("mall:ums:admin:testadmin")).thenReturn(true);
        jdbc.update("UPDATE ums_admin_cache_invalidation SET next_retry_at = '2000-01-01 00:00:00'");
        service.retryPending();
        assertEquals(0, taskCount());
        verify(redis).del("mall:ums:admin:testadmin");
    }

    @Test
    void missingCacheKeyAlsoCompletesTask() {
        when(redis.del("mall:ums:admin:testadmin")).thenReturn(false);
        transaction.executeWithoutResult(status -> service.enqueue("testadmin"));
        assertEquals(0, taskCount());
        verify(redis).del("mall:ums:admin:testadmin");
    }

    @Test
    void rejectsTaskCreationOutsideBusinessTransaction() {
        assertThrows(IllegalStateException.class, () -> service.enqueue("testadmin"));
        assertEquals(0, taskCount());
    }

    private int taskCount() {
        return jdbc.queryForObject("SELECT COUNT(*) FROM ums_admin_cache_invalidation", Integer.class);
    }
}
