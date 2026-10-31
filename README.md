# 班级工作台 2.0

一套给班主任 / 任课老师用的**双端班级工作台**：教师端在一台电脑（或一台服务器）上跑，
同班学生用手机浏览器连同一个地址即可签到、看资料、交作业。全部数据留在自己机器上，
不依赖任何第三方云服务。

- **教师端** `/teacher/`：座位表、课堂签到 / 考勤、课表、作业与学习资料下发、成绩、德育、分组、进度、待办、工具
- **学生端** `/s/`：一键签到、查看学习资料、完成并提交作业（只读投影 + 白名单写入）
- **技术栈**：Python 3 标准库单进程 HTTP 服务 + SQLite（WAL）+ 原生 JS 单页应用，**零第三方依赖**

---

## 一、Docker 一键部署（推荐）

前置：装好 Docker（Windows / macOS 用 Docker Desktop，Linux 用 docker engine + compose 插件）。

```bash
# 在项目根目录（有 docker-compose.yml 的那一层）执行
docker compose up -d --build
```

打开 `http://<本机IP>:20020/teacher/` → 首次进入会要求**设置教师密码**（密码只存在数据卷里）。

常用命令：

```bash
docker compose logs -f          # 看日志
docker compose ps               # 看状态（healthy 即正常）
docker compose restart          # 重启
docker compose down             # 停止并删除容器（数据卷保留）
docker compose down -v          # 连数据一起删（危险：清空所有班级数据）
```

改端口（不动代码、不重建镜像）：

```bash
WORKBENCH_PORT=18080 docker compose up -d
# 或复制 .env.example 为 .env，改 WORKBENCH_PORT=18080
```

不用 compose、只要单条 `docker run` 也可以：

```bash
docker build -t teacher-workbench .
docker run -d --name workbench --restart unless-stopped \
  -p 20020:20020 -v teacher-workbench-data:/app/data teacher-workbench
```

### 数据放在哪

| 内容 | 容器内路径 | 说明 |
|---|---|---|
| SQLite 库 | `/app/data/workbench.db` | 班级 / 学生 / 考勤 / 作业 / 成绩 / 配置 |
| 上传文件 | `/app/data/files/` | 作业附件、课件 |
| 每日快照 | `/app/data/backup/` | 启动时自动 VACUUM 快照，保留 14 份 |

默认用命名卷 `teacher-workbench-data`；想直接改文件就把 compose 里的
`- workbench-data:/app/data` 换成 `- ./app/data:/app/data`（记得让容器用户 uid 10001 可写）。

浏览器侧的「导出备份」仍然可用：教师端右上角 ⚙ 系统设置 → 数据与备份，导出的
`.cwbackup` / `.json` 与容器卷是两条独立的备份路径，建议都留一份。

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
├── Dockerfile                 # 单镜像构建（python:3.12-slim + tzdata）
├── docker-compose.yml         # 一键编排：端口映射、数据卷、健康检查
├── docker/entrypoint.sh       # 容器入口：准备数据目录 → 启动服务
├── .dockerignore / .gitignore
└── app/
    ├── server.py              # 主服务（教师端 + 学生端 + 同步 + SSE）
    ├── launch.py              # 本机一键启动器
    ├── index.html             # 教师端单页应用
    ├── netsync.js             # 增量同步（rev 游标）
    ├── backup.js              # 数据备份 / 恢复（唯一实现）
    ├── student/               # 学生端页面
    ├── core/                  # store / auth / projection / pages / ports
    ├── submission-system/     # 作业提交与课件服务（旧版复用）
    ├── tools/seed_demo.py     # 演示数据生成（可选）
    └── 使用说明.md            # 详细使用说明
```

---

## 四、默认值与约定

| 项 | 默认值 |
|---|---|
| 端口 | `20020`（避开 8080/3000/5000 等常用端口） |
| 绑定地址 | `0.0.0.0`（同一局域网的学生可访问） |
| 教师账号 | `teacher`，密码首次启动时自行设置 |
| 学生密码 | `123456`（教师端可重置） |
| 时区 | `Asia/Shanghai`（考勤按本地日期归档，容器内已校正） |

## 五、常见问题

- **打不开页面**：先 `docker compose ps` 看是否 `healthy`；再确认服务器防火墙放行映射端口
  （Linux `firewalld` / `ufw`，Windows 是「Windows Defender 防火墙 → 入站规则」）。
- **端口被占用**：换宿主端口 `WORKBENCH_PORT=18080 docker compose up -d`；
  容器内端口固定 20020，不需要改。
- **学生连不上**：手机与服务器要在同一局域网，用 `http://服务器IP:端口/s/`。
- **忘记教师密码**：容器内删掉配置里的密码字段风险较高，推荐用「忘记密码 → 密保」；
  实在不行停容器后删除数据卷里的 `config.json` 中 `teacher.password_hash`（会要求重新设置密码）。
- **时区不对**：确认 `TZ=Asia/Shanghai` 未被覆盖；镜像已装 `tzdata`。
