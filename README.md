# 班级工作台 2.0

一套给班主任 / 任课老师用的**双端班级工作台**：教师端在一台电脑（或一台服务器）上跑，
同班学生用手机浏览器连同一个地址即可签到、看资料、交作业。全部数据留在自己机器上，
不依赖任何第三方云服务。

- **教师端** `/teacher/`：座位表、课堂签到 / 考勤、课表、作业与学习资料下发、成绩、德育、分组、进度、待办、工具
- **学生端** `/s/`：一键签到、查看学习资料、完成并提交作业（只读投影 + 白名单写入）
- **技术栈**：Python 3 标准库单进程 HTTP 服务 + SQLite（WAL）+ 原生 JS 单页应用，**零第三方依赖**
- **许可**：MIT License · Copyright (c) 2026 Shyxiaoniu · 详见[六、许可](#六许可)

**仓库**（GitHub 与 Gitee 双平台同步，内容一致，均为公开）：

- GitHub：<https://github.com/Shyxiaoniu/teacher-workbench>
- Gitee：<https://gitee.com/shyxiaoniu/teacher-workbench>

```bash
# 两个地址任选其一，代码与提交历史完全一致
git clone https://github.com/Shyxiaoniu/teacher-workbench.git
# 或（国内网络更快）
git clone https://gitee.com/shyxiaoniu/teacher-workbench.git
cd teacher-workbench
```

> 仓库只放源码与部署配置，**不含任何真实班级数据**。
> 运行期数据（`app/data/`：密码哈希、班级与学生信息、上传附件）与数据快照
> （`workbench.json`）均已被 `.gitignore` 排除，不会随代码上传；
> 克隆下来的是空工作台，首次访问 `/teacher/` 会让你设置自己的账号密码。

---

## 一、Docker Compose 部署（推荐）

前置：装好 Docker（Windows / macOS 用 Docker Desktop，Linux 用 docker engine + compose 插件）。

```bash
# 在项目根目录（有 docker-compose.yml 的那一层）执行
docker compose up -d --build
```

打开 `http://<本机IP>:20020/teacher/` → 首次进入会要求**设置教师密码**（密码只存在数据卷里）。

> 仓库里已带 `docker-compose.yml`，只是部署的话**不需要复制任何配置**，上面那一条命令就够了。
> 1.1 给出完整文件内容，供单独分发、粘贴到别的目录，或与仓库文件对照。

### 1.1 完整配置示例（可直接复制）

把下面整段保存为 `docker-compose.yml`，与 `Dockerfile`（以及 `app/` 目录）放在同一层，
然后 `docker compose up -d --build` 即可。

```yaml
# ============================================================
# 班级工作台 2.0 · Docker Compose 编排
# ------------------------------------------------------------
# 服务与启动顺序：
#   init ──(成功退出)──▶ workbench ──(healthy)──▶ 就绪可用
# ============================================================
name: teacher-workbench

services:
  # ---------------------------------------------------------
  # init：一次性数据卷初始化
  # 命名卷首次挂载会继承镜像里的属主（10001），一般不需要这一层；
  # 但当数据卷是旧卷、或改成了 bind mount（宿主机目录属主不是 10001）时，
  # 主服务会因写不了数据目录而直接退出。这一层先把属主修正，再放行主服务。
  # ---------------------------------------------------------
  init:
    build:
      context: .
      dockerfile: Dockerfile
    image: ${IMAGE_REPO:-teacher-workbench}:${IMAGE_TAG:-2.0.3}
    container_name: teacher-workbench-init
    # root 才有权限改卷属主；只做这一件事，跑完立刻退出
    user: "0:0"
    entrypoint: ["/bin/sh", "-c"]
    # 注意：Compose 会把命令里的 $XXX 当变量插值，脚本内的 shell 变量必须写成 $$XXX，
    # 这里干脆不用变量，避免踩坑。
    command:
      - |
        set -eu
        if [ ! -w /app/data ] || [ "$(stat -c %u /app/data 2>/dev/null || echo 0)" != "10001" ]; then
          echo "[init] 数据卷属主异常，正在修正 -> 10001:10001"
          chown -R 10001:10001 /app/data
        else
          echo "[init] 数据卷属主正常（10001），无需修改"
        fi
        mkdir -p /app/data/files /app/data/backup \
                 /app/data/submissions/enh /app/data/submissions/uploads
        chown -R 10001:10001 /app/data
        echo "[init] 数据卷就绪"
    volumes:
      - workbench-data:/app/data
    environment:
      TZ: ${TZ:-Asia/Shanghai}
    restart: "no"
    network_mode: none          # 不需要网络，只碰数据卷
    security_opt:
      - no-new-privileges:true

  # ---------------------------------------------------------
  # workbench：主服务（唯一对外端口）
  # ---------------------------------------------------------
  workbench:
    build:
      context: .
      dockerfile: Dockerfile
    image: ${IMAGE_REPO:-teacher-workbench}:${IMAGE_TAG:-2.0.3}
    container_name: teacher-workbench
    # 启动顺序：init 成功退出之后才启动本服务
    depends_on:
      init:
        condition: service_completed_successfully
    restart: unless-stopped
    # 宿主机 IP:宿主机端口 : 容器端口（容器内固定 20020，换端口不用重建镜像）
    ports:
      - "${WORKBENCH_HOST_IP:-0.0.0.0}:${WORKBENCH_PORT:-20020}:20020"
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      # 以下变量由 entrypoint.sh 与 server.py 读取，决定监听地址与数据落盘位置
      PORT: "20020"
      BIND: "0.0.0.0"
      WB_DATA_DIR: /app/data
      WB_SUBMISSION_DATA_DIR: /app/data/submissions
      PYTHONUNBUFFERED: "1"
      # 宿主机端口：只用于把启动日志里的访问地址打印对。
      # 二维码 / 学生端地址不依赖它——那些由浏览器实际访问的地址自动推导。
      WB_HOST_PORT: "${WORKBENCH_PORT:-20020}"
      # 对外访问地址（可选，一般留空）：
      #   留空 = 自动跟随浏览器访问的地址，改 WORKBENCH_PORT 后二维码自动跟着变；
      #   只有当你在前面挂了反向代理、或必须固定外部域名时才需要填。
      WB_PUBLIC_URL: "${WB_PUBLIC_URL:-}"
    volumes:
      # 单一数据卷：SQLite / 配置 / 上传 / 每日快照 / 提交系统数据全在里面
      - workbench-data:/app/data
      # 想直接在宿主机上看文件，把上一行换成（Linux 需先 chown -R 10001:10001 ./app/data）：
      # - ./app/data:/app/data
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import sys,urllib.request;r=urllib.request.urlopen('http://127.0.0.1:20020/api/v2/ping',timeout=3);sys.exit(0 if r.status==200 else 1)"
      interval: 30s
      timeout: 5s
      start_period: 20s
      retries: 3
    # 给 SQLite 留时间做 WAL 检查点，避免直接 kill 导致下次启动回放
    stop_grace_period: 15s
    security_opt:
      - no-new-privileges:true
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  workbench-data:
    # 固定卷名：换 compose 项目名、重装容器都不会丢数据
    name: ${WORKBENCH_VOLUME:-teacher-workbench-data}
```

> 上面这段与仓库根目录的 `docker-compose.yml` **完全一致**；若日后两者不一致，以仓库文件为准。
> 改端口/卷名请用下面的 `.env`，**不要直接改这个 YAML**，否则下次拉代码会冲突。

可选：同目录放一个 `.env` 覆盖默认值，不写就用默认值（**不要放任何密码**）：

```dotenv
WORKBENCH_PORT=20020
WORKBENCH_HOST_IP=0.0.0.0
TZ=Asia/Shanghai
WORKBENCH_VOLUME=teacher-workbench-data
# 本地构建留空；推拉 ACR 时填完整仓库地址
IMAGE_REPO=
IMAGE_TAG=2.0.3
```

完整带注释的版本见仓库根目录的 `.env.example`（复制为 `.env` 即可）。

**没有源码、只有镜像时**不必用这份 YAML 构建：把 `IMAGE_REPO` 指向已发布的 ACR 地址，
加 `--no-build` 直接跑（详见 1.9）：

```bash
IMAGE_REPO=crpi-2xt9f8f2tdrrpst5.cn-guangzhou.personal.cr.aliyuncs.com/niugebox/teacher-workbench \
IMAGE_TAG=2.0.3 docker compose up -d --no-build
```

### 1.2 主要配置项的含义与适用场景

| 配置项 | 含义 | 适用场景 / 何时需要动它 |
|---|---|---|
| `init` 服务 | 一次性容器：修正数据卷属主为 `10001`、补建 `files/ backup/ submissions/` 子目录 | 首次用命名卷、或改成 bind mount 后属主不对时；有它就不用再手动 `chown` |
| `depends_on` + `service_completed_successfully` | 主服务必须等 `init` **成功退出**才启动 | 避免"容器先起来、发现数据目录写不了再崩"。本项目没有需要并行启动的服务 |
| `restart: unless-stopped` | 崩溃、重启机器后自动拉起（手动 `stop` 的不拉） | 长期挂在办公室电脑或服务器上；调试想手动控制时改成 `"no"` |
| `ports` 三段式 | `宿主IP:宿主端口:容器端口` | 只给本机用 → 第一段填 `127.0.0.1`；端口冲突 → 改第二段的 `WORKBENCH_PORT` |
| `volumes: workbench-data:/app/data` | 命名卷装全部数据（库/附件/快照/提交） | 默认推荐；想在资源管理器里直接翻文件 → 换 bind mount `./app/data:/app/data` |
| `healthcheck` | 每 30s 打一次 `/api/v2/ping`，3 次失败标记 `unhealthy` | 配合 `up -d --wait` 写开机自启；给反向代理/监控做探活；不关心可整段删掉 |
| `stop_grace_period: 15s` | 停止前留 15 秒给 SQLite 做 WAL 检查点 | 经常重启或会直接断电的环境；不建议调小 |
| `security_opt: no-new-privileges` | 禁止容器内进程提权 | 所有场景都建议保留，几乎零成本 |
| `logging: json-file 10m×3` | 日志轮转，最多占 30MB | 长期运行必需，否则日志会无限增长占满磁盘 |
| `user: "0:0"`（仅 `init`） | 只有 root 能改卷属主 | 只在 `init` 里用；主服务必须保持非 root `10001` |
| `IMAGE_REPO` / `IMAGE_TAG` | 镜像仓库与标签 | 推/拉 ACR、固定版本、同机跑多版本时 |
| `WB_DATA_DIR` / `WB_SUBMISSION_DATA_DIR` | 主库与提交系统的落盘位置 | 只在改挂载点时同步改；两者必须在同一个数据卷内 |

**四种典型场景，分别要改什么：**

| 场景 | 建议配置 |
|---|---|
| 办公室电脑 / 教室一体机（老师自己用） | 什么都不用改，`docker compose up -d --build` 即可 |
| 学校服务器 / NAS（多人长期访问） | 保持 `restart: unless-stopped`；用 `up -d --wait` 挂开机自启；防火墙放行端口 |
| 只允许本机、前面挂 nginx / 反代 | `WORKBENCH_HOST_IP=127.0.0.1`，由反向代理对外 |
| 同机多实例（初一/初二各一套） | `WORKBENCH_PORT` 与 `WORKBENCH_VOLUME` 各给不同值，其余不用改 |

### 1.3 服务编排与启动顺序

本项目是**单进程**架构——教师端、学生端、增量同步、旧提交接口都在同一个 Python 进程里，
共用一个端口；不依赖外部数据库、缓存或消息队列。所以编排只需要两个服务：

| 服务 | 用途 | 镜像 | 对外端口 | 生命周期 |
|---|---|---|---|---|
| `init` | 一次性初始化：把数据卷属主修正为 `10001`、补建 `files/`、`backup/`、`submissions/` 子目录 | 与主服务同一镜像，以 root 运行 | 无（`network_mode: none`，只碰卷） | 跑完即退出（`restart: "no"`） |
| `workbench` | 主服务：教师端 `/teacher/`、学生端 `/s/`、同步接口 `/api/v2/*`、作业提交 `/api/submit` | `teacher-workbench:2.0.3` | `20020`（宿主机侧可改） | 常驻，`restart: unless-stopped` |

启动顺序由 `depends_on` 显式约束：

```
init ──service_completed_successfully──▶ workbench ──healthcheck /api/v2/ping──▶ 就绪
```

- `workbench` **必须等 `init` 正常退出**才启动，不会出现"服务先起来、发现数据目录写不了再崩"。
- `init` 只在属主不对时才 `chown -R`，第二次启动基本秒退，不拖慢开机。
- `workbench` 健康检查打公开接口 `/api/v2/ping`（不需要登录），连续 3 次失败标记 `unhealthy`；
  用 `docker compose up -d --wait` 会一直等到 `healthy` 才返回，适合写进开机自启脚本。

### 1.4 容器镜像与构建方式

- **基础镜像**：`python:3.12-slim` + `tzdata`（时区必需，否则晚上 8 点后考勤会跨天归档）。
- **构建上下文**：仓库根目录，只 `COPY app/ /app/`；`.dockerignore` 已排除 `.git`、`_analysis`、
  压缩包、`app/data`、`app/submission-system/data` —— 真实数据不会被烤进镜像。
- **零第三方依赖**：没有 `requirements.txt`，也不执行 `pip install`，构建通常在 10 秒级。
- **运行身份**：非 root，uid/gid 固定 `10001`；数据目录属主需与之匹配。

```bash
docker compose build                     # 只构建
docker compose build --no-cache          # 完全重建
docker build -t teacher-workbench:2.0.3 .   # 不用 compose 时
```

镜像名与标签已参数化，改 `IMAGE_REPO` / `IMAGE_TAG` 即可打成 ACR 地址（见 1.9）。

### 1.5 端口映射

```yaml
ports:
  - "${WORKBENCH_HOST_IP:-0.0.0.0}:${WORKBENCH_PORT:-20020}:20020"
```

| 段 | 含义 | 默认值 |
|---|---|---|
| 第 1 段 | 宿主机绑定地址 | `0.0.0.0`（局域网可访问）；填 `127.0.0.1` 则仅本机，适合前面再挂反向代理 |
| 第 2 段 | 宿主机端口 | `20020`，改它不需要重建镜像 |
| 第 3 段 | 容器内端口 | 固定 `20020`（由 `PORT` 决定），不建议改 |

```bash
WORKBENCH_PORT=18080 docker compose up -d       # 临时换端口
# 或 cp .env.example .env 后改 WORKBENCH_PORT=18080
```

**改端口后不用改任何别的地方。** 服务里所有要写进页面/二维码的绝对地址（投屏二维码、
学生端入口 `entry_url`、`/api/net` 返回的端口）都按「浏览器实际访问的地址」自动推导，
不再使用容器内网 IP 和容器内端口：

| 场景 | 推导结果 |
|---|---|
| 老师访问 `http://192.168.x.x:18080/teacher/` | 二维码内容 = `http://192.168.x.x:18080/s/` |
| 改了 `.env` 的 `WORKBENCH_PORT` 并重启容器 | 同上，自动跟随，无需额外配置 |
| 前面挂了 nginx / Caddy 且传了 `X-Forwarded-Host` | 用代理传来的域名与协议（`https://…`） |
| 用 `127.0.0.1` 打开但学生要走局域网 | 自动换成本机网卡 IP（本机部署时有效；Docker 里请配 `WB_PUBLIC_URL`） |
| 配了 `WB_PUBLIC_URL` | 一律以它为准，优先级最高 |

也就是说：**只要老师是用学生能访问到的那个地址打开教师端的，二维码就一定是通的**；
唯一需要手工指定的是「反向代理改写域名」和「Docker 里用 localhost 打开」这两种情况，
填一行 `WB_PUBLIC_URL` 即可。

> 端口仍然建议避开 NAS 已占用的号（80 / 443 / 5000 / 9000 等），否则 `up -d` 会报
> `port is already allocated`——这与项目更新无关，换一个空闲端口就好。

### 1.6 环境变量

| 变量 | 作用 | 默认 | 何时需要改 |
|---|---|---|---|
| `WORKBENCH_PORT` | 宿主机端口 | `20020` | 端口冲突时 |
| `WORKBENCH_HOST_IP` | 宿主机绑定地址 | `0.0.0.0` | 仅本机 / 挂反向代理时 |
| `WB_PUBLIC_URL` | 对外访问地址覆盖 | 空（自动推导） | 挂反向代理、或用 `localhost` 打开但学生要走局域网时 |
| `WB_HOST_PORT` | 仅用于把启动日志里的地址打印对 | 等于 `WORKBENCH_PORT` | 不用改，改端口时自动带入 |
| `TZ` | 时区 | `Asia/Shanghai` | 不要改 |
| `PORT` | 容器内监听端口 | `20020` | 不建议改 |
| `BIND` | 容器内绑定地址 | `0.0.0.0` | 不建议改 |
| `WB_DATA_DIR` | 主数据目录 | `/app/data` | 只在改挂载点时 |
| `WB_SUBMISSION_DATA_DIR` | 提交系统数据目录 | `/app/data/submissions` | 同上，必须位于同一数据卷内 |
| `WORKBENCH_VOLUME` | 数据卷名 | `teacher-workbench-data` | 同机跑多实例时 |
| `IMAGE_REPO` / `IMAGE_TAG` | 镜像仓库与标签 | 空 / `2.0.3` | 推拉 ACR 时 |

**没有任何密码、密钥或数据库连接串需要配置**：服务不连外部组件，教师密码在首次访问时
由网页设置，只保存在数据卷里。`.env` 只放端口、时区、卷名这类非敏感参数。

### 1.7 数据卷挂载

| 宿主机 | 容器内 | 模式 | 内容 |
|---|---|---|---|
| 命名卷 `teacher-workbench-data` | `/app/data` | 读写 | SQLite 库、上传附件、每日快照、提交系统数据（全部在这一个卷里） |

- **一个卷装全部数据**，备份只需拷一个目录；`docker compose down` 不删卷，`down -v` 才删。
- 换成 bind mount：把 compose 里的 `- workbench-data:/app/data` 改为 `- ./app/data:/app/data`；
  Linux 下先 `sudo chown -R 10001:10001 ./app/data`（`init` 服务也会兜底修一次属主）。
- Windows / macOS 走 Docker Desktop 虚拟盘映射，属主问题不明显，但仍推荐用命名卷。

| 卷内子路径 | 内容 | 说明 |
|---|---|---|
| `workbench.db` | 班级 / 学生 / 考勤 / 作业 / 成绩 / 配置 | SQLite（WAL 模式），`workbench.db-wal` 同步存在属正常 |
| `files/` | 作业附件、课件 | 学生上传的原始文件 |
| `backup/` | 每日快照 `workbench-YYYYMMDD.db` | 启动时自动 VACUUM 快照，保留 14 份 |
| `submissions/` | `roster.json`、`submissions.json`、`uploads/`、`enh/` | 旧提交系统的名册、提交记录与上传件 |

### 1.8 本地启动与停止

```bash
# 启动
docker compose up -d --build     # 首次：构建 + 启动
docker compose up -d --wait      # 等到 healthy 才返回（开机自启脚本里用这个）

# 停止 / 重启
docker compose stop              # 停容器，容器与数据都保留
docker compose start             # 再次启动
docker compose restart workbench # 只重启主服务（改 .env 后用 up -d 才会重新读变量）
docker compose down              # 停止并删除容器与网络，数据卷保留
docker compose down -v           # 连数据卷一起删（危险：清空所有班级数据）

# 观察与排查
docker compose ps                          # STATUS=healthy 即正常
docker compose logs -f workbench           # 跟踪日志
docker compose logs --tail=50 init         # 看初始化是否报错
docker compose exec workbench sh           # 进容器
```

部署后自检：

```bash
docker compose ps                                            # 两个服务：init=exited(0)，workbench=healthy
curl -s http://127.0.0.1:20020/api/v2/ping                   # {"ok": true, "service": "workbench", ...}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:20020/s/   # 200 = 学生端可访问
```

### 1.9 推送到阿里云 ACR / 从 ACR 部署

**已发布镜像**（阿里云 ACR 个人版，广州，`linux/amd64`）：

```
crpi-2xt9f8f2tdrrpst5.cn-guangzhou.personal.cr.aliyuncs.com/niugebox/teacher-workbench:2.0.3
crpi-2xt9f8f2tdrrpst5.cn-guangzhou.personal.cr.aliyuncs.com/niugebox/teacher-workbench:latest
```

直接拉取运行（不需要源码）：

```bash
docker login crpi-2xt9f8f2tdrrpst5.cn-guangzhou.personal.cr.aliyuncs.com   # 账号全名 + ACR「固定密码」

# 首次必须先修正命名卷属主：容器以 uid 10001 运行，新建的卷属主是 root，否则启动会报不可写
docker run --rm -v teacher-workbench-data:/app/data --user 0:0 \
  crpi-2xt9f8f2tdrrpst5.cn-guangzhou.personal.cr.aliyuncs.com/niugebox/teacher-workbench:2.0.3 \
  sh -c 'mkdir -p /app/data/files /app/data/backup /app/data/submissions/enh /app/data/submissions/uploads && chown -R 10001:10001 /app/data'

docker run -d --name teacher-workbench --restart unless-stopped \
  -p 20020:20020 -v teacher-workbench-data:/app/data \
  -e TZ=Asia/Shanghai \
  crpi-2xt9f8f2tdrrpst5.cn-guangzhou.personal.cr.aliyuncs.com/niugebox/teacher-workbench:2.0.3
```

用 `docker compose up -d --no-build` 则不需要手动修正属主，编排里的 `init` 服务会做这件事。

ARM 设备（如树莓派、部分极空间/绿联型号）需要自己 `docker buildx build --platform linux/arm64` 重新构建，
上面这份清单只有 `amd64`。

自己重新构建并推送，compose 里的镜像名已参数化，不用改文件：

```bash
# 1) 登录（用户名一般是阿里云账号全名，密码取 ACR 控制台的「固定密码」）
docker login registry.cn-hangzhou.aliyuncs.com
# 地域按自己的实例替换域名：cn-beijing / cn-shanghai / cn-guangzhou / cn-shenzhen …

# 2) 构建并推送
export IMAGE_REPO=registry.cn-hangzhou.aliyuncs.com/<命名空间>/teacher-workbench
export IMAGE_TAG=2.0.3
docker compose build
docker compose push
```

在目标机器上用（不需要源码，只要有 `docker-compose.yml` 与本项目的基础镜像可拉取）：

```bash
IMAGE_REPO=$IMAGE_REPO IMAGE_TAG=2.0.3 docker compose up -d --no-build

# 没有 compose 文件时用单条 docker run
docker run -d --name teacher-workbench --restart unless-stopped \
  -p 20020:20020 -v teacher-workbench-data:/app/data \
  -e TZ=Asia/Shanghai $IMAGE_REPO:$IMAGE_TAG
```

登录凭据由 Docker 保存在本机 `~/.docker/config.json`，**不要**写进 `docker-compose.yml` 或提交到仓库。

### 1.10 不用 compose 的单容器方式

```bash
docker build -t teacher-workbench:2.0.3 .
docker run -d --name workbench --restart unless-stopped \
  -p 20020:20020 -v teacher-workbench-data:/app/data teacher-workbench:2.0.3
```

这种方式**不会**自动跑 `init`，如果挂的是宿主机目录且属主不是 `10001`，
需要先 `sudo chown -R 10001:10001 <目录>`，否则容器启动时会明确报"数据目录不可写"并退出。


### 1.11 备份建议

浏览器侧的「导出备份」仍然可用：教师端右上角 ⚙ 系统设置 → 数据与备份，导出的
`.cwbackup` / `.json` 与容器卷是两条独立的备份路径，建议都留一份。卷内 `backup/`
还有每日自动快照（保留 14 份）。

整卷打包（拷走即备份，恢复时解开挂回去）：

```bash
docker run --rm -v teacher-workbench-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/workbench-volume-$(date +%F).tar.gz -C /data .
```

---

## 二、不用 Docker 的本机启动

需要 Python 3.9+（只用标准库，无需 pip install）：

```bash
cd app
python launch.py          # 自检 + 选端口 + 打开浏览器（推荐给老师）
# 或
python server.py --port 20020
```

Windows 双击 `app/启动教师端.bat`，macOS 双击 `app/启动教师端.command`。

---

## 三、目录结构

```
.
├── Dockerfile                 # 单镜像构建（python:3.12-slim + tzdata，非 root uid 10001）
├── docker-compose.yml         # 编排：init + workbench，端口/环境变量/数据卷/依赖顺序/健康检查
├── .env.example               # 可覆盖的端口、时区、卷名、镜像地址（无任何密钥）
├── docker/entrypoint.sh       # 容器入口：准备数据目录 → 带参数启动服务
├── .dockerignore / .gitignore
└── app/
    ├── server.py              # 主服务（教师端 + 学生端 + 同步 + SSE）
    ├── launch.py              # 本机一键启动器
    ├── index.html             # 教师端单页应用
    ├── netsync.js             # 增量同步（rev 游标）
    ├── backup.js              # 数据备份 / 恢复（唯一实现）
    ├── student/               # 学生端页面
    ├── core/                  # store / auth / projection / pages / ports
    ├── submission-system/     # 作业提交与课件服务（被主服务 import，不是独立进程）
    └── 使用说明.md            # 详细使用说明
```

---

## 四、默认值与约定

| 项 | 默认值 |
|---|---|
| 端口 | `20020`（避开 8080/3000/5000 等常用端口） |
| 绑定地址 | `0.0.0.0`（同一局域网的学生可访问） |
| 数据目录 | 本机 `app/data/`；容器 `/app/data`（命名卷 `teacher-workbench-data`） |
| 容器运行用户 | `10001:10001`（非 root） |
| 教师账号 | `teacher`，密码首次启动时自行设置 |
| 学生密码 | `123456`（教师端可重置） |
| 时区 | `Asia/Shanghai`（考勤按本地日期归档，容器内已校正） |

## 五、常见问题

- **打不开页面**：先 `docker compose ps` 看是否 `healthy`；再确认服务器防火墙放行映射端口
  （Linux `firewalld` / `ufw`，Windows 是「Windows Defender 防火墙 → 入站规则」）。
- **`workbench` 一直重启、日志说数据目录不可写**：看一眼 `docker compose logs init`。
  `init` 会在属主不对时执行 `chown -R 10001:10001`；若它本身失败（比如 bind mount 的目录在
  只读文件系统上），按提示在宿主机手动改属主。
- **端口被占用**：换宿主端口 `WORKBENCH_PORT=18080 docker compose up -d`；
  容器内端口固定 20020，不需要改。
- **改了端口，二维码扫出来的地址还是旧的 / 学生打不开**：2.0.1 起不用管——二维码和
  学生端入口按「老师当前访问的地址」自动生成，改 `WORKBENCH_PORT` 后重启容器即自动生效。
  若仍不对，多半是这两种情况：① 前面挂了反向代理，需要 `WB_PUBLIC_URL=https://你的域名`；
  ② 你是用 `http://127.0.0.1:端口` 打开的，学生手机访问不到这个地址，请改用
  `http://服务器局域网IP:端口/teacher/` 打开，或直接配 `WB_PUBLIC_URL`。
- **旧版本升级**：`docker compose pull && docker compose up -d`，端口、数据卷、环境变量
  全部沿用，不需要重新配置；数据在命名卷里，升级不会丢。

- **学生连不上**：手机与服务器要在同一局域网，用 `http://服务器IP:端口/s/`。
- **学生端提示「从外网访问需要班级码」**：2.0.3 起，从**公网 IP** 拉班级名单要带班级码
  （班级名单是真实姓名 + 学号，不能对全网开放）。校内网（同一个 Wi-Fi）不用填，直接选班级即可；
  需要外网访问时把教师端「班级码」告诉学生，填在登录页的「班级码」框里。
- **忘记教师密码**：容器内删掉配置里的密码字段风险较高，推荐用「忘记密码 → 密保」；
  实在不行停容器后删除数据卷里的 `config.json` 中 `teacher.password_hash`（会要求重新设置密码）。
- **时区不对**：确认 `TZ=Asia/Shanghai` 未被覆盖；镜像已装 `tzdata`。

---

## 六、许可

本项目以 **MIT 许可证** 发布，全文见仓库根目录 [`LICENSE`](LICENSE)。

| 项目 | 内容 |
|---|---|
| 许可证类型 | MIT License（OSI 认可的开源许可证） |
| 版权归属 | Copyright (c) 2026 **Shyxiaoniu** |
| 适用年份 | 2026 年 |
| SPDX 标识 | `MIT` |

### 授予的权利

在遵守下方唯一义务的前提下，任何人**免费**获得本软件及相关文档（"本软件"）的副本，可不受限制地处理本软件，包括但不限于以下权利：

- **使用** —— 在个人、教学、学校、商业等任何场景下运行本项目，无需付费、无需申请授权、无需告知作者
- **复制** —— 完整或部分复制源码
- **修改** —— 按需改造、增删功能、适配自己的班级与教学场景
- **合并与发布** —— 将本项目并入其他作品，或作为独立项目再发布
- **分发** —— 以源码或构建产物形式提供给他人，也可用于闭源产品
- **再许可 / 销售** —— 可将本项目并入采用其他许可证的作品，亦可销售副本

### 必须履行的义务

**保留版权与许可声明。** 上述版权声明与本许可声明（permission notice）必须包含在本软件的所有副本或实质性部分中。实际做法是：分发时不要删除仓库根目录的 `LICENSE` 文件，也不要删除源码中标注版权的注释行。

除此之外没有其他条件，无需公开你的修改，也无需以相同许可证发布衍生作品。

### 免责声明与责任限制

- 本软件按**「现状」**提供，**不附带任何明示或默示的担保**，包括但不限于对适销性、特定用途适用性及不侵权的担保。
- 作者或版权持有人**不对任何索赔、损害或其他责任承担责任**，无论是在合同诉讼、侵权行为还是其他情形下，由本软件、本软件的使用或其他交易引起或与之相关。
- 本项目是辅助教学的工具，**签到、考勤、成绩等记录不构成具有法律效力的证明**，重要数据请自行另行备份。

### 不在授权范围内的部分

- **商标权**：MIT 许可证不授予商标或名称使用权。不得以本项目或作者名义为衍生作品背书，也不得暗示存在官方关联或认可。
- **数据**：仓库中**不含任何真实班级、学生或成绩数据**（见文首数据边界说明）。你自行录入的数据归你所有，本许可证不构成对该等数据的任何授权或转让。公开部署他人数据前，请自行确认符合《个人信息保护法》等适用法规与学校规定。
- **第三方组件**：本项目仅使用 Python 3 标准库与原生浏览器 API，**未引入第三方运行时依赖**，故无需要另行声明的第三方许可证。Docker 基础镜像 `python:3.12-slim` 遵循其自身的许可条款。

### 如何标注

在自己的项目或文档中引用本项目时，建议使用：

```
班级工作台 2.0 — Copyright (c) 2026 Shyxiaoniu — MIT License
https://github.com/Shyxiaoniu/teacher-workbench
```
