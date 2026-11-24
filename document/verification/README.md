# 有限并发订单与库存对账

这是本地正确性实验，不是吞吐量或延迟基准测试。脚本和历史报告均包含在仓库中。

## 历史实验

`results/learn-concurrency-8c64e8802049.json` 为 2026-11-18 的一次真实本地实验：10 个线程，每请求购买 SKU245 一件，基线 stock=4 / lock_stock=0，实际 4 成功、6 库存不足；7 项下单对账通过，随后通过业务接口关单，库存恢复基线。

报告中的 learn_stock 会员、地址 8、订单 93～96 是当时环境的数据。仓库 SQL 沿用上游公开示例数据，不包含本人完整业务库快照，因此新克隆环境不能直接照搬历史 ID 与基线。

## 新环境准备

1. 按根 README 启动依赖、初始化数据库并启动商城接口；关闭调试断点，不要同时启动多个消费同一演示队列的实例。
2. 选择专用于实验的在售商品和 SKU，通过后台业务接口把可用库存准备为少于请求购买总量的正数。例如 10 请求各 1 件，对应可用库存 4 件。禁止对正式数据运行。
3. 默认会员为初始化脚本提供的 `demo`；默认自动查找其首个收货地址，可用 `--username`、`--address-id` 指定其他演示会员及其地址。
4. 实验期间不要对同一个 SKU 进行其他下单、支付或库存修改。

## 运行

在仓库根目录执行：

```bash
python -m pip install -r document/verification/requirements.txt
python document/verification/concurrent_orders.py --sku-id 245
```

默认只读预检，不创建购物车、不发送下单请求。数据库密码交互输入，不记录到报告；也可设置环境变量 `MALL_DB_PASSWORD`。默认数据库为本机 13306 / mall，用户 mall，匹配根目录 Compose。脚本不读取含私有凭据的配置文件，不会把 Spring 的 `${...}` 当成实际密码。

如果你启动的是容器内后端：

```bash
python document/verification/concurrent_orders.py --base-url http://localhost:18085 --sku-id 245
```

自备本机数据库可加 `--db-port 3306 --db-user root`。脚本只允许 localhost 的演示库 mall 和限定的本地接口地址。

确认预检基线后，在同一条命令后加 `--run` 执行；会员密码会再次隐藏输入。不要在这里复用私人密码。

## 对账与收尾

由于加购物车接口会合并相同 SKU，脚本用 SQL 创建本轮专属、独立的购物车记录；价格取实际 SKU 值。实际锁库存、创建订单与关闭订单均通过业务接口进行。

线程在屏障处集合后发起请求，不保证数据库同时执行。使用订单明细中的本轮唯一标记归属订单，核对成功响应、实际提交订单、SKU 数量、库存增量和库存边界。超时不等于事务失败，脚本不会自动重发；对账异常时保留现场并停止自动清理。

全部对账通过后，脚本调用业务关单接口关闭本轮订单，核对锁定库存恢复基线，再软删除本轮购物车。测试订单及原延迟消息保留，后续按订单状态幂等结束。

`results/` 每次保存一份 JSON 报告，包含基线、逐请求结果、对账及关单结果，不记录密码或 token。耗时数据仅用于该次排错，不代表生产性能。

## Docker 核心接口冒烟

按根 README 的 Docker 方案启动服务，等两个后端日志出现 `Started` 后，在同一台机器执行（Python 3，无第三方依赖）：

```bash
python3 document/verification/docker_smoke.py --run --output document/verification/results/docker-smoke-local.json
```

脚本使用本机 `18080` / `18085` / `19000` 端口和公开演示账号，检查两端 Swagger/OpenAPI、管理员和会员登录、受保护商品列表、首页、MongoDB 浏览记录写入读取、MinIO 上传及公开地址读取。它会清理本轮创建的浏览记录和文件，不创建订单、不修改库存；报告不保存密码或 JWT。文件清理使用本地 MinIO 管理凭据直接签名调用存储接口，默认值匹配 Compose；修改过凭据时需设置 `MALL_MINIO_ACCESS_KEY` / `MALL_MINIO_SECRET_KEY`。初始化权限数据没有授权应用的 `/minio/delete`，脚本不会为此修改应用权限。仅在独立演示环境运行，避免同时手工修改演示会员的浏览记录。

该脚本不代替基础服务验收：还应查看 `docker compose --profile apps ps` 的运行/健康状态、MySQL 三个初始化脚本的执行情况、Redis `PING` 和 RabbitMQ 队列消费者连接。脚本通过也不代表支付、搜索或全部业务通过。

### RabbitMQ 容器重建与消息保留

根 Compose 为 RabbitMQ 固定 `hostname: rabbitmq`，使其重建后继续使用同一个节点数据目录。在独立演示环境确认服务就绪后执行：

```bash
python3 document/verification/docker_queue_persistence.py prepare --state .docker-queue-state.json
docker compose --profile apps down
docker compose --profile apps up -d --no-build
# 等待 RabbitMQ healthy，再执行核对和清理
python3 document/verification/docker_queue_persistence.py verify --state .docker-queue-state.json
```

脚本通过本机 `15673` 的管理 API 创建独立持久队列和一条 `delivery_mode=2` 的测试消息，重建后比对消息内容，再删除测试队列。它不操作订单队列；只在 Compose 演示环境使用，不要添加 `down -v`。修改了 RabbitMQ 密码时设置 `MALL_RABBIT_PASSWORD`；Docker 在虚拟机里时，需要额外转发 `15673`。状态文件仅保存随机测试标记，核对成功后自动删除。
