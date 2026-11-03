#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 一键启动器（仅标准库）

双击 启动教师端.bat / .command 就是跑这个文件。它做五件事：
  1. 自检：Python 版本、文件完整、data 目录可写、数据库可打开；
  2. 端口：默认 20020（不常用，避开 8080/3000/5000 这些常被占的号）。
     被占用时先判断“是不是自己上次没关”（是就复用），否则**弹窗**让老师
     当场改端口并记住，改完接着启动——不再出现“端口被占用”直接退出；
  3. 起服务：python server.py --port <port>（单进程，教师端 = 服务端）；
  4. 打印横幅：教师端地址、学生端局域网地址、班级码；
  5. 打开浏览器到 /teacher/。

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

ROOT = os.path.dirname(os.path.abspath(__file__))
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


def ping_qf(base="http://127.0.0.1:5055"):
    """探测 QuickForm 服务是否在线。"""
    try:
        with urllib.request.urlopen(base + "/api/tasks", timeout=1.2) as r:
            return r.status < 500
    except Exception:
        try:
            with urllib.request.urlopen(base + "/", timeout=1.2) as r:
                return r.status < 500
        except Exception:
            return False


def maybe_start_quickform(log=log):
    """自动拉起 QuickForm（可选配套服务，默认 127.0.0.1:5055）。

    Docker / NAS 部署下 QuickForm 是独立容器，由 docker-compose 的
    depends_on 与工作台一起启动，这里探测到已在线就直接跳过；
    只有「桌面双击启动」这种非容器场景才需要本函数去拉起本地进程。

    源码位置：项目根目录的 quickform/app/（与容器用的是同一份），
    兼容老部署的 app/quickform-server/。
    """
    if os.environ.get("WB_QUICKFORM_URL"):
        base = os.environ["WB_QUICKFORM_URL"].rstrip("/")
        log("[3/5] QuickForm（外部服务）：%s%s"
            % (base, " ✓" if ping_qf(base) else "（暂未就绪，稍后自动重连）"))
        return
    candidates = [
        os.path.join(os.path.dirname(ROOT), "quickform", "app"),
        os.path.join(ROOT, "quickform-server"),
        os.path.join(os.path.dirname(ROOT), "quickform-server"),
    ]
    qf_dir = next((d for d in candidates if os.path.isdir(d)), None)
    if not qf_dir:
        log("[3/5] QuickForm：未安装，跳过（可选功能，不影响其它功能；"
            "源码放到项目根目录 quickform/app/ 即可自动启动）")
        return
    if ping_qf():
        log("[3/5] QuickForm：已在运行（127.0.0.1:5055）✓")
        return
    # 常见入口：启动脚本优先，其次 Python 入口
    entries = [
        ("start.bat", ["cmd", "/c", "start.bat"]),
        ("start.sh", ["bash", "start.sh"]),
        ("main.py", [sys.executable, "main.py"]),
        ("app.py", [sys.executable, "app.py"]),
        ("server.py", [sys.executable, "server.py"]),
    ]
    for name, cmd in entries:
        if not os.path.isfile(os.path.join(qf_dir, name)):
            continue
        try:
            env = dict(os.environ)
            # 官方 app.py 默认监听 5001（QUICKFORM_PORT/PORT 才会改），
            # 而工作台的探测与 /qf 代理都约定 5055 —— 不钉住就会
            # 「启动成功了但一直报未就绪」。这里显式对齐。
            env.setdefault("QUICKFORM_PORT", "5055")
            kwargs = dict(cwd=qf_dir, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                          env=env)
            if os.name == "nt":
                kwargs["creationflags"] = 0x08000000   # CREATE_NO_WINDOW，不闪黑框
            subprocess.Popen(cmd, **kwargs)
        except Exception as e:
            log("[3/5] QuickForm 自动启动失败：%s（可手动启动它）" % e)
            return
        for _ in range(20):
            if ping_qf():
                log("[3/5] QuickForm：已自动启动 ✓（127.0.0.1:5055）")
                return
            time.sleep(0.4)
        log("[3/5] QuickForm 已尝试启动但暂未就绪，可稍后在活动页点「刷新」")
        return
    log("[3/5] QuickForm：目录存在但没找到启动入口"
        "（start.bat / main.py / app.py / server.py），请手动启动")


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

    # ---- 端口：命令行 > 配置 > 默认 ----
    cfg = read_cfg()
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
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        log("")
        log("  如果要重启，请先关掉原来那个窗口。")
        return
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

    log("[2/5] 启动服务")
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
    maybe_start_quickform(log)

    log("[4/5] 读取配置")
    cfg = read_cfg()
    log("  班级码 %s  %s" % (cfg.get("class_code", "----"),
                            "（已设教师口令）" if (cfg.get("teacher") or {}).get("password_hash")
                            else "（首次启动需设置教师口令）"))

    log("[5/5] 打开浏览器")
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
    main()
