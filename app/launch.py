#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 一键启动器（仅标准库）

双击 启动教师端.bat / .command 就是跑这个文件。它做四件事：
  1. 自检：Python 版本、文件完整、data 目录可写、数据库可打开；
  2. 端口：默认 20020（不常用，避开 8080/3000/5000 这些常被占的号）。
     被占用时先判断“是不是自己上次没关”（是就复用），否则**弹窗**让老师
     当场改端口并记住，改完接着启动——不再出现“端口被占用”直接退出；
  3. 起服务：python server.py --port <port>（单进程，教师端 = 服务端）；
  4. 打印横幅并打开浏览器到 /teacher/。

设计原则沿用原版：**绝不静默降级**。任何一步失败都打印原因和处置建议。
"""
import os
import sys
import json
import time
import socket
import argparse
import platform
import subprocess
import webbrowser
import urllib.request

# 输出重定向到管道/文件时（如计划任务、日志采集），Windows 默认按 GBK 编码，
# 打印 ✓/▲ 等符号会直接 UnicodeEncodeError 崩掉。统一强制 UTF-8 + 容错替换。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
# 项目根：随包运行时（runtime/）挂在这一层，不是 app/ 里面。
PROJ = os.path.dirname(ROOT)
# 与 server.py 保持同一套数据目录规则：容器/自定义部署时用 WB_DATA_DIR。
# 以前启动器写死 ROOT/data，于是 `WB_DATA_DIR=/data python launch.py --reset-password`
# 清的是另一份 config（用户以为密码清掉了，实际没有）。
DATA = os.environ.get("WB_DATA_DIR") or os.path.join(ROOT, "data")
CONFIG = os.path.join(DATA, "config.json")
sys.path.insert(0, os.path.join(ROOT, "core"))
import ports as PORTX                                    # noqa: E402

DEFAULT_PORT = PORTX.DEFAULT_PORT


def log(msg=""):
    print(msg, flush=True)


def hr(ch="="):
    log(ch * 62)


def die(msg, hint=""):
    log("")
    log("  ✗ " + msg)
    if hint:
        for line in hint.strip().splitlines():
            log("    " + line)
    log("")
    log("  按回车键退出…")
    try:
        if sys.stdin and sys.stdin.isatty():
            input()
    except Exception:
        pass
    sys.exit(1)


def port_free(port, host="127.0.0.1"):
    return PORTX.is_free(port, host)


def ping(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/v2/ping" % port, timeout=1.2) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def lan_ips():
    ips = []
    for probe in ("223.5.5.5", "8.8.8.8"):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
                u.settimeout(0.3)
                u.connect((probe, 80))
                ip = u.getsockname()[0]
                if ip and not ip.startswith("127.") and ip not in ips:
                    ips.append(ip)
        except Exception:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass

    def rank(x):
        if x.startswith("192.168.") or x.startswith("10."):
            return 0
        return 1 if x.startswith("172.") else 2
    ips.sort(key=lambda x: (rank(x), x))
    return ips


def read_cfg():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _needs_port_migration(cfg):
    """老安装里写死的是旧默认端口 8080，改成一个不常用的号。

    只迁移一次（写 port_migrated 标记）：老师后来自己挑的端口不再动。
    """
    if cfg.get("port_migrated"):
        return False
    try:
        return int(cfg.get("port") or 0) in (0, 8080)
    except Exception:
        return False


def check_env():
    if sys.version_info < (3, 7):
        die("Python 版本太低：%s（需要 3.7+）" % platform.python_version(),
            "Windows：https://www.python.org/downloads/ 安装时勾选 Add to PATH\n"
            "macOS：系统自带，或 brew install python3")
    need = ["server.py", "index.html", "student/index.html", "core/store.py",
            "submission-system/server.py"]
    missing = [p for p in need if not os.path.isfile(os.path.join(ROOT, p))]
    if missing:
        die("部署包不完整，缺少：" + "、".join(missing),
            "请把整个文件夹一起拷贝，不要只拷单个文件。")
    try:
        os.makedirs(DATA, exist_ok=True)
        probe = os.path.join(DATA, ".write_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as e:
        die("数据目录不可写：%s" % e, "把整个文件夹放到「文档 / 桌面 / D 盘」再试。")


def admin_hint():
    """默认管理员账号/密码只在启动终端出现（登录页已不再展示）。

    首次启动（还没有 accounts.json）才给出明文密码；之后库里只剩哈希，
    只能提示账号名与重置办法。
    """
    user = (os.environ.get("WB_ADMIN_USERNAME") or "").strip() or "teacher"
    pwd = os.environ.get("WB_ADMIN_PASSWORD") or "t12345678"
    path = os.path.join(DATA, "accounts.json")
    if not os.path.isfile(path):
        return "  管理员    %s ／ %s（首次启动自动创建，登录后请改掉）" % (user, pwd)
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
        u = str((d.get("admin") or {}).get("username") or "") or user
    except Exception:
        u = user
    return "  管理员    %s（密码已设置；忘记可用 launch.py --reset-password）" % u


def banner(port, cfg):
    ips = lan_ips()
    ip = ips[0] if ips else "127.0.0.1"
    code = cfg.get("class_code") or "----"
    hr()
    log("  班级工作台 2.0 · 服务已启动")
    log("  数据目录  %s" % DATA)
    hr()
    log("")
    log("  教师端    http://127.0.0.1:%d/teacher/" % port)
    log("  学生端    http://%s:%d/s/  （局域网）" % (ip, port))
    log("  学生端控制台  http://127.0.0.1:%d/teacher/console" % port)
    log("")
    log("  班级码  %s" % code)
    log(admin_hint())
    if len(ips) > 1:
        log("  其它网段  " + "  ".join("http://%s:%d/s/" % (x, port) for x in ips[1:3]))
    log("")
    log("  学生：连同一个 Wi-Fi → 浏览器打开上面的地址 → 选班级学号输密码")
    log("")
    log("  提示：正式部署请用 docker compose 启动，师生从统一入口（默认 :8080）访问。")
    log("")
    hr("-")
    log("  服务运行中，关闭本窗口即停止。Ctrl+C 安全退出。")
    hr("-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0, help="指定端口（默认读配置，首次为 20020）")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-dialog", action="store_true", help="端口被占时不弹窗，只报错")
    ap.add_argument("--reset-password", action="store_true")
    ap.add_argument("--no-pause", action="store_true", help="结束时不再等待回车")
    args = ap.parse_args()

    hr()
    log("  班级工作台 2.0 · 启动器")
    log("  目录：%s" % ROOT)
    hr()
    check_env()

    if args.reset_password:
        p = os.path.join(DATA, "config.json")
        cfg = read_cfg()
        cfg.setdefault("teacher", {})["password_hash"] = ""
        cfg.setdefault("teacher", {})["security"] = {"question": "", "answer_hash": ""}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        log("  已清除教师口令，下次打开 /teacher/ 会重新设置（密保问题也要重设）。")

    cfg = read_cfg()
    wb_port = args.port or PORTX.config_port(CONFIG, DEFAULT_PORT)

    # ---- 端口：命令行 > 配置 > 默认 ----
    saved = PORTX.config_port(CONFIG, DEFAULT_PORT)
    if not args.port and _needs_port_migration(cfg):
        log("  端口默认值已从 8080 改为 %d（8080 太容易被别的软件占用）" % DEFAULT_PORT)
        cfg["port"] = DEFAULT_PORT
        cfg["port_migrated"] = True
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        cfg = read_cfg()
        saved = DEFAULT_PORT
    want = args.port or saved
    log("[1/4] 检查端口 %d" % want)

    def mine(p):
        info = ping(p)
        return bool(info and info.get("service") == "workbench")

    port, state = PORTX.pick_port(
        want, cfg_path=CONFIG, is_mine=mine, no_dialog=args.no_dialog,
        log=log)
    if state == "mine":
        url = "http://127.0.0.1:%d/teacher/" % port
        log("  端口上已有本工作台在运行 → 直接复用（不会起第二个实例）")
        log("")
        log("  教师端：%s" % url)
        log("[2/4] 工作台服务：已在运行，跳过启动")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        log("")
        log("  如果要重启，请先关掉原来那个窗口。")
        return 0
    if state == "cancelled":
        die("已取消：没有选择新端口。",
            "下次启动会自动再问你一次；也可以直接改 data/config.json 里的 \"port\"。")
    if state != "free":
        die("端口 %d 被其他程序占用了。" % port,
            "找占用：netstat -ano | findstr :%d\n"
            "关掉对应程序后重试；确实要换端口：python launch.py --port 20200" % port)
    if port != saved:
        PORTX.save_port(CONFIG, port)
        log("  已记住新端口 %d（下次启动直接用它）" % port)
    log("  端口可用  ✓")

    log("[2/4] 启动工作台服务")
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py"),
                             "--port", str(port), "--bind", args.bind],
                            cwd=ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", bufsize=1)

    ready = False
    for _ in range(60):
        if proc.poll() is not None:
            break
        if ping(port):
            ready = True
            break
        time.sleep(0.3)
    if not ready:
        try:
            out = proc.stdout.read() if proc.stdout else ""
        except Exception:
            out = ""
        try:
            proc.terminate()
        except Exception:
            pass
        die("服务启动失败。", (out or "")[-1200:] + "\n\n可手动运行看完整报错：\n"
            "  %s server.py --port %d" % (sys.executable, port))
    log("  服务已就绪  ✓")

    log("[3/4] 读取配置")
    cfg = read_cfg()
    log("  班级码 %s  %s" % (cfg.get("class_code", "----"),
                            "（已设教师口令）" if (cfg.get("teacher") or {}).get("password_hash")
                            else "（首次启动需设置教师口令）"))

    log("[4/4] 打开浏览器")
    banner(port, cfg)
    url = "http://127.0.0.1:%d/teacher/" % port
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    code = 0
    try:
        while True:
            if proc.poll() is not None:
                log("! 服务已退出（代码 %s）" % proc.returncode)
                code = 1
                break
            time.sleep(1)
    except KeyboardInterrupt:
        log("")
        log("收到 Ctrl+C，正在停止…")
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=6)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        log("已停止。")
    sys.exit(code)


if __name__ == "__main__":
    sys.exit(main())
