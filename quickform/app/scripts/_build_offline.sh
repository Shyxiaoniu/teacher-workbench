#!/bin/bash
# 一次性打包脚本：构建 runtime/（macOS Apple 芯片 + Windows 64 位），并预装好全部依赖。
# 只需在有网络的机器上运行一次；产物可随项目分发，之后完全离线可用。
# 用法：bash scripts/_build_offline.sh [--with-intel] [--with-wheels]
#   --with-intel   额外构建 Intel Mac 运行时（mac-x64）
#   --with-wheels  额外保留离线 wheel 包（wheels/，用于依赖损坏自愈，约 +170MB）
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TAG=20261026
VER=3.11.18
BASE="https://github.com/astral-sh/python-build-standalone/releases/download/$TAG"
SYS_PY=python3

WITH_INTEL=0
WITH_WHEELS=0
for a in "$@"; do
  case "$a" in
    --with-intel)  WITH_INTEL=1 ;;
    --with-wheels) WITH_WHEELS=1 ;;
  esac
done

mkdir -p runtime

fetch() { # url, dest
  echo "==> 下载 $(basename "$1")"
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
    curl -fL --retry 5 --retry-all-errors --max-time 900 "$1" -o "$2"
}

# ---------- 1. 可移植 Python 运行时 ----------
fetch "$BASE/cpython-$VER+$TAG-aarch64-apple-darwin-install_only_stripped.tar.gz" /tmp/_mac_arm64.tgz
fetch "$BASE/cpython-$VER+$TAG-x86_64-pc-windows-msvc-install_only_stripped.tar.gz" /tmp/_win_x64.tgz
[ "$WITH_INTEL" = 1 ] && fetch "$BASE/cpython-$VER+$TAG-x86_64-apple-darwin-install_only_stripped.tar.gz" /tmp/_mac_x64.tgz

for p in mac-arm64:/tmp/_mac_arm64.tgz win-x64:/tmp/_win_x64.tgz; do
  name="${p%%:*}"; tgz="${p##*:}"
  rm -rf "runtime/$name"; mkdir -p "runtime/$name"
  tar -xzf "$tgz" -C "runtime/$name"
done
if [ "$WITH_INTEL" = 1 ]; then
  rm -rf runtime/mac-x64; mkdir -p runtime/mac-x64
  tar -xzf /tmp/_mac_x64.tgz -C runtime/mac-x64
fi

# ---------- 2. 下载 wheel（安装用，装完即删） ----------
build_wheels() { # dir, platform-tag...
  d="$1"; shift
  rm -rf "$d"; mkdir -p "$d"
  args=()
  for t in "$@"; do args+=(--platform "$t"); done
  "$SYS_PY" -m pip download -r requirements.txt -d "$d" \
      --python-version 3.11 --implementation cp --only-binary=:all: \
      --no-cache-dir --retries 5 --timeout 60 "${args[@]}" || return 1
  # greenlet 是 SQLAlchemy 在 x86_64/Windows 上的可选解析依赖
  case "$d" in
    *win*|*x64*)
      "$SYS_PY" -m pip download greenlet -d "$d" --platform "${2:-win_amd64}" \
        --python-version 3.11 --implementation cp --only-binary=:all: --no-deps \
        --no-cache-dir || true ;;
  esac
}

echo "==> 准备依赖包"
build_wheels /tmp/_w_mac_arm macosx_11_0_arm64 macosx_10_9_universal2
build_wheels /tmp/_w_win     win_amd64

# ---------- 3. 预装依赖进运行时 ----------
echo "==> 安装 macOS 依赖"
./runtime/mac-arm64/python/bin/python3 -m pip install --no-index \
    --find-links /tmp/_w_mac_arm -r requirements.txt \
    --disable-pip-version-check --no-cache-dir --only-binary=:all: | tail -2

echo "==> 安装 Windows 依赖（跨平台 --target）"
"$SYS_PY" -m pip install --no-index --find-links /tmp/_w_win \
    --target "runtime/win-x64/python/Lib/site-packages" \
    --platform win_amd64 --python-version 3.11 --implementation cp \
    --only-binary=:all: --no-cache-dir --upgrade -r requirements.txt | tail -2

if [ "$WITH_INTEL" = 1 ]; then
  echo "==> 安装 Intel Mac 依赖"
  build_wheels /tmp/_w_mac_x64 macosx_10_9_x86_64 macosx_10_12_universal2 macosx_11_0_x86_64
  ./runtime/mac-x64/python/bin/python3 -m pip install --no-index \
      --find-links /tmp/_w_mac_x64 -r requirements.txt \
      --disable-pip-version-check --no-cache-dir --only-binary=:all: | tail -2
fi

# ---------- 4. 精简：删除运行期用不到的文件 ----------
echo "==> 精简运行时"
for r in runtime/mac-arm64 runtime/mac-x64; do
  [ -d "$r" ] || continue
  P="$r/python"
  rm -rf "$P/lib/python3.11/ensurepip" "$P/lib/python3.11/idlelib" \
         "$P/lib/python3.11/lib2to3" "$P/lib/python3.11/pydoc_data" \
         "$P/lib/python3.11/tkinter" "$P/include"
  rm -f "$P/lib/python3.11/lib-dynload/_tkinter"*.so
done
if [ -d runtime/win-x64 ]; then
  P=runtime/win-x64/python
  rm -rf "$P/include" "$P/libs" "$P/tcl" "$P/Lib/ensurepip" "$P/Lib/idlelib" \
         "$P/Lib/lib2to3" "$P/Lib/pydoc_data" "$P/Lib/tkinter"
  rm -f "$P/DLLs/_tkinter.pyd" "$P/DLLs/tcl86t.dll" "$P/DLLs/tk86t.dll"
fi
find runtime -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null
find runtime -maxdepth 12 -type d -path "*/site-packages/*" \
     \( -name tests -o -name test \) -prune -exec rm -rf {} + 2>/dev/null

# 注意：distutils 必须保留 —— setuptools 84 仍会 import 它，删掉会报 ModuleNotFoundError

# ---------- 5. 可选：保留 wheel 兜底 ----------
if [ "$WITH_WHEELS" = 1 ]; then
  mkdir -p wheels
  cp -R /tmp/_w_mac_arm wheels/macos-arm64
  cp -R /tmp/_w_win     wheels/win-x64
  [ "$WITH_INTEL" = 1 ] && cp -R /tmp/_w_mac_x64 wheels/macos-x64
else
  rm -rf wheels
fi

echo "==> 完成"
du -sh runtime/* wheels 2>/dev/null
du -sh .
