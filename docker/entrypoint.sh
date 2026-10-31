#!/bin/sh
# ============================================================
# 容器入口：准备数据目录 → 带参数启动服务
# ------------------------------------------------------------
# 只用 POSIX sh（镜像里没有 bash），出错就退出，不做静默降级。
# ============================================================
set -eu

DATA_DIR="${DATA_DIR:-/app/data}"
PORT="${PORT:-20020}"
BIND="${BIND:-0.0.0.0}"

mkdir -p "$DATA_DIR" "$DATA_DIR/files" "$DATA_DIR/backup" 2>/dev/null || true

if [ ! -w "$DATA_DIR" ]; then
    echo "[entrypoint] ✗ 数据目录不可写：$DATA_DIR"
    echo "             如果用 bind mount 挂了宿主机目录，请先执行："
    echo "             sudo chown -R 10001:10001 <宿主机目录>"
    exit 1
fi

echo "[entrypoint] 数据目录 : $DATA_DIR"
echo "[entrypoint] 监听地址 : ${BIND}:${PORT}"
echo "[entrypoint] 首次使用请打开 http://<服务器IP>:${PORT}/teacher/ 设置教师密码"
echo "[entrypoint] 学生端     http://<服务器IP>:${PORT}/s/"

# CMD 被替换成别的命令（比如 docker run -it image sh）时不要硬塞参数
case "${1:-}" in
    python|python3)
        exec "$@" --port "$PORT" --bind "$BIND" --no-dialog
        ;;
    *)
        exec "$@"
        ;;
esac
