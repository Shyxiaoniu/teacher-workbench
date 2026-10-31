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
import sys
import json
import time
import threading
import urllib.parse
import urllib.request
import importlib.util
from http.server import ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, os.path.join(ROOT, "submission-system"))

import auth as A                       # noqa: E402
import pages as P                      # noqa: E402
import projection as PJ                # noqa: E402
import ports as PORTX                  # noqa: E402
from store import Store, TABLES        # noqa: E402

DATA_DIR = os.path.join(ROOT, "data")
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
CFG = A.load_config(CONFIG_PATH)
SESS = A.Sessions()
COND = threading.Condition()
STARTED = time.time()
VERSION = "2.0.0"
ONLINE_WINDOW = 300      # 5 分钟内活跃算在线

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


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


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


class Handler(LEG.Handler):
    server_version = "Workbench/" + VERSION

    # ------------------------------------------------------------------
    # 基础响应
    # ------------------------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html, code=200):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, url, cookie=None):
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
        return ("wb_sid=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax"
                % (token, int(float(hours) * 3600)))

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

    # ------------------------------------------------------------------
    # GET
    # ------------------------------------------------------------------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)

        try:
            if path.startswith("/api/v2/"):
                return self._v2_get(path, qs)
            if path in ("/", "/index.html"):
                return self._entry()
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
            if path == "/join":
                return self._html(P.join_page(CFG.get("class_code", ""),
                                              "http://%s:%d/s/" % (best_ip(), _port()),
                                              CFG.get("school_name", "")))
            if path == "/s":
                return self._redirect("/s/")
            if path.startswith("/s/"):
                return self._student_static(path[3:])
            if path.startswith("/teacher/"):
                return self._teacher_static(path[len("/teacher/"):])
            # 其余交给旧服务（/vendor /icons /submit /api/enh /api/cw ...）
            return super().do_GET()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            sys.stderr.write("[get] %s -> %s\n" % (path, e))
            try:
                self._json({"ok": False, "msg": "服务端异常：%s" % e}, 500)
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
            return self._html("<h1>无法读取教师端页面</h1><p>%s</p>" % e, 500)
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

    def _student_static(self, rel):
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel:
            rel = "index.html"
        if ".." in rel.split("/"):
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        full = os.path.normpath(os.path.join(ROOT, "student", rel))
        if not full.startswith(os.path.normpath(os.path.join(ROOT, "student"))):
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        if not os.path.isfile(full):
            return self._html("<h1>页面不存在</h1>", 404)
        import mimetypes
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
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
    # POST
    # ------------------------------------------------------------------
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(u.path)
        try:
            if path.startswith("/api/v2/"):
                if not self._same_origin():
                    return self._json({"ok": False, "msg": "跨站请求被拒绝"}, 403)
                return self._v2_post(path)
            return super().do_POST()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            sys.stderr.write("[post] %s -> %s\n" % (path, e))
            try:
                self._json({"ok": False, "msg": "服务端异常：%s" % e}, 500)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # /api/v2 GET
    # ------------------------------------------------------------------
    def _v2_get(self, path, qs):
        if path == "/api/v2/ping":
            return self._json({"ok": True, "service": "workbench", "version": VERSION,
                               "rev": STORE.rev(), "uptime": int(time.time() - STARTED)})
        if path == "/api/v2/state":
            st = STORE.stats()
            return self._json({"ok": True, "empty": STORE.is_empty(),
                               "rev": STORE.rev(), "total": st["total"],
                               "has_password": bool(CFG["teacher"].get("password_hash")),
                               "has_qa": has_qa(), "config_rev": CFG_VER[0],
                               "port": _port(), "version": VERSION})
        if path == "/api/v2/auth/session":
            s = self._session()
            if not s:
                return self._json({"ok": False, "role": None}, 401)
            out = {"ok": True, "role": s["role"], "rev": STORE.rev(),
                   "modules": PJ.module_flags(CFG)}
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
            return self._public_classes()
        if path == "/api/v2/public/students":
            return self._public_students(qs)
        if path == "/api/v2/sync":
            return self._sync_pull(qs)
        if path == "/api/v2/events":
            return self._sse(qs)
        if path == "/api/v2/student/me":
            return self._student_me()
        if path == "/api/v2/teacher/security":
            return self._security_status()
        if path == "/api/v2/teacher/console":
            return self._console()
        if path == "/api/v2/teacher/export":
            return self._export()
        return self._json({"ok": False, "msg": "未知接口"}, 404)

    def _sync_pull(self, qs):
        s = self._session()
        if not s:
            return self._json({"ok": False, "msg": "未登录"}, 401)
        scope = s["role"]
        want = (qs.get("scope") or [scope])[0]
        if want == "teacher" and scope != "teacher":
            return self._json({"ok": False, "msg": "越权"}, 403)
        since = int((qs.get("since") or ["0"])[0] or 0)
        limit = int((qs.get("limit") or [CFG["sync"].get("page_size", 800)])[0])
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
        if scope == "student" and "scores" in tables:
            # 班均/位次按“全班这一刻的完整成绩”算。增量同步只带 1 行，
            # 拿它算出来的“班均”其实就是这一个人的分数（甚至为空）。
            stat_rows = STORE.table_rows("scores")
        tables = PJ.project(scope, ctx, tables, CFG, stats_scores=stat_rows)
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
        since = int((qs.get("since") or ["0"])[0] or 0)
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
            "min_len": 4,
            "config_rev": CFG_VER[0],
        })

    def _public_classes(self):
        """登录页的班级下拉。只给 id + 名称，不含任何学生信息。"""
        rows = [{"id": c.get("id"), "name": c.get("name", "班级")}
                for c in STORE.table_rows("classes")]
        rows.sort(key=lambda x: str(x["name"]))
        return self._json({"ok": True, "classes": rows})

    def _public_students(self, qs):
        """选定班级后拉该班名单（学号 + 姓名），供下拉选择。

        只暴露“这个班有哪些人”，不暴露任何联系方式；密码仍然是必须的，
        所以知道学号也不能冒充别人。
        """
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
        port = _port()
        return self._json({
            "ok": True, "rev": STORE.rev(), "class_code": CFG.get("class_code"),
            "school": CFG.get("school_name", ""),
            "entry_url": "http://%s:%d/s/" % (best_ip(), port),
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
            },
            "audit": STORE.recent_audit(20),
        })

    def _export(self):
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        data = {"version": VERSION, "exported_at": now_str(),
                "tables": STORE.snapshot(), "config": {
                    k: v for k, v in CFG.items() if k != "teacher"}}
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition",
                         'attachment; filename="workbench-backup-%s.json"' % time.strftime("%Y%m%d-%H%M"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
        body = self._read_json()
        if path == "/api/v2/auth/setup":
            if CFG["teacher"].get("password_hash"):
                return self._json({"ok": False, "msg": "账号已设置"}, 400)
            user = str(body.get("username") or "").strip() or "teacher"
            pwd = str(body.get("password") or "")
            if len(user) < 2:
                return self._json({"ok": False, "msg": "账号至少 2 个字符"}, 400)
            if len(pwd) < 4:
                return self._json({"ok": False, "msg": "密码至少 4 位"}, 400)
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
            return self._set_session_headers(tok, extra={
                "must_change": bool(must), "name": hit.get("name")},
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
                "msg": "" if ok_qa else "这个账号还没设置密保问题，只能请管理员在教师机上重置",
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
            if len(pwd) < 4:
                return self._json({"ok": False, "msg": "新密码至少 4 位"}, 400)
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
            if len(new) < 4:
                return self._json({"ok": False, "msg": "新密码至少 4 位"}, 400)
            if new == old:
                return self._json({"ok": False, "msg": "新密码和原密码一样"}, 400)
            CFG["teacher"]["password_hash"] = A.hash_password(new)
            cfg_save()
            cur = A.parse_cookie(self.headers.get("Cookie"))
            SESS.drop_role("teacher", except_token=cur)
            notify_cfg()
            STORE.audit("teacher", "change-password", ip)
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
        if path == "/api/v2/student/leave":
            return self._student_leave(body)
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
        if path == "/api/v2/teacher/student-auth":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            st = CFG.setdefault("student", {})
            if "password_mode" in body:
                st["password_mode"] = "uniform" if body["password_mode"] == "uniform" else "per"
            if "initial_password" in body:
                v = str(body.get("initial_password") or "").strip()
                if len(v) < 4:
                    return self._json({"ok": False, "msg": "初始密码至少 4 位"}, 400)
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
            else:
                sid = body.get("student_id")
                if sid in (None, ""):
                    return self._json({"ok": False, "msg": "缺少 student_id"}, 400)
                STORE.clear_creds([sid])
                STORE.audit("teacher", "reset-password", str(sid))
            return self._json({"ok": True})
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
                if len(new) < 4:
                    return self._json({"ok": False, "msg": "新密码至少 4 位"}, 400)
                CFG["teacher"]["password_hash"] = A.hash_password(new)
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "account-update", user)
            return self._json({"ok": True, "username": CFG["teacher"].get("username")})
        if path == "/api/v2/teacher/modules":
            return self._teacher_toggle("modules", body)
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
        rid = int(float(hw_id)) * 100000 + int(float(sid))
        note = str(body.get("note") or "")[:2000]
        status = str(body.get("status") or "submitted")
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
        period = int(float(period)) if period not in (None, "") else (
            int(float(p.get("seq") or p.get("id") or 1)) if p else 1)
        d = today_str()
        rid = "ck%s-%s-%s" % (sid, d, period)
        old = STORE.get_row("attendance", rid) or {}
        first = str(old.get("checked_at") or "")
        row = dict(old)
        row.pop("_rev", None)
        row.update({
            "id": rid, "student_id": sid, "class_id": st.get("class_id"),
            "date": d, "period": period, "status": "checked",
            "reason": old.get("reason") or "",
            "source": "student",
            # 第一次签到的时间要留住：学生反复点不会把迟到“刷”成准时
            "checked_at": first or time.strftime("%H:%M"),
            "updated_at": now_str(),
        })
        STORE.upsert("attendance", row, "student")
        STORE.audit("student:%s" % sid, "checkin", "%s p%s" % (d, period))
        notify()
        return self._json({"ok": True, "rev": STORE.rev(), "date": d,
                           "period": period,
                           "period_label": (p or {}).get("label") or ("第 %s 节" % period),
                           "checked_at": row["checked_at"],
                           "already": bool(first)})

    def _student_leave(self, body):
        s = self._session()
        if not s or s["role"] != "student":
            return self._json({"ok": False, "msg": "未登录"}, 401)
        if not PJ.module_flags(CFG).get("leave", True):
            return self._json({"ok": False, "msg": "老师未开放请假功能"}, 403)
        d = str(body.get("date") or today_str())
        reason = str(body.get("reason") or "").strip()
        if not reason:
            return self._json({"ok": False, "msg": "请填写请假事由"}, 400)
        sid = s.get("student_id")
        row = {"id": "lv%s-%s" % (sid, d), "student_id": sid, "date": d,
               "reason": reason[:200], "status": "pending", "updated_at": now_str()}
        STORE.upsert("leave_requests", row, "student")
        STORE.audit("student:%s" % sid, "leave-request", d)
        notify()
        return self._json({"ok": True})

    def _teacher_toggle(self, kind, body):
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        cur = CFG.setdefault(kind, {})
        for k, v in (body or {}).items():
            if k in cur or kind == "modules":
                cur[k] = bool(v)
        cfg_save()
        STORE.audit("teacher", "toggle-" + kind, json.dumps(body, ensure_ascii=False))
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
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "只有教师端可以导入"}, 403)
        tables = body.get("tables") or {}
        if not isinstance(tables, dict):
            return self._json({"ok": False, "msg": "格式不对"}, 400)
        counts = STORE.import_snapshot(tables, replace=True)
        notify()
        return self._json({"ok": True, "counts": counts, "rev": STORE.rev()})

def _port():
    return PORT


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
    httpd.daemon_threads = True

    # SSE 长连接被浏览器正常关闭时会抛 ConnectionAborted；
    # 这不是错误，不要往控制台刷几十行堆栈吓老师。
    def _quiet_error(request, client_address):
        pass
    httpd.handle_error = _quiet_error
    print("[workbench] 教师端 http://127.0.0.1:%d/teacher/" % port)
    for ip in lan_ips()[:3]:
        print("[workbench] 学生端 http://%s:%d/s/" % (ip, port))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
