#!/bin/sh
# ============================================================
# 容器入口：准备数据目录 → 带参数启动服务
# ------------------------------------------------------------
# 只用 POSIX sh（镜像里没有 bash），出错就退出，不做静默降级。
# 目录约定（与 docker-compose.yml 保持一致）：
#   $WB_DATA_DIR             主数据卷：SQLite / 配置 / 上传 / 每日快照
#   $WB_SUBMISSION_DATA_DIR  提交系统数据，默认落在主数据卷的 submissions/ 下
# 两者同在一个卷里，备份时只需拷一个目录。
# ============================================================
set -eu

DATA_DIR="${WB_DATA_DIR:-/app/data}"
SUB_DATA_DIR="${WB_SUBMISSION_DATA_DIR:-$DATA_DIR/submissions}"
PORT="${PORT:-20020}"
BIND="${BIND:-0.0.0.0}"

mkdir -p "$DATA_DIR" "$DATA_DIR/files" "$DATA_DIR/backup" \
         "$SUB_DATA_DIR" "$SUB_DATA_DIR/enh" "$SUB_DATA_DIR/uploads" 2>/dev/null || true

if [ ! -w "$DATA_DIR" ]; then
    echo "[entrypoint] ✗ 主数据目录不可写：$DATA_DIR"
    echo "             如果用 bind mount 挂了宿主机目录，请先执行："
    echo "             sudo chown -R 10001:10001 <宿主机目录>"
    echo "             或直接用 compose 启动：它会先跑 init 服务修属主"
    exit 1
fi

if [ ! -w "$SUB_DATA_DIR" ]; then
    echo "[entrypoint] ✗ 提交数据目录不可写：$SUB_DATA_DIR"
    echo "             它应位于主数据卷内（$DATA_DIR/submissions），请检查挂载与属主"
    exit 1
fi

echo "[entrypoint] 主数据目录 : $DATA_DIR"
echo "[entrypoint] 提交数据目录: $SUB_DATA_DIR"
echo "[entrypoint] 监听地址   : ${BIND}:${PORT}（容器内）"
# 宿主机端口由 compose 的 ports 映射决定，可以用 WORKBENCH_PORT 改；
# 这里只是为了把日志里的地址打印对，不影响服务本身。
HOST_PORT="${WB_HOST_PORT:-$PORT}"
if [ "$HOST_PORT" != "$PORT" ]; then
    echo "[entrypoint] 对外端口   : ${HOST_PORT}（宿主机映射，容器内仍是 ${PORT}）"
fi
echo "[entrypoint] 首次使用请打开 http://<服务器IP>:${HOST_PORT}/teacher/ 设置教师密码"
echo "[entrypoint] 学生端     http://<服务器IP>:${HOST_PORT}/s/"

# CMD 被替换成别的命令（比如 docker run -it image sh）时不要硬塞参数
case "${1:-}" in
    python|python3)
        exec "$@" --port "$PORT" --bind "$BIND" --no-dialog
        ;;
    *)
        exec "$@"
        ;;
esac
