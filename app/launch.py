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
# spawn_server 在 main() 之外也要知道是否走 HTTP（--http 退回明文，默认 HTTPS）
_LAUNCH_ARGS = argparse.Namespace(http=False)
# 项目根：随包运行时（runtime/）挂在这一层，不是 app/ 里面。
PROJ = os.path.dirname(ROOT)
# 与 server.py 保持同一套数据目录规则：容器/自定义部署时用 WB_DATA_DIR。
# 以前启动器写死 ROOT/data，于是 `WB_DATA_DIR=/data python launch.py --reset-password`
# 清的是另一份 config（用户以为密码清掉了，实际没有）。
DATA = os.environ.get("WB_DATA_DIR") or os.path.join(ROOT, "data")
CONFIG = os.path.join(DATA, "config.json")
# 重启请求标记：server.py 收到 /api/v2/update/restart 时先落这个文件再退出，
# 本启动器看到「退出码 0 + 标记在」就知道是重启，重新拉起而不是报「已停止」。
RESTART_FLAG = os.path.join(DATA, ".restart-request")
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
    # 探活跟着协议走：离线包默认 HTTPS，用 http 探会失败、误判服务没起来。
    _https = os.environ.get("WB_HTTPS", "").strip() in ("1", "true", "yes") \
        and not getattr(_LAUNCH_ARGS, "http", False)
    _url = ("https" if _https else "http") + "://127.0.0.1:%d/api/v2/ping" % port
    try:
        if _https:
            import ssl
            _c = ssl._create_unverified_context()   # 自签证书，探活不需要校验证书
            with urllib.request.urlopen(_url, timeout=1.2, context=_c) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        with urllib.request.urlopen(_url, timeout=1.2) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def url_for(port, path="/teacher/"):
    """本机访问地址（离线包默认 HTTPS，--http 退回明文）"""
    _https = os.environ.get("WB_HTTPS", "").strip() in ("1", "true", "yes") \
        and not getattr(_LAUNCH_ARGS, "http", False)
    return ("https" if _https else "http") + "://127.0.0.1:%d%s" % (port, path)


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


def spawn_server(port, bind, restarting=False):
    """起 server.py 子进程。

    ⚠️ **stdout/stderr 必须是继承控制台，绝不能再接 subprocess.PIPE。**

    2026-11-20 牛哥在不联网的服务器上用离线包更新，现象是：终端停在原来的
    界面、显示「服务已停止」，新服务起不来。根因就在这条管道上 ——
    更新后 server.py 走 os.execv 换进程（Windows 上是「起新进程 → 退旧进程」），
    旧进程一退，下面的 poll() 立刻返回 0，启动器以为服务崩了，finally 里
    打印「已停止。」并 sys.exit；而新进程继承的正是那条 PIPE，父进程一退出
    管道读端就关了，新进程启动时写一行日志就抛 WinError 232
    （管道的另一端已关闭）→ 新服务当场崩，于是「旧的停了、新的起不来」。

    改成继承控制台后：服务日志实时可见、没有管道可断，重启由启动器自己重新
    Popen（见 main 里的监视循环），终端窗口也不会退。
    """
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    # 告诉 server.py「我是被启动器看着的」→ 重启时不 execv 自己，
    # 而是落标记后正常退出，由本启动器重新拉起新代码。
    env["WB_SUPERVISED"] = "1"
    env["WB_RESTART_FLAG"] = RESTART_FLAG
    # HTTPS（2026-11-25）：离线包默认启用，截图工具的屏幕采集 API 才可用。
    # 用户/运维可用 WB_HTTPS=0 环境变量或启动器 --http 参数退回纯 HTTP。
    env["WB_HTTPS"] = "0" if getattr(_LAUNCH_ARGS, "http", False) else "1"
    if restarting:
        # 端口刚放开、可能还在 TIME_WAIT：让 server.py 走「等端口空出来、
        # 绝不换端口」的分支，否则老师的书签 / 桌面图标全指着老地址。
        env["WB_RESTARTING"] = "1"
    return subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server.py"),
         "--port", str(port), "--bind", bind,
         # 端口由启动器负责：子进程绝不能再弹端口窗。以前没带这个参数，
         # 更新重启时子进程发现端口还在 TIME_WAIT 就会自己弹一个 Tk 窗
         # （macOS 上看到的「选择端口号」就是它），选完服务起在别处，
         # 老师的书签还指着老地址 → 表现为怎么都起不来。
         "--no-dialog"],
        cwd=ROOT, env=env)


def wait_ready(proc, port, seconds=25):
    """等服务应答 /api/v2/ping。子进程先退出 = 起不来，立刻返回 False。"""
    for _ in range(int(seconds / 0.3)):
        if proc.poll() is not None:
            return False
        if ping(port):
            return True
        time.sleep(0.3)
    return False


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
    _https = os.environ.get("WB_HTTPS", "").strip() in ("1", "true", "yes") \
        and not getattr(_LAUNCH_ARGS, "http", False)
    _sc = "https" if _https else "http"
    hr()
    log("  班级工作台 2.0 · 服务已启动")
    log("  数据目录  %s" % DATA)
    hr()
    log("")
    log("  教师端    %s://127.0.0.1:%d/teacher/" % (_sc, port))
    log("  学生端    %s://%s:%d/s/  （局域网）" % (_sc, ip, port))
    log("  学生端控制台  %s://127.0.0.1:%d/teacher/console" % (_sc, port))
    log("")
    log(admin_hint())
    if len(ips) > 1:
        log("  其它网段  " + "  ".join("%s://%s:%d/s/" % (_sc, x, port) for x in ips[1:3]))
    log("")
    log("  学生：连同一个 Wi-Fi → 浏览器打开上面的地址 → 选班级学号输密码")
    log("")
    if _https:
        log("  首次打开浏览器会提示「不安全」→ 点「高级 / 继续前往」即可")
        log("  （内置自签证书，截图工具需要 HTTPS；不影响数据，仅首次点一下）")
    else:
        log("  当前为 HTTP 模式（--http）：截图工具不可用，其余功能正常。")
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
    ap.add_argument("--http", action="store_true",
                    help="退回纯 HTTP（截图工具需要 HTTPS，默认已启用 HTTPS）")
    args = ap.parse_args()
    global _LAUNCH_ARGS
    _LAUNCH_ARGS = args
    # 当前进程也要带上协议标记：ping()/端口复用检测都读 os.environ，
    # 只设子进程 env 不够——否则启动时用 http 探活 HTTPS 服务会误判「端口被占」。
    os.environ["WB_HTTPS"] = "0" if args.http else "1"

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
        reuse_ok=True, log=log)
    if state == "mine":
        url = url_for(port)
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
    # 上一次重启留下的标记（正常重启后这里会删掉，这是兜底）
    try:
        if os.path.exists(RESTART_FLAG):
            os.remove(RESTART_FLAG)
    except Exception:
        pass
    proc = spawn_server(port, args.bind)
    if not wait_ready(proc, port):
        try:
            proc.terminate()
        except Exception:
            pass
        die("服务启动失败。",
            "服务自己打印的报错就在上面几行（跟本窗口同一屏），按它改。\n"
            "也可以手动跑一遍看完整输出：\n"
            "  %s server.py --port %d" % (sys.executable, port))
    log("  服务已就绪  ✓")

    log("[3/4] 读取配置")
    cfg = read_cfg()
    log("  口令状态  %s" % ("已设教师口令" if (cfg.get("teacher") or {}).get("password_hash")
                            else "首次启动需设置教师口令"))

    log("[4/4] 打开浏览器")
    banner(port, cfg)
    url = url_for(port)
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    code = 0
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                # 「正常退出（0）+ 重启标记」= 更新后要求重启，不是崩了。
                # 由本启动器把新代码重新拉起来，终端窗口不退、端口不换。
                if rc == 0 and os.path.exists(RESTART_FLAG):
                    try:
                        os.remove(RESTART_FLAG)
                    except Exception:
                        pass
                    log("")
                    log("[!] 收到重启请求，正在重新启动服务…")
                    proc = spawn_server(port, args.bind, restarting=True)
                    if wait_ready(proc, port):
                        v = (ping(port) or {}).get("version") or ""
                        log("  服务已重新就绪  ✓%s" % ("  版本 " + v if v else ""))
                        hr("-")
                        log("  服务运行中，关闭本窗口即停止。Ctrl+C 安全退出。")
                        hr("-")
                        continue
                    log("")
                    log("  ✗ 重启失败：新服务没能起来，报错就在上面几行。")
                    log("    旧版本代码还在 app/data/updates/backup-v*/ 里，")
                    log("    可以手动换回去，或直接重开本启动器。")
                    log("")
                    code = 1
                    break
                log("! 服务已退出（代码 %s）" % rc)
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
