#!/bin/bash
# 学生提交系统 · macOS 启动器
cd "$(dirname "$0")"
PYTHON=python3
if ! command -v python3 >/dev/null 2>&1; then PYTHON=python; fi
echo "启动学生提交系统（默认端口 5077）…"
"$PYTHON" server.py "$@"
