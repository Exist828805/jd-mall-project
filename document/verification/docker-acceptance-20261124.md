# Docker 实跑验收：2026-11-24

## 环境与范围

VMware Linux 虚拟机，CentOS 7 / Linux 3.10、2 核 CPU、约 3.7 GiB 内存和 3.9 GiB 交换空间；Docker Engine 26.1.4、Compose v2.27.1。使用独立项目名 `jd-mall-acceptance` 与新建命名数据卷，原有 Redis、RabbitMQ 容器保持原进程运行。

业务源码来自发布基线 `0ed2fae5fef311806206714d3de63f9e29e77129`。两个 JAR 由 Dockerfile 的 Maven / Java 17 阶段生成，Maven 构建成功，耗时约 3 分 25 秒；随后完整 Compose 构建最终运行镜像并启动服务。运行镜像 Java 版本为 17.0.21.1。首次镜像下载遇到网络停滞，使用虚拟机已有加速源完成同一标签镜像拉取。

主要启动命令：

```bash
docker compose -p jd-mall-acceptance --profile apps up -d --build
docker compose -p jd-mall-acceptance --profile apps ps
```

## 运行与接口结果

| 服务 | 实跑结果 |
| --- | --- |
| MySQL 8.0.46 | healthy；空卷自动按顺序执行 01/02/03 SQL，77 张表，管理员/会员/收货地址初始化成功 |
| Redis 7.4.12 | healthy；`PING` 返回 `PONG`，应用认证/缓存访问正常 |
| RabbitMQ 3.13.7 | healthy；订单关单队列 1 个消费者；测试结束后取消、延迟、失败队列消息数均为 0 |
| MongoDB 7.0.48 | healthy；通过会员接口创建、读取、删除本轮浏览记录 |
| MinIO | 健康接口 HTTP 200；上传成功，返回的公开 URL 内容与上传内容相同，测试对象清理后返回 404 |
| mall-admin | 容器运行；Swagger HTTP 200，OpenAPI 160 个路径，演示登录与带 JWT 的商品列表通过 |
| mall-portal | 容器运行；Swagger HTTP 200，OpenAPI 65 个路径，演示会员登录与首页通过 |

容器重建后重新执行 [docker_smoke.py](docker_smoke.py)，10 项检查全部通过。结果：[接口冒烟 JSON](results/docker-smoke-20261124.json)。报告不保存密码或 JWT。

MinIO 测试文件通过存储管理凭据直接签名清理。初始化权限数据未授权应用 `/minio/delete`；本次没有放宽应用权限。

## 下单与自动关单

在隔离数据库中通过真实接口添加购物车、创建订单，临时把普通订单超时设为 1 分钟；下单后立即恢复原值 120 分钟。RabbitMQ 延迟消息触发消费，订单自动变为已关闭，库存对账如下：

| 阶段 | SKU | stock | lock_stock | 订单状态 |
| --- | --- | --- | --- | --- |
| 基线 | 99 | 100 | 0 | 未下单 |
| 下单成功 | 99 | 100 | 1 | 0：待付款 |
| 延迟消息自动关单 | 99 | 100 | 0 | 4：已关闭 |

本轮订单 ID 78，确认后通过接口软删除，保留数据库证据。结果：[订单对账 JSON](results/docker-order-20261124.json)。

该实验选择非负库存 SKU。上游历史样例中 SKU 98 的 `lock_stock=-24`，本次没有重写历史业务数据，也未对全部样例商品做库存对账。演示下单前需确认所选 SKU 的基线有效。

## 重建与数据保留

为 RabbitMQ 增加固定 `hostname: rabbitmq`，使容器重建后节点名和持久化目录保持一致。创建独立持久队列和一条 `delivery_mode=2` 的消息，然后执行：

```bash
docker compose -p jd-mall-acceptance --profile apps down
docker compose -p jd-mall-acceptance --profile apps up -d --no-build
```

未删除数据卷。重建后读取到相同消息内容，测试队列已清理；MySQL 仍有 77 张表，订单 78 保持关闭/软删除状态，SKU 99 的库存为 100 / 0，超时配置仍为 120 分钟，MySQL 未重复执行首次初始化脚本。

复查脚本：[docker_queue_persistence.py](docker_queue_persistence.py)。结果：[消息保留 JSON](results/docker-persistence-20261124.json)。随后再次运行接口冒烟，全部通过。

## 验证边界

Compose 验收范围为 5 个中间件和 2 个后端。前端沿用根 README 的 npm 启动方案及此前构建、浏览器联调记录。本轮没有验证真实资金支付、Elasticsearch、全部前端编辑操作、故障集群或生产性能；单笔订单实验不替代历史有限并发实验，也不代表所有库存场景均已覆盖。
