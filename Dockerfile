# ============================================================
# 班级工作台 2.0 · 工作台镜像（教师端 + 学生端 + 同步服务，一个进程一个端口）
# ------------------------------------------------------------
# 只用 Python 标准库，没有 requirements.txt：不装第三方包，镜像小、构建快。
# 数据（SQLite / 上传文件 / 课件库 / 每日快照）全部落在 /app/data，用 volume 持久化。
#
# 正式部署请用编排（会同时起 gateway 与 quickform）：
#   docker compose up -d --build
#
# 只想单独跑工作台（不带 QuickForm）也可以：
#   docker build -t teacher-workbench .
#   docker run -d --name workbench -p 20020:20020 -v workbench-data:/app/data teacher-workbench
# ============================================================
FROM python:3.12-slim

# 时区：考勤 / 快照按「服务器本地日期」归档，容器默认 UTC 会在晚上 8 点后跨天，必须校正
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=20020 \
    BIND=0.0.0.0 \
    WB_DATA_DIR=/app/data \
    WB_SUBMISSION_DATA_DIR=/app/data/submissions

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 应用本体（静态页面 + 标准库服务 + 学生端 + 旧提交服务）
COPY app/ /app/
# 容器入口脚本：准备数据目录 → 执行 CMD
COPY docker/entrypoint.sh /app/docker-entrypoint.sh

# 非 root 运行；data 先建好并交给运行用户（命名卷首次挂载会继承这里的属主）
RUN useradd --create-home --uid 10001 workbench \
    && chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /app/data/files /app/data/backup \
              /app/data/submissions/enh /app/data/submissions/uploads \
    && chown -R workbench:workbench /app

USER workbench
VOLUME ["/app/data"]
EXPOSE 20020

# 健康检查打公开接口 /api/v2/ping（不需要登录）；连续失败 3 次标记 unhealthy
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,sys,urllib.request;r=urllib.request.urlopen('http://127.0.0.1:%s/api/v2/ping'%os.environ.get('PORT','20020'),timeout=3);sys.exit(0 if r.status==200 and b'workbench' in r.read() else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "server.py"]
