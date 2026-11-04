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
# 提交系统数据目录：默认 submission-system/data/；容器里用 WB_SUBMISSION_DATA_DIR
# 指到主数据卷下的 submissions/，避免容器重建丢名册与提交记录。
# 兜底：只设了 WB_DATA_DIR（比如自己手写 docker run）时，跟着它走 ——
# 否则数据会落在镜像内的 submission-system/data，容器一重建就没了。
DATA = (os.environ.get("WB_SUBMISSION_DATA_DIR")
        or (os.path.join(os.environ["WB_DATA_DIR"], "submissions")
            if os.environ.get("WB_DATA_DIR") else "")
        or os.path.join(ROOT, "data"))
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
# 与主服务（app/server.py）的对接点
# ---------------------------------------------------------------------------
# 本模块有两种运行形态：
#   ① 被主服务 import 复用（同一进程、同一端口）—— 主服务会注入下面三个钩子，
#      于是「谁是老师 / 谁是学生 / 花名册是什么」都以主服务的会话与数据库为准；
#   ② 用 python server.py 单独起（旧用法，默认只绑 127.0.0.1）—— 没有会话体系，
#      此时只有回环直连的请求被视为管理员，其它一律按未登录处理。
# 以前这里完全没有身份概念：同网任何人 POST /api/config 就能改页面、看名单、
# 甚至把 save_dir 指到别的目录。钩子就是为了补上这一层。
ROLE_FN = None           # handler -> ("teacher"|"student"|"", student_key)
STUDENT_SID_FN = None    # handler -> 学号字符串（与花名册 sid 同一口径）
ROSTER_PROVIDER = None   # () -> [{cls, sid, name}]，由主服务从学生表投影

MAX_JSON_BODY = 32 * 1024 * 1024       # JSON 请求体上限（全量备份导入要整库回传，8MB 不够）
MAX_UPLOAD_BODY = 320 * 1024 * 1024    # 上传接口上限（略大于单文件 300MB，原始字节流）
# 课件上传走的是 base64 JSON，体积膨胀 4/3：300MB 文件 → 约 400MB 请求体，
# 再留 JSON 结构与文件名的余量（2026-11-04：之前误用通用 32MB 上限，
# 实际传 24MB 以上的课件必失败，与界面承诺的 300MB 相差十几倍）。
MAX_CW_UPLOAD_BODY = 440 * 1024 * 1024

# ---------------------------------------------------------------------------
# 持久化层
# ---------------------------------------------------------------------------
def _now_iso():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# 静态资源白名单（2026-10-31 加固）
# ---------------------------------------------------------------------------
# 以前是「黑名单」：先允许一切，再把 data/、*.py、*.db 这些挡掉。
# 黑名单永远会漏——新增一个目录、换一个扩展名，就又漏一次；
# 而且静态根就是程序根，`/data/config.json`（教师口令哈希）、
# `/data/submissions/uploads_index.json`（token→文件路径）都能匿名下载。
# 现在改成白名单：只放行页面真正会引用的这几个顶层条目，其余一律 404。
_ALLOW_ROOT_FILES = ("index.html", "manifest.json",
                     "submit-enh.js", "submit-enh.css", "sw.js",
                     # 2.0.4 修复：教师端页面引用的这两个顶层脚本此前不在白名单里，
                     # 一直 403，导致服务端同步（netsync）与备份模块（backup.js）加载失败
                     "netsync.js", "backup.js")
_ALLOW_DIRS = ("vendor/", "icons/", "submit/", "student/", "assets/", "images/")


def _forbidden(name):
    """是否禁止静态访问（相对路径，已去掉前导斜杠）。

    返回 True = 拒绝。规则是白名单：顶层必须是已知文件或已知目录之一，
    且文件名不以 . 开头、扩展名在页面会用到的范围内。
    """
    low = name.lower().replace("\\", "/")
    if not low or low.startswith(".") or ".." in low.split("/"):
        return True
    parts = low.split("/")
    base = parts[-1]
    if base.startswith("."):
        return True
    if len(parts) == 1:
        return low not in _ALLOW_ROOT_FILES
    if parts[0] + "/" not in _ALLOW_DIRS:
        return True
    # 目录里仍然挡住可执行/数据类扩展名（白名单目录也可能被人塞文件进去）
    # .json 不挡：vendor/quotes-d.json 是页面要用的语料库；
    # 真正的数据文件都在 data/ 下，已经被上面的目录白名单挡住了。
    if base.endswith((".py", ".pyc", ".pyo", ".db", ".db-wal", ".db-shm",
                      ".sqlite", ".env", ".bak", ".log", ".key", ".pem")):
        return True
    return False

def lan_ips():
    """本进程所在主机的局域网 IPv4 列表（学生手机要用的那个地址）。
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
    """花名册：优先取主服务注入的实时投影，取不到才读 roster.json。

    以前这里只读 submission-system/data/roster.json，而主工作台的学生名单在
    SQLite 里，两份名单互不同步——教师在「学生」页加了人，提交页却看不到，
    于是「学生名单直接来自教师端」这个承诺一直没兑现。现在统一走 provider。
    """
    if ROSTER_PROVIDER is not None:
        try:
            rows = ROSTER_PROVIDER() or []
            if rows:
                return rows
        except Exception:
            pass
    return load_json(ROSTER_PATH, None) or []

def get_submissions():
    return load_json(SUB_PATH, [])

def save_submissions(arr):
    save_json(SUB_PATH, arr)

# 默认花名册：故意为空。
# 以前这里预置了 8 个虚构学生（张明轩 / 李思远 …），首次启动就写进 roster.json：
# 老师一打开提交系统就以为「系统里有 8 个学生」，本班真名单反而要手工覆盖；
# 这些名字还会出现在排行榜、导出 CSV 和「我的提交」里，属于无中生有的数据。
# 现在没有名册就是空，页面会明确提示「还没有学生，请先在教师端建班」。
DEFAULT_ROSTER = []

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

def grade_quiz(fields):
    """按配置里的标准答案在服务端算分。

    以前分数是浏览器算好后跟着 payload 一起提交的（`score: 100`），
    服务端原样收下——学生改一行 JS 就能拿满分；而且 /api/config 会把
    `answer` 一起发给浏览器，题目本身就等于公开了。
    现在答案只留在服务端，分数也只在这里算。
    """
    qs = ((get_config().get("quiz") or {}).get("questions")) or []
    total = 0
    for i, q in enumerate(qs, 1):
        ans = str((fields or {}).get("Q%d" % i) or "").strip()
        want = str(q.get("answer") or "").strip()
        if ans and want and ans == want:
            try:
                total += int(q.get("score") or 0)
            except Exception:
                pass
    return total


def add_submission(payload):
    """统一提交入口：去重(同号仅留最新) + 落盘 + 关联名册。返回结果字典。"""
    sid = str(payload.get("sid", "")).strip()
    if not sid:
        return {"ok": False, "msg": "缺少学号(sid)"}
    stype = payload.get("type", "file")
    provided_name = (payload.get("name") or "").strip() or None
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}

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
        "fields": fields,
        # 题目类由服务端算分；其它类型保留调用方给的分（老师补录用）
        "score": grade_quiz(fields) if stype == "quiz" else payload.get("score"),
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

def _register_upload(abs_path, sid=""):
    """登记上传文件，返回稳定且不带 ../ 的访问 token。

    同时记下归属学号：下载时据此判断「是不是你自己的」。
    老索引里是纯字符串（只有路径），读取侧做了兼容。
    """
    idx = load_json(UPLOAD_INDEX, {})
    token = uuid.uuid4().hex
    idx[token] = {"path": os.path.abspath(abs_path), "sid": str(sid or ""),
                  "at": _now_iso()}
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
    # base64 先估长度再解码：不然一个 2GB 的字符串会在 b64decode 里直接吃满内存
    if len(raw) * 3 // 4 > MAX_UPLOAD_BODY:
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
    token = _register_upload(target, sid)
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

    # ------------------------------------------------------------------
    # 同源 / 身份
    # ------------------------------------------------------------------
    def _same_origin(self):
        """跨站请求判定。浏览器发跨站 POST 一定带 Origin（表单也一样），
        所以「有 Origin 且不等于本站 Host」就拒绝。"""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        try:
            return urllib.parse.urlparse(origin).netloc == host
        except Exception:
            return False

    def _is_local(self):
        """是否回环直连（不经反向代理 / 端口映射）。

        注意：容器部署下所有请求都来自网关的内网地址，这里恒为 False，
        所以任何「按 IP 判身份」的逻辑都不能作为访问闸门——一律用 _role()。
        只用于「需要桌面环境」的原生操作（弹目录框、打开资源管理器）。
        """
        try:
            return (self.client_address or ("", 0))[0] in ("127.0.0.1", "::1")
        except Exception:
            return False

    def _role(self):
        """返回 (role, key)。role: "teacher" / "student" / "" """
        if ROLE_FN is not None:
            try:
                r = ROLE_FN(self)
                if r and r[0]:
                    return r[0], r[1]
            except Exception:
                pass
            return "", None
        # 单独运行旧服务时没有会话体系：只把回环直连当作管理员
        return ("teacher", None) if self._is_local() else ("", None)

    def _student_sid(self):
        if STUDENT_SID_FN is None:
            return ""
        try:
            return str(STUDENT_SID_FN(self) or "")
        except Exception:
            return ""

    def _deny(self, msg="需要先登录", code=401):
        self._send_json({"ok": False, "msg": msg, "need_login": code == 401}, code)
        return True

    def _need_teacher(self, write=True):
        """老师专用接口。返回 True 表示已经拒绝（调用方直接 return）。"""
        role, _ = self._role()
        if role == "teacher":
            return False
        if write:
            return self._deny("只有教师端可以执行这个操作", 403)
        return self._deny("只有教师端可以查看这个内容", 403)

    def _need_login(self):
        """任意已登录角色。"""
        role, _ = self._role()
        return None if role else self._deny("请先登录")

    # ------------------------------------------------------------------
    # 响应
    # ------------------------------------------------------------------
    def _cors(self):
        """只对同源请求回显 CORS 头。

        原来是无条件 `Access-Control-Allow-Origin: *`：任何网页都能
        `fetch('http://服务端:20020/api/roster')` 把全班名单读走（含学号姓名），
        也能读到 /api/config、/api/board。本应用所有页面都是同源加载，
        根本不需要 CORS，这里只在确实是同源时回显，跨站一律不给。
        """
        origin = self.headers.get("Origin")
        if not origin:
            return
        host = self.headers.get("Host", "")
        try:
            if urllib.parse.urlparse(origin).netloc == host:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
        except Exception:
            pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, ctype="text/plain; charset=utf-8", code=200):
        body = text.encode("utf-8") if isinstance(text, str) else text
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 学习单页（/submit/custom.html、GET /api/custom）改版后 URL 不变，
        # 没有缓存头时浏览器启发式缓存会拿旧页——学生端「改了学习单还是旧的」
        # 的另一半根因。这类内容都是动态的教师设计页，一律不准缓存。
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self, limit=MAX_JSON_BODY, drain_cap=0):
        """读取请求体，带大小上限。

        以前直接 `rfile.read(Content-Length)`：一个 `Content-Length: 4G`
        的请求就能把进程内存吃光（无需任何登录）。现在超限直接拒绝，
        并且按 max(0, ·) 处理，负数和非法值都当 0。

        超限拒绝前如果客户端还在发数据，直接回响应会被 TCP RST，
        浏览器端看到的不是「文件太大」而是「连接中断」。所以先把手头
        的请求体读掉再拒绝（最多 drain_cap 字节，防止被 4G 请求拖死）。
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length < 0:
            length = 0
        if length > limit:
            if drain_cap and length <= drain_cap:
                left = length
                while left > 0:
                    chunk = self.rfile.read(min(1024 * 1024, left))
                    if not chunk:
                        break
                    left -= len(chunk)
            raise ValueError("请求体过大（%d 字节，上限 %d）" % (length, limit))
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _read_json(self, limit=MAX_JSON_BODY):
        try:
            raw = self._read_body(limit, drain_cap=MAX_CW_UPLOAD_BODY)
        except ValueError as e:
            self._send_json({"ok": False, "msg": str(e)}, 413)
            return None
        if not raw:
            return {}
        try:
            d = json.loads(raw.decode("utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _json_content_type_ok(self):
        """写接口必须是 application/json。

        允许 text/plain 的 JSON 会造成 CSRF：恶意页面用
        `fetch(url,{method:'POST',mode:'no-cors',body:'{"custom_html":"..."}'})`
        不触发预检就把内容写进来（本服务用 json.loads 解析，不看 Content-Type）。
        """
        ct = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return ct in ("application/json", "text/json", "")

    def do_OPTIONS(self):
        # 不再无条件发 ACAO:*；同源请求本来就不需要预检
        self.send_response(204)
        self._cors()
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
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
            # port 必须取「浏览器访问的那个端口」= 宿主机映射端口，不能用 server_port：
            # Docker 里 compose 把宿主机端口改成 30000，容器内仍是 20020，
            # 用容器端口拼出来学生还是会连错。
            base = self._host_base()
            try:
                host_port = urllib.parse.urlparse(base).port or self.server.server_port
            except Exception:
                host_port = self.server.server_port
            return self._send_json({
                "ok": True,
                "port": host_port,
                "bind": self.server.server_address[0],
                "ips": lan_ips(),
                "base": base,
            })
        if path == "/api/config":
            return self._send_json(self._public_config())
        if path == "/api/quiz":
            return self._send_json(self._public_quiz())
        if path == "/api/board":
            return self._board()
        if path == "/api/custom":
            task = (qs.get("task") or [""])[0]
            return self._send_text(self._custom_html(task), "text/html; charset=utf-8")
        if path == "/api/roster":
            if self._need_login():
                return
            cls = (qs.get("class") or [""])[0]
            roster = get_roster()
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
            return self._send_text(self._custom_html(task), "text/html; charset=utf-8")
        if path.startswith("/submit/"):
            return self._serve_static_root(os.path.join("submit", path[len("/submit/"):]))

        # ---- 工作台静态资源（index.html 及其同目录文件）----
        return self._serve_static_root(path.lstrip("/"))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        # ---- 通用门槛：同源 + JSON ----
        if not self._same_origin():
            return self._send_json({"ok": False, "msg": "跨站请求被拒绝"}, 403)
        if path.startswith("/api/enh/upload") or path == "/api/cw/upload_raw":
            pass                        # 流式上传，Content-Type 是 application/octet-stream
        elif not self._json_content_type_ok():
            return self._send_json({"ok": False, "msg": "请求格式必须是 JSON"}, 415)

        if path == "/api/config":
            if self._need_teacher():
                return
            with _lock:
                data = self._read_json()
                if data is None:
                    return
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
            if self._need_teacher():
                return
            data = self._read_json()
            if data is None:
                return
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
            if data is None:
                return
            # 身份：老师可代交（导入/纠错），学生只能交自己的那一条
            role, _k = self._role()
            if role not in ("teacher", "student"):
                return self._deny("请先在 /s/ 登录后再提交", 401)
            if role == "student":
                mine = self._student_sid()
                if not mine or str(data.get("sid", "")).strip() != mine:
                    return self._send_json({"ok": False, "msg": "不能替别的同学提交"}, 403)
            with _lock:
                res = add_submission(data)
            code = 200 if res.get("ok") else 400
            return self._send_json(res, code)
        if path == "/api/custom":
            if self._need_teacher():
                return
            data = self._read_json()
            if data is None:
                return
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

        # ---- 课件库流式直传（2026-11-04）：请求体 = 文件原始字节 ----
        # base64 JSON 上传要在浏览器里把整个文件读成字符串再翻倍序列化，
        # 大文件直接把浏览器内存顶爆（OOM 崩页）。改走 octet-stream 直传。
        if path == "/api/cw/upload_raw":
            return self._cw_upload_raw(u)

        # ---- 课件库（写操作）----
        if path.startswith("/api/cw/"):
            # 上传是 base64 JSON，膨胀 4/3，单独放宽；其余课件接口走通用上限
            data = self._read_json(MAX_CW_UPLOAD_BODY if path == "/api/cw/upload"
                                   else MAX_JSON_BODY)
            if data is None:
                return
            return self._cw_post(path, u, data)

        return self._send_json({"ok": False, "msg": "未知接口"}, 404)

    # ------------------------------------------------------------------
    # 提交增强：GET / POST
    # ------------------------------------------------------------------
    def _host_base(self):
        h = self.headers.get("Host") or ("127.0.0.1:%d" % self.server.server_port)
        return "http://" + h

    # ------------------------------------------------------------------
    # 配置 / 题目 / 榜单：对外只给「页面需要的部分」
    # ------------------------------------------------------------------
    def _public_config(self):
        """给提交页的配置视图。

        原来 GET /api/config 直接把整份配置吐出来，里面有两类不该外泄的东西：
          · save_dir —— 服务器上的绝对路径（泄露用户名、目录结构）；
          · custom_html / custom_templates —— 教师自定义页，属于管理面数据；
          · quiz.questions[].answer —— 标准答案！
        学生打开 /api/config 就能拿到全部答案，随堂测直接失去意义。
        现在只给标题、截止时间、是否允许重交、是否开启题目、班级匹配模式。
        """
        cfg = get_config()
        quiz = cfg.get("quiz") or {}
        qs = quiz.get("questions") or []
        tpl_names = []
        t = cfg.get("custom_templates")
        if isinstance(t, dict):
            tpl_names = sorted([k for k in t.keys() if k])
        return {
            "ok": True,
            "title": cfg.get("title", ""),
            "deadline": cfg.get("deadline", ""),
            "allow_resubmit": bool(cfg.get("allow_resubmit", True)),
            "quiz": {
                "intro": quiz.get("intro", ""),
                "count": len(qs),
                "total_score": sum(int(q.get("score") or 0) for q in qs),
            },
            "custom_tasks": tpl_names,
        }

    def _public_quiz(self):
        """题目（不含答案）。答案只在服务端算分时用。"""
        quiz = get_config().get("quiz") or {}
        out = []
        for q in (quiz.get("questions") or []):
            out.append({
                "q": q.get("q", ""),
                "options": q.get("options") or [],
                "score": int(q.get("score") or 0),
                # 故意不带 answer
            })
        return {"intro": quiz.get("intro", ""), "questions": out}

    def _board(self):
        """排行榜：老师看全部；学生只能看自己的名次与提交状态。

        原来匿名 GET /api/board 就能拿到全班学号、姓名、提交文件名，
        而每一条的 files 里还带着 /api/files/<token> 下载地址 ——
        等于把所有人的作业公开了。现在按角色裁剪。
        """
        role, _ = self._role()
        data = build_board()
        if role == "teacher":
            return self._send_json(data)
        sid = self._student_sid() if role == "student" else ""
        if not sid:
            return self._deny("请先登录")
        board = [b for b in data.get("board", []) if str(b.get("sid", "")) == sid]
        for b in board:
            b.pop("files", None)     # 学生不需要、也不该拿到自己的下载 token
        return self._send_json({"board": board, "generated_at": data.get("generated_at", ""),
                                "self_only": True})

    def _custom_html(self, task=""):
        """教师自定义提交页（公开可见，它本身就是给学生填的页面）。

        带 task 时只认该活动的模板：查不到就回落「未配置」占位串——
        绝不能回落全局 custom_html，否则新活动会串出别的活动（或全局
        默认）页，教师端「有没有页」的探测也会失灵（2026-11-04）。
        """
        cfg = get_config()
        html = ""
        if task:
            tpls = cfg.get("custom_templates")
            if isinstance(tpls, dict):
                html = tpls.get(task, "")
        else:
            html = cfg.get("custom_html", "")
        return html or "<!doctype html><meta charset='utf-8'><p>老师还没有配置自定义提交页。</p>"

    def _auth_ctx(self):
        role, _k = self._role()
        return {"role": role, "sid": self._student_sid(), "is_local": self._is_local()}

    def _enh_get(self, path, qs):
        arg = lambda k: (qs.get(k) or [""])[0]
        auth = self._auth_ctx()
        # 下载：素材（kind=material + mid）或学生自己的作品（kind=file + name）
        if path in ("/api/enh/dl", "/api/enh/mat"):
            if not auth["role"]:
                return self._send_text("请先登录", code=401)
            kind = arg("kind")
            if kind == "material" or arg("mid"):
                got = ENH.resolve_material(arg("task"), arg("mid"), arg("token"), arg("sid"),
                                           auth=auth)
            else:
                got = ENH.resolve_file(arg("task"), arg("sid"), arg("name"), arg("token"),
                                       auth=auth)
            if not got or not os.path.isfile(got):
                return self._send_text("文件不存在或已失效", code=404)
            return self._serve_abs(got, download=(arg("dl") == "1"))
        res = ENH.get_json(path, qs, self._host_base(), (self.client_address or [""])[0],
                           auth=auth)
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
        code = 200
        if not res.get("ok", True):
            code = int(res.get("_code") or 403)
        return self._send_json(res, code)

    def _enh_post(self, path, u):
        auth = self._auth_ctx()
        if path == "/api/enh/upload":
            if not auth["role"]:
                return self._send_json({"ok": False, "code": "E_TOKEN",
                                        "msg": "请先在 /s/ 登录后再上传"}, 401)
            qs = urllib.parse.parse_qs(u.query)
            arg = lambda k: (qs.get(k) or [""])[0]
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except Exception:
                length = 0
            if length < 0:
                length = 0
            if length > MAX_UPLOAD_BODY:
                return self._send_json({"ok": False, "code": "E_TOOBIG",
                                        "msg": "文件太大（上限 300MB）"}, 413)
            res = ENH.save_upload(arg("task"), arg("sid"), arg("token"),
                                  urllib.parse.unquote(arg("name")), self.rfile, length,
                                  auth=auth)
            return self._send_json(res, 200 if res.get("ok") else 400)
        data = self._read_json()
        if data is None:
            return
        res = ENH.post_json(path, data, self._host_base(),
                            (self.client_address or [""])[0], self.headers.get("User-Agent", ""),
                            auth=auth)
        if res is None:
            return self._send_json({"ok": False, "msg": "未知接口"}, 404)
        if res.get("ok"):
            code = 200
        elif res.get("_code"):
            code = int(res["_code"])
        elif res.get("code") in ("E_CHECK", "E_SID", "E_CLS", "E_NOT_IN_ROSTER",
                                 "E_NAME", "E_EMPTY", "E_TOOBIG", "E_UPLOAD",
                                 "E_WRITE", "E_NOTHING", "E_NO_WITHDRAW"):
            code = 400
        elif res.get("code") in ("E_CLOSED", "E_NO_RESUBMIT", "E_TOKEN",
                                 "E_TPL_STALE", "E_AUTH"):
            code = 403
        else:
            code = 200
        return self._send_json(res, code)

    # ------------------------------------------------------------------
    # 课件库：GET
    # ------------------------------------------------------------------
    def _cw_allowed(self):
        """课件库是老师的资料，唯一的闸门是「教师会话」。

        以前是「本机 IP 或 allow_lan」：那套逻辑假设服务端跑在老师自己的电脑上。
        改成容器部署后，所有请求都来自网关的内网地址，按 IP 判永远为假，
        老师在自己的工作台里反而看不到课件。现在按身份判：
          · 已登录的教师会话 → 放行（谁能登录教师端，谁就是老师）；
          · 学生会话 / 未登录 → 拒绝，连列表都不给。
        网络层不再参与判定，也就不存在「任何人自己把 allow_lan 打开」的漏洞。
        """
        role, _ = self._role()
        if role != "teacher":
            return False, "课件库只对教师账号开放，请先在 /teacher/ 登录。"
        return True, ""

    def _need_local(self):
        """需要桌面环境才能做的操作（弹目录选择框、调用资源管理器）。

        容器里没有桌面，这类接口只能直接拒绝并说明替代做法，
        否则会调用到不存在的 DISPLAY / explorer.exe 然后莫名超时。
        """
        if self._is_local():
            return False
        self._send_json({"ok": False, "msg": "这个操作需要在服务器桌面上执行，容器部署下不可用。"
                                            "请在服务器上把文件放到课件目录，或用页面上的「上传」按钮。"}, 403)
        return True

    def _cw_get(self, path, qs):
        ok, why = self._cw_allowed()
        if not ok:
            # 只读元信息放行：界面要能显示「为什么被拦」，否则就是一个没有任何提示的死路。
            # 这里不返回任何课程/文件内容。
            if path == "/api/cw/config":
                return self._send_json({
                    "ok": True, "blocked": True, "msg": why,
                    "allow_lan": True, "is_local": True,
                    "root": "", "courses": [], "total": 0, "writable": False, "auto": False,
                })
            return self._send_json({"ok": False, "msg": why, "need_login": True}, 403)
        arg = lambda k, d="": (qs.get(k) or [d])[0]

        if path == "/api/cw/config":
            cfg = CW.load_cfg()
            root = CW.root_dir()
            okw, wmsg = CW.ensure_root(root)
            courses, cerr = CW.list_courses()
            total = sum(c.get("count", 0) for c in (courses or []))
            return self._send_json({
                "ok": True, "root": root, "default_root": CW.DEFAULT_ROOT,
                "auto": bool(cfg.get("auto")), "allow_lan": True,
                "writable": okw, "error": ("" if okw else wmsg) or (cerr or ""),
                "updated_at": cfg.get("updated_at", ""),
                "config_file": CW.CFG_PATH,
                "courses": courses or [], "total": total,
                "is_local": True,
            })
        if path == "/api/cw/courses":
            courses, err = CW.list_courses()
            return self._send_json({"ok": not err, "courses": courses or [], "msg": err or ""})
        if path == "/api/cw/list":
            course = arg("course")
            files = CW.list_files(course) if course else CW.all_files()[0]
            # 该课程被「设为学习单」的网页文件 id（前端据此打标 + 大屏预选）
            sheet = CW.sheet_of(course) if course else ""
            return self._send_json({"ok": True, "course": course, "files": files, "sheet": sheet})
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
        # ---- 本地资料夹（mounts）已于 2026-11-01 下线：资料统一上传到服务器课件库 ----
        if path == "/api/cw/remote":
            # 学生提交的作品文件在服务器数据目录里，浏览器没法直接渲染 docx/xlsx/pptx，
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
    def _cw_upload_raw(self, u):
        """课件流式直传（2026-11-04）：POST /api/cw/upload_raw?course=&name=
        请求体就是文件原始字节（application/octet-stream），按 256KB 分块落盘。

        替代 base64 JSON 上传的大文件场景：浏览器 fetch body=File 流式发送，
        前端不再把整个文件读进 JS 内存；服务端也只持有 256KB 缓冲。
        超限时把手头的请求体读掉再拒（同 _read_body 的 drain 逻辑），
        避免 keep-alive 连接被残留数据弄脏，浏览器误报「连接中断」。
        """
        ok, why = self._cw_allowed()
        if not ok:
            return self._send_json({"ok": False, "msg": why, "need_login": True}, 403)
        qs = urllib.parse.parse_qs(u.query)
        # parse_qs 已对值做过一次 UTF-8 解码，这里不能再 unquote——
        # 文件名本身带 % 时（如「50%进度.pptx」）二次解码会出错。
        arg = lambda k: (qs.get(k) or [""])[0]
        course = arg("course").strip() or "未分课程"
        name = arg("name")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return self._send_json({"ok": False, "msg": "没有收到文件内容"}, 400)
        if length > CW.MAX_UPLOAD:
            left = length
            while left > 0:                     # drain：读完再拒，别让连接带着残包
                chunk = self.rfile.read(min(1024 * 1024, left))
                if not chunk:
                    break
                left -= len(chunk)
            return self._send_json({"ok": False,
                                    "msg": "文件太大（单个上限 %s）" % CW.human_size(CW.MAX_UPLOAD)}, 413)
        res = CW.save_stream(course, name, self.rfile, length)
        return self._send_json(res, 200 if res.get("ok") else 400)

    def _cw_post(self, path, u, data):
        # 兼容旧前端的 allow_lan 开关：容器部署下访问权限只按教师会话判定，
        # 这里直接回成功但不再改任何配置（避免落到下面 set_root 的兜底分支）。
        if path == "/api/cw/config" and not data.get("root") and data.get("allow_lan") is not None:
            role, _ = self._role()
            if role != "teacher":
                return self._deny("只有教师账号可以管理课件库", 403)
            return self._send_json({"ok": True, "allow_lan": True,
                                    "msg": "课件库按教师账号判定访问权限，无需额外开关"})
        ok, why = self._cw_allowed()
        if not ok:
            return self._send_json({"ok": False, "msg": why, "need_login": True}, 403)

        # 会弹窗 / 打开资源管理器 / 动磁盘的，需要桌面环境
        if path == "/api/cw/open":
            if self._need_local():
                return

        if path == "/api/cw/config":
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
            res = CW.rename_or_move(data.get("id"), data.get("course"),
                                    new_name=data.get("name"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/rename_course":
            # 课节（课程）改名（2026-11-04）：整个课程文件夹重命名
            res = CW.rename_course(data.get("old"), data.get("new"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/delete_course":
            # 删除整个课程文件夹（2026-11-04 资源管理器改版，递归删除）
            res = CW.delete_course(data.get("name"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/archive":
            res = CW.archive_items(data.get("course"), data.get("activity"),
                                   data.get("base") or "", data.get("items") or [])
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/set_sheet":
            # 把课程资料里的网页文件设为该课程的学习单（id 为空 = 取消标记）
            res = CW.set_sheet(data.get("course"), data.get("id"))
            return self._send_json(res, 200 if res.get("ok") else 400)
        if path == "/api/cw/save_text":
            res = CW.save_text(data.get("course"), data.get("subdir"),
                               data.get("name"), data.get("text") or "")
            return self._send_json(res, 200 if res.get("ok") else 400)
        # ---- 本地资料夹（mounts）/ pickdir 已下线（2026-11-01）：资料统一上传到服务器课件库 ----
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
        # 目录补 index.html：/student/ 这种「文件夹当入口」的地址，
        # 浏览器地址栏里少打一个文件名也能打开，而不是回一个 404 让人以为页面没了。
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
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
        """按 token 取学生上传的作品。

        token 来自 uploads_index.json，以前是「谁拿到 token 谁就能下载」，
        而 token 又出现在匿名可读的 /api/board 里 —— 等于全班作业公开。
        现在要求已登录；新登记的条目还会校验归属（sid 对不上就不给）。
        """
        role, _k = self._role()
        if not role:
            return self._send_text("请先登录", code=401)
        idx = load_json(UPLOAD_INDEX, {})
        rec = idx.get(token)
        if isinstance(rec, dict):
            full = rec.get("path") or ""
            owner = str(rec.get("sid") or "")
        else:
            full = rec or ""
            owner = ""
        if role == "student":
            mine = self._student_sid()
            if not owner or owner != mine:
                return self._send_text("这不是你的文件", code=403)
        if not full or not os.path.isfile(full):
            return self._send_text("文件不存在", code=404)
        return self._serve_abs(full, download=True)

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
