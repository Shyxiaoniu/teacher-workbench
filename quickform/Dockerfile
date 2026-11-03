# ============================================================
# QuickForm 教师版 2.5 · 独立服务镜像
# ------------------------------------------------------------
# 为什么单独一个镜像（而不是塞进工作台镜像里）：
#   · QuickForm 是独立开源项目（gitee.com/wstlab/quickform），版本节奏和工作台不同；
#   · 它依赖 Flask / pandas / matplotlib 等一大堆包，和工作台的「纯标准库」完全两类；
#   · 单独成镜像后，升级只重建/替换这一个容器，工作台不受任何影响。
#
# 源码位置：quickform/app/（官方源码，可整目录替换）
# 构建：docker compose build quickform
# ============================================================
FROM python:3.11-slim

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    QF_SRC_DIR=/opt/quickform/app \
    QF_RUN_DIR=/run/quickform \
    QF_DATA_DIR=/data \
    QF_PREFIX=/qf \
    QUICKFORM_PORT=5055

# tzdata：报告时间要对；fontconfig + 思源黑体：matplotlib 出图时中文不是方块；
# libgomp1：pandas / numpy 的 OpenBLAS 运行时依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tzdata fontconfig fonts-noto-cjk libgomp1 \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/quickform

# 依赖单独一层：只改业务源码时不会重装依赖
COPY quickform/app/requirements.txt /tmp/qf-requirements.txt
RUN pip install --no-cache-dir -r /tmp/qf-requirements.txt

# 官方源码 + 适配层（serve.py / init_admin.py / entrypoint）
COPY quickform/app/ /opt/quickform/app/
COPY quickform/serve.py quickform/init_admin.py /opt/quickform/
COPY quickform/docker-entrypoint.sh /opt/quickform/docker-entrypoint.sh

RUN useradd --create-home --uid 10002 quickform \
    && chmod +x /opt/quickform/docker-entrypoint.sh \
    && mkdir -p /data/db /data/uploads /data/assets /data/logs /run/quickform \
    && chown -R quickform:quickform /data /run /opt/quickform

USER quickform
VOLUME ["/data"]
EXPOSE 5055

# /login 不需要登录态，返回 200 即为健康（<500 都算活着，避免首次建库慢被误杀）
HEALTHCHECK --interval=30s --timeout=6s --start-period=40s --retries=3 \
    CMD python -c "import sys,urllib.request;r=urllib.request.urlopen('http://127.0.0.1:5055/login',timeout=4);sys.exit(0 if r.status<500 else 1)"

ENTRYPOINT ["/opt/quickform/docker-entrypoint.sh"]
