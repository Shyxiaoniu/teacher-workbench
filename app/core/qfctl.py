#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QuickForm 服务生命周期管理（仅标准库）

为什么单独一个模块
------------------
QuickForm 是工作台的「可替换外挂」：Docker 部署时它是独立容器，桌面部署时
它是本机上一个 Flask 进程（默认 127.0.0.1:5055）。启动器要负责的是后者的
**可靠拉起 + 重复触发不自伤 + 状态可核对**三件事，逻辑量已经不适合塞进
launch.py，抽到这里，launch.py 只在合适的位置调用 ensure()。

四条判定线（缺一不可，全部通过才算「稳定启动」）
------------------------------------------------
  ① 进程：状态文件里的 PID 还活着（不是僵尸 PID、不是别人复用后的号）
  ② 端口：127.0.0.1:<port> 能 connect 上（说明真的在 listen，不只是进程在）
  ③ HTTP：GET /login 返回 2xx/3xx，且响应体里认得出是 QuickForm
  ④ 链路：经工作台 /qf/login 也能通（教师端「上课」页走的就是这条代理）

只做 ①② 会出现「进程在、端口开、页面 500」的假阳性；加 ③④ 才是老师
真正能用的状态。--qf-status 会把四条线的结果逐条打出来。

重复触发
--------
ensure() 先看状态文件 + 探活：已经在跑就**直接复用**，打印「服务已在运行」
和端口 / PID / 启动时间 / 已运行时长 / 访问地址 / 工作台入口，绝不再起第二个
进程抢同一个端口（Windows 上第二个进程会 bind 失败并静默退出，老师看到的是
「启动了但没反应」）。
"""
import json
import os
import re
import socket
import subprocess
import sys
import time

DEFAULT_PORT = 5055
PORT_TRIES = 10                 # 5055 被占时依次试到 5064
READY_TIMEOUT = 40              # 冷启动等待上限（首次建库 + 导入静态资源偏慢）
STATE_NAME = "quickform.runtime.json"
LOG_NAME = "quickform.log"
LOG_MAX = 2 * 1024 * 1024       # 超过 2MB 就滚动一份 .1

# Windows 进程创建标志
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _opener():
    """绕过系统代理：本机回环请求不能被 FastGithub / 公司代理截走。"""
    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def human_uptime(sec):
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return "%d 天 %d 小时" % (d, h)
    if h:
        return "%d 小时 %d 分" % (h, m)
    if m:
        return "%d 分 %d 秒" % (m, s)
    return "%d 秒" % s


def _iso(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return "-"


def pid_alive(pid):
    """PID 是否真活着（Windows 用 OpenProcess 判断，避免「PID 被系统复用」误判）。"""
    try:
        pid = int(pid or 0)
    except Exception:
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
            if not h:
                # 拒绝访问 = 进程存在但不属于我们；没有这个进程会返回 ERROR_INVALID_PARAMETER
                return ctypes.get_last_error() == 5
            try:
                code = ctypes.c_ulong()
                if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return False
                return code.value == 259                # STILL_ACTIVE
            finally:
                k.CloseHandle(h)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 状态文件
# ---------------------------------------------------------------------------
def state_file(data_dir):
    return os.path.join(data_dir, STATE_NAME)


def log_file(data_dir):
    return os.path.join(data_dir, "logs", LOG_NAME)


def read_state(data_dir):
    try:
        with open(state_file(data_dir), "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def write_state(data_dir, st):
    try:
        os.makedirs(data_dir, exist_ok=True)
        tmp = state_file(data_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
        os.replace(tmp, state_file(data_dir))
        return True
    except Exception:
        return False


def clear_state(data_dir):
    try:
        os.remove(state_file(data_dir))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 探活
# ---------------------------------------------------------------------------
def port_open(port, host="127.0.0.1", timeout=0.6):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, int(port))) == 0
    except Exception:
        return False


def port_free(port, host="127.0.0.1", timeout=0.4):
    """端口是否真的能拿来监听。

    只做 connect 判断不够：Windows 上 werkzeug 带 SO_REUSEADDR，
    别的程序占着端口时 Flask 仍能「绑定成功」，流量却被原程序接走，
    表现出来就是「日志说 Running on 5055，页面永远打不开」。
    所以这里再 bind 一次（故意不加 SO_REUSEADDR）—— 和 ports.is_free 同一口径。
    """
    if port_open(port, host, timeout):
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.bind((host, int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def probe_base(base, timeout=2.5, expect_marker=True):
    """HTTP 探活（base 可以是 http://127.0.0.1:5055 或外部服务地址）。

    先试 /login（QuickForm 的登录页，未登录也能 200），失败再试 /。
    3xx 也算「服务活着」—— 有些版本 / 会 302 到 /login。
    """
    base = (base or "").rstrip("/")
    out = {"ok": False, "status": 0, "ms": 0, "marker": False, "error": ""}
    t0 = time.time()
    last = ""
    for path in ("/login", "/"):
        try:
            req = _opener().open(base + path, timeout=timeout)
            body = req.read(4096).decode("utf-8", "replace")
            code = req.getcode() or 200
            out["status"] = code
            out["marker"] = ("quickform" in body.lower()) or ("登录" in body)
            out["ok"] = 200 <= code < 400 and (out["marker"] or not expect_marker)
            out["ms"] = int((time.time() - t0) * 1000)
            out["path"] = path
            if out["ok"]:
                return out
        except Exception as e:
            last = "%s" % (e,)
    out["ms"] = int((time.time() - t0) * 1000)
    out["error"] = last
    return out


def probe(port, timeout=2.5, expect_marker=True):
    """本机端口的探活（probe_base 的语法糖）。"""
    return probe_base("http://127.0.0.1:%d" % int(port), timeout, expect_marker)


def proxy_probe(workbench_port, timeout=3.0):
    """经工作台 /qf/ 访问一次（教师端「上课」页用的就是这条链路）。"""
    if not workbench_port:
        return {"ok": False, "skip": True, "status": 0, "ms": 0}
    t0 = time.time()
    try:
        req = _opener().open("http://127.0.0.1:%d/qf/login" % int(workbench_port), timeout=timeout)
        code = req.getcode() or 200
        req.read(2048)
        return {"ok": 200 <= code < 400, "skip": False, "status": code,
                "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "skip": False, "status": 0,
                "ms": int((time.time() - t0) * 1000), "error": "%s" % (e,)}


def status(data_dir, workbench_port=0, port=0):
    """综合状态。running 只在「进程 + HTTP」都成立时为真。"""
    st = read_state(data_dir)
    pid = int(st.get("pid") or 0)
    p = int(st.get("port") or port or DEFAULT_PORT)
    alive = pid_alive(pid)
    h = probe(p)
    running = bool(alive and h["ok"])
    started = float(st.get("started_ts") or 0)
    return {
        "running": running,
        "stale": bool(pid and not running),
        "pid": pid,
        "pid_alive": alive,
        "port": p,
        "mode": st.get("mode") or "",
        "started_ts": started,
        "started_at": _iso(started) if started else "-",
        "uptime": human_uptime(time.time() - started) if started else "-",
        "log": st.get("log") or log_file(data_dir),
        "entry": st.get("entry") or "",
        "python": st.get("python") or "",
        "http": h,
        "state": st,
    }


# ---------------------------------------------------------------------------
# 安装位置 / 解释器
# ---------------------------------------------------------------------------
def find_app_dir(root):
    """QuickForm 源码目录（与容器共用同一份）。

    WB_QF_APP_DIR 可显式指定（换源码目录、多份实例并存时用），
    其余情况按「项目根 / 老部署 app 目录 / 上级目录」依次找。
    """
    env = os.environ.get("WB_QF_APP_DIR")
    cands = ([env] if env else []) + [
        os.path.join(root, "quickform", "app"),
        os.path.join(root, "quickform-server"),
        os.path.join(os.path.dirname(root), "quickform", "app"),
    ]
    for d in cands:
        if d and os.path.isdir(d):
            return d
    return None


def find_entry(app_dir):
    """优先 serve.py：/qf 前缀适配层在里面，直跑 app.py 会丢前缀。"""
    for name in ("serve.py", "main.py", "app.py"):
        p = os.path.join(app_dir, name)
        if os.path.isfile(p):
            return p
    return None


def runtime_python(root):
    """挑一个能跑 Flask 的解释器：优先随包运行时，其次当前解释器。"""
    cands = [
        os.environ.get("WB_QF_PYTHON") or "",
        os.path.join(root, "runtime", "win-x64", "python", "python.exe"),
        os.path.join(root, "runtime", "python", "python.exe"),
        os.path.join(root, "quickform", "runtime", "python", "python.exe"),
        os.path.join(root, ".venv", "Scripts", "python.exe"),
        os.path.join(root, "quickform", ".venv", "Scripts", "python.exe"),
        sys.executable,
    ]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return sys.executable


DEP_MODULES = ("flask", "flask_sqlalchemy", "flask_login", "flask_bcrypt",
               "flask_babel", "sqlalchemy", "werkzeug")


def check_deps(py, timeout=25):
    """在目标解释器里 import 一遍 Flask 全家桶。

    不 import 会得到「启动两秒后退出、日志里一行 ModuleNotFoundError」，
    老师看不出该装什么。这里提前问，失败时能把缺失模块名和安装命令一起给出。
    """
    code = ("import importlib,sys\n"
            "miss=[]\n"
            "for m in %r:\n"
            "    try: importlib.import_module(m)\n"
            "    except Exception: miss.append(m)\n"
            "print(','.join(miss))\n" % (DEP_MODULES,))
    try:
        r = subprocess.run([py, "-c", code], capture_output=True,
                           text=True, timeout=timeout, errors="replace")
        miss = [x for x in (r.stdout or "").strip().split(",") if x]
        if r.returncode != 0 and not miss:
            return {"ok": False, "missing": [], "error": (r.stderr or "")[-400:]}
        return {"ok": not miss, "missing": miss, "error": ""}
    except Exception as e:
        return {"ok": False, "missing": [], "error": "%s" % (e,)}


# ---------------------------------------------------------------------------
# 启动 / 停止
# ---------------------------------------------------------------------------
def _rotate_log(path):
    try:
        if os.path.getsize(path) > LOG_MAX:
            os.replace(path, path + ".1")
    except Exception:
        pass


def _tail(path, n=1600):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - n * 2))
            return f.read().decode("utf-8", "replace")[-n:]
    except Exception:
        return ""


def _port_for_start(preferred):
    """挑端口：首选没人用；被别的程序占着就顺延（不改配置，只在返回里说明）。"""
    for p in range(int(preferred), int(preferred) + PORT_TRIES):
        if port_free(p):
            return p, ""
    return int(preferred), "端口 %d-%d 都被占用" % (preferred, preferred + PORT_TRIES - 1)


def find_pid_by_port(port):
    """按监听端口反查 PID（服务是别人/上次留下的、没有状态文件时用）。"""
    try:
        if os.name == "nt":
            r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                               capture_output=True, text=True, timeout=12,
                               errors="replace")
            hit = []
            for line in (r.stdout or "").splitlines():
                parts = line.split()
                if len(parts) < 5 or "LISTENING" not in line:
                    continue
                if parts[1].endswith(":%d" % int(port)):
                    hit.append(int(parts[-1]))
            return hit[0] if hit else 0
        r = subprocess.run(["lsof", "-ti", ":%d" % int(port)],
                           capture_output=True, text=True, timeout=12,
                           errors="replace")
        for line in (r.stdout or "").splitlines():
            if line.strip().isdigit():
                return int(line.strip())
    except Exception:
        pass
    return 0


def ensure(root, data_dir, preferred_port=DEFAULT_PORT, mode="background",
           secret="", log=print, adopt=True):
    """重复触发也安全的启动入口。

    返回 action：
      running  已在运行（复用，未起新进程）
      adopted  端口上有 QuickForm 但没状态文件（接管，补写状态）
      started  本次成功拉起
      degraded 进程在但 HTTP 不通（僵死，需要 --qf-restart）
      failed   启动失败（error/hint 里是原因和处置）
    """
    s = status(data_dir, port=preferred_port)

    if s["running"]:
        return {"action": "running", "status": s}

    # 状态文件丢了 / 由别的脚本或计划任务起的：端口上有货就接管，绝不起第二个
    p = int(s.get("port") or preferred_port)
    if adopt and probe(p)["ok"]:
        pid = find_pid_by_port(p)
        st = read_state(data_dir)
        if not st:
            write_state(data_dir, {
                "pid": pid, "port": p, "mode": "external",
                "started_ts": 0, "started_at": "-",
                "log": log_file(data_dir), "python": "",
                "app_dir": find_app_dir(root) or "", "entry": "external",
            })
        s2 = status(data_dir, port=p)
        s2["adopted_pid"] = pid
        return {"action": "adopted", "status": s2}

    if s["pid_alive"]:
        return {"action": "degraded", "status": s}

    res = start(root, data_dir, preferred_port=preferred_port,
                mode=mode, secret=secret, log=log)
    if not res.get("ok"):
        return {"action": "failed", "error": res.get("error"),
                "hint": res.get("hint"), "log": res.get("log"), "port": res.get("port")}
    return {"action": "started", "status": status(data_dir, port=res["port"]),
            "port": res["port"], "pid": res["pid"], "proc": res.get("proc")}


def start(root, data_dir, preferred_port=DEFAULT_PORT, mode="background",
          secret="", log=print):
    """拉起 QuickForm。返回 {ok, port, pid, mode, log, error}。"""
    app_dir = find_app_dir(root)
    if not app_dir:
        return {"ok": False, "error": "找不到 QuickForm 源码目录（quickform/app）",
                "hint": "把官方源码解压到项目根目录的 quickform/app/；"
                        "或改用外部服务：设置 WB_QUICKFORM_URL=http://主机:端口"}
    entry = find_entry(app_dir)
    if not entry:
        return {"ok": False, "error": "QuickForm 目录里没有 serve.py / app.py",
                "hint": "源码不完整，重新解压官方包到 quickform/app/"}

    py = runtime_python(root)
    dep = check_deps(py)
    if not dep["ok"]:
        missing = dep["missing"] or []
        return {"ok": False,
                "error": "Python 依赖不完整：%s（解释器 %s）"
                         % ("、".join(missing) or dep["error"], py),
                "hint": "在该解释器上装依赖：\n"
                        "  \"%s\" -m pip install -r \"%s\"\n"
                        "或改用随包运行时（项目根 runtime/win-x64/python/）。"
                        % (py, os.path.join(app_dir, "requirements.txt"))}

    port, why = _port_for_start(preferred_port)
    if why:
        return {"ok": False, "error": why,
                "hint": "用 --qf-port 指定一个空端口，或先关掉占用 5055 的程序。"}

    logs = os.path.dirname(log_file(data_dir))
    try:
        os.makedirs(logs, exist_ok=True)
        os.makedirs(data_dir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "error": "数据目录不可写：%s" % e}

    lf = log_file(data_dir)
    _rotate_log(lf)
    env = dict(os.environ)
    env["QUICKFORM_PORT"] = str(port)
    env["SECRET_KEY"] = secret or env.get("SECRET_KEY") or "wb-quickform-local"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("PYTHONHOME", None)

    cmd = [py, entry]
    fg = (mode == "foreground")
    fh = None
    if fg:
        # 前台：不重定向，QuickForm 的启动日志和请求日志直接打在老师眼前
        out, err = None, None
        log("[QuickForm] 前台模式：日志直接输出到本窗口，Ctrl+C 一并停止")
        log("[QuickForm] 解释器 %s" % py)
        log("[QuickForm] 工作目录 %s" % app_dir)
    else:
        fh = open(lf, "ab", buffering=0)
        fh.write(("\n===== 启动 %s | 端口 %d | %s =====\n"
                  % (time.strftime("%Y-%m-%d %H:%M:%S"), port, mode)).encode("utf-8"))
        out, err = fh, subprocess.STDOUT
    kwargs = dict(cwd=app_dir, env=env, stdin=subprocess.DEVNULL,
                  stdout=out, stderr=err, close_fds=True)
    if os.name == "nt":
        # 后台：脱离控制台（关掉启动窗口也不影响）；前台：继承控制台，日志就地可见
        kwargs["creationflags"] = (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                                   | CREATE_NO_WINDOW) if not fg else 0
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except Exception as e:
        if fh:
            fh.close()
        return {"ok": False, "error": "进程创建失败：%s" % e}

    # 等就绪：进程活着 + HTTP 认得出是 QuickForm
    deadline = time.time() + READY_TIMEOUT
    last = {}
    while time.time() < deadline:
        if proc.poll() is not None:
            if fh:
                fh.close()
            return {"ok": False, "error": "进程启动后立即退出（代码 %s）" % proc.returncode,
                    "hint": _tail(lf, 1200) or "见日志 %s" % lf, "log": lf}
        last = probe(port)
        if last["ok"]:
            break
        time.sleep(0.5)

    if not last.get("ok"):
        try:
            proc.terminate()
        except Exception:
            pass
        if fh:
            fh.close()
        return {"ok": False,
                "error": "等了 %d 秒仍未通过健康检查（端口 %d）" % (READY_TIMEOUT, port),
                "hint": "常见原因：端口被别的进程抢走 / 数据库文件被占用 / "
                        "依赖版本不匹配。日志尾部：\n" + (_tail(lf, 1200) or "（空）"),
                "log": lf, "port": port}

    if fh:
        fh.close()
    st = {"pid": proc.pid, "port": port, "mode": mode,
          "started_ts": time.time(), "started_at": _iso(time.time()),
          "log": lf, "python": py, "app_dir": app_dir,
          "entry": os.path.basename(entry), "workdir": app_dir}
    write_state(data_dir, st)
    log("  进程已就绪  PID %d  端口 %d" % (proc.pid, port))
    return {"ok": True, "port": port, "pid": proc.pid, "mode": mode,
            "log": lf, "python": py, "app_dir": app_dir, "proc": proc}


def stop(data_dir, log=print, wait=8):
    """停止后台常驻实例。"""
    st = read_state(data_dir)
    pid = int(st.get("pid") or 0)
    if not pid or not pid_alive(pid):
        clear_state(data_dir)
        return {"ok": True, "stopped": False, "msg": "没有正在运行的实例（状态已清理）"}
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        except Exception:
            try:
                import ctypes
                k = ctypes.windll.kernel32
                h = k.OpenProcess(0x0001, False, pid)   # PROCESS_TERMINATE
                if h:
                    k.TerminateProcess(h, 0)
                    k.CloseHandle(h)
            except Exception:
                pass
    else:
        try:
            os.kill(pid, 15)
        except Exception:
            pass
    t0 = time.time()
    while time.time() - t0 < wait:
        if not pid_alive(pid):
            break
        time.sleep(0.3)
    clear_state(data_dir)
    alive = pid_alive(pid)
    return {"ok": not alive, "stopped": True, "pid": pid,
            "msg": "已停止 PID %d" % pid if not alive else "进程 %d 仍未退出" % pid}


def restart(root, data_dir, **kw):
    stop(data_dir)
    time.sleep(0.8)
    return start(root, data_dir, **kw)


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------
def status_lines(data_dir, workbench_port=0, port=0):
    """给启动器用的状态块（纯文本行）。"""
    s = status(data_dir, workbench_port, port)
    h = s["http"]
    lines = []
    if s["running"]:
        lines.append("  状态        ● 运行中（进程 + HTTP 双重校验通过）")
    elif s["stale"]:
        lines.append("  状态        ▲ 异常：进程不在/无响应（状态文件是旧的）")
    else:
        lines.append("  状态        ○ 未运行")
    if s["pid"]:
        lines.append("  进程        PID %d%s" % (s["pid"], "" if s["pid_alive"] else "（已退出）"))
    lines.append("  端口        %d（127.0.0.1）%s" % (s["port"], " · 在监听" if port_open(s["port"]) else " · 未监听"))
    if s["started_ts"]:
        lines.append("  启动于      %s（已运行 %s）" % (s["started_at"], s["uptime"]))
    if s["mode"]:
        label = {"background": "后台常驻（关掉启动窗口也不停）",
                 "foreground": "前台（随启动窗口，关窗即停）",
                 "external": "外部实例（不是本启动器拉起的，已接管）"}.get(
                     s["mode"], s["mode"])
        lines.append("  启动方式    %s" % label)
    if h.get("ok"):
        lines.append("  健康检查    HTTP %s %s · %d ms"
                     % (h["status"], h.get("path") or "", h["ms"]))
    else:
        lines.append("  健康检查    失败 %s" % (h.get("error") or "无响应"))
    if workbench_port:
        pp = proxy_probe(workbench_port)
        lines.append("  代理链路    /qf/login → HTTP %s%s"
                     % (pp.get("status") or "-",
                        "  ✓" if pp.get("ok") else "  ✗（教师端活动页会连不上）"))
    lines.append("  服务地址    http://127.0.0.1:%d/login" % s["port"])
    if workbench_port:
        lines.append("  统一入口    http://127.0.0.1:%d/qf/" % workbench_port)
        lines.append("  工作台入口  http://127.0.0.1:%d/teacher/#guide（上课 · 课堂活动）"
                     % workbench_port)
    if s["mode"] == "external":
        lines.append("  日志        外部实例，日志在其自身窗口 / 容器里")
    else:
        lines.append("  日志        %s" % s["log"])
    return lines, s


def already_running_block(data_dir, workbench_port=0, log=print):
    """「服务已在运行」提示块：让老师一眼看到入口，不用猜。"""
    lines, s = status_lines(data_dir, workbench_port)
    log("\n  " + "=" * 58)
    log("  QuickForm 服务已在运行 —— 直接复用，不再启动第二个实例")
    log("  " + "=" * 58)
    for ln in lines:
        log(ln)
    log("  " + "-" * 58)
    if workbench_port:
        log("  下一步：浏览器打开上面的「工作台入口」→ 上课 → 课堂活动，")
        log("          活动列表来自 QuickForm，能列出活动即说明链路正常。")
    log("  需要重启：%s launch.py --qf-restart" % os.path.basename(sys.executable))
    log("")
    return s
