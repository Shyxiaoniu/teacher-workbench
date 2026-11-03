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

# 输出重定向到管道/文件时（如计划任务、日志采集），Windows 默认按 GBK 编码，
# 打印 ✓/▲ 等符号会直接 UnicodeEncodeError 崩掉。统一强制 UTF-8 + 容错替换。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
# 项目根：QuickForm 源码（quickform/app）与随包运行时（runtime/）都挂在这一层，
# 不是 app/ 里面。qfctl 的目录探测与解释器选择都以项目根为基准。
PROJ = os.path.dirname(ROOT)
# 与 server.py 保持同一套数据目录规则：容器/自定义部署时用 WB_DATA_DIR。
# 以前启动器写死 ROOT/data，于是 `WB_DATA_DIR=/data python launch.py --reset-password`
# 清的是另一份 config（用户以为密码清掉了，实际没有）。
DATA = os.environ.get("WB_DATA_DIR") or os.path.join(ROOT, "data")
CONFIG = os.path.join(DATA, "config.json")
sys.path.insert(0, os.path.join(ROOT, "core"))
import ports as PORTX                                    # noqa: E402
import qfctl                                             # noqa: E402

DEFAULT_PORT = PORTX.DEFAULT_PORT
QF_DEFAULT_PORT = qfctl.DEFAULT_PORT


def qf_cfg(cfg):
    """读 data/config.json 里的 quickform 段（缺省时给一套保守默认值）。"""
    q = cfg.get("quickform")
    if not isinstance(q, dict):
        q = {}
    try:
        port = int(q.get("port") or QF_DEFAULT_PORT)
    except Exception:
        port = QF_DEFAULT_PORT
    mode = str(q.get("mode") or "").lower()
    if not mode:
        # 课堂活动已并进工作台，QuickForm 默认不再随启动器拉起。
        # 只有老师显式配置了 quickform.mode（或命令行 --quickform xxx）才启动它。
        backend = str((cfg.get("activity") or {}).get("backend")
                      or os.environ.get("WB_ACTIVITY_BACKEND") or "builtin").lower()
        mode = "background" if backend == "qf" else "off"
    if mode not in ("background", "foreground", "off"):
        mode = "background"
    return {"port": port, "mode": mode, "secret": q.get("secret") or ""}


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


def qf_external():
    return (os.environ.get("WB_QUICKFORM_URL") or "").rstrip("/")


def precheck_quickform(cfgq):
    """[1/6] 启动前检查：源码 / 解释器 / 依赖 / 端口，逐项给出结论。

    只警告不阻断：QuickForm 是可替换外挂，缺了它工作台其它功能照常可用，
    但老师需要当场知道「哪一项没过、该怎么补」，而不是等页面报错。
    """
    ext = qf_external()
    log("[1/6] QuickForm 启动前检查")
    if ext:
        h = qfctl.probe_base(ext)
        log("  服务来源  外部服务 %s" % ext)
        log("  连通性    %s" % ("可达 ✓" if h["ok"] else "暂不可达（稍后自动重连）"))
        return True
    if cfgq["mode"] == "off":
        log("  启动方式  已关闭（quickform.mode=off），本次不启动 QuickForm")
        return True

    ok_all = True
    app_dir = qfctl.find_app_dir(PROJ)
    if app_dir:
        log("  源码目录  %s  ✓" % app_dir)
    else:
        log("  源码目录  缺失 ✗（应在 quickform/app/）")
        log("            处置：解压官方源码到 quickform/app/，或设置 "
            "WB_QUICKFORM_URL 指向已有服务")
        return False

    entry = qfctl.find_entry(app_dir)
    log("  启动入口  %s  %s" % (os.path.basename(entry) if entry else "无",
                               "✓" if entry else "✗"))
    ok_all = ok_all and bool(entry)

    py = qfctl.runtime_python(PROJ)
    log("  解释器    %s" % py)
    dep = qfctl.check_deps(py)
    if dep["ok"]:
        log("  依赖      Flask 全家桶齐全  ✓")
    else:
        ok_all = False
        log("  依赖      缺少 %s  ✗" % ("、".join(dep["missing"]) or dep["error"]))
        log("            处置：\"%s\" -m pip install -r \"%s\""
            % (py, os.path.join(app_dir, "requirements.txt")))

    s = qfctl.status(DATA, port=cfgq["port"])
    if s["running"]:
        log("  端口      %d 上已有实例在跑（PID %d）→ 将直接复用"
            % (s["port"], s["pid"]))
    elif qfctl.port_open(cfgq["port"]):
        pid = qfctl.find_pid_by_port(cfgq["port"])
        log("  端口      %d 被占用（PID %s），将接管或自动顺延"
            % (cfgq["port"], pid or "未知"))
    else:
        log("  端口      %d 空闲  ✓" % cfgq["port"])
    return ok_all


def show_qf_block(port, action=""):
    """打印 QuickForm 状态块（启动成功 / 已在运行 / 接管 共用）。"""
    lines, s = qfctl.status_lines(DATA, workbench_port=port)
    if action == "started":
        head = "QuickForm 服务已启动"
    elif action == "adopted":
        head = "QuickForm 服务已在运行（非本启动器拉起，已接管）"
    else:
        head = "QuickForm 服务已在运行 —— 直接复用，不再启动第二个实例"
    log("\n  " + "=" * 58)
    log("  " + head)
    log("  " + "=" * 58)
    for ln in lines:
        log(ln)
    log("  " + "-" * 58)
    if port:
        log("  下一步：浏览器打开上面的「工作台入口」→ 上课 → 课堂活动，")
        log("          活动列表能列出来，即说明 QuickForm 与 /qf 代理链路都正常。")
    log("  重启 / 停止：launch.py --qf-restart  |  launch.py --qf-stop")
    log("")
    return s


def ensure_quickform(port, cfgq):
    """[4/6] 拉起或复用 QuickForm，返回 (ok, s)。

    重复触发是这里的核心场景：已经在跑就只打印状态和入口，绝不起第二个进程。
    """
    log("[4/6] QuickForm 服务")
    ext = qf_external()
    if ext:
        h = qfctl.probe_base(ext)
        log("  外部服务  %s  %s" % (ext, "可达 ✓" if h["ok"] else "暂不可达"))
        return h["ok"], None, None
    if cfgq["mode"] == "off":
        log("  已关闭（配置 quickform.mode=off），跳过")
        return True, None, None

    if port and qfctl.status(DATA, workbench_port=port, port=cfgq["port"])["running"]:
        s = show_qf_block(port, "running")
        return s["http"]["ok"], s, None

    res = qfctl.ensure(PROJ, DATA, preferred_port=cfgq["port"],
                       mode=cfgq["mode"], secret=cfgq["secret"], log=log)
    a = res.get("action")
    if a in ("running", "adopted"):
        port_used = res["status"]["port"]
        s = show_qf_block(port, a if a == "adopted" else "running")
        if port_used != cfgq["port"]:
            log("  注意：实际端口是 %d（配置里写的是 %d），以这里显示为准。"
                % (port_used, cfgq["port"]))
        return True, s, None
    if a == "degraded":
        log("  ▲ 进程还在（PID %d），但 HTTP 探活不通 —— 属于僵死状态。"
            % res["status"]["pid"])
        log("    处置：launch.py --qf-restart（会先结束旧进程再拉起）")
        return False, res["status"], None
    if a == "failed":
        log("  ✗ 启动失败：%s" % res.get("error"))
        if res.get("hint"):
            for line in str(res["hint"]).splitlines():
                log("    " + line)
        return False, None, None
    # started
    s = show_qf_block(port, "started")
    if cfgq["mode"] == "foreground":
        log("  前台模式：本窗口会持续显示 QuickForm 日志，关窗即停。")
    return True, s, res.get("proc")



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


def banner(port, cfg, qf_state=None):
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
    log("  QuickForm http://127.0.0.1:%d/qf/  %s"
        % (port, "（同一入口）" if (qf_state or {}).get("running") else "（未运行）"))
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
    qf_mode = (qf_state or {}).get("mode") or ""
    if qf_mode == "background":
        log("  工作台服务运行中，关闭本窗口即停止工作台。")
        log("  QuickForm 为后台常驻，关窗不影响；停止用 launch.py --qf-stop。")
    elif qf_mode == "foreground":
        log("  工作台 + QuickForm（前台）运行中，关闭本窗口两者一起停止。")
    else:
        log("  服务运行中，关闭本窗口即停止。Ctrl+C 安全退出。")
    hr("-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0, help="指定端口（默认读配置，首次为 20020）")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--no-dialog", action="store_true", help="端口被占时不弹窗，只报错")
    ap.add_argument("--reset-password", action="store_true")
    # ---- QuickForm 服务 ----
    ap.add_argument("--quickform", choices=("auto", "background", "foreground", "off"),
                    default="", help="QuickForm 启动方式（默认 off：课堂活动已内置）；"
                                     "background 后台常驻、foreground 前台随窗口")
    ap.add_argument("--qf-port", type=int, default=0, help="QuickForm 端口（默认 5055）")
    ap.add_argument("--qf-status", action="store_true", help="只看状态，不启动任何服务")
    ap.add_argument("--qf-stop", action="store_true", help="停止后台常驻的 QuickForm")
    ap.add_argument("--qf-restart", action="store_true", help="重启 QuickForm")
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
    qcfg = qf_cfg(cfg)
    if args.quickform and args.quickform != "auto":
        qcfg["mode"] = args.quickform
    if args.qf_port:
        qcfg["port"] = args.qf_port
    wb_port = args.port or PORTX.config_port(CONFIG, DEFAULT_PORT)

    # ---- 只针对 QuickForm 的运维命令：不启动工作台，直接做完退出 ----
    if args.qf_status:
        lines, s = qfctl.status_lines(DATA, workbench_port=wb_port, port=qcfg["port"])
        hr("-")
        log("  QuickForm 服务状态")
        hr("-")
        for ln in lines:
            log(ln)
        hr("-")
        if s["running"]:
            log("  结论：服务正常。工作台入口 http://127.0.0.1:%d/teacher/#guide" % wb_port)
            return 0
        if s["stale"]:
            log("  结论：状态文件残留 / 进程已死 → launch.py --qf-restart")
            return 2
        log("  结论：未运行。课堂活动已内置，通常无需启动；"
            "确需回退旧服务用 launch.py --quickform background")
        return 1
    if args.qf_stop:
        r = qfctl.stop(DATA)
        log("  %s" % r["msg"])
        return 0 if r["ok"] else 2
    if args.qf_restart:
        log("  正在重启 QuickForm（端口 %d，方式 %s）…" % (qcfg["port"], qcfg["mode"]))
        r = qfctl.restart(PROJ, DATA, preferred_port=qcfg["port"],
                          mode=qcfg["mode"], secret=qcfg["secret"], log=log)
        if not r.get("ok"):
            log("  ✗ %s" % r.get("error"))
            if r.get("hint"):
                for line in str(r["hint"]).splitlines():
                    log("    " + line)
            return 2
        show_qf_block(wb_port, "started")
        return 0

    qf_ready = precheck_quickform(qcfg)

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
    log("[2/6] 检查端口 %d" % want)

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
        log("[3/6] 工作台服务：已在运行，跳过启动")
        ensure_quickform(port, qcfg)
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

    log("[3/6] 启动工作台服务")
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

    qf_ok, qf_state, qf_proc = ensure_quickform(port, qcfg)
    if not qf_ready and not qf_ok:
        log("  ▲ QuickForm 未就绪：工作台其它功能不受影响，修好后可 "
            "launch.py --qf-restart")

    log("[5/6] 读取配置")
    cfg = read_cfg()
    log("  班级码 %s  %s" % (cfg.get("class_code", "----"),
                            "（已设教师口令）" if (cfg.get("teacher") or {}).get("password_hash")
                            else "（首次启动需设置教师口令）"))

    log("[6/6] 打开浏览器")
    banner(port, cfg, qf_state)
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
            if qf_proc is not None and qf_proc.poll() is not None:
                log("! QuickForm（前台模式）已退出，代码 %s；"
                    "可 launch.py --qf-restart 重新拉起" % qf_proc.returncode)
                qf_proc = None
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
        if qf_proc is not None:
            try:
                qf_proc.terminate()
                qf_proc.wait(timeout=6)
            except Exception:
                try:
                    qf_proc.kill()
                except Exception:
                    pass
            try:
                qfctl.clear_state(DATA)
            except Exception:
                pass
            log("QuickForm（前台模式）已随窗口停止。")
        log("已停止。")
    sys.exit(code)


if __name__ == "__main__":
    sys.exit(main())

