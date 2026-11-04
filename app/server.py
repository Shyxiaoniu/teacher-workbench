#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 主服务（仅标准库）

一个进程、一个端口，同时是：
  · 教师端页面（/teacher/，沿用原 3 万行单文件，数据层接上本服务）
  · 学生端页面（/s/，只读投影 + 白名单写入）
  · 同步服务（/api/v2/sync，rev 游标增量）
  · 实时通道（/api/v2/events，SSE 只推版本号）
  · 旧接口兼容（/api/enh、/api/cw、/api/submit 交给原 submission-system）

启动：python server.py --port 8080
"""
import os
import re
import sys
import json
import base64
import time
import shutil
import zipfile
import threading
import urllib.parse
import urllib.request
import urllib.error
import importlib.util
import html as _html
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# 输出被重定向到管道/文件时（launch.py 用 PIPE 拉起本进程），Windows 默认按
# GBK 编码，打印 ✓/▲ 等符号会 UnicodeEncodeError。统一强制 UTF-8 + 容错替换。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, os.path.join(ROOT, "submission-system"))

import auth as A                       # noqa: E402
import pages as P                      # noqa: E402
import projection as PJ                # noqa: E402
import ports as PORTX                  # noqa: E402
from store import Store, TABLES, ImportRefused   # noqa: E402
from activity import ActivityStore, parse_multipart   # noqa: E402  课堂活动（自研）
from typing_store import TypingStore   # noqa: E402
from updater import Updater            # noqa: E402  在线更新（检查/下载/安装/回滚）
import ooxml_preview as OOX            # noqa: E402  课件在线预览（纯 Python 解析 OOXML）

# 数据目录：默认与程序同级的 data/（不设环境变量时）；
# 容器部署时用 WB_DATA_DIR 指到挂载卷，两者行为一致。
DATA_DIR = os.environ.get("WB_DATA_DIR") or os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "workbench.db")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
UPLOAD_DIR = os.path.join(DATA_DIR, "files")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------------------------------------------------------------------------
# 旧服务：静态托管 + 学生提交 / 课件接口，原样复用
# ---------------------------------------------------------------------------
LEGACY_DIR = os.path.join(ROOT, "submission-system")
_spec = importlib.util.spec_from_file_location("legacy_submit", os.path.join(LEGACY_DIR, "server.py"))
LEG = importlib.util.module_from_spec(_spec)
sys.modules["legacy_submit"] = LEG
_spec.loader.exec_module(LEG)
LEG.STATIC_ROOT = ROOT

PORT = PORTX.DEFAULT_PORT
STORE = Store(DB_PATH)
# 课堂活动（自研）：活动/提交/附件落自己的表，附件落
# <数据目录>/activity/<活动号>/，附件 URL 用 /static/uploads/…，
# 前端大屏、课件库归档、自定义页面都按这个前缀取。
ACT = ActivityStore(DB_PATH, os.path.join(DATA_DIR, "activity"))
CFG = A.load_config(CONFIG_PATH)
SESS = A.Sessions()
# 打字冒险岛：独立成绩存储 + 配置缺省（教师开放开关 / 年级词库范围）
TYPING = TypingStore(os.path.join(DATA_DIR, "typing.json"))
if not isinstance(CFG.get("typing"), dict):
    CFG["typing"] = {}
CFG["typing"].setdefault("enabled", False)
CFG["typing"].setdefault("grades", [])
# 课堂活动配置（提交有效时长等；后端只有自研一种，不再有开关）
if not isinstance(CFG.get("activity"), dict):
    CFG["activity"] = {}
# 在线更新配置（2026-11-04）：更新源地址 / 访问令牌 / 自动检查
if not isinstance(CFG.get("update"), dict):
    CFG["update"] = {}
CFG["update"].setdefault("url", "")            # 空 = 默认更新源（Gitee raw version.json）
CFG["update"].setdefault("token", "")          # 私有仓库的访问令牌
CFG["update"].setdefault("auto", True)         # 后台定时检查
CFG["update"].setdefault("interval_hours", 24) # 检查间隔（小时）
PET_IDS = ["cat", "dog", "rabbit", "penguin", "panda", "dino", "unicorn", "dragon"]
COND = threading.Condition()
STARTED = time.time()
VERSION = "2.0"
ONLINE_WINDOW = 300      # 5 分钟内活跃算在线
# 可内联直出的附件类型（其余一律按下载处理，防止同源执行脚本）
_INLINE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "pdf",
                "mp4", "webm", "ogg", "mp3", "wav", "m4a", "txt"}
# 在线更新器（2026-11-04）：状态/下载/备份放数据目录；ROOT 即 app 代码目录
UPD = Updater(DATA_DIR, VERSION, ROOT)


def _update_auto_loop():
    """后台定时检查更新：启动后 25 秒查一次，之后按配置间隔循环。
    任何失败都只记状态不打日志刷屏——离线环境是常态，不烦老师。"""
    time.sleep(25)
    while True:
        try:
            uc = dict(CFG.get("update") or {})
            if uc.get("auto", True):
                UPD.check(uc)
        except Exception:
            pass
        try:
            hrs = max(1, int((CFG.get("update") or {}).get("interval_hours") or 24))
        except Exception:
            hrs = 24
        time.sleep(hrs * 3600)


threading.Thread(target=_update_auto_loop, daemon=True, name="update-check").start()

# SSE 长连接上限（每个连接一个线程）
_SSE_MAX = 200
_SSE_N = [0]
_SSE_LOCK = threading.Lock()


def _sse_enter():
    with _SSE_LOCK:
        if _SSE_N[0] >= _SSE_MAX:
            return False
        _SSE_N[0] += 1
        return True


def _sse_leave():
    with _SSE_LOCK:
        _SSE_N[0] = max(0, _SSE_N[0] - 1)

# 配置（模块开关 / 可见字段 / 账号密码）改动时自增：SSE 靠它把“设置变了”
# 推给所有在线页面。数据改动走 STORE 的 rev，两套互不干扰。
CFG_VER = [1]

# 密保验证通过后发的一次性票据：token -> {exp, user}
QA_TICKETS = {}
QA_LOCK = threading.Lock()

BASE_HTML_HEAD = '<base href="/">'


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def lan_ips():
    return LEG.lan_ips()


def best_ip():
    ips = lan_ips()
    return ips[0] if ips else "127.0.0.1"


# --- 对外访问地址 -----------------------------------------------------------
# 二维码 / 学生端地址要写进 HTML 和图片里，必须是「学生手机能打开的绝对地址」。
# 以前用 best_ip() + 进程端口拼接，在 Docker 里会出错：
#   · best_ip() 拿到的是容器网卡地址（172.x.x.x），学生根本连不上
#   · 端口是容器内端口，compose 里改了宿主机端口后对不上
# 现在改成按优先级推导，改端口/换部署方式都不用再手工维护：
#   1. WB_PUBLIC_URL 环境变量（反向代理、固定域名、或用 localhost 打开但学生要走局域网）
#   2. X-Forwarded-Host / X-Forwarded-Proto（nginx、Caddy 等反向代理）
#   3. 本次请求的 Host 头 —— 老师用什么地址打开，学生就用什么地址，
#      浏览器访问的本来就是「宿主机IP:宿主机端口」，天然跟着 compose 走
#   4. 兜底：主机网卡 IP + 进程端口（不经网关、直接访问容器时用）
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1", "127.0.1.1")


def _safe_host(raw):
    """Host 头只允许域名/IP/端口字符，避免把奇怪内容写进二维码。"""
    h = (raw or "").strip()
    if not h or len(h) > 128:
        return ""
    for ch in h:
        if not (ch.isascii() and (ch.isalnum() or ch in ".-:[]_")):
            return ""
    return h


def _host_only(hostport):
    """从 host[:port] 里取出主机名，兼容 [::1]:8080 这种 IPv6 写法。"""
    h = hostport.strip()
    if h.startswith("["):
        return h.split("]")[0].lstrip("[").lower()
    return h.rsplit(":", 1)[0].lower() if h.count(":") == 1 else h.lower()


def public_base(headers=None):
    """对外访问根地址，形如 http://192.168.x.x:20020（不带结尾斜杠）。"""
    env = (os.environ.get("WB_PUBLIC_URL") or "").strip()
    if env:
        low = env.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            env = "http://" + env
        return env.rstrip("/")

    if headers is not None:
        fwd = _safe_host((headers.get("X-Forwarded-Host") or "").split(",")[0])
        if fwd:
            proto = (headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip().lower()
            return "%s://%s" % ("https" if proto == "https" else "http", fwd)
        host = _safe_host(headers.get("Host"))
        if host and _host_only(host) not in _LOOPBACK_HOSTS:
            return "http://" + host

    return "http://%s:%d" % (best_ip(), _port())


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _is_private_ip(ip):
    """回环 / 内网地址判定（用于「首次设置账号」这种只该在本地完成的操作）。"""
    import ipaddress
    try:
        a = ipaddress.ip_address(str(ip or "").strip())
    except Exception:
        return False
    return bool(a.is_loopback or a.is_private or a.is_link_local)


def today_str():
    return time.strftime("%Y-%m-%d")


def notify():
    """任何写操作后唤醒所有 SSE 连接"""
    with COND:
        COND.notify_all()


def notify_cfg():
    """配置类改动（开关 / 可见字段 / 账号密码 / 密保）：推一次设置变更。

    这些不改业务数据表，所以不会产生新 rev；只唤 SSE 的话客户端会以为
    “没什么新东西”而不刷新。额外带一个 cfg 版本号，各端看到就重取设置。
    """
    CFG_VER[0] += 1
    notify()


def cfg_save():
    A.save_config(CONFIG_PATH, CFG)


def student_row(sid):
    for r in STORE.table_rows("students"):
        if str(r.get("id")) == str(sid):
            return r
    return None


# ----------------------------------------------------------------------
# 座位表配置（2026-11-03）
# 教师端的「行列数 / 排布方式 / 起点 / 方向 / 讲台方位」原本只落在浏览器
# localStorage（键 wb-seat-cfg-<班>），学生端一行都读不到，座位表就无从
# 「同步老师那张图」。这里按班级存进服务端设置（config.json 的 seatmap），
# 教师端保存时推上来，学生端只读。
# ----------------------------------------------------------------------
SEAT_CFG_KEYS = ("rows", "cols", "order", "start", "snakeDir", "stage")


def _seat_i(v, default=0):
    """座位/行列相关的数字：脏数据（''、None、'7排'）一律退回 default"""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _seat_cfg_norm(raw):
    """把外部传进来的配置收敛成合法值（越界/乱码一律不落盘）"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    n = _seat_i(raw.get("rows"))
    if n > 0:
        out["rows"] = max(1, min(16, n))
    n = _seat_i(raw.get("cols"))
    if n > 0:
        out["cols"] = max(1, min(16, n))
    if raw.get("order") in ("row", "col", "snake"):
        out["order"] = raw["order"]
    if raw.get("start") in ("front", "back"):
        out["start"] = raw["start"]
    if raw.get("snakeDir") in ("left", "right"):
        out["snakeDir"] = raw["snakeDir"]
    if raw.get("stage") in ("top", "bottom", "left", "right"):
        out["stage"] = raw["stage"]
    if raw.get("updated"):
        out["updated"] = str(raw["updated"])[:32]
    return out


def _tip_page(title, msg):
    """导学单闸门遇到异常时给学生看的一页（不抛裸 JSON，学生看不懂）"""
    esc = lambda t: (str(t or "").replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;").replace('"', "&quot;"))
    return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>" + esc(title) + "</title><style>"
            "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
            "background:#F5F7F4;font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;"
            "color:#22312A;padding:24px;box-sizing:border-box}"
            ".c{max-width:420px;background:#fff;border-radius:16px;padding:28px 26px;"
            "box-shadow:0 10px 34px rgba(24,52,42,.10);text-align:center}"
            "h1{font-size:19px;margin:0 0 10px;color:#0F6E56}"
            "p{font-size:14px;line-height:1.8;color:#5B6A60;margin:0 0 18px}"
            "a{display:inline-block;background:#0F6E56;color:#fff;text-decoration:none;"
            "padding:10px 22px;border-radius:10px;font-size:14px}"
            "</style></head><body><div class=\"c\"><h1>" + esc(title) + "</h1><p>" + esc(msg)
            + "</p><a href=\"/s/\">返回学生端</a></div></body></html>")


def security_of():
    """教师密保：{question, answer_hash}。老配置里没有这个键，按“未设置”处理。"""
    sec = (CFG.get("teacher") or {}).get("security")
    if not isinstance(sec, dict):
        sec = {}
    return {"question": str(sec.get("question") or ""),
            "answer_hash": str(sec.get("answer_hash") or "")}


def has_qa():
    s = security_of()
    return bool(s["question"] and s["answer_hash"])


def new_ticket():
    import secrets as _s
    tok = _s.token_urlsafe(24)
    with QA_LOCK:
        now = time.time()
        for k in [k for k, v in QA_TICKETS.items() if v["exp"] < now]:
            QA_TICKETS.pop(k, None)
        QA_TICKETS[tok] = {"exp": now + 600}
    return tok


def take_ticket(tok):
    with QA_LOCK:
        v = QA_TICKETS.pop(str(tok or ""), None)
    if not v or v["exp"] < time.time():
        return False
    return True


# ---------------------------------------------------------------------------
# 导学单「闸门票据」
# ---------------------------------------------------------------------------
# 学生端点开导学单条目时，由本服务签发一张票：票里记住（学生、导学单、条目、
# 任务号）。交给页面的是票，不是身份本身 —— 页面就算被人改 URL 也换不了别人。
# 票据放内存：重启即失效，有效期默认 12 小时（够一堂课 + 课后补交）。
GTICKETS = {}
GT_LOCK = threading.Lock()


def _gt_ttl():
    try:
        return int(float((CFG.get("activity") or {}).get("submit_hours", 12)) * 3600)
    except Exception:
        return 12 * 3600


def new_gticket(payload):
    """签一张导学单票据，返回 token"""
    import secrets as _s
    tok = _s.token_urlsafe(20)
    now = time.time()
    with GT_LOCK:
        # 顺手清过期的，避免常开的机器越积越多
        for k in [k for k, v in GTICKETS.items() if v["exp"] < now]:
            GTICKETS.pop(k, None)
        GTICKETS[tok] = dict(payload, exp=now + _gt_ttl())
    return tok


def get_gticket(tok):
    """验票：过期/不存在都返回 None"""
    with GT_LOCK:
        v = GTICKETS.get(str(tok or ""))
    if not v or v["exp"] < time.time():
        return None
    return v


# ---------------------------------------------------------------------------
# 课件在线预览：Office 文档（PPT/Word/Excel）在服务端渲染成 HTML 后内联
# ---------------------------------------------------------------------------
# 机房学生机无外网，浏览器又读不了 Office 二进制格式，所以由服务端把文档内容
# 直接渲染成 HTML 给学生看：.pptx/.docx/.xlsx 走 core/ooxml_preview.py 纯 Python
# 解析 OOXML，不依赖任何外部程序、不需要联网。渲染结果按「文件名+大小+修改时间」
# 缓存成 .html，第二次打开是秒开。
# 为什么不用「WPS 转 PDF」：本机 WPS 未注册 COM 自动化组件（没有
# KWPP.Application 等 ProgID），clitool\\wpscli.exe 也只是启动器、不含
# ppt2pdf 之类子命令，pywin32 与 LibreOffice 都不在，外部转换这条路走不通（2026-11-02 实测）。
# 老式 .doc/.ppt/.xls 是二进制格式，纯 Python 解析不了，给出「另存为新格式」提示。
_OOXML_EXTS = ("pptx", "docx", "xlsx")
_LEGACY_EXTS = ("doc", "ppt", "xls")
_OFFICE_EXTS = _OOXML_EXTS + _LEGACY_EXTS
_OFFICE_LOCK = threading.Lock()  # 同一份文档只让一个线程渲染（有缓存，之后都是秒回）


def _ext_of(name):
    return os.path.splitext(str(name or ""))[1].lstrip(".").lower()


def _safe_name(name):
    s = str(name or "").replace("\\", "/").split("/")[-1]
    s = re.sub(r'[\x00-\x1f<>:"|?*]', "", s).strip().strip(".")
    return s or "未命名"


def _preview_cache_dir():
    """预览缓存目录（按需创建），返回 (dir, err)。"""
    d = os.path.join(DATA_DIR, "preview_cache")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return None, "缓存目录不可写"
    return d, None


def _office_to_html(src):
    """把 Office 文档渲染成 HTML，返回 (html_str, error_str)。

    渲染失败时 error_str 非空，调用方据此回退到「下载原文件」提示，
    绝不让学生端看到 500。"""
    ext = _ext_of(src)
    if ext in _LEGACY_EXTS:
        return None, ("老式 .%s 是二进制格式，服务端没法在线渲染。"
                      "用 WPS 打开后「另存为」.%sx 再上传，就能在线预览了。" % (ext, ext))
    cache, err = _preview_cache_dir()
    if not cache:
        return None, err
    try:
        st = os.stat(src)
        # 缓存键带「大小+修改时间」：老师换文件会自动重新渲染
        key = "%s_%d_%d" % (_safe_name(os.path.basename(src)), st.st_size, int(st.st_mtime))
    except Exception:
        return None, "源文件不可读"
    out_html = os.path.join(cache, key + ".html")

    def _read():
        try:
            with open(out_html, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            return None

    def _fresh():
        return os.path.isfile(out_html) and os.path.getmtime(out_html) >= st.st_mtime

    if _fresh():
        cached = _read()
        if cached:
            return cached, None
    with _OFFICE_LOCK:
        if _fresh():
            cached = _read()
            if cached:
                return cached, None
        try:
            html = OOX.render(src)
        except Exception as e:
            msg = str(e)[:80] or "格式异常"
            return None, "解析这份文档时出错（%s）" % msg
        if not html:
            return None, "这份文档里没有可提取的内容"
        try:
            tmp = out_html + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(html)
            os.replace(tmp, out_html)
        except Exception:
            pass
        return html, None


def _preview_fallback_html(title, fid, reason):
    """Office 文档渲染不出来时的回退页（iframe 内显示）。"""
    href = "/cw/" + urllib.parse.quote(fid)
    return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>暂不支持在线预览</title><style>"
            "body{font-family:system-ui,\"Microsoft YaHei\",sans-serif;background:#fff;color:#223;"
            "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}"
            ".b{max-width:440px;text-align:center;padding:28px;line-height:1.7}"
            ".b .ic{font-size:40px;margin-bottom:6px}"
            ".b h2{font-size:18px;margin:6px 0 10px;color:#1a2}"
            ".b p{color:#667;font-size:14px;margin:6px 0}"
            ".b code{background:#f1f5f3;padding:1px 6px;border-radius:6px;font-size:13px}"
            ".b a{display:inline-block;margin-top:16px;background:#0F6E56;color:#fff;"
            "padding:10px 20px;border-radius:10px;text-decoration:none;font-size:14px}"
            "</style></head><body><div class=\"b\"><div class=\"ic\">📄</div>"
            "<h2>这个文件暂时无法在线预览</h2>"
            "<p>「" + _html.escape(title or "文件", quote=True) + "」暂时没法在线预览，可以下载到本机用 WPS 打开。</p>"
            "<p>" + _html.escape(reason or "这个格式暂时还不支持在线预览。", quote=True) + "</p>"
            "<a href=\"" + _html.escape(href, quote=True) + "\" target=\"_blank\" rel=\"noopener\">下载原文件</a>"
            "</div></body></html>")


class Handler(LEG.Handler):
    server_version = "Workbench/" + VERSION

    # ------------------------------------------------------------------
    # 基础响应
    # ------------------------------------------------------------------
    def end_headers(self):
        """统一补安全响应头。

        · nosniff：防止浏览器把 .txt 当 HTML/JS 执行；
        · SAMEORIGIN：本应用的页面不该被别的站点套 iframe（点击劫持）；
        · Referrer-Policy：跳外链时不把带 session 的 URL 带出去；
        · Permissions-Policy：本应用不用摄像头/麦克风/定位。
        """
        if not getattr(self, "_sec_sent", False):
            self._sec_sent = True
            try:
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "SAMEORIGIN")
                self.send_header("Referrer-Policy", "same-origin")
                self.send_header("Permissions-Policy",
                                 "geolocation=(), microphone=(), camera=()")
            except Exception:
                pass
        BaseHTTPRequestHandler.end_headers(self)

    def _json(self, obj, code=200):
        self._sec_sent = False
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # 全量恢复的请求体要带学生作品附件（base64），远大于通用 JSON 上限
    MAX_IMPORT_BODY = 96 * 1024 * 1024

    def _read_json(self):
        """按路径决定请求体上限：导入接口放宽，其余沿用通用限制。

        通用上限是 32MB，而全量备份里的附件 base64 后很容易超过它——
        不放开的话「一键恢复」会在带附件时莫名 413，用户只看到「失败」。
        """
        path = urllib.parse.urlparse(self.path).path
        if path != "/api/v2/admin/import":
            return LEG.Handler._read_json(self)
        try:
            raw = self._read_body(self.MAX_IMPORT_BODY)
        except ValueError as e:
            self._json({"ok": False, "code": "too_large",
                        "msg": "备份文件太大，服务端装不下这次恢复（%s）。"
                               "可先清理过大的学生作品附件后重新导出。" % e}, 413)
            return None
        if not raw:
            return {}
        try:
            d = json.loads(raw.decode("utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            self._json({"ok": False, "code": "bad_json",
                        "msg": "备份内容不是有效的 JSON（文件可能损坏或没下载完整）。"}, 400)
            return None

    def _download(self, body, filename, ctype):
        """带下载头的二进制响应（导出 CSV/JSON 用，中文名走 RFC 5987）。"""
        self._sec_sent = False
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"; filename*=UTF-8\'\'%s'
                         % (filename, urllib.parse.quote(filename)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _html(self, html, code=200, csp=True):
        self._sec_sent = False
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if csp:
            # 服务端渲染的登录 / 二维码 / 找回密码页只用同源资源与内联脚本，
            # 可以直接上严格一点的 CSP（教师端 3 万行单文件不适用，见 README）
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                             "connect-src 'self'; form-action 'self'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url, cookie=None):
        self._sec_sent = False
        self.send_response(302)
        self.send_header("Location", url)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _set_cookie(self, token, hours):
        # hours 是小时数（不是秒）：Max-Age 必须是 hours*3600。
        # 之前调用方传的是 12*3600，等于把 cookie 续了 4.9 年，
        # 服务端会话早就过期了、浏览器还留着，学生端就会“莫名跳登录页”。
        # Secure：只有经 HTTPS（含反向代理）访问时才加，
        # 否则纯 HTTP 的教室局域网会直接登录不了。
        secure = ""
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        if proto == "https":
            secure = "; Secure"
        return ("wb_sid=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax%s"
                % (token, int(float(hours) * 3600), secure))

    def _session(self):
        return SESS.get(A.parse_cookie(self.headers.get("Cookie")))

    def _is_teacher(self):
        s = self._session()
        return bool(s and s["role"] == "teacher")

    def _same_origin(self):
        """跨站提交防护：有 Origin 就必须同源（服务端不知道自己的域名，
        所以只比对 Host 部分）"""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        try:
            return urllib.parse.urlparse(origin).netloc == host
        except Exception:
            return False

    def _setup_allowed(self):
        """首次创建管理员账号的窗口限制。

        三条同时满足才允许：
          · 账号还没设置（调用方已判断）；
          · 进程启动 24 小时内，避免一个长期空转的实例一直敞着注册口；
          · 请求来自回环或内网地址，公网陌生人不能抢先注册。
        在 NAS/服务器上从别的机器打开也属于内网，正常流程不受影响。
        实在要从公网初始化，可以设环境变量 WB_ALLOW_REMOTE_SETUP=1。
        """
        if os.environ.get("WB_ALLOW_REMOTE_SETUP") == "1":
            return True, ""
        if time.time() - STARTED > 24 * 3600:
            return False, ("首次设置窗口已过（服务启动超过 24 小时）。"
                           "请重启一次服务（docker compose restart workbench），再打开页面完成设置。")
        ip = A.client_ip(self)
        if _is_private_ip(ip):
            return True, ""
        return False, ("首次设置只允许在局域网内完成。"
                       "请用同一内网的电脑或手机打开页面创建账号。")

    # ------------------------------------------------------------------
    # 课堂活动接口：/api/act/*（自研）
    # ------------------------------------------------------------------
    # 形状沿用原来的活动数据服务契约，前端与自定义页面不用改：
    #   GET  /api/act/tasks            活动列表
    #   GET  /api/act/<活动号>[/all]    全量提交（带 学号/姓名/班级/附件）
    #   GET  /api/act/<活动号>/count    提交条数
    #   POST /api/act/<活动号>         匿名提交（老师自设计页面直连用）
    #   GET  /api/act/static/uploads/…  附件（也可直接用同源的 /static/uploads/…）
    def _act_api(self, path, query="", body=None):
        # 统一入口：do_GET / do_POST 都把 /api/act* 交给这里
        return self._act_local(path, query, body)

    def _act_task_json(self, t):
        """把内部活动行翻译成 /api/act/tasks 的形状（api 给相对路径）。"""
        return {"task_id": t["task_id"], "title": t["title"],
                "description": t.get("description") or "",
                "count": t.get("count", 0),
                "created_at": t.get("created_at") or "",
                "api": "/api/act/" + urllib.parse.quote(t["task_id"], safe="")}

    def _act_local(self, path, query="", body=None):
        sub = path[len("/api/act"):] or "/"
        method_post = body is not None

        # 附件：/api/act/static/uploads/<活动>/<文件>（也可直接用 /static/uploads/…）
        if sub.startswith("/static/uploads/"):
            return self._act_file(path)

        # 活动列表
        if sub == "/tasks":
            tasks = [self._act_task_json(t) for t in ACT.tasks()]
            return self._json_cors_success({"success": True, "tasks": tasks})

        if sub.startswith("/"):
            rest = sub[1:].strip("/")
            if not rest:
                return self._json_cors({"ok": False, "msg": "缺少活动号"}, 400)
            parts = rest.split("/")
            quoted = urllib.parse.unquote(parts[0])
            if "/" in quoted or ".." in quoted:
                return self._json_cors({"ok": False, "msg": "活动号不合法"}, 400)
            task = ACT.get_task(quoted)
            if not task:
                return self._json_cors({"error": "任务不存在", "ok": False,
                                        "msg": "任务不存在"}, 404)
            action = parts[1] if len(parts) > 1 else ""
            if action == "count" and not method_post:
                return self._json_cors_success({"total_submissions": ACT.count(quoted),
                                                "task_id": quoted})
            if action in ("all", "") and not method_post:
                subs = ACT.submissions(quoted)
                return self._json_cors_success({
                    "submissions": subs, "total_submissions": len(subs),
                    "task_id": quoted, "task_title": task["title"]})
            if action == "" and method_post:
                return self._act_api_submit(quoted, task, body)
            return self._json_cors({"ok": False, "msg": "不支持的接口"}, 404)

        return self._json_cors({"ok": False, "msg": "不支持的接口"}, 404)

    def _act_api_submit(self, task_key, task, raw):
        """匿名提交口（/api/act/<活动号> POST）。

        学生正常走 /api/v2/student/submit（带票、身份由服务端补）；
        这个口留给老师自己设计的页面直连，身份就按提交里带的学号/姓名认。

        raw 由调用方读好再传进来：do_POST 已经把请求体消费掉了，
        这里再 read 一次会一直等剩余字节（表现为浏览器转圈、请求超时）。
        """
        ctype = (self.headers.get("Content-Type") or "").lower()
        if not raw:
            return self._json_cors({"ok": False, "msg": "提交内容为空"}, 400)
        fields, files = {}, []
        if ctype.startswith("multipart/form-data"):
            fields, files = parse_multipart(raw, self.headers.get("Content-Type") or "")
        elif "json" in ctype:
            try:
                obj = json.loads(raw.decode("utf-8") or "{}")
                fields = obj if isinstance(obj, dict) else {"data": obj}
            except Exception:
                return self._json_cors({"ok": False, "msg": "提交内容不是合法 JSON"}, 400)
        else:
            try:
                fields = dict(urllib.parse.parse_qsl(raw.decode("utf-8", "replace"),
                                                     keep_blank_values=True))
            except Exception:
                fields = {"data": raw.decode("utf-8", "replace")[:5000]}
        try:
            res = ACT.add_submission(task_key, fields, files,
                                     ip=self._client_ip_safe())
        except ValueError as e:
            return self._json_cors({"ok": False, "message": str(e)}, 413)
        except LookupError:
            return self._json_cors({"ok": False, "message": "任务不存在"}, 404)
        return self._json_cors_success({"message": "提交成功",
                                        "id": res["id"],
                                        "attachment": res.get("attachment")})

    def _client_ip_safe(self):
        try:
            return A.client_ip(self)
        except Exception:
            return ""

    def _json_cors_success(self, obj, code=200):
        return self._json_cors(obj, code)

    def _act_file(self, path):
        """发附件。图片/PDF/音视频内联，其余强制下载，避免同源执行脚本。"""
        p = ACT.file_path(path)
        if not p:
            return self._json({"ok": False, "msg": "文件不存在"}, 404)
        ext = os.path.splitext(p)[1].lstrip(".").lower()
        import mimetypes
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError:
            return self._json({"ok": False, "msg": "文件读取失败"}, 500)
        disp = "inline" if ext in _INLINE_EXTS else "attachment"
        self._sec_sent = False
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         '%s; filename="%s"' % (disp, os.path.basename(p)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "private, max-age=3600")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _act_upload(self):
        """活动教学资料上传（multipart）。教师专用，单文件 40MB。"""
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        q2 = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        task = str((q2.get("task") or [""])[0] or "").strip()
        if not ACT.get_task(task):
            return self._json({"ok": False, "msg": "活动不存在"}, 404)
        ctype = self.headers.get("Content-Type") or ""
        if not ctype.lower().startswith("multipart/form-data"):
            return self._json({"ok": False, "msg": "请用表单上传文件"}, 400)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return self._json({"ok": False, "msg": "没有收到内容"}, 400)
        if length > 40 * 1024 * 1024:
            return self._json({"ok": False, "msg": "文件太大（单文件上限 40MB）"}, 413)
        raw = self.rfile.read(length)
        _fields, files = parse_multipart(raw, ctype)
        if not files:
            return self._json({"ok": False, "msg": "没有收到文件"}, 400)
        saved = []
        for f in files[:10]:
            try:
                saved.append(ACT.add_attachment(task, f.get("name"),
                                                f.get("data") or b"", f.get("mime")))
            except ValueError as e:
                return self._json({"ok": False, "msg": str(e)}, 413)
        STORE.audit("teacher", "activity-upload",
                    "%s 上传 %d 个资料" % (task, len(saved)))
        return self._json({"ok": True, "files": saved,
                           "attachments": ACT.attachments(task)})

    def do_GET(self):
        self._sec_sent = False
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)

        try:
            if path.startswith("/api/v2/"):
                return self._v2_get(path, qs)
            if path in ("/", "/index.html"):
                return self._entry()
            # 站点图标：Windows「新建 → 快捷方式」填网址后，系统只会去取根目录的
            # /favicon.ico；没有它，桌面上就是一个白纸图标。学生端页面另有自己的
            # /s/favicon.ico（_student_static 直接发），两边互不影响。
            if path in ("/favicon.ico", "/apple-touch-icon.png"):
                return self._site_icon(path)
            if path in ("/teacher", "/teacher/"):
                return self._teacher_page()
            if path == "/teacher/login":
                return self._teacher_login_page()
            if path == "/teacher/forgot":
                if not CFG["teacher"].get("password_hash"):
                    return self._redirect("/teacher/login")
                return self._html(P.forgot_page())
            if path == "/teacher/console":
                if not self._is_teacher():
                    return self._redirect("/teacher/login")
                return self._html(P.console_page())
            # 「/join 扫码进入班级」整页已删除（2026-11-01 牛哥：不要扫码功能）。
            # 旧二维码/收藏还指着它 → 302 到学生端首页，别让人看到 404。
            if path == "/join":
                return self._redirect("/s/")
            if path == "/g" or path.startswith("/g/"):
                return self._guide_gate(path)
            # 课件下发闸门：学生在导学单「课件库」里打开老师下发的课件（2026-11-01）
            if path == "/cw" or path.startswith("/cw/"):
                return self._courseware_gate(path)
            # 课件在线预览闸门：图片/音视频/PDF 原样内联，Office 经 WPS 转 PDF 内联（2026-11-02）
            if path == "/cwpv" or path.startswith("/cwpv/"):
                return self._courseware_preview_gate(path)
            if path == "/s":
                return self._redirect("/s/")
            if path.startswith("/s/"):
                return self._student_static(path[3:])
            # 打字冒险岛：静态页面（页面内容无敏感信息，公开可开；
            # 成绩与配置一律走 /api/v2/typing/* 鉴权接口）
            if path == "/typing":
                return self._redirect("/typing/")
            if path.startswith("/typing/"):
                return self._typing_static(path[len("/typing/"):])
            # 课堂活动接口：/api/act/*（自研）
            if path.startswith("/api/act/") or path == "/api/act":
                return self._act_api(path, u.query)
            # 课堂活动附件：URL 就是 /static/uploads/<活动>/<文件>，
            # 大屏、课件库归档、自定义页面都按这个前缀取，所以必须可访问。
            if path.startswith("/static/uploads/"):
                return self._act_file(path)
            if path.startswith("/teacher/"):
                return self._teacher_static(path[len("/teacher/"):])
            # 学习单开关：活动被「关闭」后，非教师直接打开学生页会被拦下；
            # 学生端仍能看到条目（projection 会下发 closed 行），只是点开时打不开。
            if path == "/submit/custom.html" and not self._is_teacher():
                q2 = urllib.parse.parse_qs(u.query)
                _task = (q2.get("task") or [""])[0]
                _cname = (q2.get("class") or [""])[0]
                if _task and _cname:
                    _cls = next((c for c in STORE.table_rows("classes")
                                 if not c.get("deleted")
                                 and str(c.get("name") or "") == _cname), None)
                    if _cls:
                        _hit = next((p for p in STORE.table_rows("activity_pushes")
                                     if not p.get("deleted")
                                     and str(p.get("task_id")) == _task
                                     and str(p.get("class_id")) == str(_cls.get("id"))), None)
                        if _hit and str(_hit.get("status") or "published") == "closed":
                            return self._html(_tip_page(
                                "这份学习单已关闭",
                                "老师暂时关闭了这份学习单，等老师重新开启后再打开。"), 403)
            # 其余交给旧服务（/vendor /icons /submit /api/enh /api/cw ...）
            return super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            # 只把异常写进服务端日志；回给浏览器的是一句固定的话。
            # 以前直接把异常字符串拼进响应，等于把文件路径、SQL、变量名
            # 这些内部细节送给任何能触发错误的人。
            eid = "%08x" % (int(time.time() * 1000) & 0xFFFFFFFF)
            sys.stderr.write("[get] %s -> [%s] %r\n" % (path, eid, e))
            try:
                self._json({"ok": False, "msg": "服务端出错了（错误号 %s），请查看服务端日志（docker compose logs workbench）" % eid}, 500)
            except Exception:
                pass

    def _entry(self):
        ua = (self.headers.get("User-Agent") or "").lower()
        mobile = any(k in ua for k in ("iphone", "android", "mobile", "ipad", "harmony"))
        return self._redirect("/s/" if mobile else "/teacher/")

    def _teacher_page(self):
        if not CFG["teacher"].get("password_hash"):
            return self._redirect("/teacher/login")
        if not self._is_teacher():
            return self._redirect("/teacher/login")
        full = os.path.join(ROOT, "index.html")
        try:
            with open(full, "rb") as f:
                body = f.read()
        except Exception as e:
            sys.stderr.write("[teacher] 读取 index.html 失败: %r\n" % (e,))
            return self._html("<h1>无法读取教师端页面</h1>"
                              "<p>部署包可能不完整，请把整个文件夹重新拷贝一次。</p>", 500)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _teacher_login_page(self):
        if not CFG["teacher"].get("password_hash"):
            return self._html(P.login_page_setup())
        return self._html(P.login_page())

    def _teacher_static(self, rel):
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        # 教师端静态资源只有登录后才给（vendor / icons / submit-enh.js 等）
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "unauthorized"}, 401)
        # 走与旧服务同一套白名单：教师端只需要页面里引用得到的那几个目录，
        # 不该顺手把 /teacher/data/config.json 这种也发出去（里面有口令哈希）。
        if LEG._forbidden(rel):
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not full.startswith(os.path.normpath(ROOT)):
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        if not os.path.isfile(full):
            return self._json({"ok": False, "msg": "not found"}, 404)
        import mimetypes
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _site_icon(self, path):
        """根路径图标：快捷方式 / 添加到主屏时系统来取的那两个文件。"""
        name = "favicon.ico" if path == "/favicon.ico" else "apple-touch-icon-180.png"
        full = os.path.join(ROOT, "icons", name)
        if not os.path.isfile(full):
            return self._html("<h1>页面不存在</h1>", 404)
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type",
                         "image/x-icon" if name.endswith(".ico") else "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def _student_static(self, rel):
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel:
            rel = "index.html"
        if ".." in rel.split("/"):
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        base = os.path.normpath(os.path.join(ROOT, "student"))
        full = os.path.normpath(os.path.join(base, rel))
        # commonpath 而不是 startswith：startswith 会把 /a/bc 判成 /a/b 的子路径
        try:
            if os.path.commonpath([base, full]) != base:
                return self._json({"ok": False, "msg": "forbidden"}, 403)
        except ValueError:
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        if not os.path.isfile(full):
            return self._html("<h1>页面不存在</h1>", 404)
        # 学生端目录只应有页面/脚本/样式/图标；别的一律不给（防止误放数据文件）
        if not full.lower().endswith((".html", ".js", ".css", ".png", ".svg",
                                      ".ico", ".webmanifest", ".jpg", ".webp")):
            return self._html("<h1>页面不存在</h1>", 404)
        import mimetypes
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if full.lower().endswith(".webmanifest"):
            # Windows 的 mimetypes 不认 .webmanifest，会退成 octet-stream，
            # 浏览器据此直接忽略清单文件（图标就丢了），这里显式给对类型。
            ctype = "application/manifest+json"
        if ctype.startswith("text/") or ctype in ("application/javascript",
                                                  "application/manifest+json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _typing_static(self, rel):
        """打字冒险岛静态资源：/typing/ → 学生端，/typing/teacher.html → 教师管理页。
        静态文件本身无敏感信息，未登录也可打开（页面进"演示模式"）；
        数据读写全部走鉴权 API，这里只管把文件送出去。"""
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel:
            rel = "student.html"
        if ".." in rel.split("/"):
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        base = os.path.normpath(os.path.join(ROOT, "typing"))
        full = os.path.normpath(os.path.join(base, rel))
        try:
            if os.path.commonpath([base, full]) != base:
                return self._json({"ok": False, "msg": "forbidden"}, 403)
        except ValueError:
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        if not os.path.isfile(full):
            return self._html("<h1>页面不存在</h1>", 404)
        if not full.lower().endswith((".html", ".js", ".css", ".png", ".svg", ".ico", ".json")):
            return self._html("<h1>页面不存在</h1>", 404)
        import mimetypes
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    # 导学单入口：/g/<导学单id>/<条目序号>
    # ------------------------------------------------------------------
    # 设计要点（2026-11-01）：学生在工作台里已经登录过，导学单页面就不该再让他
    # 填任何地址/学号。这里由服务端验会话、查出条目、签一张票，再跳过去；
    # 票只代表「这个学生 + 这一条」，页面改 URL 也换不成别人。
    # ------------------------------------------------------------------
    # 课件下发闸门：/cw/<课件库文件id>
    # ------------------------------------------------------------------
    # 课件库接口（/api/cw/*）只对教师开放，学生直接访问会被 403。
    # 老师在课件页点「下发」后，materials 里会多一条 from='courseware'、
    # src_id=文件id 的记录；这里凭学生会话 + 这条记录放行单个文件——
    # 没下发的课件、别的班的课件，仍然一个字节都拿不到。
    def _courseware_resolve(self, fid):
        """共用：校验「学生会话 + 本班 + 已下发」并解析出课件绝对路径。

        返回 (full_path, None) 表示放行；返回 (None, response) 表示拦截
        （response 已是可直接返回的 HTTP 响应）。"""
        s = self._session()
        if not s or s["role"] != "student":
            return None, self._redirect("/s/")
        r = student_row(s.get("student_id"))
        if not r:
            return None, self._html(_tip_page("登录状态已失效",
                                        "请回到学生端重新登录后再打开课件。"), 403)
        if not fid:
            return None, self._html(_tip_page("找不到这份课件", "请回到导学单重新点一次。"), 404)

        allowed = False
        for row in STORE.table_rows("materials"):
            if row.get("deleted"):
                continue
            if str(row.get("from") or "") != "courseware":
                continue
            if str(row.get("src_id") or "") != fid:
                continue
            if str(row.get("class_id") or "") == str(r.get("class_id")):
                allowed = True
                break
        if not allowed:
            return None, self._html(_tip_page("这份课件不对你开放",
                "它可能已被老师收回，或者不属于你所在的班级。"), 403)

        try:
            full = LEG.CW.abs_of(fid, must_exist=True)
        except Exception:
            full = None
        if not full or not os.path.isfile(full):
            return None, self._html(_tip_page("课件文件不存在",
                "老师可能已经移动或删除了这个文件。"), 404)
        return full, None

    def _courseware_gate(self, path):
        # do_GET 入口已对整个 path 做过一次 unquote，课件 id 里的「课程/文件名」
        # 斜杠已被还原 —— 不能再 split/unquote，否则 id 会被从斜杠处切断。
        fid = path[len("/cw/"):].strip("/") if path.startswith("/cw/") else ""
        full, err = self._courseware_resolve(fid)
        if err:
            return err
        return self._serve_abs(full)

    def _courseware_preview_gate(self, path):
        """课件在线预览闸门（2026-11-02）：与 /cw/ 同权限，但会按类型选择
        浏览器能直接看的形式返回 —— 图片/音视频/PDF/文本原样内联；.pptx/.docx/
        .xlsx 由服务端渲染成 HTML 内联（纯 Python 解析，不依赖外部程序）；
        渲染不出来时回退「下载原文件」提示页。"""
        fid = path[len("/cwpv/"):].strip("/") if path.startswith("/cwpv/") else ""
        full, err = self._courseware_resolve(fid)
        if err:
            return err
        ext = _ext_of(full)
        if ext in _OFFICE_EXTS:
            page, e = _office_to_html(full)
            if page:
                return self._html(page, 200)
            return self._html(_preview_fallback_html(os.path.basename(full), fid, e), 200)
        # 其余类型（图片/音视频/PDF/HTML/纯文本等）原样输出，交给浏览器原生渲染
        return self._serve_abs(full)

    def _guide_gate(self, path):
        s = self._session()
        if not s or s["role"] != "student":
            # 未登录：回学生端；登录后从「今日」再点一次即可
            return self._redirect("/s/")
        r = student_row(s.get("student_id"))
        if not r:
            return self._html(_tip_page("登录状态已失效",
                                        "请回到学生端重新登录后再打开这个环节。"), 403)

        parts = [p for p in path.split("/") if p]
        # 课堂活动学习单入口：/g/act/<push_id>（2026-11-03）
        # 与导学单同款机制：校验身份与签到状态后，把票和身份挂在 URL 上再跳。
        if len(parts) >= 2 and parts[1] == "act":
            return self._activity_gate(r, parts[2] if len(parts) > 2 else "")

        gid = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ""
        try:
            idx = int(parts[2]) if len(parts) > 2 else 0
        except Exception:
            idx = 0

        g = None
        for row in STORE.table_rows("guides"):
            if str(row.get("id")) == str(gid):
                g = row
                break
        if not g or str(g.get("class_id")) != str(r.get("class_id")):
            return self._html(_tip_page("找不到这份导学单",
                                        "它可能已被老师收回，或者不属于你所在的班级。"), 404)
        if str(g.get("status") or "published") != "published":
            return self._html(_tip_page("这份导学单还没有发放",
                                        "老师还在这份导学单的草稿状态，稍后再点一次。"), 403)

        items = g.get("items") or []
        if isinstance(items, str):
            try:
                items = json.loads(items)
            except Exception:
                items = []
        if not isinstance(items, list) or idx < 0 or idx >= len(items):
            return self._html(_tip_page("这个环节还没有内容", "换一个环节试试。"), 404)
        it = items[idx] if isinstance(items[idx], dict) else {}

        url = str(it.get("url") or "").strip()
        if not url:
            return self._html(_tip_page(
                "这个环节还没有可打开的页面",
                "老师在这个条目里没有填页面地址（或只写了文字内容）。"
                "如果它是一段资料，回到学生端「今日 · 导学单」里就能直接读到。"), 404)
        if url.startswith("/"):
            url = public_base(self.headers) + url

        # 任务号：条目里显式写了就用，否则从页面地址的查询串里认
        task = str(it.get("task") or "").strip()
        if not task:
            try:
                q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                task = str((q.get("task") or [""])[0] or "").strip()
            except Exception:
                task = ""

        cls_name = self._class_name(r.get("class_id"))
        tok = new_gticket({
            "sid": str(r.get("id") or ""),
            "no": str(r.get("student_no") or ""),
            "name": str(r.get("name") or ""),
            "cls": cls_name,
            "gid": str(gid),
            "idx": idx,
            "task": task,
        })

        # 票 + 身份一起挂在 URL 上：页面自己能显示"你是某某"，提交时只认票
        sep = "&" if "?" in url else "?"
        qs = urllib.parse.urlencode({
            "wbT": tok,
            "wbSid": str(r.get("student_no") or ""),
            "wbName": str(r.get("name") or ""),
            "wbCls": cls_name,
            "wbTask": task,
            "wbBase": public_base(self.headers),
            "wbGid": str(gid),
        })
        return self._redirect(url + sep + qs)

    def _activity_gate(self, r, push_id):
        """课堂活动学习单入口：/g/act/<push_id>（2026-11-03）。

        学生端打开学习单统一走这里，三件事：
        ① 身份注入：把「票 + 学号/姓名/班级」挂到学习单 URL 上——以前学生端
           直接 window.open 裸链接，页面拿不到身份，教师端导入的学生
           班级/姓名/学号在学习单上永远显示不出来；
        ② 签到闸门：今天一笔有效签到都没有（或缺勤）就不放行，
           先回学生端签到（牛哥要求：不签到不能打开课程对应的学习单）；
        ③ 发放校验：只放行本班、已发放（published）的学习单。
        """
        push = None
        for row in STORE.table_rows("activity_pushes"):
            if str(row.get("id")) == str(push_id):
                push = row
                break
        if not push or str(push.get("class_id")) != str(r.get("class_id")):
            return self._html(_tip_page("找不到这份学习单",
                                        "它可能已被老师收回，或者不属于你所在的班级。"), 404)
        if str(push.get("status") or "published") != "published":
            return self._html(_tip_page("这份学习单还没有发放",
                                        "老师还没发放或已收回，等老师重新开启后再打开。"), 403)

        # 签到闸门：出勤/迟到/请假都算有效签到；一笔都没有或缺勤 → 拦下
        d = time.strftime("%Y-%m-%d")
        ok_st = ("checked", "late", "sick", "personal", "present", "early_leave")
        signed = any(
            str(a.get("status") or "") in ok_st
            for a in STORE.table_rows("attendance")
            if str(a.get("student_id")) == str(r.get("id"))
            and str(a.get("date") or "") == d and not a.get("deleted"))
        if not signed:
            return self._html(_tip_page(
                "请先签到",
                "今天还没有你的签到记录，先回到学生端完成签到，再打开这门课的学习单。"), 403)

        url = str(push.get("url") or "").strip()
        if not url:
            return self._html(_tip_page("这份学习单还没有可打开的地址",
                                        "老师可能还没完成配置，稍后再试。"), 404)
        if url.startswith("/"):
            url = public_base(self.headers) + url

        task = str(push.get("task_id") or "").strip()
        cls_name = self._class_name(r.get("class_id"))
        tok = new_gticket({
            "sid": str(r.get("id") or ""),
            "no": str(r.get("student_no") or ""),
            "name": str(r.get("name") or ""),
            "cls": cls_name,
            "gid": "", "idx": 0, "task": task,
        })
        sep = "&" if "?" in url else "?"
        qs = urllib.parse.urlencode({
            "wbT": tok,
            "wbSid": str(r.get("student_no") or ""),
            "wbName": str(r.get("name") or ""),
            "wbCls": cls_name,
            "wbTask": task,
            "wbBase": public_base(self.headers),
        })
        return self._redirect(url + sep + qs)

    # ------------------------------------------------------------------
    # 学生答题提交中转：POST /api/v2/student/submit?t=<票>
    # ------------------------------------------------------------------
    # 为什么不让页面直接 POST 到活动数据服务：
    #   ① 页面大多在另一个端口（5055），直连要它开 CORS、还得把地址写进页面；
    #   ② 地址一旦写进页面，老师换机器就要全班改一遍；
    #   ③ 身份字段如果由页面自己填，学生改个数字就能冒充别人。
    # 走这里：学生只带一张票，身份由服务端补，活动服务只需要在内网可达。
    def _student_submit(self, qs):
        tok = (qs.get("t") or [""])[0] or self.headers.get("X-WB-Ticket") or ""
        gt = get_gticket(tok)
        if not gt:
            return self._json_cors({"ok": False, "msg": "提交凭证已失效，请回到学生端重新打开这一环节"}, 401)
        task = str(gt.get("task") or "").strip()
        if not task:
            return self._json_cors({"ok": False,
                                    "msg": "这个环节没有绑定活动任务，无法提交到数据大屏"}, 400)
        # 任务号会拼进 URL / 当作目录名，只允许安全字符
        if "/" in task or ".." in task:
            return self._json_cors({"ok": False, "msg": "任务号不合法"}, 400)

        ctype = self.headers.get("Content-Type") or ""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length > 40 * 1024 * 1024:
            return self._json_cors({"ok": False, "msg": "文件太大（上限 40MB）"}, 413)
        raw = self.rfile.read(length) if length > 0 else b""

        who = {"学号": gt.get("no") or "", "姓名": gt.get("name") or "",
               "班级": gt.get("cls") or ""}

        # 提交直接落本地活动库（自研后端），不再有跨进程转发
        return self._student_submit_local(task, gt, raw, ctype, who)

    def _student_submit_local(self, task, gt, raw, ctype, who):
        """学生提交（自研后端）：解析 → 补身份 → 落库，全程本进程内完成。

        比转发版本多做了两件事：
          · 文件确实落到 <数据目录>/activity/<活动>/，并回填 /static/uploads/… 路径；
          · 同一学生重复提交时给出「已覆盖」提示（旧的转发模式只会追加一条，
            数据大屏上同一人出现两行，老师要自己认哪条是新的）。
        """
        if not ACT.get_task(task):
            return self._json_cors({"ok": False,
                                    "msg": "这个活动已被删除，请让老师重新发放"}, 404)
        fields, files = {}, []
        low = (ctype or "").lower()
        if low.startswith("multipart/form-data"):
            fields, files = parse_multipart(raw, ctype)
            if not fields and not files:
                return self._json_cors({"ok": False, "msg": "提交内容格式无法解析"}, 400)
        elif "json" in low:
            try:
                obj = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._json_cors({"ok": False, "msg": "提交内容不是合法 JSON"}, 400)
            fields = obj if isinstance(obj, dict) else {"答案": obj}
        else:
            txt = raw.decode("utf-8", "ignore")
            try:
                fields = dict(urllib.parse.parse_qsl(txt, keep_blank_values=True))
            except Exception:
                fields = {"答案": txt[:5000]}
        # 身份以服务端签的票为准，页面自己填的一律覆盖（防冒名）
        fields.update(who)
        try:
            res = ACT.add_submission(task, fields, files, ip=self._client_ip_safe(),
                                     sid=who.get("学号"), name=who.get("姓名"),
                                     cls=who.get("班级"))
        except ValueError as e:
            return self._json_cors({"ok": False, "msg": str(e)}, 413)
        except LookupError:
            return self._json_cors({"ok": False, "msg": "任务不存在"}, 404)
        out = {"ok": True, "message": "提交成功", "id": res["id"],
               "attachment": res.get("attachment")}
        return self._json_cors(out, 200)

    def _json_cors(self, obj, code=200):
        """给跨端提交用的 JSON：页面可能来自 5055 那个域，要显式放开。

        安全性不靠 Origin：这里认的是「票」，票是服务端签给某个学生的，
        伪造一张票比伪造一个 Origin 难得多。
        """
        self._sec_sent = False
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-WB-Ticket")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    # POST
    # ------------------------------------------------------------------
    def do_OPTIONS(self):
        """跨端提交的预检：只给提交接口放开，别的一律不响应"""
        u = urllib.parse.urlparse(self.path)
        if urllib.parse.unquote(u.path) != "/api/v2/student/submit":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-WB-Ticket")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        self._sec_sent = False
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)
        try:
            # 学生答题提交：走票鉴权，不能用 _same_origin 挡（页面在别的端口）
            if path == "/api/v2/student/submit":
                return self._student_submit(qs)
            if path.startswith("/api/v2/"):
                if not self._same_origin():
                    return self._json({"ok": False, "msg": "跨站请求被拒绝"}, 403)
                return self._v2_post(path)
            if path.startswith("/api/act/") or path == "/api/act":
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n > 0 else b""
                return self._act_api(path, u.query, body=body)
            return super().do_POST()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            eid = "%08x" % (int(time.time() * 1000) & 0xFFFFFFFF)
            sys.stderr.write("[post] %s -> [%s] %r\n" % (path, eid, e))
            try:
                self._json({"ok": False, "msg": "服务端出错了（错误号 %s），请查看服务端日志（docker compose logs workbench）" % eid}, 500)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # /api/v2 GET
    # ------------------------------------------------------------------
    def _v2_get(self, path, qs):
        if path == "/api/v2/ping":
            return self._json({"ok": True, "service": "workbench", "version": VERSION,
                               "rev": STORE.rev(), "uptime": int(time.time() - STARTED)})
        if path == "/api/v2/time":
            # 服务端时间是考勤判定的唯一时间源（三端校时用，公开无敏感信息）
            lt = time.localtime()
            return self._json({"ok": True, "ts": time.time(),
                               "date": today_str(),
                               "time": time.strftime("%H:%M:%S", lt),
                               "dow": lt.tm_wday})
        if path == "/api/v2/state":
            st = STORE.stats()
            return self._json({"ok": True, "empty": STORE.is_empty(),
                               "rev": STORE.rev(), "total": st["total"],
                               "has_password": bool(CFG["teacher"].get("password_hash")),
                               "has_qa": has_qa(), "config_rev": CFG_VER[0],
                               "port": _port(), "version": VERSION,
                               "activity_tasks": len(ACT.tasks())})
        if path == "/api/v2/auth/session":
            s = self._session()
            if not s:
                return self._json({"ok": False, "role": None}, 401)
            out = {"ok": True, "role": s["role"], "rev": STORE.rev(),
                   "modules": PJ.module_flags(CFG)}
            # 打字冒险岛开关：学生端靠它决定要不要显示"打字"入口
            out["typing"] = {"enabled": bool((CFG.get("typing") or {}).get("enabled"))}
            if s["role"] == "teacher":
                out["username"] = CFG["teacher"].get("username", "teacher")
            if s["role"] == "student":
                r = student_row(s.get("student_id"))
                out["student"] = PJ.student_context(r)
                out["student"]["name"] = (r or {}).get("name", "")
                out["student"]["student_no"] = (r or {}).get("student_no", "")
                out["student"]["class_name"] = self._class_name((r or {}).get("class_id"))
                cred = STORE.get_cred(s.get("student_id"))
                out["must_change"] = bool(cred and cred.get("must_change"))
            return self._json(out)
        if path == "/api/v2/public/classes":
            return self._public_classes(qs)
        if path == "/api/v2/public/students":
            return self._public_students(qs)
        if path == "/api/v2/act-api":
            # 学生交互式学习单页在「页面加载时」自动拉取本活动的提交地址，
            # 这样老师分享的裸链接（只带 task、不带 api）也能直接提交。
            # 地址是同源相对路径（浏览器必然可达），不暴露后端部署细节。
            task = (qs.get("task") or [""])[0].strip()
            if not task:
                return self._json({"ok": False, "msg": "缺少 task"}, 400)
            api = "/api/act/" + urllib.parse.quote(task, safe="")
            return self._json({"ok": True, "task": task, "api": api})
        if path == "/api/v2/act-data":
            # 数据大屏读取提交（带 _submission_id，供「删除」定位单条记录）。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            task = (qs.get("task") or [""])[0].strip()
            if not task:
                return self._json({"ok": False, "msg": "缺少 task"}, 400)
            t = ACT.get_task(task)
            if not t:
                return self._json({"ok": False, "msg": "活动不存在"}, 404)
            subs = ACT.submissions(task)
            return self._json({"ok": True, "submissions": subs, "task_id": task,
                               "task_title": t["title"],
                               "total_submissions": len(subs)})
        if path == "/api/v2/teacher/activity/list":
            # 活动管理页：活动列表 + 每条的提交数 + 教学资料附件
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            tasks = ACT.tasks(include_archived=True)
            for t in tasks:
                t["attachments"] = ACT.attachments(t["task_id"])
            return self._json({"ok": True, "tasks": tasks})
        if path == "/api/v2/teacher/activity/data":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            task = (qs.get("task") or [""])[0].strip()
            t = ACT.get_task(task)
            if not t:
                return self._json({"ok": False, "msg": "活动不存在"}, 404)
            subs = ACT.submissions(task)
            return self._json({"ok": True, "task_id": task, "task_title": t["title"],
                               "submissions": subs, "total_submissions": len(subs)})
        if path == "/api/v2/teacher/activity/export":
            # 导出：csv（Excel 直接打开，带 BOM）/ json（原始结构，给二次处理）
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            task = (qs.get("task") or [""])[0].strip()
            fmt = ((qs.get("format") or ["csv"])[0] or "csv").lower()
            t = ACT.get_task(task)
            if not t:
                return self._json({"ok": False, "msg": "活动不存在"}, 404)
            if fmt == "json":
                body = json.dumps({"task": t, "submissions": ACT.submissions(task)},
                                  ensure_ascii=False, indent=2).encode("utf-8")
                return self._download(body, "activity-%s.json" % task,
                                      "application/json; charset=utf-8")
            return self._download(ACT.export_csv(task), "activity-%s.csv" % task,
                                  "text/csv; charset=utf-8")
        if path == "/api/v2/sync":
            return self._sync_pull(qs)
        if path == "/api/v2/events":
            return self._sse(qs)
        if path == "/api/v2/student/me":
            return self._student_me()
        if path == "/api/v2/student/seatmap":
            # 学生端座位表（2026-11-03）：姓名/学号/座位号/小组/班干部，不带考勤，
            # 见 _student_seatmap
            return self._student_seatmap(qs)
        if path == "/api/v2/student/now":
            # 学生端「现在第几节」：以服务端时钟为准，签到卡显示的节数
            # 才能和教师端、实际签到记录三方对上（学生机时钟经常不准）
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            st = student_row(s.get("student_id"))
            if not st:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            return self._json(self._now_payload(cid=st.get("class_id")))
        if path == "/api/v2/teacher/now":
            # 教师端考勤页默认节次：同样以服务端时钟为准
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json(self._now_payload())
        if path == "/api/v2/teacher/seat-config":
            # 教师端读回某班已同步的座位表配置（换设备 / 清缓存后本地丢了能补回来）
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            cid = _seat_i((qs.get("class_id") or ["0"])[0])
            if cid <= 0:
                return self._json({"ok": False, "msg": "缺少 class_id"}, 400)
            return self._json({"ok": True, "class_id": cid, "cfg": self._seat_cfg_of(cid)})
        if path == "/api/v2/student/guide-ctx":
            return self._guide_ctx(qs)
        if path == "/api/v2/teacher/entry":
            # 首页「学生入口」卡：给出学生机该打开的局域网地址。
            # 只给教师：地址本身不算秘密，但班级码是（防爬名单），
            # 所以整块都挂在教师会话后面，学生拿不到。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            # 地址跟着「老师当前打开工作台用的地址」走：反向代理（NAS 上的
            # 统一入口）会把 Host / X-Forwarded-* 带过来，学生端链接自然是
            # http://<NAS>:<入口端口>/s/，不再写死容器内的 20020。
            port = _port()
            base = public_base(self.headers)
            urls = [base + "/s/"]
            return self._json({"ok": True, "urls": urls, "port": port,
                               "class_code": CFG.get("class_code") or "",
                               "school": CFG.get("school_name") or ""})
        if path == "/api/v2/teacher/activity":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json({"ok": True,
                               "submit_hours": (CFG.get("activity") or {}).get("submit_hours", 12)})
        if path == "/api/v2/teacher/security":
            return self._security_status()
        if path == "/api/v2/teacher/console":
            return self._console()
        if path == "/api/v2/teacher/export":
            return self._export(qs)
        if path == "/api/v2/teacher/export-files":
            return self._export_files()
        if path == "/api/v2/typing/config":
            # 开放状态与年级词库范围：登录与否都可读（学生端入口 / 演示模式都要用）
            t = CFG.get("typing") or {}
            return self._json({"ok": True, "enabled": bool(t.get("enabled")),
                               "grades": t.get("grades") or []})
        if path == "/api/v2/update/status":
            # 在线更新状态（2026-11-04）：仅教师可读
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json(UPD.info(CFG.get("update") or {}))
        if path == "/api/v2/update/progress":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json({"ok": True, "progress": UPD.state.get("progress") or {},
                               "phase": UPD.state.get("phase", "idle")})
        if path == "/api/v2/typing/me":
            s = self._session()
            if not s:
                return self._json({"ok": False, "msg": "未登录"}, 401)
            if s["role"] != "student":
                if s["role"] == "teacher":
                    # 教师练习模式（2026-11-03）：同一张打字地图教师也能练，
                    # 成绩默认只存本机；前端页顶有「计入班级排行榜」开关。
                    return self._json({"ok": False, "role": "teacher",
                                       "msg": "教师练习模式"})
                return self._json({"ok": False, "msg": "只有学生有练习档案"}, 403)
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            p = TYPING.progress(str(r.get("id")))
            return self._json({"ok": True,
                               "me": {"id": str(r.get("id")), "name": r.get("name", ""),
                                      "class_id": str(r.get("class_id")),
                                      "class_name": self._class_name(r.get("class_id"))},
                               "config": {"enabled": bool((CFG.get("typing") or {}).get("enabled")),
                                          "grades": (CFG.get("typing") or {}).get("grades") or []},
                               "progress": p,
                               "pets": PET_IDS})
        if path == "/api/v2/typing/leaderboard":
            s = self._session()
            if not s:
                return self._json({"ok": False, "msg": "未登录"}, 401)
            scope = (qs.get("scope") or ["week"])[0]
            if scope not in ("week", "total", "speed"):
                scope = "week"
            me_id = None
            if s["role"] == "teacher":
                class_id = (qs.get("class_id") or [None])[0]
            else:
                r = student_row(s.get("student_id"))
                if not r:
                    return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
                class_id = str(r.get("class_id"))
                me_id = str(r.get("id"))
            if class_id in (None, "", "None"):
                return self._json({"ok": True, "rows": [], "scope": scope})
            data = TYPING.leaderboard_data(scope)
            rows = []
            for st in STORE.table_rows("students"):
                if st.get("deleted"):
                    continue
                if str(st.get("class_id")) != str(class_id):
                    continue
                uid = str(st.get("id"))
                d = data.get(uid)
                if not d:
                    continue
                if scope == "week" and d["value"] <= 0:
                    continue
                rows.append({"student_id": uid, "name": st.get("name", ""),
                             "student_no": st.get("student_no", ""),
                             "value": d["value"], "stars": d["stars"],
                             "pet": d["pet"], "decos": d["decos"]})
            rows.sort(key=lambda x: -x["value"])
            # 教师练习成绩（2026-11-03）：开启「计入班级排行榜」后，以「老师」
            # 一行参与排序；本周星为 0 时不进周榜（与学生口径一致）。
            tv = TYPING.teacher_lb_value(class_id, scope)
            if tv and not (scope == "week" and tv["value"] <= 0):
                # 教师行显示名：始终用教师端设置里当前账号昵称（username），
                # 不用成绩首次上报时缓存的旧名——老师改了昵称排行榜要跟着变
                tn = str((CFG.get("teacher") or {}).get("username") or "").strip()
                if tn:
                    tv["name"] = tn
                rows.append({"student_id": "__teacher__", "name": tv["name"],
                             "student_no": "", "value": tv["value"],
                             "stars": tv["stars"], "pet": "", "decos": []})
                rows.sort(key=lambda x: -x["value"])
            rows = rows[:50]
            if me_id:
                for i, row in enumerate(rows):
                    if row["student_id"] == me_id:
                        row["me"] = True
                        row["rank"] = i + 1
            return self._json({"ok": True, "scope": scope,
                               "class_name": self._class_name(class_id), "rows": rows})
        if path == "/api/v2/typing/overview":
            # 教师端：各班参与统计（班级下拉 + 数据卡）
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            data = TYPING.overview()
            classes = []
            for c in STORE.table_rows("classes"):
                if c.get("deleted"):
                    continue
                n = {"id": str(c.get("id")), "name": c.get("name", ""),
                     "students": 0, "joined": 0, "levels": 0, "stars": 0, "best_wpm": 0}
                for st in STORE.table_rows("students"):
                    if st.get("deleted") or str(st.get("class_id")) != str(c.get("id")):
                        continue
                    n["students"] += 1
                    d = data.get(str(st.get("id")))
                    if d and d["levels"] > 0:
                        n["joined"] += 1
                        n["levels"] += d["levels"]
                        n["stars"] += d["stars"]
                        n["best_wpm"] = max(n["best_wpm"], d["best_wpm"])
                classes.append(n)
            t = CFG.get("typing") or {}
            return self._json({"ok": True, "classes": classes,
                               "enabled": bool(t.get("enabled")),
                               "grades": t.get("grades") or []})
        return self._json({"ok": False, "msg": "未知接口"}, 404)

    def _sync_pull(self, qs):
        s = self._session()
        if not s:
            return self._json({"ok": False, "msg": "未登录"}, 401)
        scope = s["role"]
        want = (qs.get("scope") or [scope])[0]
        if want == "teacher" and scope != "teacher":
            return self._json({"ok": False, "msg": "越权"}, 403)
        try:
            since = int(float((qs.get("since") or ["0"])[0] or 0))
        except Exception:
            since = 0
        try:
            limit = int(float((qs.get("limit") or [CFG["sync"].get("page_size", 800)])[0]))
        except Exception:
            limit = CFG["sync"].get("page_size", 800)
        limit = max(50, min(limit, 5000))

        if since <= 0:
            tables = STORE.snapshot()
            rev = STORE.rev()
            has_more = False
        else:
            rev, tables, has_more = STORE.changes_since(since, limit)

        ctx = {}
        if scope == "student":
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在，请联系老师"}, 403)
            ctx = PJ.student_context(r)
        stat_rows = None
        group_src = None
        if scope == "student" and "scores" in tables:
            # 班均/位次按“全班这一刻的完整成绩”算。增量同步只带 1 行，
            # 拿它算出来的“班均”其实就是这一个人的分数（甚至为空）。
            stat_rows = STORE.table_rows("scores")
        if scope == "student" and "groups" in tables:
            # 小组成员名单同理：组员 id → 姓名要全量 students/seats 才能映射
            # （增量同步的 tables 里往往没有这两张表），由服务端注入后计算。
            group_src = {"students": STORE.table_rows("students"),
                         "seats": STORE.table_rows("seats")}
        tables = PJ.project(scope, ctx, tables, CFG, stats_scores=stat_rows,
                            group_src=group_src)
        SESS.touch(A.parse_cookie(self.headers.get("Cookie")))
        return self._json({
            "ok": True, "rev": rev, "since": since, "has_more": has_more,
            "tables": tables,
            "modules": PJ.module_flags(CFG),
            "visibility": CFG.get("visibility", {}),
            "server_time": now_str(),
        })

    def _sse(self, qs):
        s = self._session()
        if not s:
            return self._json({"ok": False, "msg": "未登录"}, 401)
        # 长连接要有人数上限：每个 SSE 占一个线程 + 一个 socket，
        # 一个脚本开几百个连接就能把服务拖垮（ThreadingHTTPServer 没有连接池上限）。
        if not _sse_enter():
            return self._json({"ok": False, "msg": "同时在线页面太多，请稍后再试"}, 503)
        try:
            return self._sse_loop(qs)
        finally:
            _sse_leave()

    def _sse_loop(self, qs):
        try:
            since = int(float((qs.get("since") or ["0"])[0] or 0))
        except Exception:
            since = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        rev = STORE.rev()
        cfg_rev = CFG_VER[0]
        try:
            if rev > since:
                self.wfile.write(("event: rev\ndata: %d\n\n" % rev).encode())
            self.wfile.write(("event: cfg\ndata: %d\n\n" % cfg_rev).encode())
            last_ping = time.time()
            while True:
                with COND:
                    COND.wait(timeout=10)
                cur = STORE.rev()
                if cur != rev:
                    rev = cur
                    self.wfile.write(("event: rev\ndata: %d\n\n" % rev).encode())
                    self.wfile.flush()
                if CFG_VER[0] != cfg_rev:
                    cfg_rev = CFG_VER[0]
                    self.wfile.write(("event: cfg\ndata: %d\n\n" % cfg_rev).encode())
                    self.wfile.flush()
                elif time.time() - last_ping > 20:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
                # 会话过期就断开
                if not SESS.get(A.parse_cookie(self.headers.get("Cookie"))):
                    break
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        return None

    def _student_me(self):
        s = self._session()
        if not s or s["role"] != "student":
            return self._json({"ok": False, "msg": "未登录"}, 401)
        r = student_row(s.get("student_id"))
        if not r:
            return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
        cls = {}
        for c in STORE.table_rows("classes"):
            if str(c.get("id")) == str(r.get("class_id")):
                cls = c
                break
        return self._json({"ok": True, "me": {
            "id": r.get("id"), "name": r.get("name"), "student_no": r.get("student_no"),
            "gender": r.get("gender"), "seat_no": r.get("seat_no"),
            "class_id": r.get("class_id"), "class_name": cls.get("name", ""),
        }, "modules": PJ.module_flags(CFG)})

    def _class_name(self, cid):
        for c in STORE.table_rows("classes"):
            if str(c.get("id")) == str(cid):
                return c.get("name", "")
        return ""

    # ------------------------------------------------------------------
    # 座位表（2026-11-03）
    # ------------------------------------------------------------------
    def _seat_cfg_of(self, cid):
        """取某班已同步的座位表配置（没有就返回空字典）"""
        raw = (CFG.get("seatmap") or {}).get(str(cid))
        return _seat_cfg_norm(raw) if isinstance(raw, dict) else {}

    def _student_seatmap(self, qs):
        """学生端座位表：整间教室的网格 + 我的座位。

        与 /api/v2/sync 的裁剪口径不同（同步只下发本人那一行座位，防爬名单），
        座位表必须能看到"整间教室"才有意义，所以单独开一个只读接口：
          · 每格给 姓名 / 学号 / 座位号 / 小组（组长·组员号）/ 班干部职务，
            与教师端座位卡同一套信息；**不带考勤状态**（学生端不显示），
            也不含电话 / 成绩 / 内部 id；
          · 只有本班已登录学生能读自己班，教师可带 class_id 读任意班；
          · 行列与讲台方位取自教师端同步上来的配置，没同步过就按座位数据推断。
        客户端负责把「讲台永远朝上」的翻转换算好（见 app/student/app.js vSeat）。
        """
        s = self._session()
        if not s:
            return self._json({"ok": False, "msg": "未登录"}, 401)
        if s["role"] == "teacher":
            cid = _seat_i((qs.get("class_id") or ["0"])[0])
        else:
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            cid = _seat_i(r.get("class_id"))
        if cid <= 0:
            return self._json({"ok": False, "msg": "没有对应的班级"}, 400)

        me_id = "" if s["role"] == "teacher" else str(s.get("student_id") or "")
        cfg = self._seat_cfg_of(cid)

        names, nos, deleted = {}, {}, set()
        for x in STORE.table_rows("students"):
            if _seat_i(x.get("class_id")) != cid:
                continue
            if x.get("deleted"):
                deleted.add(str(x.get("id")))
                continue
            names[str(x.get("id"))] = str(x.get("name") or "")
            nos[str(x.get("id"))] = str(x.get("student_no") or "")

        # 班干部职务（duty_positions，与教师端 dutyPosNameOf 同源同口径）
        # 2026-11-03 起该表带 class_id 按班隔离；老数据没有 class_id 时
        # 保持原行为（按 student_id 归属，学生本身不会跨班，天然不串）。
        pos_of = {}
        for p in STORE.table_rows("duty_positions"):
            if p.get("enabled") is False:
                continue
            pc = p.get("class_id")
            if pc not in (None, "") and _seat_i(pc) != cid:
                continue
            sid = str(p.get("student_id") or "")
            nm = str(p.get("name") or "").strip()
            if not sid or not nm:
                continue
            pos_of.setdefault(sid, []).append((_seat_i(p.get("sort_order")), nm))
        for sid in list(pos_of):
            pos_of[sid] = " · ".join(nm for _, nm in sorted(pos_of[sid]))

        # 小组：seat_id → {组名, 颜色, 组长?, 组员号}。
        # 角色判定与教师端 roleOf() 完全同源：显式 roles 优先，
        # 没有显式组长才允许 0 号位兜底成组长（防止一个组冒出两个组长）。
        GROUP_COLORS = ["#1D9E75", "#378ADD", "#EF9F27", "#D4537E",
                        "#7F77DD", "#0EA5E9", "#E24B4A", "#1089C9"]
        grp_of = {}
        gs = [g for g in STORE.table_rows("groups")
              if _seat_i(g.get("class_id")) == cid and not g.get("deleted")]
        gs.sort(key=lambda g: _seat_i(g.get("id")))
        for gi, g in enumerate(gs):
            gname = str(g.get("name") or "").strip()
            seat_ids = [str(x) for x in (g.get("seat_ids") or [])]
            if not gname or not seat_ids:
                continue
            roles = g.get("roles") or {}
            has_leader = any(str(roles.get(sid2) or "") == "leader"
                             for sid2 in seat_ids)
            gc = GROUP_COLORS[gi % len(GROUP_COLORS)]

            def role_of(sid2):
                rv = str(roles.get(sid2) or "")
                if rv:
                    return rv
                i = seat_ids.index(sid2)
                if has_leader:
                    return "member" + (str(i) if i > 0 else "1")
                return "leader" if i == 0 else "member" + (str(i) if i > 0 else "1")

            for sid2 in seat_ids:
                rv = role_of(sid2)
                if rv == "leader":
                    grp_of[sid2] = {"name": gname, "color": gc,
                                    "lead": True, "mem": ""}
                else:
                    m = re.match(r"member(\d+)", rv)
                    n = int(m.group(1)) if m else max(1, seat_ids.index(sid2))
                    grp_of[sid2] = {"name": gname, "color": gc,
                                    "lead": False, "mem": "组员 " + str(n).zfill(2)}

        cells = []          # 有座位记录的位置（可能坐着人，也可能是空位）
        max_r = max_c = 0
        seat_rows = []
        for x in STORE.table_rows("seats"):
            if _seat_i(x.get("class_id")) != cid or x.get("deleted"):
                continue
            r_, c_ = _seat_i(x.get("row")), _seat_i(x.get("col"))
            if r_ <= 0 or c_ <= 0:
                continue
            max_r, max_c = max(max_r, r_), max(max_c, c_)
            seat_rows.append(x)
            sid = str(x.get("student_id") or "")
            nm = names.get(sid, "") if sid and sid not in deleted else ""
            item = {"r": r_, "c": c_, "name": nm}
            if sid and sid == me_id:
                item["me"] = True
            cells.append((x, item))

        # 座位号（序号）：教师端「固定过」的 seq 优先；
        # 老班没固定过 → 按教师端 seatSeqMap() 同一套规则现算（order/start/snakeDir）。
        fixed = {str(x.get("id")): _seat_i(x.get("seq"))
                 for x in seat_rows if _seat_i(x.get("seq")) > 0}
        if len(fixed) < len(seat_rows) and seat_rows:
            order = cfg.get("order") or "snake"
            dir_left = (cfg.get("snakeDir") or "right") == "left"
            from_back = (((cfg.get("start") or "front") == "back")
                         != ((cfg.get("stage") or "top") == "bottom"))
            rmin = min(_seat_i(x.get("row")) for x in seat_rows)
            rmax = max(_seat_i(x.get("row")) for x in seat_rows)

            def rank(r):
                return (rmax - r) if from_back else (r - rmin)

            def sort_key(x):
                r_, c_ = _seat_i(x.get("row")), _seat_i(x.get("col"))
                if order == "col":                      # 逐列
                    return (c_ if dir_left else -c_,
                            r_ if from_back else -r_)
                ka = rank(r_)
                if order == "snake":                    # 蛇形：相邻两排方向相反
                    want_left = (not dir_left) if (ka % 2 == 1) else dir_left
                    return (ka, c_ if want_left else -c_)
                return (ka, c_ if dir_left else -c_)    # 横排：每行同向

            for i, x in enumerate(sorted(seat_rows, key=sort_key)):
                fixed.setdefault(str(x.get("id")), i + 1)

        out_cells = []
        for x, item in cells:
            sid = str(x.get("id") or "")
            stu = str(x.get("student_id") or "")
            seq = fixed.get(sid, 0)
            if item.get("name"):
                item["no"] = nos.get(stu, "")
                if seq:
                    item["seq"] = seq
                g = grp_of.get(sid)
                if g:
                    item["g"] = g
                if stu and pos_of.get(stu):
                    item["pos"] = pos_of[stu]
            out_cells.append(item)

        rows = cfg.get("rows") or max_r
        cols = cfg.get("cols") or max_c
        # 配置里的行列不能把已排好的座位裁掉（老师改了行数但没重排的过渡态）
        rows, cols = max(rows, max_r), max(cols, max_c)

        me = None
        if me_id:
            for _, it in cells:
                if it.get("me"):
                    me = {"r": it["r"], "c": it["c"]}
                    break

        return self._json({
            "ok": True, "class_id": cid, "class_name": self._class_name(cid),
            "rows": rows, "cols": cols,
            "stage": cfg.get("stage") or "top",
            "order": cfg.get("order") or "snake",
            "start": cfg.get("start") or "front",
            "seats": out_cells,
            "placed": sum(1 for x in out_cells if x.get("name")),
            "total": len(names), "me": me,
            "updated": cfg.get("updated") or "",
        })

    def _guide_ctx(self, qs):
        """同源页面问"我是谁、这一条交到哪"。

        /g/<gid>/<idx> 那种跳转已经把票和身份挂在 URL 上了，一般用不上这里。
        这个接口是给「页面自己不知道被哪条导学单打开」的情况兜底，比如老师把
        页面地址直接写进导学单条目、学生从学生端同源打开。
        """
        s = self._session()
        if not s or s["role"] != "student":
            return self._json({"ok": False, "msg": "未登录"}, 401)
        r = student_row(s.get("student_id"))
        if not r:
            return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
        gid = str((qs.get("gid") or [""])[0] or "")
        try:
            idx = int((qs.get("idx") or ["0"])[0] or 0)
        except Exception:
            idx = 0

        task = ""
        it = {}
        for row in STORE.table_rows("guides"):
            if str(row.get("id")) != gid:
                continue
            if str(row.get("class_id")) != str(r.get("class_id")):
                break
            items = row.get("items") or []
            if isinstance(items, str):
                try:
                    items = json.loads(items)
                except Exception:
                    items = []
            if isinstance(items, list) and 0 <= idx < len(items) and isinstance(items[idx], dict):
                it = items[idx]
                task = str(it.get("task") or "").strip()
            break

        cls_name = self._class_name(r.get("class_id"))
        tok = new_gticket({
            "sid": str(r.get("id") or ""), "no": str(r.get("student_no") or ""),
            "name": str(r.get("name") or ""), "cls": cls_name,
            "gid": gid, "idx": idx, "task": task,
        })
        return self._json({
            "ok": True,
            "sid": str(r.get("student_no") or ""), "name": str(r.get("name") or ""),
            "cls": cls_name, "gid": gid, "idx": idx,
            "task": task, "ticket": tok,
            "submit": "/api/v2/student/submit?t=" + tok,
        })

    def _security_status(self):
        """教师端「账号与密码」卡片用：当前账号 + 密保是否已设置"""
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        sec = security_of()
        return self._json({
            "ok": True,
            "username": CFG["teacher"].get("username", "teacher"),
            "has_qa": bool(sec["question"] and sec["answer_hash"]),
            "question": sec["question"],
            "qa_hint": ("密保问题：" + sec["question"]) if sec["question"] else "",
            "min_len": 8,
            "config_rev": CFG_VER[0],
        })

    def _need_public_code(self, qs):
        """从公网拉名单时要求带班级码。

        班级名单是「真实姓名 + 学号」，属于个人信息。校内网（私网来源）本来
        全班都连在同一张网里，直接放行不改变体验；从公网访问就必须带上班主任
        口头告知的班级码，否则任何人都能把整所学校的名单爬下来。
        """
        ip = A.client_ip(self)
        if _is_private_ip(ip):
            return None
        if not A.rate_ok("pub:" + ip, 60, 60):
            return self._json({"ok": False, "msg": "请求太频繁，请 1 分钟后再试"}, 429)
        code = ((qs or {}).get("code") or [""])[0]
        want = str(CFG.get("class_code") or "")
        if want and str(code).upper() == want.upper():
            return None
        return self._json({"ok": False, "need_code": True,
                           "msg": "从外网访问需要班级码，问一下老师"}, 401)

    def _public_classes(self, qs):
        """登录页的班级下拉。只给 id + 名称，不含任何学生信息。"""
        bad = self._need_public_code(qs)
        if bad:
            return bad
        rows = [{"id": c.get("id"), "name": c.get("name", "班级")}
                for c in STORE.table_rows("classes")]
        rows.sort(key=lambda x: str(x["name"]))
        return self._json({"ok": True, "classes": rows})

    def _public_students(self, qs):
        """选定班级后拉该班名单（学号 + 姓名），供下拉选择。

        只暴露“这个班有哪些人”，不暴露任何联系方式；密码仍然是必须的，
        所以知道学号也不能冒充别人。公网来源见 _need_public_code。
        """
        bad = self._need_public_code(qs)
        if bad:
            return bad
        cid = (qs.get("class_id") or [""])[0]
        if not cid:
            return self._json({"ok": False, "msg": "缺少 class_id"}, 400)
        out = []
        for r in STORE.table_rows("students"):
            if str(r.get("class_id")) != str(cid):
                continue
            out.append({"id": r.get("id"),
                        "no": str(r.get("student_no") or ""),
                        "name": str(r.get("name") or "")})
        out.sort(key=lambda x: (x["no"] or "", x["name"]))
        return self._json({"ok": True, "students": out})

    def _console(self):
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        stats = STORE.stats()["tables"]
        students = STORE.table_rows("students")
        leaves = [l for l in STORE.table_rows("leave_requests")
                  if str(l.get("status")) == "pending"]
        leaves.sort(key=lambda x: str(x.get("date") or ""), reverse=True)
        name_of = {str(s.get("id")): s.get("name", "") for s in students}
        for l in leaves:
            l["name"] = name_of.get(str(l.get("student_id")), "")
        open_hw = 0
        td = today_str()
        for h in STORE.table_rows("homeworks"):
            if str(h.get("due") or "9999") >= td:
                open_hw += 1
        base = public_base(self.headers)
        try:
            port = urllib.parse.urlparse(base).port or _port()
        except Exception:
            port = _port()
        return self._json({
            "ok": True, "rev": STORE.rev(), "class_code": CFG.get("class_code"),
            "school": CFG.get("school_name", ""),
            "entry_url": base + "/s/",
            "online": SESS.online("student", ONLINE_WINDOW),
            "students": stats.get("students", 0),
            "open_homework": open_hw,
            "pending_leaves": len(leaves),
            "leaves": leaves[:20],
            "modules": PJ.module_flags(CFG),
            "visibility": CFG.get("visibility", {}),
            "teacher_username": CFG["teacher"].get("username", "teacher"),
            "teacher_has_qa": has_qa(),
            "teacher_question": security_of()["question"],
            "config_rev": CFG_VER[0],
            "port": port,
            "student_auth": {
                "mode": (CFG.get("student") or {}).get("password_mode", "uniform"),
                "initial_password": (CFG.get("student") or {}).get("initial_password", "123456"),
                "force_change": bool((CFG.get("student") or {}).get("force_change")),
                "custom_count": STORE.cred_count(),
                "custom_ids": STORE.cred_ids(),
            },
            "audit": STORE.recent_audit(20),
        })

    def _export(self, qs=None):
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        qs = qs or {}
        # full=1：全量备份模式（系统设置「全量备份导出」用）。
        # 以前只给数据表 + 脱敏配置，恢复到新机器后教师账号、学生口令全丢，
        # 还得重新初始化账号逐个重设密码，谈不上「换机一键可用」。
        # 全量模式带上教师账号（含口令哈希与密保哈希）和学生口令哈希表——
        # 文件本身由前端用备份密码整体加密后落盘，明文不出服务端。
        full = (qs.get("full") or [""])[0] in ("1", "true")
        data = {"version": VERSION, "exported_at": now_str(),
                "tables": STORE.snapshot(),
                "config": {k: v for k, v in CFG.items() if k != "teacher"}}
        if full:
            data["mode"] = "full"
            data["config"] = json.loads(json.dumps(CFG))     # 含 teacher（口令是哈希，不是明文）
            data["creds"] = STORE.export_creds()
            data["sub"] = _sub_snapshot()                    # 提交系统：题目/花名册/提交记录
            # 2026-11-03（换机测试 F3/F4）：打字练习成绩与提交附件都不在 29 表里，
            # 以前不打包 —— 换机后「打字进度金币装扮全归零」「提交记录在、作品文件没了」。
            data["typing"] = _typing_snapshot()
            # 课堂活动（2026-11-03 方案 B）：活动/提交/附件已并入工作台自己的库，
            # 备份口径随之合一——活动数据也在本库里，导出导入一并处理。
            data["activity"] = _act_snapshot()
            files, meta = _sub_files_snapshot()
            if files:
                data["files"] = files
            data["files_meta"] = meta
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if not full:
            self.send_header("Content-Disposition",
                             'attachment; filename="workbench-backup-%s.json"' % time.strftime("%Y%m%d-%H%M"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------
    # 课程资料包：课件库文件的整包导出 / 恢复
    # ------------------------------------------------------------------
    # 为什么数据备份之外还要一个文件包：
    #   全量备份是 JSON（表 + 配置 + 口令哈希），塞不下几百 MB 的课件二进制；
    #   而课件目录在容器里是数据卷 —— 只恢复 JSON 的话，materials 里有记录、
    #   文件却不见了。所以两者配对使用：数据备份 + 课程资料包。
    def _cw_root(self):
        try:
            return os.path.normpath(LEG.CW.root_dir())
        except Exception:
            return ""

    def _export_files(self):
        """把课件库整棵树打成 zip 下载（保留课程子目录结构）。"""
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        root = self._cw_root()
        if not root or not os.path.isdir(root):
            return self._json({"ok": False,
                               "msg": "课件库目录不存在（还没有上传过资料）"}, 404)
        bdir = os.path.join(DATA_DIR, "backup")
        os.makedirs(bdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M")
        out = os.path.join(bdir, "courseware-%s.zip" % stamp)
        n = 0
        try:
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for fn in filenames:
                        if fn.startswith("."):
                            continue
                        full = os.path.join(dirpath, fn)
                        rel = os.path.relpath(full, root).replace(os.sep, "/")
                        try:
                            z.write(full, rel)
                            n += 1
                        except Exception:
                            pass
        except Exception as e:
            return self._json({"ok": False, "msg": "打包失败：%s" % e}, 500)
        if not n:
            try:
                os.remove(out)
            except Exception:
                pass
            return self._json({"ok": False, "msg": "课件库是空的，没有可导出的文件"}, 404)
        # 只留最近 3 个包，别把数据卷撑满
        try:
            olds = sorted([os.path.join(bdir, f) for f in os.listdir(bdir)
                           if f.startswith("courseware-") and f.endswith(".zip")])
            for p in olds[:-3]:
                os.remove(p)
        except Exception:
            pass
        self._serve_abs(out, download=True,
                        ctype="application/zip",
                        name="课程资料包-%s.zip" % stamp)
        return None

    def _import_files(self):
        """接收课程资料包（zip），解压回课件库目录。"""
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "只有教师端可以导入"}, 403)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return self._json({"ok": False, "msg": "没有收到文件内容"}, 400)
        if length > 2 * 1024 * 1024 * 1024:
            return self._json({"ok": False, "msg": "资料包太大（上限 2GB）"}, 413)
        root = self._cw_root()
        if not root:
            return self._json({"ok": False, "msg": "课件库目录不可用"}, 500)
        os.makedirs(root, exist_ok=True)
        tmp = os.path.join(DATA_DIR, "backup", "_import-files.zip")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        remain = length
        try:
            with open(tmp, "wb") as f:
                while remain > 0:
                    chunk = self.rfile.read(min(1 << 20, remain))
                    if not chunk:
                        break
                    f.write(chunk)
                    remain -= len(chunk)
        except Exception as e:
            return self._json({"ok": False, "msg": "接收文件失败：%s" % e}, 500)
        n = skipped = 0
        try:
            with zipfile.ZipFile(tmp) as z:
                for info in z.infolist():
                    name = info.filename.replace("\\", "/")
                    if info.is_dir() or name.startswith("__MACOSX") or "/." in name:
                        continue
                    # 目录穿越防护：只允许相对路径，且不能逃出课件库根目录
                    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
                    if not parts or name.startswith("/"):
                        skipped += 1
                        continue
                    dest = os.path.join(root, *parts)
                    if not os.path.abspath(dest).startswith(os.path.abspath(root) + os.sep):
                        skipped += 1
                        continue
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with z.open(info) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    n += 1
        except Exception as e:
            return self._json({"ok": False, "msg": "解压失败：%s" % e}, 400)
        finally:
            try:
                os.remove(tmp)
            except Exception:
                pass
        try:
            LEG.CW.ensure_root(root)
        except Exception:
            pass
        STORE.audit("teacher", "import-courseware", "%d 个文件 -> %s" % (n, root))
        notify()
        return self._json({"ok": True, "files": n, "skipped": skipped, "root": root})

    # ------------------------------------------------------------------
    # 学生口令校验
    # ------------------------------------------------------------------
    def _check_student_pwd(self, row, pwd):
        """返回 (通过, 是否必须改密, 失败原因)

        优先用这名学生自己的密码；没设过就回落到全班初始密码。
        这样老师不用给 50 个人逐个设密码，也能防住“知道学号就能冒充”。
        """
        cred = STORE.get_cred(row.get("id"))
        if cred:
            if A.verify_password(pwd, cred.get("pwd_hash")):
                return True, bool(cred.get("must_change")), ""
            return False, False, "密码不正确"
        mode = str(CFG["student"].get("password_mode") or "uniform")
        if mode != "uniform":
            return False, False, "老师还没给你设密码，请联系老师"
        init = str(CFG["student"].get("initial_password") or "123456")
        if pwd and pwd == init:
            return True, bool(CFG["student"].get("force_change")), ""
        return False, False, "密码不正确（初始密码请问老师）"

    # ------------------------------------------------------------------
    # /api/v2 POST
    # ------------------------------------------------------------------
    def _v2_post(self, path):
        # 课程资料包是 zip 二进制，必须在这里截住：下面 _read_json 会把它当 JSON 读坏
        if path == "/api/v2/admin/import-files":
            return self._import_files()
        # 活动教学资料是 multipart 二进制，同理必须在 _read_json 之前截住
        if path == "/api/v2/teacher/activity/upload":
            return self._act_upload()
        body = self._read_json()
        if body is None:
            return          # 请求体过大，_read_json 已经回过 413 了
        # 数据大屏：清空本活动全部提交 / 删除单条提交（需求 5，2026-11-02）
        # 参数走查询串（前端 fetch 只带 URL），body 为空时用 body 兜底。
        # 必须教师登录：学生页与教师页同源，只靠同源校验拦不住学生乱调。
        if path in ("/api/v2/act-clear", "/api/v2/act-del-sub"):
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            q2 = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            task = str((q2.get("task") or [body.get("task") or ""])[0] or "").strip()
            if path == "/api/v2/act-clear":
                if not task:
                    return self._json({"ok": False, "msg": "缺少 task"}, 400)
                n, _ids = ACT.clear(task)
                STORE.audit("teacher", "activity-clear", "%s 清空 %d 条" % (task, n))
                return self._json({"ok": True, "deleted": n,
                                   "message": "已清空所有提交数据"})
            sub = str((q2.get("sub") or [body.get("sub") or ""])[0] or "").strip()
            if not sub.isdigit():
                return self._json({"ok": False, "msg": "缺少或非法的 sub"}, 400)
            ok = ACT.delete_submission(int(sub))
            return self._json({"ok": ok, "message": "提交数据已删除" if ok else "提交数据不存在"},
                              200 if ok else 404)
        # ------------------------------------------------------------------
        # 活动管理（2026-11-03 · 方案 B）：新建/改名/删除/清空/删单条
        # ------------------------------------------------------------------
        if path == "/api/v2/teacher/activity/save":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            task = str(body.get("task_id") or "").strip()
            try:
                if task:
                    if not ACT.get_task(task):
                        return self._json({"ok": False, "msg": "活动不存在"}, 404)
                    t = ACT.update_task(task, title=body.get("title"),
                                        description=body.get("description"),
                                        class_id=body.get("class_id") or 0,
                                        course=body.get("course") or "",
                                        archived=body.get("archived"))
                    act = "activity-update"
                else:
                    t = ACT.create_task(body.get("title"), body.get("description") or "",
                                        body.get("class_id") or 0, body.get("course") or "")
                    act = "activity-create"
            except ValueError as e:
                return self._json({"ok": False, "msg": str(e)}, 400)
            STORE.audit("teacher", act, "%s %s" % (t["task_id"], t["title"]))
            t["attachments"] = ACT.attachments(t["task_id"])
            return self._json({"ok": True, "task": t})
        if path == "/api/v2/teacher/activity/delete":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            task = str(body.get("task_id") or "").strip()
            if not ACT.get_task(task):
                return self._json({"ok": False, "msg": "活动不存在"}, 404)
            ok = ACT.delete_task(task)
            STORE.audit("teacher", "activity-delete", task)
            # 发放记录一起清：留着指向已删活动的条目，学生端会点开一个空页面
            for p in STORE.table_rows("activity_pushes"):
                if str(p.get("task_id")) == task:
                    STORE.delete("activity_pushes", p.get("id"))
            return self._json({"ok": True, "deleted": ok})
        if path == "/api/v2/teacher/activity/clear":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            task = str(body.get("task_id") or "").strip()
            if not ACT.get_task(task):
                return self._json({"ok": False, "msg": "活动不存在"}, 404)
            n, _ids = ACT.clear(task)
            STORE.audit("teacher", "activity-clear", "%s 清空 %d 条" % (task, n))
            return self._json({"ok": True, "deleted": n})
        if path == "/api/v2/teacher/activity/del-sub":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            sub = str(body.get("sub") or "").strip()
            if not sub.isdigit():
                return self._json({"ok": False, "msg": "提交号不合法"}, 400)
            ok = ACT.delete_submission(int(sub))
            return self._json({"ok": ok, "msg": "已删除" if ok else "提交不存在"},
                              200 if ok else 404)
        if path == "/api/v2/teacher/activity/del-file":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            task = str(body.get("task_id") or "").strip()
            url = str(body.get("url") or "").strip()
            ok = ACT.delete_attachment(task, url)
            return self._json({"ok": ok}, 200 if ok else 404)
        # ------------------------------------------------------------------
        # 在线更新（2026-11-04）：检查 / 安装 / 回滚 / 配置 / 重启，全部仅教师
        # ------------------------------------------------------------------
        if path == "/api/v2/update/check":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = UPD.check(CFG.get("update") or {})
            return self._json(r, 200 if r.get("ok") else 502)
        if path == "/api/v2/update/apply":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = UPD.apply(CFG.get("update") or {})
            STORE.audit("teacher", "update-apply", str((UPD.state.get("manifest") or {}).get("version") or ""))
            return self._json(r, 200 if r.get("ok") else 400)
        if path == "/api/v2/update/rollback":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = UPD.rollback()
            STORE.audit("teacher", "update-rollback", r.get("msg", ""))
            return self._json(r, 200 if r.get("ok") else 400)
        if path == "/api/v2/update/config":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            u = CFG["update"]
            if "url" in body:
                u["url"] = str(body.get("url") or "").strip()
            if "token" in body:
                u["token"] = str(body.get("token") or "").strip()
            if "auto" in body:
                u["auto"] = bool(body.get("auto"))
            if "interval_hours" in body:
                try:
                    u["interval_hours"] = max(1, min(24 * 30, int(body.get("interval_hours"))))
                except Exception:
                    pass
            A.save_config(CONFIG_PATH, CFG)
            CFG_VER[0] += 1
            notify()
            STORE.audit("teacher", "update-config", "url=%s auto=%s" % (u.get("url") or "默认", u.get("auto")))
            return self._json({"ok": True})
        if path == "/api/v2/update/restart":
            # 原地重启（execv 换进程映像，端口由新进程重新绑定）。
            # 前端发起后按「连接断开 → 轮询 /api/v2/state 恢复」处理。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            STORE.audit("teacher", "update-restart", "version=%s" % VERSION)
            try:
                sys.stdout.flush()
                sys.stderr.flush()
                os.execv(sys.executable, [sys.executable] + list(sys.argv))
            except Exception as e:
                return self._json({"ok": False,
                                   "msg": "自动重启没成功（%s）。请手动重启：Windows 计划任务/直接关掉重开启动脚本；"
                                          "Docker 用 docker compose restart workbench。" % e}, 500)
        if path == "/api/v2/auth/setup":
            if CFG["teacher"].get("password_hash"):
                return self._json({"ok": False, "msg": "账号已设置"}, 400)
            # 首次设置账号只允许「刚启动的这段时间 + 内网来源」。
            # 否则部署完到你打开浏览器之间，任何人先访问一次就能把管理员账号设成他的
            # —— 之后你只能清库重来，中间所有数据都归他。
            ok_setup, why_setup = self._setup_allowed()
            if not ok_setup:
                return self._json({"ok": False, "msg": why_setup}, 403)
            user = str(body.get("username") or "").strip() or "teacher"
            pwd = str(body.get("password") or "")
            if len(user) < 2:
                return self._json({"ok": False, "msg": "账号至少 2 个字符"}, 400)
            bad = A.weak_password(pwd)
            if bad:
                return self._json({"ok": False, "msg": bad}, 400)
            CFG["teacher"]["username"] = user
            CFG["teacher"]["password_hash"] = A.hash_password(pwd)
            q = str(body.get("question") or "").strip()
            a = str(body.get("answer") or "")
            if q:
                CFG["teacher"]["security"] = {"question": q[:60],
                                              "answer_hash": A.hash_answer(a)}
            cfg_save()
            notify_cfg()
            tok = SESS.create("teacher", CFG["teacher"].get("session_hours", 12))
            STORE.audit("teacher", "setup", "首次设置教师账号（密保：%s）" % ("已设" if q else "未设"))
            return self._set_session_headers(
                tok, extra={"username": user, "has_qa": bool(q)},
                hours=CFG["teacher"].get("session_hours", 12))
        if path == "/api/v2/auth/teacher":
            ip = A.client_ip(self)
            if not A.rate_ok("t:" + ip, 20, 60):
                return self._json({"ok": False, "msg": "尝试太频繁，请 1 分钟后再试"}, 429)
            user = str(body.get("username") or "").strip()
            pwd = str(body.get("password") or "")
            want_user = str(CFG["teacher"].get("username") or "teacher")
            if user and user != want_user:
                STORE.audit("?", "login-fail", "%s user=%s" % (ip, user))
                return self._json({"ok": False, "msg": "账号或密码不正确"}, 401)
            if not A.verify_password(pwd, CFG["teacher"].get("password_hash")):
                STORE.audit("?", "login-fail", ip)
                return self._json({"ok": False, "msg": "账号或密码不正确"}, 401)
            tok = SESS.create("teacher", CFG["teacher"].get("session_hours", 12))
            STORE.audit("teacher", "login", ip)
            return self._set_session_headers(
                tok, extra={"username": want_user},
                hours=CFG["teacher"].get("session_hours", 12))
        if path == "/api/v2/auth/student":
            ip = A.client_ip(self)
            # 机房/代理上网时同一个出口 IP 后面可能是整班 45 人，
            # IP 维度只做“异常刷接口”的粗拦截；防爆破靠下面的学号维度。
            if not A.rate_ok("s:" + ip, 120, 60):
                return self._json({"ok": False, "msg": "登录请求太多，请 1 分钟后再试"}, 429)
            cls_id = body.get("class_id")
            sid = body.get("student_id")
            no = str(body.get("no") or "").strip()
            code = str(body.get("code") or "").strip().upper()
            pwd = str(body.get("password") or "")
            hit = None
            if cls_id not in (None, "") or sid not in (None, ""):
                for r in STORE.table_rows("students"):
                    if cls_id not in (None, "") and str(r.get("class_id")) != str(cls_id):
                        continue
                    if sid not in (None, "") and str(r.get("id")) == str(sid):
                        hit = r
                        break
                    if sid in (None, "") and no and str(r.get("student_no") or "").strip() == no:
                        hit = r
                        break
            else:
                # 兼容旧入口：班级码 + 学号
                if code != str(CFG.get("class_code", "")).upper():
                    return self._json({"ok": False, "msg": "班级码不正确，问一下老师"}, 401)
                for r in STORE.table_rows("students"):
                    if str(r.get("student_no") or "").strip() == no:
                        hit = r
                        break
            if not hit:
                return self._json({"ok": False, "msg": "没找到这个学生，问一下老师"}, 401)
            # 单学号的尝试次数才是防爆破的关键（换 IP 也绕不过）
            if not A.rate_ok("s:u:%s" % hit.get("id"), 10, 60):
                return self._json({"ok": False, "msg": "这个账号尝试次数过多，请 1 分钟后再试"}, 429)
            ok, must, why = self._check_student_pwd(hit, pwd)
            if not ok:
                STORE.audit("?", "student-login-fail", "%s %s" % (ip, hit.get("student_no")))
                return self._json({"ok": False, "msg": why}, 401)
            tok = SESS.create("student", CFG["student"].get("session_hours", 8),
                              student_id=hit.get("id"))
            STORE.audit("student:%s" % hit.get("name"), "login", ip)
            # v2 考勤：登录成功即自动签到（迟到 / 出勤由服务端按课表时间判定）。
            # 失败不影响登录本身——签到还可以在学生端手动补。
            auto_ck = None
            try:
                _ok, _info = self._auto_checkin(hit.get("id"))
                if _ok:
                    auto_ck = _info
            except Exception:
                pass
            return self._set_session_headers(tok, extra={
                "must_change": bool(must), "name": hit.get("name"),
                "checkin": auto_ck or None},
                hours=CFG["student"].get("session_hours", 8))
        if path == "/api/v2/auth/logout":
            tok = A.parse_cookie(self.headers.get("Cookie"))
            SESS.drop(tok)
            return self._set_session_headers("", expire=True)

        # ---------------- 密保问题：忘记密码时自助改密 ----------------
        if path == "/api/v2/auth/qa/question":
            ip = A.client_ip(self)
            if not A.rate_ok("qa:" + ip, 20, 60):
                return self._json({"ok": False, "msg": "请求太频繁，请 1 分钟后再试"}, 429)
            user = str(body.get("username") or "").strip()
            want_user = str(CFG["teacher"].get("username") or "teacher")
            if user and user != want_user:
                return self._json({"ok": False, "msg": "账号不存在"}, 401)
            sec = security_of()
            ok_qa = bool(sec["question"] and sec["answer_hash"])
            return self._json({
                "ok": True, "has_qa": ok_qa, "username": want_user,
                "question": sec["question"] if ok_qa else "",
                "msg": "" if ok_qa else "这个账号还没设置密保问题，只能请管理员在服务器上重置",
            })
        if path == "/api/v2/auth/qa/verify":
            ip = A.client_ip(self)
            # 密保答案是可以被猜的短字符串，比密码更需要限速
            if not A.rate_ok("qav:" + ip, 8, 60):
                return self._json({"ok": False, "msg": "尝试太频繁，请 1 分钟后再试"}, 429)
            if not has_qa():
                return self._json({"ok": False, "msg": "没有设置密保问题"}, 400)
            ans = str(body.get("answer") or "")
            if not A.verify_answer(ans, security_of()["answer_hash"]):
                STORE.audit("?", "qa-fail", ip)
                return self._json({"ok": False, "msg": "密保答案不正确"}, 401)
            tok = new_ticket()
            STORE.audit("teacher", "qa-verify", ip)
            return self._json({"ok": True, "ticket": tok, "expires_in": 600})
        if path == "/api/v2/auth/qa/reset":
            ip = A.client_ip(self)
            if not A.rate_ok("qar:" + ip, 10, 60):
                return self._json({"ok": False, "msg": "请求太频繁，请 1 分钟后再试"}, 429)
            pwd = str(body.get("password") or "")
            # 先校验新密码格式，再核销票据：老师把两次密码打得不一样、
            # 或者只输了 3 位，不该让这张一次性票据白白作废（还得重新答密保）。
            bad = A.weak_password(pwd)
            if bad:
                return self._json({"ok": False, "msg": bad}, 400)
            if not take_ticket(body.get("ticket")):
                return self._json({"ok": False,
                                   "msg": "验证已过期，请重新回答密保问题"}, 400)
            CFG["teacher"]["password_hash"] = A.hash_password(pwd)
            cfg_save()
            # 旧密码可能是别人知道的：密保改密后把所有教师会话踢下线，
            # 下面再给当前这台机器发一个新会话，老师不用重新登录。
            SESS.drop_role("teacher")
            notify_cfg()
            STORE.audit("teacher", "reset-password-qa", ip)
            tok = SESS.create("teacher", CFG["teacher"].get("session_hours", 12))
            return self._set_session_headers(
                tok, extra={"username": CFG["teacher"].get("username", "teacher"),
                            "via": "qa"},
                hours=CFG["teacher"].get("session_hours", 12))
        if path == "/api/v2/teacher/security":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            if CFG["teacher"].get("password_hash"):
                if not A.verify_password(str(body.get("password") or ""),
                                         CFG["teacher"].get("password_hash")):
                    return self._json({"ok": False, "msg": "当前密码不正确"}, 401)
            q = str(body.get("question") or "").strip()
            a = str(body.get("answer") or "")
            if q and len(q) < 4:
                return self._json({"ok": False, "msg": "密保问题至少 4 个字"}, 400)
            if q and not a.strip():
                return self._json({"ok": False, "msg": "请填写密保答案"}, 400)
            CFG["teacher"]["security"] = {
                "question": q[:60],
                "answer_hash": A.hash_answer(a) if q else "",
            }
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "security-update",
                        (q[:30] if q else "清除密保问题"))
            return self._json({"ok": True, "has_qa": bool(q), "question": q})
        if path == "/api/v2/teacher/password":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            ip = A.client_ip(self)
            if not A.rate_ok("tpw:" + ip, 10, 60):
                return self._json({"ok": False, "msg": "尝试太频繁，请 1 分钟后再试"}, 429)
            old = str(body.get("old") or "")
            new = str(body.get("new") or "")
            if not A.verify_password(old, CFG["teacher"].get("password_hash")):
                return self._json({"ok": False, "msg": "当前密码不正确"}, 401)
            bad = A.weak_password(new)
            if bad:
                return self._json({"ok": False, "msg": bad}, 400)
            if new == old:
                return self._json({"ok": False, "msg": "新密码和原密码一样"}, 400)
            CFG["teacher"]["password_hash"] = A.hash_password(new)
            cfg_save()
            cur = A.parse_cookie(self.headers.get("Cookie"))
            SESS.drop_role("teacher", except_token=cur)
            notify_cfg()
            STORE.audit("teacher", "change-password", ip)
            return self._json({"ok": True})

        # ---------------- 打字冒险岛 ----------------
        if path == "/api/v2/typing/config":
            # 教师开放开关 + 年级词库选择（关掉后学生端入口实时消失）
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            t = CFG.setdefault("typing", {})
            if "enabled" in body:
                t["enabled"] = bool(body["enabled"])
            if "grades" in body:
                gs = []
                for g in (body.get("grades") or []):
                    try:
                        gi = int(g)
                    except Exception:
                        continue
                    if 1 <= gi <= 9 and gi not in gs:
                        gs.append(gi)
                t["grades"] = sorted(gs)
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "typing-config",
                        json.dumps(t, ensure_ascii=False))
            return self._json({"ok": True, "enabled": t["enabled"], "grades": t["grades"]})
        if path == "/api/v2/typing/record":
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            if not (CFG.get("typing") or {}).get("enabled"):
                return self._json({"ok": False, "msg": "老师还没有开放打字练习"}, 403)
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            res = TYPING.record(str(r.get("id")), body)
            if res.get("ok") and res.get("new_best"):
                STORE.audit("student:%s" % r.get("name"), "typing-record",
                            "s%sc%sl%s %s星" % (body.get("stage"), body.get("chapter"),
                                               body.get("level"), res.get("stars")))
            return self._json(res)
        if path == "/api/v2/typing/teacher-record":
            # 教师练习成绩上报（2026-11-03）：前端勾选「计入班级排行榜」才调
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            tname = str((CFG.get("teacher") or {}).get("username") or "老师")
            res = TYPING.teacher_record(body.get("class_id"), tname, body or {})
            if res.get("ok"):
                STORE.audit("teacher", "typing-teacher-record",
                            "class=%s s%sc%sl%s %s星" % (body.get("class_id"),
                                                         body.get("stage"), body.get("chapter"),
                                                         body.get("level"), res.get("stars")))
            return self._json(res)
        if path == "/api/v2/typing/claim":
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json(TYPING.claim_egg(str(s.get("student_id")), PET_IDS))
        if path == "/api/v2/typing/equip":
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json(TYPING.equip(str(s.get("student_id")),
                                           str(body.get("pet") or ""),
                                           body.get("decos") or []))
        if path == "/api/v2/typing/buy":
            # 金币商店：kind = deco（装扮）/ item（道具）
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            if not (CFG.get("typing") or {}).get("enabled"):
                return self._json({"ok": False, "msg": "老师还没有开放打字练习"}, 403)
            uid = str(s.get("student_id"))
            kind = str(body.get("kind") or "")
            oid = str(body.get("id") or "")
            if kind == "deco":
                res = TYPING.buy_deco(uid, oid)
            elif kind == "item":
                res = TYPING.buy_item(uid, oid)
            else:
                res = {"ok": False, "msg": "未知的商品类型"}
            if res.get("ok"):
                STORE.audit("student", "typing-buy", "%s:%s" % (kind, oid))
            return self._json(res)
        if path == "/api/v2/typing/use-item":
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json(TYPING.use_item(str(s.get("student_id")),
                                              str(body.get("id") or "")))
        if path == "/api/v2/typing/reset-week":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            TYPING.reset_week()
            STORE.audit("teacher", "typing-reset-week", "")
            return self._json({"ok": True})
        if path == "/api/v2/sync":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "只有教师端可以写入"}, 403)
            changes = body.get("changes") or []
            base_rev = body.get("base_rev") or 0
            res = STORE.apply_changes(changes, base_rev, "teacher")
            res["ok"] = True
            if res.get("applied"):
                notify()
            return self._json(res)
        if path == "/api/v2/student/submission":
            return self._student_submission(body)
        if path == "/api/v2/student/checkin":
            return self._student_checkin(body)
        # 学生自助请假接口已移除（2026-10-31）：学生端定位「课堂使用」，
        # 病假/事假由教师在考勤页登记。以前留着这个接口但哪儿都没有入口，
        # 二次开发时极易误以为"差个界面就能用"。
        if path == "/api/v2/student/password":
            s0 = self._session()
            if not s0 or s0["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = student_row(s0.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            old = str(body.get("old") or "")
            new = str(body.get("new") or "")
            ok, _, why = self._check_student_pwd(r, old)
            if not ok:
                return self._json({"ok": False, "msg": "原密码不正确"}, 401)
            if len(new) < 4:
                return self._json({"ok": False, "msg": "新密码至少 4 位"}, 400)
            STORE.set_cred(r.get("id"), A.hash_password(new), 0)
            STORE.audit("student:%s" % r.get("name"), "change-password", "")
            return self._json({"ok": True})
        if path == "/api/v2/teacher/seat-config":
            # 教师端保存排座配置时同步一份上来，供学生端座位表使用（2026-11-03）
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            cid = _seat_i(body.get("class_id"))
            if cid <= 0:
                return self._json({"ok": False, "msg": "缺少 class_id"}, 400)
            cur = dict(self._seat_cfg_of(cid))
            for k in SEAT_CFG_KEYS:
                if k in (body.get("cfg") or {}):
                    cur[k] = body["cfg"][k]
            cur = _seat_cfg_norm(cur)
            cur["updated"] = now_str()
            CFG.setdefault("seatmap", {})[str(cid)] = cur
            cfg_save()
            notify_cfg()
            return self._json({"ok": True, "cfg": cur})
        if path == "/api/v2/teacher/student-auth":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            st = CFG.setdefault("student", {})
            if "password_mode" in body:
                st["password_mode"] = "uniform" if body["password_mode"] == "uniform" else "per"
            if "initial_password" in body:
                v = str(body.get("initial_password") or "").strip()
                if len(v) < 6:
                    return self._json({"ok": False, "msg": "初始密码至少 6 位（学生要能记住，但别用 123456 这种）"}, 400)
                st["initial_password"] = v
            if "force_change" in body:
                st["force_change"] = bool(body["force_change"])
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "student-auth", json.dumps(body, ensure_ascii=False))
            return self._json({"ok": True, "student": st})
        if path == "/api/v2/teacher/reset-cred":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            if body.get("all"):
                STORE.clear_creds()
                STORE.audit("teacher", "reset-all-passwords", "")
                return self._json({"ok": True, "mode": "uniform"})
            sid = body.get("student_id")
            if sid in (None, ""):
                return self._json({"ok": False, "msg": "缺少 student_id"}, 400)
            row = student_row(sid)
            if not row:
                return self._json({"ok": False, "msg": "找不到这名学生（可能已转班或删除）"}, 404)
            who = str(row.get("name") or sid)
            # 2026-11-03：教师可以「直接指定一个新密码」给单个学生。
            # 原来这里只能清掉独立密码、回落到全班初始密码——一旦班级处在
            # 「独立密码模式」，清掉就等于这名学生再也登不进来。
            pwd = str(body.get("password") or "").strip()
            if pwd:
                if len(pwd) < 4:
                    return self._json({"ok": False, "msg": "新密码至少 4 位"}, 400)
                if pwd.lower() in A.WEAK_PASSWORDS:
                    return self._json({"ok": False, "msg": "这个密码太常见了，换一个"}, 400)
                must = 1 if body.get("must_change", True) else 0
                STORE.set_cred(row.get("id"), A.hash_password(pwd), must)
                STORE.audit("teacher", "reset-password",
                            "%s（指定新密码%s）" % (who, "，首次登录须修改" if must else ""))
                return self._json({"ok": True, "mode": "password", "name": who,
                                   "must_change": bool(must)})
            STORE.clear_creds([row.get("id")])
            STORE.audit("teacher", "reset-password", "%s（回落班级初始密码）" % who)
            return self._json({"ok": True, "mode": "uniform", "name": who})
        if path == "/api/v2/teacher/account":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            user = str(body.get("username") or "").strip()
            old = str(body.get("old") or "")
            new = str(body.get("new") or "")
            if user:
                if len(user) < 2:
                    return self._json({"ok": False, "msg": "账号至少 2 个字符"}, 400)
                CFG["teacher"]["username"] = user
            if new:
                if not A.verify_password(old, CFG["teacher"].get("password_hash")):
                    return self._json({"ok": False, "msg": "原密码不正确"}, 401)
                bad = A.weak_password(new)
                if bad:
                    return self._json({"ok": False, "msg": bad}, 400)
                CFG["teacher"]["password_hash"] = A.hash_password(new)
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "account-update", user)
            return self._json({"ok": True, "username": CFG["teacher"].get("username")})
        if path == "/api/v2/teacher/modules":
            return self._teacher_toggle("modules", body)
        if path == "/api/v2/teacher/activity":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            act = CFG.setdefault("activity", {})
            try:
                h = float(body.get("submit_hours") or 12)
            except Exception:
                h = 12
            act["submit_hours"] = max(1, min(h, 72))
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "activity-cfg",
                        "提交有效时长 %s 小时" % act["submit_hours"])
            return self._json({"ok": True, "submit_hours": act["submit_hours"]})
        if path == "/api/v2/teacher/visibility":
            return self._teacher_toggle("visibility", body)
        if path == "/api/v2/teacher/code":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            CFG["class_code"] = A.new_class_code()
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "class-code", CFG["class_code"])
            return self._json({"ok": True, "class_code": CFG["class_code"]})
        if path == "/api/v2/teacher/leave":
            return self._teacher_leave(body)
        if path == "/api/v2/admin/import":
            return self._import(body)
        return self._json({"ok": False, "msg": "未知接口"}, 404)

    def _set_session_headers(self, token, expire=False, extra=None, hours=12):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        cookie = ("wb_sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" if expire else
                  self._set_cookie(token, hours))
        self.send_header("Set-Cookie", cookie)
        payload = {"ok": True}
        if extra:
            payload.update(extra)
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return None

    def _student_submission(self, body):
        s = self._session()
        if not s or s["role"] != "student":
            return self._json({"ok": False, "msg": "未登录"}, 401)
        if not PJ.module_flags(CFG).get("homework", True):
            return self._json({"ok": False, "msg": "老师未开放作业功能"}, 403)
        hw_id = body.get("homework_id")
        if hw_id in (None, ""):
            return self._json({"ok": False, "msg": "缺少 homework_id"}, 400)
        sid = s.get("student_id")
        st = student_row(sid)
        # 作业必须真实存在、且属于本班。
        # 以前直接拿 hw_id 拼 rid 写库：给个不存在的 id 就凭空造出一条提交记录，
        # 给别的班的 id 就写进了别人的作业里。
        hw = STORE.get_row("homeworks", str(hw_id))
        if not hw:
            return self._json({"ok": False, "msg": "这项作业不存在（可能已被老师删除）"}, 404)
        if str(hw.get("class_id")) != str((st or {}).get("class_id")):
            return self._json({"ok": False, "msg": "这不是你们班的作业"}, 403)
        try:
            rid = int(float(hw_id)) * 100000 + int(float(sid))
        except Exception:
            rid = "hw%s-%s" % (hw_id, sid)
        note = str(body.get("note") or "")[:2000]
        status = str(body.get("status") or "submitted")
        if status not in ("submitted", "done", "practice", "checked"):
            status = "submitted"
        old = STORE.get_row("homework_records", rid) or {}
        old_status = str(old.get("status") or "")
        if old_status in ("checked", "done", "graded") and status in ("submitted", "done"):
            # 老师已经批改过：学生再点提交只算“补充说明”，
            # 否则一次重提就把 checked 打回 submitted，老师得重批一遍
            status = old_status
        row = dict(old)
        row.pop("_rev", None)
        row.update({
            "id": rid, "homework_id": hw_id, "student_id": sid,
            "status": status,
            "type": old.get("type") or "practice",
            "note": note,
            "updated_at": now_str(),
        })
        STORE.upsert("homework_records", row, "student")
        STORE.audit("student:%s" % sid, "homework-submit", str(hw_id))
        notify()
        return self._json({"ok": True, "rev": STORE.rev(), "status": status,
                           "kept_checked": status != str(body.get("status") or "submitted")})

    # ---------------------------------------------------------------
    # 课堂签到：学生端「一键签到」→ 写考勤 → 教师端座位表实时看到
    # ---------------------------------------------------------------
    def _current_period(self):
        """按作息表判断“现在第几节”。

        找不到（课间 / 没排作息 / 放学后）就返回 None，让前端按第 1 节处理，
        绝不猜一个错的节次把签到记到别的课上去。
        """
        rows = [r for r in STORE.table_rows("periods") if not r.get("deleted")]
        if not rows:
            return None
        now = time.localtime()
        cur = now.tm_hour * 60 + now.tm_min

        def mins(v):
            try:
                a = str(v or "").split(":")
                return int(a[0]) * 60 + int(a[1] or 0)
            except Exception:
                return -1
        def seq_of(r):
            try:
                return float(r.get("seq") if r.get("seq") not in (None, "") else r.get("id"))
            except Exception:
                return 999.0
        rows = sorted(rows, key=seq_of)
        for r in rows:
            s, e = mins(r.get("start_time")), mins(r.get("end_time"))
            if s >= 0 and e > s and s <= cur <= e:
                return r
        # 不在任何一节里：取“已经过去的最近一节”，方便课间补签
        past = [r for r in rows if mins(r.get("start_time")) >= 0
                and mins(r.get("start_time")) <= cur]
        return past[-1] if past else rows[0]

    def _period_time_ok(self, r):
        """签到时间窗校验：允许「开始前 15 分钟」到「结束后 30 分钟」（课间补签）。

        返回 (ok, msg)。作息表里没填时间的节次不拦（老师没排作息就不约束）。
        以前不校验时间：周六晚上也能把当天所有节挨个签到一遍。
        """
        if not r:
            return True, ""
        def mins(v):
            try:
                a = str(v or "").split(":")
                return int(a[0]) * 60 + int(a[1] or 0)
            except Exception:
                return -1
        s, e = mins(r.get("start_time")), mins(r.get("end_time"))
        if s < 0 or e <= s:
            return True, ""
        cur = time.localtime().tm_hour * 60 + time.localtime().tm_min
        label = r.get("label") or ("第 %s 节" % (r.get("seq") or r.get("id") or "?"))
        if cur < s - 15:
            return False, "还没到%s（%s 开始），先不用签" % (label, r.get("start_time"))
        if cur > e + 30:
            return False, "%s 已结束（%s 下课），如需补签请让老师在考勤页登记" % (label, r.get("end_time"))
        return True, ""

    def _now_payload(self, cid=None):
        """服务端当前节次（2026-11-04 牛哥反馈：签到节数两端不一致）。

        服务端时间 = 唯一时间源（三端校时定版）：学生机 / 教师机时钟不准时，
        各端自己算出来的「第几节」会和实际签到记录对不上。
        统一由服务端算好发给两端显示；带 cid 时顺带给出本班该节是否排课
        （lesson：True 有课 / False 没课 / "no_table" 本班课表没在用）。
        """
        p = self._current_period()
        period = 0
        label = ""
        if p:
            try:
                period = int(float(p.get("seq") or p.get("id") or 0))
            except Exception:
                period = 0
            label = str(p.get("label") or ("第 %s 节" % (p.get("seq") or p.get("id") or "?")))
        out = {"ok": True, "date": today_str(), "time": time.strftime("%H:%M"),
               "period": period, "label": label}
        if cid is not None and period:
            out["lesson"] = self._class_lesson_at(cid, time.localtime().tm_wday, period)
        return out

    def _student_checkin(self, body):
        s = self._session()
        if not s or s["role"] != "student":
            return self._json({"ok": False, "msg": "未登录"}, 401)
        sid = s.get("student_id")
        st = student_row(sid)
        if not st:
            return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
        # 机房里整班同学共用一个出口 IP，按学号限速（同一人反复点也不怕）
        if not A.rate_ok("ck:%s" % sid, 20, 60):
            return self._json({"ok": False, "msg": "签到太频繁了，稍等一下"}, 429)

        p = self._current_period()
        period = body.get("period")
        if period not in (None, ""):
            try:
                period = int(float(period))
            except Exception:
                return self._json({"ok": False, "msg": "节次不对"}, 400)
            # 只接受作息表里真实存在的节次。
            # 以前爱填几填几：学生可以把 1～20 节全部签到一遍，
            # 统计里就出现一整天「全勤」的假记录。
            valid = set()
            for r in STORE.table_rows("periods"):
                for k in ("seq", "id"):
                    try:
                        valid.add(int(float(r.get(k))))
                    except Exception:
                        pass
            if valid and period not in valid:
                return self._json({"ok": False, "msg": "没有这一节课"}, 400)
            if not (1 <= period <= 20):
                return self._json({"ok": False, "msg": "节次超出范围"}, 400)
            # 时间窗校验：还没开始 / 已结束（含课间补签窗口）都明确拒绝
            prow = None
            for r in STORE.table_rows("periods"):
                for k in ("seq", "id"):
                    try:
                        if int(float(r.get(k))) == period:
                            prow = r
                            break
                    except Exception:
                        pass
                if prow:
                    break
            ok, msg = self._period_time_ok(prow)
            if not ok:
                return self._json({"ok": False, "msg": msg}, 400)
        else:
            period = (int(float(p.get("seq") or p.get("id") or 1)) if p else 1)
            ok, msg = self._period_time_ok(p)
            if not ok:
                return self._json({"ok": False, "msg": msg}, 400)
        d = today_str()
        ok2, info = self._write_checkin(sid, st, p, period, d, "student")
        if not ok2:
            return self._json({"ok": False, "msg": info or "签到失败"}, 400)
        return self._json(dict({"ok": True, "rev": STORE.rev()}, **info))

    def _class_lesson_at(self, cid, dow, period):
        """本班在「周几 · 第几节」有没有排课。

        口径与缺勤扫描（absent_sweep_once）一致：day_of_week 0=周一。
        返回 True（有课）/ False（这个时段没课）/ "no_table"（本班课表完全没在用，
        老师从没排过课就不约束签到——与缺勤扫描的退化口径相同）。
        week_pattern（单双周）不参与判定：按「该时段有课」从宽，
        宁可多放行一次签到，也不把单双周排课的班误拦在门外。
        考勤行存的 period 是作息表 seq（1~20），lessons.period_id 引用的是
        作息表 id——先把节次号映射回作息表行，再用该行的 id/seq 双口径去匹配。
        """
        ids = set()
        for r in STORE.table_rows("periods"):
            if r.get("deleted"):
                continue
            for k in ("seq", "id"):
                try:
                    if int(float(r.get(k))) == int(period):
                        for k2 in ("id", "seq"):
                            try:
                                ids.add(int(float(r.get(k2))))
                            except Exception:
                                pass
                        break
                except Exception:
                    pass
        if not ids:
            ids.add(int(period))          # 没有作息表就按号直比（老数据兜底）
        has_any = False
        for l in STORE.table_rows("lessons"):
            if l.get("deleted"):
                continue
            if str(l.get("class_id") or "") != str(cid):
                continue
            has_any = True
            try:
                if int(float(l.get("day_of_week"))) != int(dow):
                    continue
                if int(float(l.get("period_id"))) not in ids:
                    continue
            except Exception:
                continue
            return True
        return "no_table" if not has_any else False

    def _write_checkin(self, sid, st, p, period, d, source):
        """登录自动签到 / 学生手动签到共用的落库核心。

        v2 考勤判定规则（服务端时间为唯一时间源）：
        · 课表开始 5 分钟内签到 → 出勤（checked）；超过 5 分钟 → 迟到（late）
        · 教师已登记病假 / 事假 → 保留请假状态，不被签到覆盖（请假优先级最高）
        · 已被自动记缺勤的学生到堂 → 改判迟到，reason 留痕「缺勤后到堂」
        · 首次签到时间留住：反复签到不会把迟到「刷」成准时
        """
        rid = "ck%s-%s-%s" % (sid, d, period)
        old = STORE.get_row("attendance", rid) or {}
        first = str(old.get("checked_at") or "")
        old_status = str(old.get("status") or "")

        # 课表闸门（2026-11-03 牛哥反馈）：本班这个时段没排课就不允许签到。
        # 以前只校验节次合法性与时间窗：课表没排课的日子（周末/无课日下午）
        # 登录照样被记一笔出勤，考勤统计出现假全勤。
        # 退化口径：本班课表完全没在用（0 条课）就不拦，保持旧行为。
        lesson = self._class_lesson_at(st.get("class_id"),
                                       time.localtime().tm_wday, period)
        if lesson is False:
            return False, "第 %s 节本班今天没有排课，无需签到" % period

        now_min = time.localtime().tm_hour * 60 + time.localtime().tm_min
        def _mins(v):
            a = str(v or "").split(":")
            try:
                return int(a[0]) * 60 + int(a[1] or 0)
            except Exception:
                return -1
        start_min = _mins((p or {}).get("start_time"))
        late = start_min >= 0 and now_min - start_min > 5

        if old_status in ("sick", "personal"):
            status = old_status                       # 请假优先级最高
        elif old_status == "absent":
            status = "late"                           # 缺勤线后到堂 → 改判迟到
        else:
            status = "late" if late else "checked"

        reason = str(old.get("reason") or "")
        if old_status == "absent" and status == "late" and "缺勤后到堂" not in reason:
            reason = (reason + "；" if reason else "") + "缺勤后到堂"
        row = dict(old)
        row.pop("_rev", None)
        row.update({
            "id": rid, "student_id": sid, "class_id": st.get("class_id"),
            "date": d, "period": period, "status": status,
            "reason": reason,
            "source": source,
            "checked_at": first or time.strftime("%H:%M"),
            "updated_at": now_str(),
        })
        STORE.upsert("attendance", row, source)
        STORE.audit("%s:%s" % (source, sid), "checkin", "%s p%s %s" % (d, period, status))
        notify()
        return True, {"date": d, "period": period, "status": status,
                      "period_label": (p or {}).get("label") or ("第 %s 节" % period),
                      "checked_at": row["checked_at"],
                      "already": bool(first)}

    def _auto_checkin(self, sid):
        """学生登录成功即自动签到（按当前节次，窗口外静默跳过）。"""
        st = student_row(sid)
        if not st:
            return False, "学生档案不存在"
        if not A.rate_ok("ck:%s" % sid, 20, 60):
            return False, "签到太频繁了，稍等一下"
        p = self._current_period()
        if not p:
            return False, ""                          # 课间/放学后不产生考勤
        ok, msg = self._period_time_ok(p)
        if not ok:
            return False, msg
        period = int(float(p.get("seq") or p.get("id") or 1))
        return self._write_checkin(sid, st, p, period, today_str(), "login")

    def _teacher_toggle(self, kind, body):
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        cur = CFG.setdefault(kind, {})
        allowed = set(PJ.MODULE_SCOPES) if kind == "modules" else set(cur.keys())
        changed = {}
        for k, v in (body or {}).items():
            # 只接受已知开关：以前 modules 允许任意 key，随便 POST 一个
            # {"xx":true} 就往 config.json 里塞一条永远没人读的垃圾字段。
            if k in allowed:
                cur[k] = bool(v)
                changed[k] = cur[k]
        if not changed:
            return self._json({"ok": False, "msg": "没有可修改的开关"}, 400)
        cfg_save()
        STORE.audit("teacher", "toggle-" + kind, json.dumps(changed, ensure_ascii=False))
        notify_cfg()
        return self._json({"ok": True, kind: cur})

    def _teacher_leave(self, body):
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        lid = body.get("id")
        action = str(body.get("action") or "")
        rows = [r for r in STORE.table_rows("leave_requests") if str(r.get("id")) == str(lid)]
        if not rows:
            return self._json({"ok": False, "msg": "申请不存在"}, 404)
        row = dict(rows[0])
        row["status"] = "approved" if action == "approve" else "rejected"
        row["decided_at"] = now_str()
        row["updated_at"] = now_str()
        STORE.upsert("leave_requests", row, "teacher")
        STORE.audit("teacher", "leave-" + action, str(lid))
        notify()
        return self._json({"ok": True})

    def _import(self, body):
        """全量恢复的唯一入口（教师端「从备份一键恢复」）。

        2026-11-03 加固（换机测试 F1/F2/F5）：
          · F1 先校验后落库 —— 空表 / 缺表 / 数据量骤减一律在删数据之前拒绝；
            需要强导时前端会带 force=1 再提交一次。拒绝时本机数据零改动。
          · F2 学生口令表改成「整体替换」：带了 creds 字段就先清空再写，
            不再出现「备份 2 条、目标机剩 3 条」的残留。
          · F5 未知表名不再静默丢弃，回包告诉调用方跳过了哪些。
        """
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "只有教师端可以导入"}, 403)
        tables = body.get("tables")
        if tables is None:
            return self._json({
                "ok": False, "code": "bad_format",
                "msg": "这不是一份完整备份：缺少数据表段。请在原设备用「一键导出」重新导出后再导入。"}, 400)
        if not isinstance(tables, dict):
            return self._json({"ok": False, "code": "bad_format",
                               "msg": "备份格式不对（数据表段不是键值结构），文件可能已损坏。"}, 400)

        force = bool(body.get("force"))
        try:
            res = STORE.import_snapshot(tables, replace=True, force=force)
        except ImportRefused as e:
            # 409 只用于「需要用户再确认一次」，前端据此弹二次确认后带 force 重试
            return self._json({"ok": False, "code": e.code, "msg": e.msg,
                               "detail": e.detail}, 409 if e.code == "needs_force" else 400)

        counts = res.get("counts") or {}
        warnings = []

        # 全量备份（v2.0.4）还带服务端配置与学生口令哈希：
        # 换机导入后登录账号、初始密码、模块开关都跟原机一致，才叫「一键可用」。
        # 端口 / 绑定地址是「这台机器」的属性，不跟着数据走。
        cfg_in = body.get("config")
        if isinstance(cfg_in, dict):
            for k, v in cfg_in.items():
                if k in ("port", "port_migrated", "bind"):
                    continue
                CFG[k] = v
            cfg_save()
            notify_cfg()

        creds = body.get("creds")
        if "creds" in body and isinstance(creds, list):
            # 整体替换：只做 import_creds（纯新增）会留下目标机独有的旧密码
            STORE.clear_creds()
            counts["student_creds"] = STORE.import_creds(creds)

        # 打字冒险岛（独立 typing.json，不进 29 表）
        if "typing" in body:
            n = _typing_restore(body.get("typing"))
            if n:
                counts["typing_students"] = n

        # 课堂活动：与 29 表同源但不同表，单独恢复（含附件 base64）
        if isinstance(body.get("activity"), dict) and body["activity"]:
            ar = _act_restore(body.get("activity"))
            if ar.get("tasks") or ar.get("submissions"):
                counts["activity_tasks"] = ar.get("tasks", 0)
                counts["activity_submissions"] = ar.get("submissions", 0)
                counts["activity_files"] = ar.get("files", 0)

        # 全量备份还带提交系统（随堂测 / 作品收集）的自有数据：
        # 题目配置、花名册、提交记录都在 submissions/ 下的独立 JSON 里，
        # 不随 STORE 表走——漏了它们，恢复完随堂测就变回默认题库。
        sub = body.get("sub")
        if isinstance(sub, dict) and sub:
            counts["submission_data"] = _sub_restore(sub)

        # 学生作品附件：换机后记录在、文件不在就等于没交
        files = body.get("files")
        if isinstance(files, dict) and files:
            r = _sub_files_restore(files)
            if r.get("written"):
                counts["submission_files"] = r["written"]
            if not r.get("ok"):
                warnings.append("学生作品附件没有还原成功：%s" % (r.get("msg") or "未知原因"))

        if res.get("skipped_tables"):
            warnings.append("以下表名本机不认识，已跳过：%s" % "、".join(res["skipped_tables"][:8]))
        if res.get("kept_tables"):
            warnings.append("备份里没有这些表，本机原有数据已保留：%s"
                            % "、".join(res["kept_tables"][:8]))

        notify()
        return self._json({"ok": True, "counts": counts, "rev": STORE.rev(),
                           "rows": res.get("rows", 0),
                           "skipped_tables": res.get("skipped_tables") or [],
                           "kept_tables": res.get("kept_tables") or [],
                           "warnings": warnings})

def _sub_snapshot():
    """提交系统（随堂测 / 作品收集）自有数据：配置 / 花名册 / 提交记录。

    这些数据在 <数据目录>/submissions/ 下的独立 JSON 里，不进 STORE，
    全量备份必须单独带上，否则换机恢复后随堂测会退回默认题库。
    读不到（旧版本部署、文件损坏）就跳过对应项，不阻塞导出。
    """
    out = {}
    for key, path in (("config", getattr(LEG, "CONFIG_PATH", None)),
                      ("roster", getattr(LEG, "ROSTER_PATH", None)),
                      ("submissions", getattr(LEG, "SUB_PATH", None))):
        try:
            if path and os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    out[key] = json.load(f)
        except Exception:
            pass
    return out


def _sub_restore(sub):
    """把备份里的提交系统数据写回 submissions/ 下，返回写回的文件数。"""
    n = 0
    for key, path in (("config", getattr(LEG, "CONFIG_PATH", None)),
                      ("roster", getattr(LEG, "ROSTER_PATH", None)),
                      ("submissions", getattr(LEG, "SUB_PATH", None))):
        if key in sub and path:
            val = sub[key]
            # 保存目录是「这台机器」的属性：旧机器的绝对路径在新机器上多半不存在，
            # 照搬会让上传直接失败。指不到就清空，回落到本机默认目录。
            if key == "config" and isinstance(val, dict):
                sd = str(val.get("save_dir") or "")
                if sd and not os.path.isdir(sd):
                    val = dict(val)
                    val["save_dir"] = ""
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(val, f, ensure_ascii=False)
                n += 1
            except Exception:
                pass
    return n


# ---------------------------------------------------------------------------
# 打字练习数据：独立 typing.json，不进 29 表体系，必须单独搬
# ---------------------------------------------------------------------------
def _act_snapshot():
    """课堂活动快照（活动 / 提交 / 附件）。读失败不阻塞导出。"""
    try:
        return ACT.snapshot()
    except Exception:
        return {}


def _act_restore(snap):
    """恢复课堂活动。返回 {tasks, submissions, files} 条数。"""
    try:
        return ACT.restore(snap)
    except Exception:
        return {"tasks": 0, "submissions": 0, "files": 0}


def _typing_snapshot():
    try:
        d = TYPING.data
        if isinstance(d, dict) and isinstance(d.get("students"), dict):
            return json.loads(json.dumps(d, ensure_ascii=False))
    except Exception:
        pass
    return {}


def _typing_restore(data):
    """整份替换打字成绩，返回恢复的学生数（0 表示没恢复）。"""
    if not isinstance(data, dict) or not isinstance(data.get("students"), dict):
        return 0
    try:
        blank = {"students": {}}
        if not data.get("students"):
            data = blank
        TYPING.data = json.loads(json.dumps(data, ensure_ascii=False))
        TYPING._save()
        return len(TYPING.data.get("students") or {})
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 学生作品附件：提交记录指向 uploads/ 下的真实文件，换机必须一起搬
# ---------------------------------------------------------------------------
# 上限 40MB 原始体积：base64 进 JSON 先涨 4/3，整体再 AES 加密 + base64 又涨一次，
# 教室里的老机器扛不住更大的包。超限的单文件跳过并在回包里点名，不静默丢。
SUB_FILES_CAP = 40 * 1024 * 1024
SUB_FILE_MAX = 10 * 1024 * 1024
_SUB_FILE_SKIP_DIRS = {"_rwcache"}
# 这三个已由 _sub_snapshot 单独带，不重复打包
_SUB_FILE_DUP = {"config.json", "roster.json", "submissions.json"}


def _sub_files_snapshot():
    """收集提交系统的附件与增强模块数据 → ({相对路径: base64}, meta)。"""
    root = getattr(LEG, "DATA", "") or ""
    files, skipped, total = {}, [], 0
    if not root or not os.path.isdir(root):
        return files, {"included": 0, "bytes": 0, "skipped": [],
                       "note": "提交系统数据目录不可用", "cap": SUB_FILES_CAP}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _SUB_FILE_SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith(".") or fn.endswith(".tmp"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel in _SUB_FILE_DUP:
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size > SUB_FILE_MAX:
                skipped.append({"name": rel, "size": size, "why": "single"})
                continue
            if total + size > SUB_FILES_CAP:
                skipped.append({"name": rel, "size": size, "why": "cap"})
                continue
            try:
                with open(full, "rb") as f:
                    files[rel] = base64.b64encode(f.read()).decode("ascii")
                total += size
            except Exception:
                skipped.append({"name": rel, "size": size, "why": "read"})
    meta = {"included": len(files), "bytes": total, "skipped": skipped,
            "cap": SUB_FILES_CAP, "single_max": SUB_FILE_MAX}
    return files, meta


def _sub_files_restore(files):
    """把备份里的附件写回本机，并按文件名修好 uploads_index 里的绝对路径。"""
    root = getattr(LEG, "DATA", "") or ""
    if not root:
        return {"ok": False, "msg": "提交系统数据目录不可用"}
    written = 0
    restored = {}          # 文件名 → 本机绝对路径（本次真正写回来的那些）
    for rel, b64 in (files or {}).items():
        if not isinstance(rel, str) or not isinstance(b64, str):
            continue
        rel = rel.replace("\\", "/").lstrip("/")
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or ".." in parts or parts[0] in _SUB_FILE_SKIP_DIRS:
            continue
        try:
            blob = base64.b64decode(b64)
        except Exception:
            continue
        target = os.path.join(root, *parts)
        try:
            os.makedirs(os.path.dirname(target) or root, exist_ok=True)
            with open(target, "wb") as f:
                f.write(blob)
            written += 1
            restored[os.path.basename(target)] = os.path.abspath(target)
        except Exception:
            pass
    return {"ok": True, "written": written,
            "index_fixed": _fix_upload_index(root, restored)}


def _fix_upload_index(root, restored=None):
    """uploads_index.json 存的是旧机器的绝对路径，换机后按文件名重新指回本机。

    以前判断「旧路径还在就跳过」是错的：在同一台机器上换数据目录（或重新恢复）
    时，旧路径可能依然存在，于是一整份备份恢复完，索引还指向旧目录里的文件。
    现在优先用「本次刚写回来的那份」——它才是这次恢复的真相。
    """
    path = getattr(LEG, "UPLOAD_INDEX", "")
    if not path or not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        return 0
    if not isinstance(idx, dict):
        return 0
    restored = restored or {}
    by_name = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SUB_FILE_SKIP_DIRS]
        for fn in filenames:
            by_name.setdefault(fn, os.path.join(dirpath, fn))
    fixed = 0
    for token, rec in list(idx.items()):
        old = rec.get("path") if isinstance(rec, dict) else rec
        base = os.path.basename(str(old or ""))
        if not base:
            continue
        new = restored.get(base)
        if not new:
            if old and os.path.isfile(str(old)):
                continue
            new = by_name.get(base)
        if not new:
            continue
        if isinstance(rec, dict):
            rec["path"] = os.path.abspath(new)
        else:
            idx[token] = os.path.abspath(new)
        fixed += 1
    if fixed:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(idx, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return fixed


def _port():
    return PORT


# ---------------------------------------------------------------------------
# 把主服务的「身份 / 学生 / 花名册」注入旧提交系统
# ---------------------------------------------------------------------------
# 旧模块（/api/enh、/api/cw、/api/config…）原本完全没有身份概念，默认
# 「同一个 Wi-Fi 里都是自己人」。现在它们被复用在同一进程里，就统一按主服务的
# 会话判断角色；单独运行旧服务时钩子为空，那边会退化成「只认回环地址」。
def _leg_role(handler):
    s = handler._session()
    if not s:
        return ("", None)
    return (s["role"], s.get("student_id"))


def _leg_student_sid(handler):
    """会话里的内部学生 id → 旧模块用的学号（student_no，缺省回落到 id）。"""
    s = handler._session()
    if not s or s["role"] != "student":
        return ""
    r = student_row(s.get("student_id"))
    if not r:
        return ""
    return str(r.get("student_no") or r.get("id") or "")


def _leg_roster():
    """花名册投影：与学生表同源，避免「教师端加了人、提交页看不到」。"""
    names = {str(c.get("id")): c.get("name", "") for c in STORE.table_rows("classes")}
    out = []
    for s in STORE.table_rows("students"):
        out.append({"cls": names.get(str(s.get("class_id")), ""),
                    "sid": str(s.get("student_no") or s.get("id") or ""),
                    "name": str(s.get("name") or "")})
    return out


LEG.ROLE_FN = _leg_role
LEG.STUDENT_SID_FN = _leg_student_sid
LEG.ROSTER_PROVIDER = _leg_roster
try:
    LEG.ENH.ROSTER_PROVIDER = _leg_roster
except Exception:
    pass


# ----------------------------------------------------------------------
# v2 考勤：缺勤自动扫描（服务端线程，每 30 秒一次）
# 上课开始 15 分钟仍未登录、且没有任何考勤记录（含教师登记的请假）
# → 自动记缺勤；之后到堂的，登录签到时自动改判迟到（见 _write_checkin）。
# ----------------------------------------------------------------------
def _mins_of(v):
    a = str(v or "").split(":")
    try:
        return int(a[0]) * 60 + int(a[1] or 0)
    except Exception:
        return -1


def absent_sweep_once():
    rows = [r for r in STORE.table_rows("periods") if not r.get("deleted")]
    if not rows:
        return 0
    lt = time.localtime()
    cur = lt.tm_hour * 60 + lt.tm_min
    dow = lt.tm_wday                      # 0=周一，与 lessons.day_of_week 同口径
    curp = None
    for r in rows:
        s, e = _mins_of(r.get("start_time")), _mins_of(r.get("end_time"))
        if s >= 0 and e > s and s <= cur <= e:
            curp = r
            break
    if not curp:
        return 0                          # 课间 / 放学后不判定
    s = _mins_of(curp.get("start_time"))
    e = _mins_of(curp.get("end_time"))
    if not (s + 15 <= cur <= e + 30):
        return 0                          # 15 分钟线之前 / 下课 30 分钟后不扫
    try:
        period = int(float(curp.get("seq") or curp.get("id") or 1))
    except Exception:
        return 0
    d = today_str()
    # 今天有排课的班级（按课表判定；课表为空则退化为「本节已有人签到」的班级）
    lesson_cls = set()
    for l in STORE.table_rows("lessons"):
        if l.get("deleted"):
            continue
        try:
            if int(float(l.get("day_of_week"))) == dow and \
               l.get("class_id") not in (None, ""):
                lesson_cls.add(str(l.get("class_id")))
        except Exception:
            pass
    if not lesson_cls:
        for a in STORE.table_rows("attendance"):
            if str(a.get("date")) == d and \
               int(float(a.get("period") or 1)) == period and \
               a.get("class_id") not in (None, ""):
                lesson_cls.add(str(a.get("class_id")))
    n = 0
    for cls in lesson_cls:
        stus = [r for r in STORE.table_rows("students")
                if not r.get("deleted") and str(r.get("class_id")) == cls]
        for st in stus:
            sid = st.get("id")
            rid = "ck%s-%s-%s" % (sid, d, period)
            if STORE.get_row("attendance", rid):
                continue                  # 已有记录（出勤/迟到/请假）不动
            STORE.upsert("attendance", {
                "id": rid, "student_id": sid, "class_id": cls,
                "date": d, "period": period, "status": "absent",
                "reason": "上课15分钟未登录", "source": "auto",
                "updated_at": now_str(),
            }, "auto")
            n += 1
    if n:
        STORE.audit("auto", "absent-sweep", "%s p%d n=%d" % (d, period, n))
        notify()
    return n


def absent_sweeper():
    while True:
        time.sleep(30)
        try:
            absent_sweep_once()
        except Exception:
            pass                          # 扫描失败不影响主服务


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0,
                    help="端口（默认读 data/config.json，首次为 20020）")
    ap.add_argument("--bind", default=CFG.get("bind", "0.0.0.0"))
    ap.add_argument("--no-dialog", action="store_true", help="端口被占时不弹窗")
    args = ap.parse_args()
    global PORT
    # 一次性迁移：老配置里写死 8080（旧默认值）→ 换成不常用的 20020。
    # 只在“用户没显式指定 --port”时做，且只做一次（port_migrated 标记）。
    if not args.port:
        try:
            raw = PORTX.read_cfg(CONFIG_PATH)
            if not raw.get("port_migrated") and int(raw.get("port") or 0) in (0, 8080):
                CFG["port"] = PORTX.DEFAULT_PORT
                CFG["port_migrated"] = True
                A.save_config(CONFIG_PATH, CFG)
                print("[workbench] 默认端口已从 8080 改为 %d（避免和常用软件冲突）"
                      % PORTX.DEFAULT_PORT)
        except Exception:
            pass
    want = args.port or PORTX.config_port(CONFIG_PATH, PORTX.DEFAULT_PORT)

    def mine(p):
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/api/v2/ping" % p,
                                        timeout=1.0) as r:
                return json.loads(r.read().decode()).get("service") == "workbench"
        except Exception:
            return False

    port, state = PORTX.pick_port(want, cfg_path=CONFIG_PATH, is_mine=mine,
                                  no_dialog=args.no_dialog)
    if state == "mine":
        print("[workbench] 端口 %d 上已有一个本工作台实例在运行，不重复启动。" % port)
        print("            要重启请先关掉那个窗口。")
        return
    if state != "free":
        print("[workbench] 端口 %d 不可用（state=%s），退出。" % (port, state))
        sys.exit(2)
    PORT = port

    # 每日一次快照：SQLite 的 VACUUM INTO 是事务一致的，不会拷到半个库
    try:
        if CFG.get("backup", {}).get("auto_daily", True):
            bk = os.path.join(DATA_DIR, "backup")
            os.makedirs(bk, exist_ok=True)
            target = os.path.join(bk, "workbench-%s.db" % time.strftime("%Y%m%d"))
            if not os.path.exists(target):
                STORE.vacuum_into(target)
                keep = int(CFG["backup"].get("keep", 14))
                files = sorted([f for f in os.listdir(bk) if f.endswith(".db")])
                for old in files[:-keep]:
                    try:
                        os.remove(os.path.join(bk, old))
                    except Exception:
                        pass
                print("[workbench] 已生成今日快照 %s" % os.path.basename(target))
    except Exception as e:
        print("[workbench] 快照失败（不影响启动）：%s" % e)

    try:
        httpd = ThreadingHTTPServer((args.bind, port), Handler)
    except OSError as e:
        print("[workbench] 端口 %d 绑定失败：%s" % (port, e))
        print("            可以手动指定端口：python server.py --port 20200")
        sys.exit(2)

    # v2 考勤：缺勤自动扫描线程（上课 15 分钟未登录 → 缺勤）
    threading.Thread(target=absent_sweeper, daemon=True,
                     name="absent-sweeper").start()
    httpd.daemon_threads = True

    # 会话/限速表的定期清理：长时间运行（教室整学期不关机）时防止内存缓慢增长
    def _janitor():
        while True:
            time.sleep(600)
            try:
                n = SESS.gc()
                if n > 200:
                    sys.stderr.write("[workbench] 清理过期会话，剩余 %d 个\n" % n)
            except Exception:
                pass
    threading.Thread(target=_janitor, daemon=True).start()

    # SSE 长连接被浏览器正常关闭时会抛 ConnectionAborted；
    # 这不是错误，不要往控制台刷几十行堆栈吓老师。
    def _quiet_error(request, client_address):
        pass
    httpd.handle_error = _quiet_error
    print("[workbench] 服务已启动 · 教师端 http://127.0.0.1:%d/teacher/（容器内地址）" % port)
    for ip in lan_ips()[:3]:
        print("[workbench] 学生端 http://%s:%d/s/" % (ip, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
