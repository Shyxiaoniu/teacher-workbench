#!/bin/sh
# ============================================================
# 部署后自检：确认容器真的起来了、三个入口都能访问
# ------------------------------------------------------------
#   ./docker/smoke-test.sh            # 默认端口 20020
#   ./docker/smoke-test.sh 18080      # 换了宿主机端口就带上
# ============================================================
set -eu

PORT="${1:-${WORKBENCH_PORT:-20020}}"
BASE="http://127.0.0.1:${PORT}"

# 有 curl 用 curl，没有就用 python（两者任一即可，不装新东西）
fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -s -m 5 "$1"
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import urllib.request,sys;sys.stdout.write(urllib.request.urlopen('$1',timeout=5).read().decode())"
    else
        python -c "import urllib.request,sys;sys.stdout.write(urllib.request.urlopen('$1',timeout=5).read().decode())"
    fi
}

echo "→ 等待服务就绪：${BASE}/api/v2/ping"
i=0
while [ "$i" -lt 30 ]; do
    if body=$(fetch "${BASE}/api/v2/ping" 2>/dev/null) && echo "$body" | grep -q '"workbench"'; then
        echo "  ✓ ping 正常：${body}"
        break
    fi
    i=$((i + 1))
    sleep 2
done
if [ "$i" -ge 30 ]; then
    echo "  ✗ 60 秒内没能连上，排查：docker compose ps / docker compose logs"
    exit 1
fi

code=$(fetch "${BASE}/s/" >/dev/null 2>&1 && echo 200 || echo fail)
[ "$code" = "200" ] && echo "  ✓ 学生端 /s/ 可访问" || { echo "  ✗ 学生端不可访问"; exit 1; }

# 未登录时教师端应 302 到登录页（能 302 就说明服务与路由都正常）
if command -v curl >/dev/null 2>&1; then
    loc=$(curl -s -o /dev/null -w '%{http_code}' "${BASE}/teacher/" || true)
    if [ "$loc" = "200" ] || [ "$loc" = "302" ]; then
        echo "  ✓ 教师端 /teacher/ 可访问（HTTP ${loc}）"
    else
        echo "  ✗ 教师端异常（HTTP ${loc}）"
        exit 1
    fi
fi

echo "→ 自检通过。教师端：${BASE}/teacher/  学生端：${BASE}/s/"
