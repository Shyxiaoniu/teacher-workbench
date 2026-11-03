#!/bin/sh
# ============================================================
# QuickForm 容器入口
# ------------------------------------------------------------
# 设计原则：官方源码保持只读、可整目录替换；所有运行期状态都落在数据卷。
#
#   $QF_SRC_DIR   官方源码（镜像内置或宿主目录挂载，只读）
#   $QF_RUN_DIR   启动时的运行副本（可写，指向数据卷的软链在这里建）
#   $QF_DATA_DIR  数据卷：db/（SQLite）uploads/（学生提交附件）assets/（任务附件）
#
# 为什么复制一份再跑：
#   ① 挂载目录可能是 :ro，不能在里面建 data/uploads 软链；
#   ② 复制只要 0.1 秒（约 1.5MB 源码），换来「宿主目录直接替换 = 升级」的简单模型。
# ============================================================
set -eu

SRC="${QF_SRC_DIR:-/opt/quickform/app}"
RUN="${QF_RUN_DIR:-/run/quickform}"
DATA="${QF_DATA_DIR:-/data}"

if [ ! -f "$SRC/app.py" ]; then
    echo "[quickform] ✗ 找不到 $SRC/app.py"
    echo "            请检查镜像内置源码，或 QF_SRC_DIR 挂载是否指向 QuickForm 官方源码目录"
    exit 1
fi

mkdir -p "$DATA/db" "$DATA/uploads" "$DATA/assets" "$DATA/logs"

if [ ! -w "$DATA" ]; then
    echo "[quickform] ✗ 数据目录不可写：$DATA"
    echo "            用 bind mount 时请先执行：sudo chown -R 10002:10002 <宿主机目录>"
    exit 1
fi

# 每次启动重建运行副本：让「替换源码 + 重启」立刻生效，不会残留旧文件
rm -rf "$RUN"
mkdir -p "$RUN"
cp -a "$SRC/." "$RUN/"

# 三处运行期目录 → 数据卷
rm -rf "$RUN/data" "$RUN/static/uploads" "$RUN/static/assets"
ln -sfn "$DATA/db" "$RUN/data"
ln -sfn "$DATA/uploads" "$RUN/static/uploads"
ln -sfn "$DATA/assets" "$RUN/static/assets"

# 「作品提交文件夹」这类配置放在数据卷，升级源码不会丢
export QUICKFORM_STORAGE_CONFIG="${QUICKFORM_STORAGE_CONFIG:-$DATA/storage.json}"
export QF_PREFIX="${QF_PREFIX:-/qf}"
export QUICKFORM_PORT="${QUICKFORM_PORT:-5055}"

cd "$RUN"

echo "[quickform] 源码   : $SRC"
echo "[quickform] 运行副本: $RUN"
echo "[quickform] 数据卷 : $DATA（db / uploads / assets）"
echo "[quickform] 对外路径: ${QF_PREFIX}/（由网关反代，容器内监听 ${QUICKFORM_PORT}）"

python /opt/quickform/init_admin.py

exec python /opt/quickform/serve.py
