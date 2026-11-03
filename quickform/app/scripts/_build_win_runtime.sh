#!/bin/bash
# 只构建 Windows 64 位可移植运行时（runtime/win-x64），并把 QuickForm 依赖预装进去。
# 与 _build_offline.sh 用的是同一个 Python 构建（astral-sh/python-build-standalone，3.11.18），
# 保证与 macOS 版行为一致：解压即用、不装 Python、不联网、不用 pip。
#
# 用法：bash scripts/_build_win_runtime.sh [--with-wheels]
#   --with-wheels  额外保留 wheels/win-x64（离线依赖包，用于机房现场修依赖，约 +80MB）
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG=20261026
VER=3.11.18
BASE="https://github.com/astral-sh/python-build-standalone/releases/download/$TAG"
PKG="cpython-$VER+$TAG-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
SYS_PY="${SYS_PY:-python3}"
WITH_WHEELS=0
[ "${1:-}" = "--with-wheels" ] && WITH_WHEELS=1

# 沙箱/代理环境里 curl 走系统代理常常 502，这里统一绕开
CURL="env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY curl -fL --retry 5 --retry-all-errors"
PIP_MIRROR="${PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"

echo "==> 1/5 下载 Windows 可移植 Python $VER"
mkdir -p runtime
if [ ! -f /tmp/_win_x64.tgz ]; then
  $CURL --max-time 1800 "$BASE/$PKG" -o /tmp/_win_x64.tgz || { echo "下载失败"; exit 1; }
fi
echo "    $(du -h /tmp/_win_x64.tgz | cut -f1)"

echo "==> 2/5 解压到 runtime/win-x64"
rm -rf runtime/win-x64
mkdir -p runtime/win-x64
tar -xzf /tmp/_win_x64.tgz -C runtime/win-x64
ls runtime/win-x64/python/python.exe >/dev/null || { echo "解压后找不到 python.exe"; exit 1; }

echo "==> 3/5 下载 Windows 依赖 wheel（win_amd64 / cp311）"
rm -rf /tmp/_w_win; mkdir -p /tmp/_w_win
"$SYS_PY" -m pip download -r requirements.txt -d /tmp/_w_win \
    --python-version 3.11 --implementation cp --only-binary=:all: \
    --no-cache-dir --retries 5 --timeout 60 -i "$PIP_MIRROR" \
    --platform win_amd64 || exit 1
# greenlet：SQLAlchemy 在某些平台上的可选依赖，缺了会在建表时报错
"$SYS_PY" -m pip download greenlet -d /tmp/_w_win --platform win_amd64 \
    --python-version 3.11 --implementation cp --only-binary=:all: --no-deps \
    --no-cache-dir -i "$PIP_MIRROR" || true
echo "    $(ls /tmp/_w_win | wc -l | tr -d ' ') 个 wheel，$(du -sh /tmp/_w_win | cut -f1)"

echo "==> 4/5 把依赖装进运行时（跨平台 --target）"
"$SYS_PY" -m pip install --no-index --find-links /tmp/_w_win \
    --target "runtime/win-x64/python/Lib/site-packages" \
    --platform win_amd64 --python-version 3.11 --implementation cp \
    --only-binary=:all: --no-cache-dir --upgrade -r requirements.txt | tail -3

echo "==> 5/6 补 C++ 运行库（msvcp140 等）"
# numpy/pandas/matplotlib 的 C++ 扩展依赖 msvcp140.dll，而 Windows 10/11 默认不带它。
# 放在 python.exe 同目录即可被优先加载 → 机房机器不用装 VC++ 运行库、也不用管理员权限。
# 文件取自微软官方 REDIST 清单（PyPI 包 msvc_runtime 内为原始未修改文件）。
VC_DIR="$PWD/python/_vctmp"
if [ ! -f "$P/python/msvcp140.dll" ]; then
  rm -rf /tmp/_msvc "$VC_DIR"; mkdir -p /tmp/_msvc
  "$SYS_PY" -m pip download msvc-runtime --platform win_amd64 --python-version 3.11 \
      --only-binary=:all: --no-deps -d /tmp/_msvc -i "$PIP_MIRROR" >/dev/null 2>&1 || true
  WH=$(ls /tmp/_msvc/*.whl 2>/dev/null | head -1)
  if [ -n "$WH" ]; then
    "$SYS_PY" - "$WH" "$P/python" <<'PYEOF'
import sys, zipfile, os, shutil
whl, dst = sys.argv[1], sys.argv[2]
need = ['msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','msvcp140_atomic_wait.dll',
        'msvcp140_codecvt_ids.dll','concrt140.dll']
z = zipfile.ZipFile(whl)
pre = [n for n in z.namelist() if n.endswith('/msvcp140.dll')][0].rsplit('/',1)[0] + '/'
for n in need:
    if pre + n in z.namelist():
        with z.open(pre + n) as f, open(os.path.join(dst, n), 'wb') as o:
            shutil.copyfileobj(f, o)
        print('    放入', n)
PYEOF
  else
    echo "    ! 没取到 msvc-runtime（离线构建可忽略；目标机若缺 VC++ 运行库会提示找不到 msvcp140.dll）"
  fi
else
  echo "    已存在，跳过"
fi

echo "==> 6/6 精简运行时"
P=runtime/win-x64/python
rm -rf "$P/include" "$P/libs" "$P/tcl" "$P/Lib/ensurepip" "$P/Lib/idlelib" \
       "$P/Lib/lib2to3" "$P/Lib/pydoc_data" "$P/Lib/tkinter"
rm -f "$P/DLLs/_tkinter.pyd" "$P/DLLs/tcl86t.dll" "$P/DLLs/tk86t.dll"
find runtime/win-x64 -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
# 注意：distutils 必须保留（setuptools 仍会 import 它）
find runtime/win-x64 -type d -path "*/site-packages/*" \
     \( -name tests -o -name test \) -prune -exec rm -rf {} + 2>/dev/null

if [ "$WITH_WHEELS" = 1 ]; then
  mkdir -p wheels
  rm -rf wheels/win-x64
  cp -R /tmp/_w_win wheels/win-x64
  echo "    离线 wheel 已保留：wheels/win-x64（$(du -sh wheels/win-x64 | cut -f1)）"
else
  rm -rf wheels/win-x64
fi

echo "==> 关键文件核对"
for f in python/python.exe python/python311.dll python/Lib/site-packages/flask \
         python/Lib/site-packages/pandas python/Lib/site-packages/matplotlib \
         python/Lib/site-packages/bcrypt python/Lib/site-packages/PIL \
         python/Lib/site-packages/sqlalchemy python/Lib/site-packages/qrcode; do
  [ -e "runtime/win-x64/$f" ] && echo "    ✓ $f" || echo "    ✗ 缺少 $f"
done
echo "==> 完成：$(du -sh runtime/win-x64 | cut -f1)"
