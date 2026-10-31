#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学生提交系统 · 核心服务（仅依赖 Python 标准库）

核心数据规则
------------
1. 以「学号 (sid)」作为唯一主键做去重；
2. 同一学生多次提交 -> 仅保留「最新版本」（按服务端接收时间 received_at 判定）；
3. 所有记录按提交时间生成排行榜（提交顺序：received_at 倒序）；
4. 三类提交方式（文件 / 测试题 / 自定义）全部统一进入上述去重 + 排行流程；
5. 自动适配既有学生名册（班级 / 学号 / 姓名），提交记录与对应学生信息正确关联。

启动：python3 server.py  （默认端口 5077，可用 --port 覆盖）
"""
import os
import sys
import json
import socket
import mimetypes
import time
import uuid
import base64
import shutil
import threading
import datetime
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import courseware as CW
import enhance as ENH

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
DATA = os.path.join(ROOT, "data")
DEFAULT_UPLOAD = os.path.join(DATA, "uploads")
# 静态根目录：默认指向工作台根（class-workbench 目录），使本服务可同时托管
# index.html 与 /submit/* 学生提交页；launch_workbench.py 会用 --static 传入工作台根。
STATIC_ROOT = os.path.dirname(ROOT)

os.makedirs(DATA, exist_ok=True)
os.makedirs(DEFAULT_UPLOAD, exist_ok=True)

CONFIG_PATH = os.path.join(DATA, "config.json")
ROSTER_PATH = os.path.join(DATA, "roster.json")
SUB_PATH = os.path.join(DATA, "submissions.json")
UPLOAD_INDEX = os.path.join(DATA, "uploads_index.json")  # token -> 文件绝对路径

_lock = threading.Lock()

# ---------------------------------------------------------------------------
# 持久化层
# ---------------------------------------------------------------------------
def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 静态目录里「不该被局域网访问」的东西：服务端数据、源码、备份、内部配置。
# 老师把链接发到教室 Wi-Fi，学生（或同网任何人）都能访问这个端口，
# 以前 /submission-system/data/submissions.json 这类文件是能直接下载的。
_FORBIDDEN_DIRS = ("submission-system/", "quickform-server/", "config/", "sql/",
                   "__pycache__/", "._", "_bak/", "_dl/", "qa-audit/", "tools-verify/",
                   "courseware/")   # 课件库只能走 /api/cw/*（可限制仅本机访问），不给静态直链
_FORBIDDEN_EXT = (".py", ".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".sqlite",
                  ".env", ".cwbackup", ".bak", ".log", ".ds_store")
_ALLOW_ROOT_JSON = ("quickform.config.json", "manifest.json")


def _forbidden(name):
    """是否禁止静态访问（相对路径，已去掉前导斜杠）"""
    low = name.lower()
    base = low.rsplit("/", 1)[-1]
    if low in _ALLOW_ROOT_JSON:
        return False
    for d in _FORBIDDEN_DIRS:
        if low.startswith(d) or ("/" + d) in ("/" + low):
            return True
    for ext in _FORBIDDEN_EXT:
        if base.endswith(ext):
            return True
    if base.startswith("."):
        return True
    return False

def lan_ips():
    """本机的局域网 IPv4 列表（学生手机要用的那个地址）。
    只做探测，不发包：UDP connect 不会真的连出去，但能让系统选好出口网卡。
    2026-10-28：活动页链接原来固定用 127.0.0.1，学生手机必然打不开。"""
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
    # 校园/家庭常见网段优先（192.168 / 10 / 172.16-31），虚拟网卡（198.18 等）排后面
    def rank(x):
        if x.startswith("192.168.") or x.startswith("10."):
            return 0
        if x.startswith("172."):
            return 1
        return 2
    ips.sort(key=lambda x: (rank(x), x))
    return ips

def open_folder_cmd(path):
    """跨平台「在文件管理器中打开目录」的命令（拿不到就返回 None）"""
    import platform
    s = platform.system().lower()
    if s == "darwin":
        return ["open", path]
    if s == "windows":
        return ["explorer", os.path.normpath(path)]
    if os.path.isdir(path):
        return ["xdg-open", path]
    return None

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)

def get_config():
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        cfg = default_config()
        save_json(CONFIG_PATH, cfg)
    # 兼容缺字段
    base = default_config()
    for k, v in base.items():
        cfg.setdefault(k, v)
    return cfg

def default_config():
    return {
        "title": "第4课 AI听音识传说 · 作品提交",
        "save_dir": "",
        "deadline": "",
        "allow_resubmit": True,
        "quiz": {
            "intro": "【导学单】请先观看 AI 听音识传说 微课，再完成下列小题。",
            "questions": [
                {"q": "「听音识传说」中 AI 主要通过什么方式识别内容？",
                 "options": ["语音转文字", "图像识别", "手势识别", "气味识别"],
                 "answer": "语音转文字", "score": 25},
                {"q": "下列哪个属于「传说」的典型特征？",
                 "options": ["可重复实验验证", "口耳相传、版本不一", "由公式严格推导", "仅存在于实验室"],
                 "answer": "口耳相传、版本不一", "score": 25},
                {"q": "把一段口头传说整理成文字稿，第一步通常应该？",
                 "options": ["直接改写结局", "忠实记录原话", "删去所有细节", "翻译成外语"],
                 "answer": "忠实记录原话", "score": 25},
                {"q": "用 AI 生成传说插画时，提示词越具体，结果往往？",
                 "options": ["越随机", "越贴合需求", "越模糊", "没有区别"],
                 "answer": "越贴合需求", "score": 25},
            ],
        },
        "custom_html": DEFAULT_CUSTOM_HTML,
        "custom_templates": {},   # 按活动任务维度存放自定义上传页（task_id -> html）
    }

def get_roster():
    return load_json(ROSTER_PATH, None) or DEFAULT_ROSTER

def get_submissions():
    return load_json(SUB_PATH, [])

def save_submissions(arr):
    save_json(SUB_PATH, arr)

DEFAULT_ROSTER = [
    {"cls": "七(3)班", "sid": "20250301", "name": "张明轩"},
    {"cls": "七(3)班", "sid": "20250302", "name": "李思远"},
    {"cls": "七(3)班", "sid": "20250303", "name": "王雨桐"},
    {"cls": "七(3)班", "sid": "20250304", "name": "陈子豪"},
    {"cls": "七(3)班", "sid": "20250305", "name": "刘欣怡"},
    {"cls": "七(3)班", "sid": "20250306", "name": "赵梓涵"},
    {"cls": "七(3)班", "sid": "20250307", "name": "孙浩然"},
    {"cls": "七(3)班", "sid": "20250308", "name": "周可昕"},
]

DEFAULT_CUSTOM_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>自定义提交</title>
<style>
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;max-width:640px;margin:24px auto;padding:0 16px;color:#1A2E28}
  h2{color:#0F6E56}
  label{display:block;margin:12px 0 4px;font-weight:600}
  input,textarea{width:100%;padding:10px;border:1px solid #cfe3dc;border-radius:10px;box-sizing:border-box;font-size:14px}
  button{margin-top:16px;background:#0F6E56;color:#fff;border:0;padding:11px 20px;border-radius:10px;font-size:15px;cursor:pointer}
  #msg{margin-top:12px;font-size:14px}
  .ok{color:#0F6E56}.err{color:#E24B4A}
</style>
</head>
<body>
  <h2>自定义导学单 / 网页</h2>
  <p>这是一份示例自定义提交页。你可以替换下方表单，只要最终调用
     <code>postSubmit(payload)</code> 把数据 POST 到服务器 <code>/api/submit</code>（type=custom）即可。</p>

  <label>学号 *</label><input id="sid" placeholder="如 20250301">
  <label>自定义字段 A</label><input id="a" placeholder="例如：作品主题">
  <label>自定义字段 B</label><textarea id="b" placeholder="例如：创作说明"></textarea>
  <button onclick="submitCustom()">提交</button>
  <div id="msg"></div>

<script>
// 通用提交函数：把任意 payload 发到 /api/submit（type=custom）
async function postSubmit(payload){
  const r = await fetch('/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(Object.assign({type:'custom'}, payload))});
  return await r.json();
}
async function submitCustom(){
  const sid = document.getElementById('sid').value.trim();
  if(!sid){ setMsg('请填写学号','err'); return; }
  const res = await postSubmit({
    sid,
    fields:{ '自定义字段A': document.getElementById('a').value,
             '自定义字段B': document.getElementById('b').value }
  });
  setMsg(res.ok ? ('提交成功：'+ (res.msg||'')) : ('失败：'+ (res.msg||'未知错误')),
          res.ok?'ok':'err');
}
function setMsg(t,cls){ const m=document.getElementById('msg'); m.textContent=t; m.className=cls; }
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# 业务核心：去重 / 排行 / 关联
# ---------------------------------------------------------------------------
def resolve_student(sid, provided_name=None):
    """根据学号在名册中查找班级/姓名；找不到则标记未匹配。"""
    roster = get_roster()
    for s in roster:
        if str(s.get("sid", "")).strip() == str(sid).strip():
            name = provided_name or s.get("name") or ""
            return {"cls": s.get("cls", ""), "name": name, "matched": True}
    return {"cls": "", "name": provided_name or "", "matched": False}

def add_submission(payload):
    """统一提交入口：去重(同号仅留最新) + 落盘 + 关联名册。返回结果字典。"""
    sid = str(payload.get("sid", "")).strip()
    if not sid:
        return {"ok": False, "msg": "缺少学号(sid)"}
    stype = payload.get("type", "file")
    provided_name = (payload.get("name") or "").strip() or None

    # 1) 关联名册
    info = resolve_student(sid, provided_name)
    cls, name, matched = info["cls"], info["name"], info["matched"]

    # 2) 文件落盘
    file_urls = []
    for f in (payload.get("files") or []):
        url = save_upload(sid, f)
        if url:
            file_urls.append(url)

    record = {
        "sid": sid,
        "name": name,
        "cls": cls,
        "matched": matched,
        "type": stype,
        "title": (payload.get("title") or "").strip(),
        "intro": (payload.get("intro") or "").strip(),
        "fields": payload.get("fields") or {},
        "score": payload.get("score"),
        "files": file_urls,
        "submitted_at": (payload.get("submitted_at") or _now_iso()),
        "received_at": _now_iso(),
    }

    # 3) 去重：同号多次提交仅保留最新版本（按 received_at 倒序后保留最新即移除其余）
    arr = get_submissions()
    arr = [r for r in arr if str(r.get("sid", "")) != sid]  # 移除该学号历史
    arr.append(record)
    save_submissions(arr)

    rank = compute_rank(arr, sid)
    return {"ok": True, "msg": "提交成功，已存入并按学号去重", "record": _pub(record), "rank": rank}

def compute_rank(arr, sid):
    ordered = sorted(arr, key=lambda r: r.get("received_at", ""), reverse=True)
    for i, r in enumerate(ordered, 1):
        if str(r.get("sid", "")) == sid:
            return i
    return None

def _register_upload(abs_path):
    """登记上传文件，返回稳定且不带 ../ 的访问 token。"""
    idx = load_json(UPLOAD_INDEX, {})
    token = uuid.uuid4().hex
    idx[token] = os.path.abspath(abs_path)
    save_json(UPLOAD_INDEX, idx)
    return token

def save_upload(sid, f):
    cfg = get_config()
    save_dir = cfg.get("save_dir") or DEFAULT_UPLOAD
    try:
        os.makedirs(save_dir, exist_ok=True)
    except Exception as e:
        save_dir = DEFAULT_UPLOAD
        os.makedirs(save_dir, exist_ok=True)
    raw = f.get("data")
    if not raw:
        return None
    try:
        blob = base64.b64decode(raw)
    except Exception:
        return None
    fname = f.get("name") or "file"
    fname = os.path.basename(fname)  # 安全文件名
    stamp = datetime.datetime.now().strftime("%H%M%S")
    safe_sid = "".join(ch for ch in sid if ch.isalnum()) or "x"
    target = os.path.join(save_dir, f"{safe_sid}_{stamp}_{fname}")
    with open(target, "wb") as fh:
        fh.write(blob)
    token = _register_upload(target)
    return "/api/files/" + token

def build_board():
    """排行榜：去重后的记录按提交时间（received_at）倒序排列。
    说明：排行榜的「班级总人数 / 未交人数」等统计由工作台端用其自身的学生 / 班级数据计算
    （即「排行榜直接使用工作台的数据」），服务端只负责去重与排序并返回提交记录本身。"""
    arr = get_submissions()
    ordered = sorted(arr, key=lambda r: r.get("received_at", ""), reverse=True)
    board = []
    for i, r in enumerate(ordered, 1):
        item = _pub(r)
        item["rank"] = i
        board.append(item)
    return {"board": board, "generated_at": _now_iso()}

def _pub(r):
    d = dict(r)
    d.pop("received_at", None)
    return d

# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "StudentSubmit/1.0"

    def log_message(self, *a):
        pass  # 静默

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, ctype="text/plain; charset=utf-8", code=200):
        body = text.encode("utf-8") if isinstance(text, str) else text
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _read_json(self):
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        # 中文文件名必须解码：浏览器发来的是 %E4%B8%AD%E6%96%87.md，
        # 不 unquote 就永远匹配不到磁盘上的「中文.md」（ASCII 名不受影响）。
        # 解码后再交给 _serve_static_root 做 ../ 与越权校验，安全边界不变。
        path = urllib.parse.unquote(u.path)
        qs = urllib.parse.parse_qs(u.query)

        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return

        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        # ---- API ----
        if path == "/api/status":
            return self._send_json({"ok": True, "service": "student-submit", "time": _now_iso()})
        if path == "/api/net":
            # 给工作台用：学生链接要换成局域网地址（127.0.0.1 学生手机打不开）
            return self._send_json({
                "ok": True,
                "port": self.server.server_port,
                "bind": self.server.server_address[0],
                "ips": lan_ips(),
            })
        if path == "/api/config":
            return self._send_json(get_config())
        if path == "/api/quiz":
            return self._send_json(get_config().get("quiz", {}))
        if path == "/api/board":
            return self._send_json(build_board())
        if path == "/api/custom":
            task = (qs.get("task") or [""])[0]
            cfg = get_config()
            html = ""
            if task and isinstance(cfg.get("custom_templates"), dict):
                html = cfg["custom_templates"].get(task, "")
            if not html:
                html = cfg.get("custom_html", "")
            return self._send_text(html, "text/html; charset=utf-8")
        if path == "/api/roster":
            cls = (qs.get("class") or [""])[0]
            roster = load_json(ROSTER_PATH, [])
            if cls:
                roster = [r for r in roster if str(r.get("cls", "")) == str(cls)]
            return self._send_json({
                "cls": cls,
                "students": [{"sid": r.get("sid", ""), "name": r.get("name", "")} for r in roster]
            })
        if path.startswith("/api/files/"):
            return self._serve_file(path[len("/api/files/"):])

        # ---- 提交增强（模板 / 素材 / 门户 / 提交索引 / 下载）----
        if path.startswith("/api/enh/"):
            return self._enh_get(path, qs)

        # ---- 课件库（教学 → 课件）----
        if path.startswith("/api/cw/"):
            return self._cw_get(path, qs)

        # ---- 学生门户（新入口：一条链接看任务 / 下素材 / 交作品 / 看历史）----
        if path in ("/s", "/s/") or (path.startswith("/s/") and "." not in path.rsplit("/", 1)[-1]):
            return self._serve_static_root("s/index.html")
        if path.startswith("/s/"):
            return self._serve_static_root(path.lstrip("/"))

        # ---- 学生提交页（自定义页由服务端按配置动态返回）----
        if path == "/submit/custom.html":
            task = (qs.get("task") or [""])[0]
            cfg = get_config()
            html = ""
            if task and isinstance(cfg.get("custom_templates"), dict):
                html = cfg["custom_templates"].get(task, "")
            if not html:
                html = cfg.get("custom_html", "")
            return self._send_text(html, "text/html; charset=utf-8")
        if path.startswith("/submit/"):
            return self._serve_static_root(os.path.join("submit", path[len("/submit/"):]))

        # ---- 工作台静态资源（index.html 及其同目录文件）----
        return self._serve_static_root(path.lstrip("/"))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path == "/api/config":
            with _lock:
                data = self._read_json()
                cfg = get_config()
                for k in ("title", "save_dir", "deadline", "allow_resubmit"):
                    if k in data:
                        cfg[k] = data[k]
                if "quiz" in data:
                    cfg["quiz"] = data["quiz"]
                if "custom_html" in data:
                    cfg["custom_html"] = data["custom_html"]
                # 校验 save_dir 可写
                if cfg.get("save_dir"):
                    try:
                        os.makedirs(cfg["save_dir"], exist_ok=True)
                        ok = os.access(cfg["save_dir"], os.W_OK)
                        if not ok:
                            cfg["save_dir"] = ""
                            save_json(CONFIG_PATH, cfg)
                            return self._send_json({"ok": False, "msg": "保存目录不可写，已回退默认目录"}, 400)
                    except Exception:
                        cfg["save_dir"] = ""
                        save_json(CONFIG_PATH, cfg)
                        return self._send_json({"ok": False, "msg": "保存目录无效，已回退默认目录"}, 400)
                save_json(CONFIG_PATH, cfg)
            return self._send_json({"ok": True, "config": cfg})
        if path == "/api/roster":
            data = self._read_json()
            # 支持按班级增量更新：{cls, students:[{sid,name}]}
            if isinstance(data, dict) and "cls" in data:
                cls = str(data.get("cls", "")).strip()
                students = data.get("students") or []
                if not cls:
                    return self._send_json({"ok": False, "msg": "缺少 cls"}, 400)
                cur = load_json(ROSTER_PATH, [])
                # 移除该班级旧名单，再合并新名单（多班级共存）
                cur = [r for r in cur if str(r.get("cls", "")) != cls]
                for s in students:
                    sid = str(s.get("sid", "")).strip()
                    if not sid:
                        continue
                    cur.append({"cls": cls,
                                "sid": sid,
                                "name": str(s.get("name", "")).strip()})
                save_json(ROSTER_PATH, cur)
                return self._send_json({"ok": True, "count": len(cur)})
            # 兼容旧的整表替换：{roster:[...]}
            arr = data.get("roster") if isinstance(data, dict) else None
            if not isinstance(arr, list):
                return self._send_json({"ok": False, "msg": "roster 必须为数组或提供 {cls,students}"}, 400)
            # 规整
            clean = []
            for s in arr:
                sid = str(s.get("sid", "")).strip()
                if not sid:
                    continue
                clean.append({"cls": str(s.get("cls", "")).strip(),
                              "sid": sid,
                              "name": str(s.get("name", "")).strip()})
            save_json(ROSTER_PATH, clean)
            return self._send_json({"ok": True, "count": len(clean)})
        if path == "/api/submit":
            data = self._read_json()
            with _lock:
                res = add_submission(data)
            code = 200 if res.get("ok") else 400
            return self._send_json(res, code)
        if path == "/api/custom":
            data = self._read_json()
            if "html" in data:
                cfg = get_config()
                task = (data.get("task") or "").strip()
                if task:
                    tpls = cfg.setdefault("custom_templates", {})
                    tpls[task] = data["html"]
                else:
                    cfg["custom_html"] = data["html"]
                save_json(CONFIG_PATH, cfg)
                return self._send_json({"ok": True, "task": task})
            return self._send_json({"ok": False, "msg": "缺少 html 字段"}, 400)

        # ---- 提交增强（写操作，含流式上传）----
        if path.startswith("/api/enh/"):
            return self._enh_post(path, u)

        # ---- 课件库（写操作）----
        if path.startswith("/api/cw/"):
            return self._cw_post(path, u, self._read_json())

        return self._send_json({"ok": False, "msg": "未知接口"}, 404)

    # ------------------------------------------------------------------
    # 提交增强：GET / POST
    # ------------------------------------------------------------------
    def _host_base(self):
        h = self.headers.get("Host") or ("127.0.0.1:%d" % self.server.server_port)
        return "http://" + h

    def _enh_get(self, path, qs):
        arg = lambda k: (qs.get(k) or [""])[0]
        # 下载：素材（kind=material + mid）或学生自己的作品（kind=file + name）
        if path in ("/api/enh/dl", "/api/enh/mat"):
            kind = arg("kind")
            if kind == "material" or arg("mid"):
                got = ENH.resolve_material(arg("task"), arg("mid"), arg("token"), arg("sid"))
            else:
                got = ENH.resolve_file(arg("task"), arg("sid"), arg("name"), arg("token"))
            if not got or not os.path.isfile(got):
                return self._send_text("文件不存在或已失效", code=404)
            return self._serve_abs(got, download=(arg("dl") == "1"))
        res = ENH.get_json(path, qs, self._host_base(), (self.client_address or [""])[0])
        if res is None:
            return self._send_json({"ok": False, "msg": "未知接口"}, 404)
        if "__csv" in res:
            body = res["__csv"].encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" +
                             urllib.parse.quote("核对报告-%s.csv" % datetime.datetime.now().strftime("%Y%m%d")))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "__html" in res:
            return self._send_text(res["__html"], "text/html; charset=utf-8")
        return self._send_json(res)

    def _enh_post(self, path, u):
        if path == "/api/enh/upload":
            qs = urllib.parse.parse_qs(u.query)
            arg = lambda k: (qs.get(k) or [""])[0]
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except Exception:
                length = 0
            res = ENH.save_upload(arg("task"), arg("sid"), arg("token"),
                                  urllib.parse.unquote(arg("name")), self.rfile, length)
            return self._send_json(res, 200 if res.get("ok") else 400)
        data = self._read_json()
        res = ENH.post_json(path, data, self._host_base(),
                            (self.client_address or [""])[0], self.headers.get("User-Agent", ""))
        if res is None:
            return self._send_json({"ok": False, "msg": "未知接口"}, 404)
        code = (200 if res.get("ok") else
                400 if res.get("code") in ("E_CHECK", "E_SID", "E_CLS", "E_NOT_IN_ROSTER",
                                           "E_NAME", "E_EMPTY", "E_TOOBIG", "E_UPLOAD",
                                           "E_WRITE", "E_NOTHING", "E_NO_WITHDRAW") else
                403 if res.get("code") in ("E_CLOSED", "E_NO_RESUBMIT", "E_TOKEN", "E_TPL_STALE") else
                200)
        return self._send_json(res, code)

    # ------------------------------------------------------------------
    # 课件库：GET
    # ------------------------------------------------------------------
    def _cw_allowed(self):
        """课件是老师的资料：默认只允许本机访问，避免同 Wi-Fi 的学生随手翻到。
        老师用局域网地址打开工作台时，界面上可以显式打开「允许局域网访问」。"""
        try:
            cfg = CW.load_cfg()
        except Exception:
            cfg = {}
        if cfg.get("allow_lan"):
            return True, ""
        ip = (self.client_address or ("", 0))[0]
        if ip in ("127.0.0.1", "::1", "localhost"):
            return True, ""
        return False, ("课件库默认只允许本机访问（保护老师资料）。当前是从 %s 打开的；"
                       "如果你确实要用这个地址管理课件，请在课件页把「允许局域网访问」打开。" % ip)

    def _cw_get(self, path, qs):
        ok, why = self._cw_allowed()
        if not ok:
            # 只读元信息放行：老师用局域网地址打开时，界面要能显示「为什么被拦 + 怎么开」，
            # 否则就是一个没有任何提示的死路。这里不返回任何课程/文件内容。
            if path == "/api/cw/config":
                cfg = CW.load_cfg()
                return self._send_json({
                    "ok": True, "blocked": True, "msg": why,
                    "allow_lan": bool(cfg.get("allow_lan")), "is_local": False,
                    "root": "", "courses": [], "total": 0, "writable": False, "auto": False,
                })
            return self._send_json({"ok": False, "msg": why, "need_local": True}, 403)
        arg = lambda k, d="": (qs.get(k) or [d])[0]

        if path == "/api/cw/config":
            cfg = CW.load_cfg()
            root = CW.root_dir()
            okw, wmsg = CW.ensure_root(root)
            courses, cerr = CW.list_courses()
            total = sum(c.get("count", 0) for c in (courses or []))
            return self._send_json({
                "ok": True, "root": root, "default_root": CW.DEFAULT_ROOT,
                "auto": bool(cfg.get("auto")), "allow_lan": bool(cfg.get("allow_lan")),
                "writable": okw, "error": ("" if okw else wmsg) or (cerr or ""),
                "updated_at": cfg.get("updated_at", ""),
                "config_file": CW.CFG_PATH,
                "courses": courses or [], "total": total,
                "is_local": (self.client_address or ("", 0))[0] in ("127.0.0.1", "::1"),
            })
        if path == "/api/cw/courses":
            courses, err = CW.list_courses()
            return self._send_json({"ok": not err, "courses": courses or [], "msg": err or ""})
        if path == "/api/cw/list":
            course = arg("course")
            files = CW.list_files(course) if course else CW.all_files()[0]
            return self._send_json({"ok": True, "course": course, "files": files})
        if path == "/api/cw/file":
            full = CW.abs_of(arg("id"), must_exist=True)
            if not full or not os.path.isfile(full):
                return self._send_text("文件不存在或已被移动", code=404)
            return self._serve_abs(full, download=(arg("dl") == "1"))
        if path == "/api/cw/preview":
            full = CW.abs_of(arg("id"), must_exist=True)
            if not full or not os.path.isfile(full):
                return self._send_text("文件不存在或已被移动", code=404)
            kind = CW.kind_of(full)
            if kind in ("image", "video", "audio", "pdf"):
                # 这几种浏览器能直接显示，不必再包一层 HTML
                return self._serve_abs(full)
            try:
                page = CW.preview_html(full, os.path.basename(full), dark=(arg("dark") == "1"))
            except Exception as e:
                page = "<p>预览失败：%s</p>" % str(e)
            return self._send_text(page, "text/html; charset=utf-8")
        # ---- 资料夹（本地文件夹，只读加载）----
        if path == "/api/cw/mounts":
            return self._send_json({"ok": True, "mounts": CW.mounts_list(),
                                    "max": CW.MAX_MOUNTS, "scan_limit": CW.SCAN_LIMIT,
                                    "depth": CW.SCAN_DEPTH})
        if path == "/api/cw/mount/scan":
            res = CW.mount_scan(arg("id"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/mount/file":
            full = CW.mount_abs(arg("id"), arg("rel"))
            if not full:
                return self._send_text("文件不存在或不在这个资料夹里", code=404)
            return self._serve_abs(full, download=(arg("dl") == "1"))
        if path == "/api/cw/mount/preview":
            full = CW.mount_abs(arg("id"), arg("rel"))
            if not full:
                return self._send_text("文件不存在或不在这个资料夹里", code=404)
            kind = CW.kind_of(full)
            if kind in ("image", "video", "audio", "pdf"):
                return self._serve_abs(full)
            try:
                page = CW.preview_html(full, os.path.basename(full), dark=(arg("dark") == "1"))
            except Exception as e:
                page = "<p>预览失败：%s</p>" % str(e)
            return self._send_text(page, "text/html; charset=utf-8")
        if path == "/api/cw/remote":
            # 学生提交的作品在 QuickForm 里，浏览器没法直接渲染 docx/xlsx/pptx，
            # 这里取回来现场解析成 HTML（图片/音视频/PDF 前端直接连原地址，不绕这一道）
            try:
                page, name, kind = CW.remote_preview_html(arg("base"), arg("url"),
                                                          dark=(arg("dark") == "1"))
            except Exception as e:
                return self._send_text(
                    "<!doctype html><meta charset='utf-8'><body style=\"font:14px/1.9 -apple-system,'PingFang SC',sans-serif;"
                    "padding:26px;color:#8A5A10;background:#FFF9EF\">预览失败：%s</body>" % str(e),
                    "text/html; charset=utf-8")
            if page is None:
                return self._send_text("这个格式请直接打开原文件", code=204)
            return self._send_text(page, "text/html; charset=utf-8")
        return self._send_json({"ok": False, "msg": "未知接口"}, 404)

    # ------------------------------------------------------------------
    # 课件库：POST
    # ------------------------------------------------------------------
    def _cw_post(self, path, u, data):
        ok, why = self._cw_allowed()
        if not ok:
            # 唯一的例外：从这个地址把「允许局域网访问」打开（否则老师永远没法自己解锁）
            if path == "/api/cw/config" and not data.get("root") and data.get("allow_lan") is not None:
                cfg = CW.load_cfg()
                cfg["allow_lan"] = bool(data.get("allow_lan"))
                cfg["updated_at"] = CW._now()
                CW.save_cfg(cfg)
                return self._send_json({"ok": True, "msg": "已允许局域网访问课件库", "allow_lan": cfg["allow_lan"]})
            return self._send_json({"ok": False, "msg": why, "need_local": True}, 403)

        if path == "/api/cw/config":
            if data.get("allow_lan") is not None and not data.get("root"):
                cfg = CW.load_cfg()
                cfg["allow_lan"] = bool(data.get("allow_lan"))
                cfg["updated_at"] = CW._now()
                CW.save_cfg(cfg)
                return self._send_json({"ok": True, "msg": "已更新访问权限", "allow_lan": cfg["allow_lan"]})
            ok2, cfg, msg = CW.set_root(data.get("root"), bool(data.get("move")))
            return self._send_json({"ok": ok2, "msg": msg, "root": (cfg or {}).get("root", "")},
                                   200 if ok2 else 400)
        if path == "/api/cw/upload":
            res = CW.save_upload(data.get("course") or "未分课程", data.get("files") or [])
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/delete":
            res = CW.delete_file(data.get("id"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/move":
            res = CW.rename_or_move(data.get("id"), data.get("course"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/archive":
            res = CW.archive_items(data.get("course"), data.get("activity"),
                                   data.get("base") or "", data.get("items") or [])
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/save_text":
            res = CW.save_text(data.get("course"), data.get("subdir"),
                               data.get("name"), data.get("text") or "")
            return self._send_json(res, 200 if res.get("ok") else 400)
        # ---- 资料夹（本地文件夹，只读加载）----
        if path == "/api/cw/pickdir":
            okp, val = CW.pick_dir()
            if okp:
                return self._send_json({"ok": True, "path": val, "msg": "已选择：" + val})
            return self._send_json({"ok": False, "msg": val, "cancelled": ("取消" in val)}, 400)
        if path == "/api/cw/mount/add":
            res = CW.mount_add(data.get("path"), data.get("name"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/mount/update":
            res = CW.mount_update(data.get("id"), data.get("name"), data.get("path"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/mount/remove":
            res = CW.mount_remove(data.get("id"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/mount/alias":
            res = CW.mount_set_alias(data.get("id"), data.get("rel"), data.get("alias"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/mount/hide":
            res = CW.mount_set_hidden(data.get("id"), data.get("rels") or [],
                                      bool(data.get("hidden", True)))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/mount/to_library":
            res = CW.mount_copy_to_library(data.get("id"), data.get("rels") or [],
                                           data.get("course") or "", bool(data.get("move")))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/mount/open":
            # 支持三种传参：rel（单个）/ rels（多选）/ 都不传（打开资料夹根目录）
            target = ""
            rels = [str(x) for x in (data.get("rels") or []) if str(x).strip()]
            rel = str(data.get("rel") or "").strip()
            if rel:
                rels.append(rel)
            dirs = set()
            resolved = 0
            for r in rels:
                full = CW.mount_abs(data.get("id"), r)
                if full:
                    resolved += 1
                    dirs.add(os.path.dirname(full))
            if rels and not resolved:
                return self._send_json({"ok": False, "msg": "这些文件已不在原位置（可能被移动或删除），无法定位。点「重新加载」刷新列表后再试"}, 404)
            if len(dirs) == 1:
                target = dirs.pop()
            if not target:
                m = CW.mount_find(data.get("id"))
                target = CW.mount_root(m)
            if not target or not os.path.isdir(target):
                return self._send_json({"ok": False, "msg": "文件夹不存在或已断开"}, 400)
            opener = open_folder_cmd(target)
            if not opener:
                return self._send_json({"ok": False, "msg": "这个系统不支持自动打开文件夹，请手动打开：" + target}, 400)
            import subprocess
            subprocess.Popen(opener, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            msg = ("所选文件分散在多个子文件夹，已打开资料夹根目录：" if len(dirs) > 1 else "已在文件管理器中打开：") + target
            return self._send_json({"ok": True, "path": target, "msg": msg})
        if path == "/api/cw/open":
            target = CW.root_dir()
            course = str(data.get("course") or "").strip()
            if course:
                p = CW.abs_of(CW.safe_course(course))
                if p and os.path.isdir(p):
                    target = p
            try:
                opener = open_folder_cmd(target)
                if not opener:
                    return self._send_json({"ok": False, "msg": "这个系统不支持自动打开文件夹，请手动打开：" + target}, 400)
                import subprocess
                subprocess.Popen(opener, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self._send_json({"ok": True, "msg": "已打开：" + target, "path": target})
            except Exception as e:
                return self._send_json({"ok": False, "msg": "打开失败：%s" % e}, 400)
        return self._send_json({"ok": False, "msg": "未知接口"}, 404)

    def _serve_abs(self, full, download=False, ctype=None, name=None):
        """提供任意绝对路径文件（已由调用方做路径校验），支持 Range 以便音视频拖动进度。"""
        try:
            size = os.path.getsize(full)
        except Exception:
            return self._send_text("读取失败", code=500)
        ctype = ctype or mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "application/javascript"):
            ctype += "; charset=utf-8"
        rng = self.headers.get("Range") or ""
        m = None
        if rng.startswith("bytes="):
            m = rng[len("bytes="):].split(",")[0]
        if m:
            try:
                a, b = m.split("-", 1)
                start = int(a) if a else 0
                end = int(b) if b else size - 1
                end = min(end, size - 1)
                if start > end or start < 0:
                    raise ValueError
            except Exception:
                start, end = 0, size - 1
            length = end - start + 1
            self.send_response(206)
            self._cors()
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.send_header("Content-Length", str(length))
            if download:
                self.send_header("Content-Disposition",
                                 "attachment; filename*=UTF-8''" + urllib.parse.quote(name or os.path.basename(full)))
            self.end_headers()
            try:
                with open(full, "rb") as f:
                    f.seek(start)
                    remain = length
                    while remain > 0:
                        chunk = f.read(min(65536, remain))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remain -= len(chunk)
            except Exception:
                pass
            return
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(size))
        if download:
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''" + urllib.parse.quote(name or os.path.basename(full)))
        self.end_headers()
        try:
            with open(full, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 65536)
        except Exception:
            pass


    def _serve_static(self, name):
        p = os.path.join(PUBLIC, name)
        if not os.path.isfile(p):
            return self._send_text("文件不存在: " + name, code=404)
        ctype = "text/html; charset=utf-8" if name.endswith(".html") else "application/octet-stream"
        with open(p, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static_root(self, name):
        """从工作台静态根目录安全地提供文件（index.html、/submit/* 等）。"""
        name = (name or "").replace("\\", "/").lstrip("/")
        if not name or name.startswith("/") or ".." in name.split("/"):
            return self._send_text("禁止访问", code=403)
        if _forbidden(name):
            return self._send_text("禁止访问", code=403)
        base = os.path.normpath(STATIC_ROOT)
        full = os.path.normpath(os.path.join(base, name))
        # 用 commonpath 而不是 startswith：startswith 会把 /a/bc 当成 /a/b 的子路径
        try:
            if os.path.commonpath([base, full]) != base:
                return self._send_text("禁止访问", code=403)
        except ValueError:
            return self._send_text("禁止访问", code=403)
        if not os.path.isfile(full):
            return self._send_text("未找到资源: " + name, code=404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        try:
            with open(full, "rb") as f:
                body = f.read()
        except Exception:
            return self._send_text("读取失败", code=500)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, token):
        # token 由 _register_upload 登记，映射为文件绝对路径（避免 ../ 穿越与丑陋路径）
        idx = load_json(UPLOAD_INDEX, {})
        full = idx.get(token)
        if not full or not os.path.isfile(full):
            return self._send_text("文件不存在", code=404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def main():
    global STATIC_ROOT
    port = 5077
    bind = "127.0.0.1"
    for i, a in enumerate(sys.argv):
        if a == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
        elif a.startswith("--port="):
            port = int(a.split("=", 1)[1])
        elif a == "--static" and i + 1 < len(sys.argv):
            STATIC_ROOT = os.path.abspath(sys.argv[i + 1])
        elif a.startswith("--static="):
            STATIC_ROOT = os.path.abspath(a.split("=", 1)[1])
        elif a == "--bind" and i + 1 < len(sys.argv):
            bind = sys.argv[i + 1]
        elif a.startswith("--bind="):
            bind = a.split("=", 1)[1]
    # 预热默认数据
    get_config()
    if not os.path.isfile(ROSTER_PATH):
        save_json(ROSTER_PATH, DEFAULT_ROSTER)
    httpd = ThreadingHTTPServer((bind, port), Handler)
    print(f"[学生提交系统] 已启动：http://127.0.0.1:{port}/")
    print(f"[学生提交系统] 工作台：http://127.0.0.1:{port}/index.html")
    print(f"[学生提交系统] 学生页：/submit/file.html  /submit/quiz.html  /submit/custom.html")
    if bind in ("0.0.0.0", "", "::"):
        for ip in lan_ips()[:3]:
            print(f"[学生提交系统] 局域网（学生手机连同一 Wi-Fi 用）：http://{ip}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")

if __name__ == "__main__":
    main()
