#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QuickForm 教师版 —— 一键启动器（跨平台，仅依赖 Python 标准库）

职责：
  1. 探测并复用已有运行环境（项目内 .venv / 系统 Python），都没有则用随项目自带的可移植运行时；
  2. 依赖缺失时用随项目自带的 wheel 离线补全，全程不联网；
  3. 端口占用检测与处理；
  4. 启动 app.py，输出实时打印并同时写入 logs/ 日志文件；
  5. 健康检查通过后自动打开浏览器；
  6. 启动失败时给出可读的错误定位与日志路径。

用法：
    python scripts/start.py            # 默认 5001 端口
    python scripts/start.py --port 8080
    python scripts/start.py --no-browser
"""
from __future__ import annotations

import argparse
import datetime
import os
import platform
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_PY = ROOT / "app.py"
LOG_DIR = ROOT / "logs"
REQUIREMENTS = ROOT / "requirements.txt"
DEFAULT_PORT = 5001

# 本次启动的日志文件路径，进入 main() 后立即确定，供全流程复用
LOGFILE = LOG_DIR / "quickform-pending.log"

# 需要校验的模块（import 名），覆盖 app.py 的全部第三方依赖
REQUIRED_MODULES = [
    "flask", "flask_login", "flask_sqlalchemy", "flask_bcrypt", "flask_babel",
    "sqlalchemy", "bcrypt", "dotenv", "pandas", "matplotlib", "requests",
    "qrcode", "PIL",
]

_PROBE = (
    "import importlib,sys\n"
    "miss=[]\n"
    "for m in {mods!r}:\n"
    "    try:\n"
    "        importlib.import_module(m)\n"
    "    except Exception as e:\n"
    "        miss.append(m+':'+type(e).__name__)\n"
    "print('MISSING='+','.join(miss))\n"
)

_PROBE_QUICK = (
    "import importlib.util,sys\n"
    "miss=[]\n"
    "for m in {mods!r}:\n"
    "    try:\n"
    "        if importlib.util.find_spec(m) is None:\n"
    "            miss.append(m+':NotFound')\n"
    "    except Exception as e:\n"
    "        miss.append(m+':'+type(e).__name__)\n"
    "print('MISSING='+','.join(miss))\n"
)

LINE = "=" * 62


# --------------------------------------------------------------------------
# 输出工具
# --------------------------------------------------------------------------
def _ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


def log(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:  # 极老的 Windows 控制台
        print(msg.encode(sys.stdout.encoding or "utf-8", "replace").decode(
            sys.stdout.encoding or "utf-8", "replace"), flush=True)


def info(msg: str) -> None:
    log(f"[{_ts()}] {msg}")


def ok(msg: str) -> None:
    log(f"[{_ts()}] [OK] {msg}")


def warn(msg: str) -> None:
    log(f"[{_ts()}] [WARN] {msg}")


def fail(msg: str) -> None:
    log(f"[{_ts()}] [ERROR] {msg}")


# --------------------------------------------------------------------------
# 平台 / 运行时
# --------------------------------------------------------------------------
def platform_key() -> str:
    s = platform.system().lower()
    m = platform.machine().lower()
    if s == "darwin":
        return "mac-arm64" if m in ("arm64", "aarch64") else "mac-x64"
    if s == "windows":
        return "win-x64"
    return f"{s}-{m}"


def wheel_dir(key: str) -> Path:
    mapping = {"mac-arm64": "macos-arm64", "mac-x64": "macos-x64", "win-x64": "win-x64"}
    return ROOT / "wheels" / mapping.get(key, key)


def bundled_python(key: str) -> Path | None:
    base = ROOT / "runtime" / key / "python"
    if key.startswith("win"):
        cands = [base / "python.exe"]
    else:
        cands = [base / "bin" / "python3", base / "bin" / "python"]
    for c in cands:
        if c.is_file():
            return c
    return None


def venv_pythons() -> list[Path]:
    out = []
    for name in (".venv", "venv", "env"):
        v = ROOT / name
        if not v.is_dir():
            continue
        if os.name == "nt":
            cands = [v / "Scripts" / "python.exe"]
        else:
            cands = [v / "bin" / "python3", v / "bin" / "python"]
        for c in cands:
            if c.is_file():
                out.append(c)
                break
    return out


def system_pythons() -> list[Path]:
    out: list[Path] = []
    runtime_root = (ROOT / "runtime").resolve()
    seen = set()

    def add(p) -> None:
        if not p:
            return
        try:
            rp = Path(p).resolve()
        except OSError:
            return
        if rp in seen or not rp.is_file():
            return
        seen.add(rp)
        try:  # 排除内置运行时自身
            rp.relative_to(runtime_root)
            return
        except ValueError:
            pass
        out.append(rp)

    for cmd in ("python3", "python", "py -3"):
        add(shutil.which(cmd.split()[0]))
    if os.name != "nt":
        for p in ("/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3"):
            add(Path(p))
    return out


def probe(py: Path, quick: bool = False) -> tuple[bool, list[str]]:
    """在候选解释器中检查依赖是否齐全，返回 (可用, 缺失列表)

    quick=True 时只用 find_spec 检查模块是否在位，不做真正的 import。
    内置运行时是打包时验证过的，用 quick 模式可省掉一次 import 开销
    （实测 flask_babel 单独导入就要 4 秒，而应用启动时还会再导入一次）。
    """
    code = _PROBE_QUICK.format(mods=REQUIRED_MODULES) if quick else _PROBE.format(mods=REQUIRED_MODULES)
    try:
        r = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                           timeout=120, cwd=str(ROOT))
    except Exception:
        return False, ["<无法执行>"]
    if r.returncode != 0:
        return False, ["<解释器不可用>"]
    line = ""
    for ln in r.stdout.splitlines():
        if ln.startswith("MISSING="):
            line = ln[len("MISSING="):].strip()
    missing = [x for x in line.split(",") if x]
    return (not missing), missing


def ver_of(py: Path) -> str:
    try:
        r = subprocess.run([str(py), "-c", "import platform;print(platform.python_version())"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or "?"
    except Exception:
        return "?"


# --------------------------------------------------------------------------
# 依赖离线安装
# --------------------------------------------------------------------------
def offline_install(py: Path, key: str) -> bool:
    wd = wheel_dir(key)
    if not wd.is_dir() or not sorted(wd.glob("*.whl")):
        fail(f"内置运行时的依赖不完整，且本包未附带离线 wheel 包（{wd} 不存在）。")
        fail("这是精简版分发包的已知情况：运行时依赖本应已预装。")
        fail("解决办法：重新解压原始压缩包；或在联网机器上运行 scripts/_build_offline.sh")
        fail("重建 wheels/ 后再启动（会离线补装，不改动 app.py 与数据）。")
        return False
    wheels = sorted(wd.glob("*.whl"))

    info(f"首次启动：正在用内置 wheel 离线安装依赖（共 {len(wheels)} 个包，约 1-2 分钟）...")
    cmd = [str(py), "-m", "pip", "install", "--no-index", "--find-links", str(wd),
           "-r", str(REQUIREMENTS), "--disable-pip-version-check",
           "--no-cache-dir", "--no-input", "--only-binary=:all:"]
    env = os.environ.copy()
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    # 实时打印 + 同时写入本次日志文件
    fh = LOGFILE.open("a", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", bufsize=1)
        for ln in iter(proc.stdout.readline, ""):
            ln = ln.rstrip("\r\n")
            if ln:
                log("  | " + ln)
                fh.write(ln + "\n")
        proc.wait()
    finally:
        fh.close()

    if proc.returncode == 0:
        ok("依赖安装完成")
        return True

    fail(f"依赖离线安装失败（退出码 {proc.returncode}），完整日志：{LOGFILE}")
    return False


# --------------------------------------------------------------------------
# 端口
# --------------------------------------------------------------------------
def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def port_holders(port: int) -> str:
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                               capture_output=True, text=True, timeout=10)
            lines = [l for l in r.stdout.splitlines()[1:] if l.strip()]
            return "\n".join("      " + l for l in lines) or "      <未能取得进程信息>"
        if os.name == "nt":
            r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                               capture_output=True, text=True, timeout=20,
                               encoding="utf-8", errors="replace")
            rows = [l.strip() for l in r.stdout.splitlines()
                    if f":{port}" in l and "LISTENING" in l.upper()]
            return "\n".join("      " + l for l in rows) or "      <未能取得进程信息>"
    except Exception:
        pass
    return "      <未能取得进程信息>"


def next_free_port(start: int) -> int:
    p = start
    while p < start + 200:
        if not port_in_use(p):
            return p
        p += 1
    return start


def tcp_ready(port: int) -> bool:
    return port_in_use(port)


def http_ok(port: int) -> bool:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/",
                                     headers={"User-Agent": "QuickForm-Launcher"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as e:
        return e.code < 500
    except Exception:
        return False


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def open_browser(url: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url])
        elif os.name == "nt":
            os.startfile(url)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", url])
        ok(f"已打开浏览器：{url}")
    except Exception as e:
        warn(f"自动打开浏览器失败（{e}），请手动访问：{url}")


# --------------------------------------------------------------------------
# 子进程输出 -> 控制台 + 日志
# --------------------------------------------------------------------------
class Tee(threading.Thread):
    def __init__(self, stream, logfile: Path):
        super().__init__(daemon=True)
        self.stream = stream
        self.logfile = logfile
        self.q: "queue.Queue[str]" = queue.Queue()
        self.lines: list[str] = []
        self._stop = threading.Event()

    def run(self) -> None:
        fh = self.logfile.open("a", encoding="utf-8", errors="replace")
        try:
            for ln in iter(self.stream.readline, ""):
                if self._stop.is_set():
                    break
                ln = ln.rstrip("\r\n")
                self.q.put(ln)
                self.lines.append(ln)
                if len(self.lines) > 500:
                    self.lines = self.lines[-500:]
                fh.write(ln + "\n")
                fh.flush()
        finally:
            try:
                fh.close()
            except Exception:
                pass

    def drain(self) -> None:
        while True:
            try:
                ln = self.q.get_nowait()
            except queue.Empty:
                return
            log("  | " + ln)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def pick_interpreter(key: str) -> Path | None:
    builtin = bundled_python(key)

    # 内置运行时排第一：它是唯一保证依赖齐全的环境，探测也最快。
    # 若排在其他候选之后，新设备上总要先把系统里的 Python 逐个真 import 一遍才淘汰，
    # 实测会白白多花 20~30 秒。
    cands: list[tuple[str, Path]] = []
    if builtin:
        cands.append(("内置运行时", builtin))
    for p in venv_pythons():
        cands.append(("项目虚拟环境", p))
    for p in system_pythons():
        if builtin and p == builtin.resolve():
            continue
        cands.append(("系统 Python", p))

    info("正在检测可用的运行环境...")
    for kind, p in cands:
        is_builtin = bool(builtin) and p == builtin
        good, missing = probe(p, quick=is_builtin)
        if good:
            if is_builtin:
                ok(f"复用 {kind}：{p}（Python {ver_of(p)}，已预装依赖）")
            else:
                ok(f"复用 {kind}：{p}（Python {ver_of(p)}）")
            return p
        log(f"  - {kind} {p} 缺少依赖：{', '.join(missing[:6])}")

    # 走到这里说明只能用内置运行时离线补包
    if builtin:
        info(f"未发现可用环境，改用内置运行时：{builtin}（Python {ver_of(builtin)}）")
        if offline_install(builtin, key):
            good, missing = probe(builtin)
            if good:
                ok("内置运行时就绪")
                return builtin
            fail(f"内置运行时仍缺少依赖：{', '.join(missing)}")
    return None


def main() -> int:
    global LOGFILE
    ap = argparse.ArgumentParser(description="QuickForm 一键启动器")
    ap.add_argument("--port", type=int, default=None, help="指定端口，默认 5001")
    ap.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    args = ap.parse_args()

    key = platform_key()
    log(LINE)
    log("  QuickForm 教师版 2.5 —— 一键启动")
    log(LINE)
    info(f"项目目录：{ROOT}")
    info(f"当前平台：{platform.platform()}  [{key}]")

    # ---------- 基础检查 ----------
    if not APP_PY.is_file():
        fail(f"未找到入口文件：{APP_PY}")
        fail("请确认脚本放在项目根目录下，且 app.py 存在。")
        return 2
    if not REQUIREMENTS.is_file():
        warn(f"未找到 {REQUIREMENTS.name}，将跳过依赖校验。")

    try:
        for d in (LOG_DIR, ROOT / "data", ROOT / "static" / "uploads",
                  ROOT / "static" / "assets"):
            if not d.is_dir():          # 先判断再创建，避免对已存在目录调用 mkdir
                d.mkdir(parents=True)
        LOGFILE = LOG_DIR / (
            "quickform-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")
        LOGFILE.write_text(
            f"# QuickForm 启动日志  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n",
            encoding="utf-8")
    except Exception as e:
        fail(f"项目目录不可写：{e}")
        fail("请把整个项目文件夹解压到有写权限的位置（如桌面 / 文稿），再双击启动。")
        return 2

    # ---------- 解释器 ----------
    py = pick_interpreter(key)
    if py is None:
        log(LINE)
        fail("启动失败：未能准备可用的 Python 运行环境。")
        if key not in ("mac-arm64", "win-x64"):
            if key == "mac-x64":
                fail("本包只内置了 Apple 芯片（M 系列）与 Windows 64 位的运行时，")
                fail("不含 Intel Mac 运行时。请在 Intel Mac 上安装 Python 3.8-3.11，")
                fail("或运行 scripts/_build_offline.sh 重新打包 mac-x64。")
            else:
                fail(f"当前平台 [{key}] 没有随项目提供内置运行时，请自行安装 Python 3.8-3.11 后重试。")
        else:
            fail(f"内置运行时缺失或不完整：{ROOT / 'runtime' / key}")
            fail("请重新完整解压项目压缩包（务必保留 runtime/ 目录）。")
        return 3

    # ---------- 端口 ----------
    port = args.port or int(os.getenv("QUICKFORM_PORT") or DEFAULT_PORT)
    if port_in_use(port):
        warn(f"端口 {port} 已被占用，占用进程信息：")
        log(port_holders(port))
        choice = "q"
        if sys.stdin and sys.stdin.isatty():
            try:
                log("")
                log("  请选择：[1] 结束占用进程并继续  [2] 换一个端口  [3] 退出")
                choice = input("  输入 1 / 2 / 3（默认 3）：").strip() or "3"
            except (EOFError, KeyboardInterrupt):
                choice = "3"
        else:
            choice = "2"
            info("当前为非交互模式，自动改用其他端口。")

        if choice == "1":
            killed = False
            if sys.platform == "darwin":
                r = subprocess.run(["lsof", "-ti", f"tcp:{port}"],
                                   capture_output=True, text=True)
                for pid in r.stdout.split():
                    subprocess.run(["kill", "-9", pid])
                    killed = True
            elif os.name == "nt":
                r = subprocess.run(["netstat", "-ano", "-p", "TCP"],
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
                for ln in r.stdout.splitlines():
                    if f":{port}" in ln and "LISTENING" in ln.upper():
                        pid = ln.split()[-1]
                        subprocess.run(["taskkill", "/F", "/PID", pid],
                                       capture_output=True)
                        killed = True
            time.sleep(1)
            if not killed or port_in_use(port):
                fail(f"无法释放端口 {port}，请手动关闭占用程序后重试。")
                return 4
            ok(f"端口 {port} 已释放")
        elif choice == "2":
            newp = next_free_port(port + 1)
            if newp == port:
                fail("未找到可用端口。")
                return 4
            warn(f"改用端口 {newp}（注意：换端口后 API 回调地址也会随之变化）")
            port = newp
        else:
            info("已取消启动。")
            return 0

    # ---------- 启动 ----------
    logfile = LOGFILE

    env = os.environ.copy()
    env.update({
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "MPLCONFIGDIR": str(ROOT / "runtime" / ".mplconfig"),
        "QUICKFORM_PORT": str(port),
        "PORT": str(port),
    })
    # 让 Python 不去读任何外部站点配置，保证离线
    env.pop("PIP_INDEX_URL", None)
    env.pop("PIP_EXTRA_INDEX_URL", None)

    mpl = ROOT / "runtime" / ".mplconfig"
    if not mpl.is_dir():
        mpl.mkdir(parents=True)

    info(f"启动命令：{py.name} app.py   （端口 {port}）")
    info(f"日志文件：{logfile}")
    log(LINE)

    try:
        proc = subprocess.Popen(
            [str(py), "-u", "app.py"],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace", bufsize=1,
        )
    except Exception as e:
        fail(f"无法启动进程：{e}")
        return 5

    tee = Tee(proc.stdout, logfile)
    tee.start()

    url = f"http://127.0.0.1:{port}/"
    lan = f"http://{local_ip()}:{port}/"
    opened = False
    deadline = time.time() + 90

    try:
        while True:
            tee.drain()
            rc = proc.poll()

            if not opened and rc is None and http_ok(port):
                opened = True
                log(LINE)
                ok("QuickForm 启动成功！")
                log(f"  本机访问：{url}")
                log(f"  局域网访问：{lan}")
                log(f"  登录账号：使用已有账号登录；默认账号 wst / quickform 登录后请立即修改")
                log(LINE)
                if not args.no_browser:
                    open_browser(url)
                info("服务运行中，关闭本窗口或按 Ctrl+C 即可停止。")
                log(LINE)

            if rc is not None:
                tee.drain()
                if not opened:
                    fail(f"服务进程已退出，退出码 {rc}。启动失败，最近日志：")
                    log(LINE)
                    for ln in tee.lines[-30:]:
                        log("  " + ln)
                    log(LINE)
                    tail_text = "\n".join(tee.lines[-60:])
                    if "ModuleNotFoundError" in tail_text or "ImportError" in tail_text:
                        fail("检测到依赖缺失/损坏：内置运行时可能被改动或解压不完整。")
                        fail("解决办法：重新解压原始压缩包；或在联网机器上运行")
                        fail("scripts/_build_offline.sh 重新生成 runtime/ 后再启动。")
                    fail(f"完整日志：{logfile}")
                else:
                    info(f"服务已停止（退出码 {rc}）。完整日志：{logfile}")
                return rc if rc else 0

            if not opened and time.time() > deadline:
                fail("等待 90 秒后服务仍未响应，启动超时。最近日志：")
                for ln in tee.lines[-30:]:
                    log("  " + ln)
                fail(f"完整日志：{logfile}")
                proc.terminate()
                return 6

            time.sleep(0.4)
    except KeyboardInterrupt:
        log("")
        info("收到 Ctrl+C，正在停止服务...")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        tee.drain()
        info(f"已停止。完整日志：{logfile}")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # 兜底，避免窗口一闪而过
        fail(f"启动器异常：{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(9)
