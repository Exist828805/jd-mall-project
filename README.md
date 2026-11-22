# mall-study｜电商后端学习与改进项目

基于 [macrozheng/mall](https://github.com/macrozheng/mall) 的电商系统学习项目，围绕商品、订单、库存、缓存与消息消费进行增量改进。保留上游源码和 [LICENSE](LICENSE)，具体贡献与验证范围见下文。

**仓库包含可编译的 Java 源码、数据库结构及示例数据、增量 SQL、测试代码与本地启动配置。当前交付为后端工程，通过 Swagger 操作接口；不包含独立的仿京东商城页面或后台前端源码。** 上游演示与前端地址见 [上游 README](README.upstream.md)。

## 功能与改进

基础业务沿用上游：管理员认证与权限、商品与品牌管理、会员认证、商品浏览、购物车、订单、支付接口及搜索模块。

本仓库的增量改进：

| 方向 | 实现 | 已验证范围 |
| --- | --- | --- |
| 库存锁定 | 用带可用库存条件的原子 UPDATE 锁定 SKU，检查影响行数 | 本地 10 请求并发实验，库存 4 件时 4 成功、6 库存不足，对账和关单恢复通过 |
| 支付与关单竞争 | 状态条件 UPDATE，失败方停止后续库存操作 | 支付先完成、关单先完成及受控交错场景 |
| 消费异常处理 | 关单消费总计最多尝试 3 次，耗尽后转存失败队列 | 故障回滚、失败留存、人工重新投递、重复消费 |
| 缓存删除补偿 | 业务与失效任务同事务保存；提交后删缓存，失败留存定时重试 | 自动化事务测试与真实 MySQL/Redis 故障恢复 |
| 业务校验 | 品牌参数、商品状态批量校验及订单管理接口调整 | 以仓库实现与实验记录为准 |

实现边界：这是学习项目的有限正确性验证，不是生产压测；不承诺所有路径强一致、消息零丢失或所有并发场景无超卖。缓存补偿覆盖管理员资料更新路径；失败消息恢复采用人工重新发送同一订单 ID；支付业务不代表已验收真实资金闭环。增量实现由 AI 协作编写、本人参与验证与学习。

## 目录与技术栈

```text
mall-common/       通用工具、公共配置及接口返回
mall-mbg/          数据库模型及 MyBatis Mapper
mall-security/     Spring Security、JWT 与缓存相关封装
mall-admin/        后台管理接口（默认 8080）
mall-portal/       买家商城接口（默认 8085）
mall-search/       Elasticsearch 商品搜索（默认 8081，扩展模块）
mall-demo/         上游框架示例，核心业务启动不需要
document/sql/     完整初始化数据、缓存补偿表、本地演示账号
document/verification/  自动化实验脚本及历史报告
compose.yaml       独立本地中间件与可选后端容器
Dockerfile         从源码构建后端镜像
```

技术栈以 `pom.xml` 为准：Java 17、Maven 3.9、Spring Boot 3.5.14、Spring Security、MyBatis、MySQL、Redis、RabbitMQ、MongoDB；对象存储使用 MinIO，搜索模块使用 Elasticsearch。运行核心后台和商城接口无需 Elasticsearch、Logstash 或 Kibana。

## 方式一：Docker 启动后端和依赖

需要 Git、Docker Engine/Docker Desktop 与支持 profiles 的 Docker Compose v2。首次构建需要联网下载镜像及 Maven 依赖，建议预留至少 6 GB 内存。

```bash
git clone https://github.com/Exist828805/mall-study.git
cd mall-study
docker compose --profile apps up -d --build
```

这条命令启动 MySQL、Redis、RabbitMQ、MongoDB、MinIO、后台管理与买家接口。MySQL **首次创建空数据卷**时按以下顺序初始化，无需手工执行 SQL：

1. `document/sql/mall.sql`：上游表结构及公开示例数据。
2. `document/sql/admin-cache-invalidation.sql`：本项目的缓存失效任务表。
3. `document/sql/local-demo.sql`：本地演示账号及演示收货地址。

启动后查看状态和日志：

```bash
docker compose --profile apps ps
docker compose logs --tail=100 admin portal
```

| 服务 | Docker 访问地址 | 默认账号 |
| --- | --- | --- |
| 后台接口文档 | http://localhost:18080/swagger-ui/index.html | `admin` / `admin123` |
| 商城接口文档 | http://localhost:18085/swagger-ui/index.html | `demo` / `demo123` |
| RabbitMQ 管理界面 | http://localhost:15673 | `mall` / `mall-rabbit-local-only` |
| MinIO 控制台 | http://localhost:19001 | `mall-local` / `mall-minio-local-only` |

以上均为仓库公开的本地演示凭据，只绑定本机端口，不用于公网部署。上传文件时自动创建 `mall` 存储桶；返回的图片地址使用浏览器可访问的 `http://localhost:19000`。

可复制 `.env.example` 为 `.env` 调整 Compose 密码与 JWT 密钥；`.env` 不提交到仓库。已有 MySQL/RabbitMQ 数据卷不会因为修改 `.env` 自动修改原账号密码，变更现有环境时应同步更新对应服务中的凭据。

停止服务并保留数据：

```bash
docker compose --profile apps down
```

普通重启不需要重新导入 SQL。`mall.sql` 含 DROP TABLE，只用于新的演示数据库，不要导入已有业务库。

## 方式二：IDEA / 本地 Java 启动

需要 **JDK 17** 和 Maven 3.9；运行 `java -version`、`mvn -version` 确认 Maven 也使用 JDK 17。

先启动依赖，不启动后端容器：

```bash
docker compose up -d
```

构建实际业务模块（Windows PowerShell 中保留参数引号）：

```bash
mvn "-Ddocker.skip=true" "-DskipTests" -pl mall-admin,mall-portal -am package
```

`docker.skip=true` 用于跳过上游绑定到 package 阶段的远程 Docker 构建，避免打包时连接原作者的 Docker 地址。

在两个终端分别运行：

```bash
java -jar mall-admin/target/mall-admin-1.0-SNAPSHOT.jar --spring.profiles.active=local
```

```bash
java -jar mall-portal/target/mall-portal-1.0-SNAPSHOT.jar --spring.profiles.active=local
```

此时文档地址为 http://localhost:8080/swagger-ui/index.html 和 http://localhost:8085/swagger-ui/index.html。在 IDEA 中导入根 `pom.xml`，启动 `MallAdminApplication`、`MallPortalApplication`，并把 Active profiles 设置为 `local`，效果相同。不要同时启动容器后端与 IDE 后端消费同一批测试订单消息。

`local` 配置连接下列 Compose 端口：

| 依赖 | 本机地址 | 配置入口 |
| --- | --- | --- |
| MySQL | localhost:13306 / mall | `MALL_DB_HOST`、`MALL_DB_PORT`、`MALL_DB_NAME`、`MALL_DB_USER`、`MALL_DB_PASSWORD` |
| Redis | localhost:16379 | `MALL_REDIS_HOST`、`MALL_REDIS_PORT`、`MALL_REDIS_PASSWORD` |
| RabbitMQ | localhost:5673，虚拟主机 `/mall` | `MALL_RABBIT_HOST`、`MALL_RABBIT_PORT`、`MALL_RABBIT_USER`、`MALL_RABBIT_PASSWORD` |
| MongoDB | localhost:27018 / mall-port | `MALL_MONGO_HOST`、`MALL_MONGO_PORT` |
| MinIO | localhost:19000 | `MALL_MINIO_ENDPOINT`、`MALL_MINIO_PUBLIC_ENDPOINT`、`MALL_MINIO_ACCESS_KEY`、`MALL_MINIO_SECRET_KEY` |

Java / IDEA 不会自动读取根目录 `.env`；修改凭据时需要把对应 `MALL_*` 变量也设置到启动 Java 的终端或 IDEA 运行配置。`local` 有完整演示默认值；上游 `dev` / `prod` 配置不属于本地快速启动方案。

如果已经有自己的中间件，可通过这些变量连接自己的**演示环境**，并按上面的顺序手动导入三个 SQL；数据库账户需有建表与业务读写权限。无需使用任何作者私有账号、密钥或虚拟机 IP。

## 第一次验证

1. 打开后台 Swagger，执行 `POST /admin/login`，JSON 请求体：
   ```json
   {"username":"admin","password":"admin123"}
   ```
2. 返回业务 `code=200` 后，在 Swagger 的 Authorize 中填写 `data.token`（HTTP Bearer 模式会自动添加前缀）。若通过其他 HTTP 客户端调用，Authorization 请求头使用 `data.tokenHead + data.token`，即完整的 `Bearer …`。再调用商品列表等受保护接口。
3. 在商城 Swagger 调用 `POST /sso/login`，表单参数 `username=demo`、`password=demo123`；调用首页与商品列表，确认数据库示例数据可读。认证方式同上。
4. 下单前确认该会员已有收货地址、商品 SKU 可用库存充足，并先添加购物车。演示账号初始化 SQL 已准备一条地址，具体 ID 通过接口查询，不要硬编码历史实验 ID。
5. 检查 HTTP 状态和返回体业务 `code`；HTTP 200 不代表业务一定成功。

商品图片沿用上游公开图片链接，需要外网可访问；也可通过 MinIO 上传自己的演示图片。

## 自动化验证与实验材料

缓存补偿事务测试不依赖外部 MySQL / Redis，使用 H2 与 mock：

```bash
mvn "-Ddocker.skip=true" "-DskipTests=false" "-Dtest=AdminCacheInvalidationServiceTest" "-Dsurefire.failIfNoSpecifiedTests=false" -pl mall-admin -am test
```

历史有限并发实验与报告见 [验证说明](document/verification/README.md)。报告中记录的会员、地址、订单、库存是当时的本地测试数据，不是新克隆数据库的初始状态；复现实验前按说明准备数据，不要直接使用历史 ID。

## 本次发布验证（2026-11-22）

- JDK 17 下，按上面的 Maven 命令从源码成功打包后台和商城核心模块。
- 4 项缓存失效补偿自动化测试通过。
- 三个 SQL 按顺序导入新建隔离数据库，生成 77 张表和演示账号；验证环境 MySQL 9.7，不修改原业务库。
- 启动打包后的两个服务，后台 / 商城 Swagger 页面和分组文档可用，分别包含 160 / 65 个接口；演示管理员登录、带 JWT 的商品查询、演示会员登录和首页数据读取通过。
- YAML 配置语法及文档本地链接检查通过。

当前机器未安装 Docker，因此 **Dockerfile / Compose 尚未在本机进行容器实跑验收**，MySQL 8.0 容器初始化也未单独实测。上述 Java 运行验证使用新建数据库及已有 Redis，并关闭验证进程的 RabbitMQ 消费；本次未重新执行下单、第三方支付、MinIO 上传或 Elasticsearch 搜索链路。运行环境与验证范围在这里明确列出，不将配置文件检查等同于容器运行通过。

## 扩展模块与功能边界

- `mall-search` 源码已包含，但默认 Compose 未启动 Elasticsearch；使用该模块需另行配置与当前 Spring Data Elasticsearch 兼容的服务及代码中用到的中文分词插件，再导入商品索引。它不是核心后台、首页与订单接口的启动前置条件。
- 支付宝沙箱、阿里云 OSS 需要自行申请凭据。核心浏览、登录、商品管理和订单接口不需要这些私有密钥；未配置时不能承诺第三方支付或 OSS 上传可用。
- 核心演示使用 MinIO，不需要阿里云账号。
- 上游 `document/docker/` 下的配置作为参考保留，当前快速启动统一使用根目录 `compose.yaml`。

## 常见问题

- **数据库连接失败**：先检查 MySQL healthy、13306 端口及 `local` profile；首次导入数据可能需要等待。
- **提示补偿任务表不存在**：确认初始化包含 `admin-cache-invalidation.sql`；旧数据卷不会自动补跑新 SQL。
- **登录失败**：`admin123` / `demo123` 来自第三个初始化脚本；只导入 `mall.sql` 不会得到这两个演示密码。
- **接口文档 401/404**：使用 `/swagger-ui/index.html`，不要依赖旧地址 `/swagger-ui.html`。
- **Redis/RabbitMQ 连到旧虚拟机**：检查是否仍使用 `dev`，或环境中残留了覆盖配置的 `SPRING_*` 变量。
- **Maven 打包连接 192.168.*:2375**：构建时加 `"-Ddocker.skip=true"`。
- **端口被占用**：停止重复实例或调整端口映射与对应连接参数，不要直接停止不属于本项目的服务。

## 来源与许可

原项目：[macrozheng/mall](https://github.com/macrozheng/mall)。上游文档见 [README.upstream.md](README.upstream.md)，许可见 [LICENSE](LICENSE)。基础商城架构与大部分业务代码来自上游，本仓库重点展示上表中的增量改进及可复现材料。
