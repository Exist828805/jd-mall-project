-- Public local demo credentials only. Import after mall.sql into a NEW demo database.
UPDATE ums_admin SET password='$2a$10$zD2Mfh4Z6UVQG1yxdKfGxOMDYQ.ThiewDb3lXL5iGEFXZRKmMG7xG', status=1 WHERE username='admin';
INSERT INTO ums_member (member_level_id, username, password, nickname, phone, status, create_time, integration, growth)
VALUES (4, 'demo', '$2a$10$bypvU/jkjyyYmcqzT8u3huRDeT7hfj/JD10V/BOJ.QC6XXzRs4ZAC', '本地演示会员', '19900000000', 1, NOW(), 0, 0)
ON DUPLICATE KEY UPDATE password=VALUES(password), status=1;
INSERT INTO ums_member_receive_address (member_id, name, phone_number, default_status, post_code, province, city, region, detail_address)
SELECT id, '演示用户', '19900000000', 1, '100000', '北京市', '北京市', '海淀区', '仅用于本地演示的测试地址'
FROM ums_member WHERE username='demo' AND NOT EXISTS (
SELECT 1 FROM ums_member_receive_address WHERE member_id=ums_member.id);
