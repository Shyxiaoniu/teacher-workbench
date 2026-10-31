#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
学生提交增强模块 · 纯标准库实现（无第三方依赖）

职责
----
把「班级活动作品收集」从「只能收文件」升级为：
  1) 老师可定义「要收什么」（模板：系统字段 + 自定义字段）
  2) 自动核对（必填 / 类型 / 超限 / 学号不在册 / 文件名 → 三级：阻止 / 提醒 / 仅展示）
  3) 一键下发素材（老师电脑里的文件 → 学生页可点链接下载）
  4) 学生门户（一条链接：身份 → 任务 → 详情 → 提交 → 我的提交）
  5) 老师端「④ 提交设置」卡片（模板 / 核对 / 素材 / 门户 / 状态 集中管理）

数据全部存放在 submission-system/data/enh/ 下，与原有 submissions.json 互不污染：
  tasks.json          任务配置（标题 / 模板 / 素材 / 核对开关 / 状态）
  subs.log.jsonl      提交流水（追加写，不重写，并发安全）
  subs.index.json     派生索引 {task_key:{sid:{latest,versions[]}}}
  tokens.json         身份 token（task_key+sid → 有效期）
  uploads/            提交时的临时上传（upload_id → 文件）
  subs_files/         已提交作品落盘（按 task/sid/版本 归档）
  materials/          素材副本（copy 模式）

与 QuickForm 的关系：本模块自管存储与统计，门户 / 我的提交 / 核对大屏都读这里。
（符合「门户与旧链接并行」策略；旧 class-activity 链接仍走 QuickForm，互不干扰。）
"""

import os
import re
import json
import time
import uuid
import threading
import datetime
import hashlib
import shutil
import urllib.parse
import urllib.request
import urllib.error

import courseware as CW

ROOT = os.path.dirname(os.path.abspath(__file__))      # submission-system/
# 同 submission-server：容器部署时由 WB_SUBMISSION_DATA_DIR 指到数据卷；
# 只设了 WB_DATA_DIR 时跟着走，避免容器重建把上传和任务配置丢在镜像里。
DATA = (os.environ.get("WB_SUBMISSION_DATA_DIR")
        or (os.path.join(os.environ["WB_DATA_DIR"], "submissions")
            if os.environ.get("WB_DATA_DIR") else "")
        or os.path.join(ROOT, "data"))
ENH_DIR = os.path.join(DATA, "enh")
for _d in (ENH_DIR, os.path.join(ENH_DIR, "uploads"),
           os.path.join(ENH_DIR, "subs_files"), os.path.join(ENH_DIR, "materials")):
    os.makedirs(_d, exist_ok=True)

ROSTER_PATH = os.path.join(DATA, "roster.json")
TASKS_PATH = os.path.join(ENH_DIR, "tasks.json")
INDEX_PATH = os.path.join(ENH_DIR, "subs.index.json")
TOKENS_PATH = os.path.join(ENH_DIR, "tokens.json")

# 由主服务注入：() -> [{cls, sid, name}]（与学生表同源）
ROSTER_PROVIDER = None

_LOCK = threading.Lock()
TOKEN_TTL = 60 * 60 * 12  # 身份 token 12 小时有效（覆盖一节课 + 课后补交）

# 身份字段：由系统自动带出，模板不可编辑、不可占用这三个 key
SYS_FIELDS = [
    {"key": "sid", "label": "学号", "type": "text", "required": True, "locked": True, "hint": "自动从花名册带出"},
    {"key": "name", "label": "姓名", "type": "text", "required": True, "locked": True, "hint": "自动从花名册带出"},
    {"key": "cls", "label": "班级", "type": "text", "required": True, "locked": True, "hint": "自动从花名册带出"},
]
SYS_KEYS = set(f["key"] for f in SYS_FIELDS)
SYS_LABELS = {f["key"]: f["label"] for f in SYS_FIELDS}

# 核对规则默认值（老师端 policy 结构，前后端唯一真相源）
def default_policy():
    return {
        "check": {"block_on_fail": True, "warn_confirm": True, "content_level": False},
        "submit": {"allow_resubmit": True, "allow_withdraw": True, "allow_late": True},
        # 班级匹配：auto=自动匹配系统导入的班级列表（默认）；none=不匹配
        "class_match": CLASS_MATCH_DEFAULT,
    }


# 班级匹配模式：
#   auto  自动匹配系统导入的班级列表（默认）——学生信息按学号去花名册里找对应班级
#   none  不匹配——不做班级匹配处理，学生自己填什么就是什么
CLASS_MATCH_MODES = ("auto", "none")
CLASS_MATCH_DEFAULT = "auto"


def normalize_class_mode(v):
    """把外部传入的班级匹配模式归一化；认不出来的按默认 auto 处理。"""
    s = str(v or "").strip().lower()
    if s in ("none", "off", "no", "0", "false", "不匹配"):
        return "none"
    if s in ("auto", "on", "yes", "1", "true", "匹配", "自动匹配"):
        return "auto"
    return CLASS_MATCH_DEFAULT


def normalize_class_match(p):
    """从 policy 里读班级匹配模式并归一化。"""
    if not isinstance(p, dict):
        return CLASS_MATCH_DEFAULT
    for k in ("class_match", "classMatch", "match_class"):
        if k in p:
            return normalize_class_mode(p.get(k))
    return CLASS_MATCH_DEFAULT


def normalize_policy(p):
    """把老师端传来的 policy 归一化，缺的补默认，类型对齐。"""
    d = default_policy()
    if isinstance(p, dict):
        for grp in ("check", "submit"):
            src = p.get(grp) or {}
            if isinstance(src, dict):
                for k in d[grp]:
                    if k in src:
                        d[grp][k] = bool(src[k])
        d["class_match"] = normalize_class_match(p)
    return d


# ---------------------------------------------------------------------------
# 持久化层
# ---------------------------------------------------------------------------
def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load(path, default):
    try:
        if not os.path.isfile(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _ensure_dir(path):
    """确保目录存在。模块导入时建过一次，但目录可能被清理或被移动，
    写入前再确认一次，避免「文件写入失败：No such file or directory」。"""
    try:
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


# ---------------------------------------------------------------------------
# 花名册
# ---------------------------------------------------------------------------
def roster_all():
    """花名册：优先用主服务注入的实时投影（与学生表同源），否则读 roster.json。"""
    if ROSTER_PROVIDER is not None:
        try:
            rows = ROSTER_PROVIDER() or []
            if rows:
                return rows
        except Exception:
            pass
    return _load(ROSTER_PATH, [])


def roster_lookup(cls, sid):
    """返回该学号在指定班级的记录，或 None。"""
    sid = str(sid or "").strip()
    for r in roster_all():
        if str(r.get("sid", "")).strip() == sid and str(r.get("cls", "")).strip() == str(cls or "").strip():
            return r
    # 班级没匹配上，再放宽：同 sid 任意班级（防止老师选错班级时彻底卡死）
    for r in roster_all():
        if str(r.get("sid", "")).strip() == sid:
            return r
    return None


def classes_of(sid):
    """该学号出现在哪些班级（用于门户展示归属）。"""
    sid = str(sid or "").strip()
    return sorted({str(r.get("cls", "")).strip() for r in roster_all()
                   if str(r.get("sid", "")).strip() == sid and r.get("cls")})


def roster_class_names():
    """系统导入的班级列表（花名册里去重后的班级名，排序后返回）。"""
    out = set()
    for r in roster_all():
        c = str(r.get("cls", "")).strip()
        if c:
            out.add(c)
    return sorted(out)


def class_exists(cls):
    """这个班级是否在系统导入的班级列表里。"""
    c = str(cls or "").strip()
    if not c:
        return False
    return c in set(roster_class_names())


def resolve_student_class(sid, want_cls="", mode=CLASS_MATCH_DEFAULT):
    """按班级匹配模式，决定学生最终归属的班级。

    返回 (cls, matched, msg)：
      mode=auto（默认）——优先用学生提交的班级，但必须在系统导入的班级列表里；
                        提交为空时按学号自动回填该学号所在班级。
                        若提交了不在列表里的班级，matched=False，由调用方给提示。
      mode=none——不做班级匹配，直接采用学生提交的班级（原样保留）。

    matched 表示「最终班级是否落在系统导入的班级列表内」。
    """
    mode = normalize_class_mode(mode)
    sid = str(sid or "").strip()
    want = str(want_cls or "").strip()

    if mode == "none":
        # 不匹配：不查花名册，也不做任何班级纠正
        return want, False, ""

    if want:
        if class_exists(want):
            return want, True, ""
        # 提交了系统里没有的班级：仍按提交值走，但标记为未匹配上
        return want, False, "班级「%s」不在系统导入的班级列表里" % want

    # 没填班级：按学号自动回填
    auto = classes_of(sid)
    if auto:
        return auto[0], True, ""
    return "", False, ""


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _safe(name):
    name = str(name or "file").strip()
    # 除了路径分隔符，Windows 还禁止 : * ? " < > | 和控制字符，
    # 其中 ":" 会被当成 NTFS 数据流语法 → 打开就报“拒绝访问”。
    # 学生从手机/平板上传的文件名里完全可能有这些字符，统一换成下划线。
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    name = re.sub(r"^\.+", "", name)
    # Windows 会悄悄吃掉文件名结尾的点和空格，"报告." 和 "报告" 会互相覆盖
    name = name.rstrip(". ")
    return name[:120] or "file"


def _fmt_size(n):
    n = int(n or 0)
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024)
    if n < 1024 * 1024 * 1024:
        return "%.1f MB" % (n / 1024 / 1024)
    return "%.2f GB" % (n / 1024 / 1024 / 1024)


def _hash(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


# ---------------------------------------------------------------------------
# 默认模板
# ---------------------------------------------------------------------------
def default_template():
    return {
        "version": 1,
        "name": "作品提交",
        "updated_at": _now(),
        "sections": [
            {
                "title": "基本信息",
                "fields": [
                    {"key": "work_title", "label": "作品标题", "type": "text", "required": True,
                     "hint": "给你的作品起个名字", "locked": False},
                    {"key": "intro", "label": "作品简介", "type": "textarea", "required": False,
                     "min": 0, "max": 500, "hint": "一句话说说你做了什么", "locked": False},
                    # 默认带一个文件字段：作品收集类活动不交文件就没意义，
                    # 老师可以在模板编辑器里改数量、大小和格式。
                    {"key": "work_files", "label": "作品文件", "type": "file", "required": True,
                     "maxCount": 5, "maxMB": 100,
                     "accept": ["jpg", "png", "pdf", "docx", "pptx", "mp4"], "locked": False},
                ],
            }
        ],
    }


# 模板预设（老师端「套预设」）
PRESETS = {
    "A": {
        "name": "作品提交（通用）",
        "sections": [{"title": "作品信息", "fields": [
            {"key": "work_title", "label": "作品标题", "type": "text", "required": True,
             "hint": "给你的作品起个名字", "max": 60},
            {"key": "intro", "label": "创作说明", "type": "textarea", "required": True,
             "min": 50, "max": 800, "hint": "说说你怎么做的，至少 50 字"},
            {"key": "topic", "label": "选题方向", "type": "select", "required": False,
             "options": ["听音识传说", "AI 绘梦", "代码实践"]},
            {"key": "work_files", "label": "作品文件", "type": "file", "required": True,
             "maxCount": 5, "maxMB": 100, "accept": ["jpg", "png", "pdf", "docx", "pptx", "mp4"]},
        ]}],
    },
    "B": {
        "name": "课堂任务（轻量）",
        "sections": [{"title": "任务", "fields": [
            {"key": "work_title", "label": "任务名称", "type": "text", "required": True, "max": 60},
            {"key": "work_files", "label": "成果文件", "type": "file", "required": True,
             "maxCount": 3, "maxMB": 100, "accept": ["jpg", "png", "pdf", "docx", "mp4"]},
            {"key": "intro", "label": "完成度", "type": "select", "required": True,
             "options": ["完全完成", "基本完成", "部分完成"]},
        ]}],
    },
    "C": {
        "name": "图文记录（过程性）",
        "sections": [{"title": "活动记录", "fields": [
            {"key": "work_title", "label": "活动名称", "type": "text", "required": True, "max": 60},
            {"key": "work_files", "label": "过程照片", "type": "file", "required": True,
             "maxCount": 3, "maxMB": 20, "accept": ["jpg", "png", "jpeg"]},
            {"key": "intro", "label": "一句话感受", "type": "textarea", "required": True,
             "min": 20, "max": 300, "hint": "至少 20 字"},
        ]}],
    },
}


# ---------------------------------------------------------------------------
# 任务配置
# ---------------------------------------------------------------------------
def task_get(key):
    tasks = _load(TASKS_PATH, {})
    return tasks.get(key)


def ensure_task(key, api="", class_names=None):
    """取任务；不存在则用合理默认值创建（复用 QuickForm 的 task_id 作为 key）。"""
    tasks = _load(TASKS_PATH, {})
    t = tasks.get(key)
    if not t:
        t = {
            "task_key": key,
            "title": key,
            "api": api or "",
            "class_names": class_names or [],
            "class_ids": [],
            "status": "open",            # draft / open / closed / archived
            "deadline": "",              # 空 = 不限时间
            "pin": "",                   # 提交 PIN（空=不启用）
            "policy": default_policy(),
            "template": default_template(),
            "materials": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        tasks[key] = t
        _save(TASKS_PATH, tasks)
    # 老数据兼容：补齐后来新增的字段
    changed = False
    for k, v in (("policy", default_policy()), ("deadline", ""), ("status", "open")):
        if k not in t:
            t[k] = v
            changed = True
    if changed:
        tasks[key] = t
        _save(TASKS_PATH, tasks)
    return t


def task_public(t):
    """对外暴露的任务视图：字段名对齐老师端 JS 与学生门户。

    老师端 JS 读 policy.check.* / policy.submit.*、deadline、materials[].available；
    学生门户读 class_names、template、materials[].available。
    统一在这里做一次投影，避免两边各写一套映射。
    """
    t = dict(t or {})
    t["policy"] = normalize_policy(t.get("policy"))
    t["classes"] = list(t.get("class_names") or [])
    mats = []
    for m in (t.get("materials") or []):
        mm = dict(m)
        mm["available"] = _mat_available(mm)
        mm["kind"] = "copy" if mm.get("mode") == "copy" else "ref"
        mm.setdefault("ext", os.path.splitext(mm.get("name", ""))[1].lstrip(".").upper() or "FILE")
        mm.setdefault("size", 0)
        mm.setdefault("visible", True)
        mm.setdefault("note", "")
        mats.append(mm)
    t["materials"] = mats
    return t


def _mat_available(m):
    """素材是否还打得开：引用模式看原路径，副本模式看副本。"""
    if m.get("mode") == "copy":
        p = m.get("copy_path") or ""
        return bool(p) and os.path.isfile(p)
    p = m.get("path") or ""
    return bool(p) and os.path.isfile(p)


def task_save(data):
    """保存（或新建）一个任务配置。data 含 task_key 及可覆盖字段。"""
    key = data.get("task_key") or data.get("key") or data.get("task")
    if not key:
        return {"ok": False, "msg": "缺少 task_key"}
    tasks = _load(TASKS_PATH, {})
    t = tasks.get(key) or ensure_task(key)
    for k in ("title", "api", "class_names", "class_ids", "status", "deadline", "pin"):
        if k in data:
            t[k] = data[k]
    if "policy" in data:
        t["policy"] = normalize_policy(data.get("policy"))
    if "template" in data and isinstance(data["template"], dict):
        # 保存模板时版本 +1（保留历史可回滚，这里至少记录版本号）
        old = t.get("template", {}).get("version", 0)
        new_tpl = dict(data["template"])
        new_tpl["version"] = old + 1
        new_tpl["updated_at"] = _now()
        t["template"] = new_tpl
    if "materials" in data and isinstance(data["materials"], list):
        t["materials"] = data["materials"]
    t["updated_at"] = _now()
    tasks[key] = t
    _save(TASKS_PATH, tasks)
    return {"ok": True, "task": task_public(t)}


def qf_base(task):
    """从 task.api 反推 QuickForm 的站点根（拼 /static/... 附件地址时用）。"""
    api = str((task or {}).get("api") or "")
    m = re.match(r"^(https?://[^/]+)", api)
    return m.group(1) if m else ""


def portal_url(task, host=""):
    key = task.get("task_key", "")
    cls = (task.get("class_names") or [""])[0] if task.get("class_names") else ""
    cls_q = ("&class=" + urllib.parse.quote(cls)) if cls else ""
    base = (host or "").rstrip("/") or "http://127.0.0.1:20020"
    return "%s/s/?task=%s%s" % (base, urllib.parse.quote(key), cls_q)


# ---------------------------------------------------------------------------
# 身份 token
# ---------------------------------------------------------------------------
def issue_token(task_key, cls, sid, name):
    tokens = _load(TOKENS_PATH, {})
    tok = uuid.uuid4().hex
    tokens[tok] = {
        "task_key": task_key, "cls": cls, "sid": sid, "name": name,
        "exp": time.time() + TOKEN_TTL,
    }
    _save(TOKENS_PATH, tokens)
    return tok


def check_token(token, cls="", sid=""):
    """校验 token：过期 / 不匹配班级或学号（若传入）则失败。返回记录或 None。"""
    if not token:
        return None
    tokens = _load(TOKENS_PATH, {})
    rec = tokens.get(token)
    if not rec:
        return None
    if rec.get("exp", 0) < time.time():
        return None
    if sid and str(rec.get("sid", "")) != str(sid):
        return None
    if cls and rec.get("cls") and str(rec.get("cls", "")) != str(cls):
        return None
    return rec


# ---------------------------------------------------------------------------
# 上传（临时）
# ---------------------------------------------------------------------------
# 临时上传文件的命名：<32 位 uid>__<原始文件名>
#
# 原来用 "::" 当分隔符。":" 在 Windows 上是非法文件名字符，open() 直接抛
# [Errno 13] Permission denied —— 也就是说在 Windows 教师机上「学生上传作品」
# 这一步百分之百失败，而且报的还是「写入失败」，看着像磁盘权限问题，很难查。
# 分隔符改成两个下划线，uid 长度固定 32 位，解析时不会和文件名里的下划线混淆；
# 老的 "::" 文件仍然能被认出来（兼容已经在 Linux/Mac 上交过的历史数据）。
_UP_SEP = "__"


def _up_name(uid, oname):
    return uid + _UP_SEP + _safe(oname)


def _up_parse(fn):
    """从临时文件名里拆出 (uid, 原始文件名)；认不出返回 ("", "")。"""
    for sep in (_UP_SEP, "::"):
        if sep in fn:
            a, b = fn.split(sep, 1)
            if len(a) == 32:
                return a, b
    return "", ""


def save_upload(task_key, sid, token, name, stream, length, auth=None):
    """把学生上传的文件先落到临时 uploads/，返回 upload_id。

    文件名记为 <uid>__<safe_original_name>，务必保留学生原始文件名——
    核对引擎的「文件名含学号」检查、以及归档后展示/下载，都用原始名，不能丢。
    """
    ident = check_token(token, "", sid)
    if not (_may_act_as(auth, sid) or (ident and str(ident.get("sid", "")) == str(sid))):
        return {"ok": False, "code": "E_TOKEN", "msg": "身份已过期，请重新登录后再上传"}
    if length > MAX_FILE_TOTAL * 2:
        return {"ok": False, "code": "E_TOOBIG", "msg": "文件太大（上限 300MB）"}
    uid = uuid.uuid4().hex
    up_dir = _ensure_dir(os.path.join(ENH_DIR, "uploads"))
    dest = os.path.join(up_dir, _up_name(uid, name))
    try:
        n = 0
        with open(dest, "wb") as f:
            while length > 0:
                chunk = stream.read(min(65536, length))
                if not chunk:
                    break
                f.write(chunk)
                n += len(chunk)
                length -= len(chunk)
    except Exception as e:
        return {"ok": False, "code": "E_WRITE", "msg": "文件写入失败：" + str(e)}
    return {"ok": True, "upload_id": uid, "size": n, "name": _safe(name)}


def _claim_uploads(upload_ids):
    """取出临时上传文件，返回 [(original_name, abs_path, size, upload_id)]。

    upload_ids 两种形态都收：纯字符串 id，或前端传来的 {upload_id, name, size} 对象。
    只认纯字符串会让前端传对象时一个都认不出来，直接报「上传文件已过期」。
    """
    out = []
    updir = os.path.join(ENH_DIR, "uploads")
    if not os.path.isdir(updir):
        return out
    try:
        listing = os.listdir(updir)
    except Exception:
        listing = []
    for item in (upload_ids or []):
        if isinstance(item, dict):
            uid = str(item.get("upload_id") or "").strip()
        else:
            uid = str(item or "").strip()
        if not uid:
            continue
        hit = None
        for fn in listing:
            if _up_parse(fn)[0] == uid:
                hit = fn
                break
        if hit:
            p = os.path.join(updir, hit)
            oname = _up_parse(hit)[1]
            try:
                sz = os.path.getsize(p)
            except Exception:
                sz = 0
            out.append((oname, p, sz, uid))
    return out


# ---------------------------------------------------------------------------
# 核对引擎（三级）
# ---------------------------------------------------------------------------
# 级别：block（阻止提交）/ warn（可强行提交，留标记）/ info（仅展示，不参与排序）
MAX_FILE_TOTAL = 200 * 1024 * 1024   # 单次提交文件总大小上限 200MB
BLOCK_EXT = (".exe", ".bat", ".cmd", ".sh", ".js", ".app", ".dmg", ".scr", ".vbs", ".com", ".pif")


def run_check(task, sid, name, cls, fields, files):
    """返回 {level, items:[{lv, code, msg}], blocked, warned}。

    三级语义：
      block  阻止提交（必填 / 字数 / 选项 / 文件类型与大小）
      warn   只提醒，学生可「仍然提交」，结果只给老师看
      info   仅展示，不参与任何判断
    老师端关掉哪个开关，这里就跳过对应检查。
    """
    items = []
    template = task.get("template", {})
    pol = normalize_policy(task.get("policy"))
    chk_pol = pol["check"]
    sub_pol = pol["submit"]

    # 学生填的字段 = 模板里的字段（学号/姓名/班级由身份带出，不让学生填）
    all_fields = _custom_fields(template)

    # --- 必填 / 类型 / 字数 ---
    for f in all_fields:
        key = f.get("key")
        # 文件型字段不走文本校验：文件是单独上传的，下面单独查「有没有传文件」
        if f.get("type") == "file":
            continue
        val = (fields or {}).get(key, "")
        if f.get("required") and not str(val).strip():
            items.append({"lv": "block", "code": "REQUIRED",
                          "msg": "「%s」还没填" % f.get("label", key)})
            continue
        if f.get("type") == "textarea" and val:
            mn = int(f.get("min", 0) or 0)
            mx = int(f.get("max", 0) or 0)
            if mn and len(val) < mn:
                items.append({"lv": "block", "code": "MINLEN",
                              "msg": "「%s」至少写 %d 字（现在 %d 字）" % (f.get("label", key), mn, len(val))})
            if mx and len(val) > mx:
                items.append({"lv": "block", "code": "MAXLEN",
                              "msg": "「%s」最多写 %d 字（现在 %d 字）" % (f.get("label", key), mx, len(val))})
        if f.get("type") == "select" and val:
            opts = [o.get("value") if isinstance(o, dict) else o for o in f.get("options", [])]
            if opts and val not in opts:
                items.append({"lv": "block", "code": "OPTION",
                              "msg": "「%s」选项无效" % f.get("label", key)})

    # --- 文件型必填字段：得有文件 ---
    for f in all_fields:
        if f.get("type") != "file" or not f.get("required"):
            continue
        if not files:
            items.append({"lv": "block", "code": "REQUIRED_FILE",
                          "msg": "「%s」还没传文件" % f.get("label", f.get("key"))})
        else:
            mx_n = int(f.get("maxCount") or 0)
            if mx_n and len(files) > mx_n:
                items.append({"lv": "block", "code": "TOO_MANY_FILES",
                              "msg": "「%s」最多 %d 个，现在传了 %d 个"
                                     % (f.get("label", f.get("key")), mx_n, len(files))})
            mx_mb = int(f.get("maxMB") or 0)
            if mx_mb:
                for x in files:
                    if x[2] > mx_mb * 1024 * 1024:
                        items.append({"lv": "block", "code": "FILE_TOOBIG",
                                      "msg": "「%s」超过单个上限 %dMB（可压缩或分段）"
                                             % (x[0], mx_mb)})

    # --- 文件大小 / 类型 / 总数 ---
    total = sum(x[2] for x in files)
    for f in files:
        _n, _p, sz = f[0], f[1], f[2]
        if sz > 80 * 1024 * 1024:
            items.append({"lv": "block", "code": "FILE_TOOBIG",
                          "msg": "「%s」超过 80MB，单文件上限 80MB（可压缩或分段）" % _n})
        ext = os.path.splitext(_n)[1].lower()
        if ext in BLOCK_EXT:
            items.append({"lv": "block", "code": "FILE_TYPE",
                          "msg": "「%s」这类文件不安全，不允许提交" % _n})
    if total > MAX_FILE_TOTAL:
        items.append({"lv": "block", "code": "TOTAL_TOOBIG",
                      "msg": "本次提交总大小 %.1fMB，超过 %.0fMB 上限"
                             % (total / 1024 / 1024, MAX_FILE_TOTAL / 1024 / 1024)})

    # --- 文件名含学号（提醒级）---
    if chk_pol.get("warn_confirm", True):
        for f in files:
            _n, _p, _s = f[0], f[1], f[2]
            if sid and sid not in _n:
                items.append({"lv": "warn", "code": "NAME_NO_SID",
                              "msg": "「%s」文件名里没有你的学号，老师整理时可能认不出是谁的" % _n})

    # --- 重复提交（提醒级）---
    if chk_pol.get("warn_confirm", True) and sub_pol.get("allow_resubmit", True):
        idx = _load(INDEX_PATH, {})
        rec = idx.get(task.get("task_key"), {}).get(sid)
        if rec and rec.get("versions"):
            items.append({"lv": "warn", "code": "DUP",
                          "msg": "你之前已经交过（第 %d 版），再交会变成新版本，旧版老师仍能看到"
                                 % rec["latest"]})

    blocked = [i for i in items if i["lv"] == "block"]
    warned = [i for i in items if i["lv"] == "warn"]
    level = "block" if blocked else ("warn" if warned else "pass")
    # 老师关掉「阻止级校验」时，block 项降级为 warn：不再拦住提交，但老师仍看得到
    if level == "block" and not chk_pol.get("block_on_fail", True):
        for i in blocked:
            i["lv"] = "warn"
            i["downgraded"] = True
        blocked = []
        warned = [i for i in items if i["lv"] == "warn"]
        level = "warn"
    return {"level": level, "items": items, "blocked": blocked, "warned": warned}


def _custom_fields(template):
    """模板里的学生可填字段（去掉系统字段 key）。"""
    out = []
    for sec in (template or {}).get("sections", []):
        for f in sec.get("fields", []):
            if f.get("key") in SYS_KEYS:
                continue
            out.append(f)
    return out

# ---------------------------------------------------------------------------
# 提交
# ---------------------------------------------------------------------------
def _past_deadline(task):
    """截止时间是否已过（空 = 不限）。格式 'YYYY-MM-DD HH:MM'。"""
    ddl = str(task.get("deadline") or "").strip()
    if not ddl:
        return False
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(ddl[:16], fmt)
            return dt < datetime.datetime.now()
        except Exception:
            continue
    return False


def commit_submission(task_key, sid, token, fields, uploads, tpl_version, auth=None):
    task = ensure_task(task_key)
    ident = check_token(token, "", sid)
    if not ident:
        return {"ok": False, "code": "E_TOKEN", "msg": "身份已过期，刷新页面重新输学号"}
    cls = ident.get("cls", "")
    name = ident.get("name", "")

    pol = normalize_policy(task.get("policy"))
    closed = task.get("status") in ("closed", "archived") or _past_deadline(task)
    if closed and not pol["submit"].get("allow_late", True):
        return {"ok": False, "code": "E_CLOSED", "msg": "活动已截止，老师没开补交"}

    # 模板版本校验：学生端看到的版本必须 <= 服务端当前版本（不能用旧模板提交）
    cur_v = task.get("template", {}).get("version", 1)
    if int(tpl_version or 0) > cur_v:
        return {"ok": False, "code": "E_TPL_STALE", "msg": "模板已更新，请刷新页面再提交"}

    # 取临时上传文件
    claimed = _claim_uploads(uploads)
    if uploads and not claimed:
        return {"ok": False, "code": "E_UPLOAD", "msg": "上传文件已过期，请重新选择文件"}

    # 核对（先跑一遍基础规则）
    chk = run_check(task, sid, name, cls, fields, claimed)

    # 班级归属：按任务配置的班级匹配模式再解析一次（服务端重算，不信前端）
    # cls 来自 token（签发时已按模式解析过），这里做最终确认并得到匹配结果
    class_mode = pol.get("class_match", CLASS_MATCH_DEFAULT)
    if class_mode == "auto":
        _c, class_matched, _m = resolve_student_class(sid, cls, "auto")
        # 班级不在系统导入的列表里 → 提醒级（学生可「仍然提交」），不直接拦
        if not class_matched and cls and not any(
                i.get("code") == "CLASS_NOT_FOUND" for i in chk["items"]):
            chk["items"].append({
                "lv": "warn", "code": "CLASS_NOT_FOUND",
                "msg": "班级「%s」不在系统导入的班级列表里，提交后老师可能归不到正确的班" % cls})
            chk["warned"] = [i for i in chk["items"] if i["lv"] == "warn"]
            if chk["level"] == "pass":
                chk["level"] = "warn"
    else:
        # 不匹配模式：不做班级匹配处理，班级原样保留
        class_matched = False
    if chk["level"] == "block":
        # 核对不通过：把临时文件还回 uploads（保留原始名），让学生可重试，不重复选
        for (oname, p, sz, _uid) in claimed:
            try:
                os.rename(p, os.path.join(ENH_DIR, "uploads", _up_name(_uid, oname)))
            except Exception:
                try:
                    os.remove(p)
                except Exception:
                    pass
        return {"ok": False, "code": "E_CHECK",
                "msg": "还有 %d 处必须改好才能提交" % len(chk["blocked"]),
                "check": chk}

    # 老师关掉「允许重新提交」时，已有版本就拒绝再交
    if not pol["submit"].get("allow_resubmit", True):
        idx0 = _load(INDEX_PATH, {})
        old = idx0.get(task_key, {}).get(sid)
        if old and old.get("versions"):
            return {"ok": False, "code": "E_NO_RESUBMIT",
                    "msg": "老师关闭了重新提交，你已经交过了（第 %d 版）" % old["latest"]}

    # 归档文件到 subs_files/<task>/<sid>/v<ver>/<upload_id>（物理名用 upload_id，避免同名覆盖）
    idx = _load(INDEX_PATH, {})
    tnode = idx.setdefault(task_key, {})
    snode = tnode.setdefault(sid, {"latest": 0, "versions": []})
    version = snode["latest"] + 1
    is_late = closed
    ver_dir = _ensure_dir(os.path.join(ENH_DIR, "subs_files", _safe(task_key),
                                       _safe(sid), "v%d" % version))
    saved_files = []
    for (oname, p, sz, _uid) in claimed:
        dest = os.path.join(ver_dir, _uid)  # 物理名用 upload_id，稳妥
        try:
            shutil.move(p, dest)
        except Exception:
            dest = p
        saved_files.append({"name": oname, "size": sz, "ver": version, "fid": _uid})

    rec = {
        "task_key": task_key, "sid": sid, "name": name, "cls": cls,
        "version": version, "ts": _now(), "late": is_late,
        "fields": dict(fields or {}), "files": saved_files,
        "check": {"level": chk["level"], "items": chk["items"]},
        "tpl_version": cur_v,
        # 班级匹配：本次提交用的模式 + 最终班级是否落在系统班级列表内
        "class_match": class_mode,
        "class_matched": class_matched,
    }
    with _LOCK:
        _ensure_dir(ENH_DIR)
        _append_jsonl(os.path.join(ENH_DIR, "subs.log.jsonl"), rec)
        snode["latest"] = version
        snode["versions"].append({
            "v": version, "ts": rec["ts"], "level": chk["level"],
            "items": chk["items"], "files": saved_files, "fields": rec["fields"], "late": is_late,
            "class_match": rec["class_match"], "class_matched": rec["class_matched"],
            "cls": rec.get("cls", ""),
        })
        _save(INDEX_PATH, idx)

    return {"ok": True, "version": version, "late": is_late, "check": chk,
            "files": [f["name"] for f in saved_files],
            "cls": cls, "class_match": class_mode, "class_matched": class_matched}


# ---------------------------------------------------------------------------
# 历史 / 我的任务
# ---------------------------------------------------------------------------
def my_record(task_key, sid):
    idx = _load(INDEX_PATH, {})
    snode = idx.get(task_key, {}).get(sid)
    if not snode:
        return {"submitted": False, "latest": 0, "versions": []}
    return {"submitted": True, "latest": snode["latest"], "versions": snode["versions"]}


def my_tasks(cls, sid):
    """门户首页：列出该学生所属班级里、启用增强的任务。"""
    sid = str(sid or "").strip()
    classes = classes_of(sid) if not cls else [cls]
    tasks = _load(TASKS_PATH, {})
    out = []
    for key, t in tasks.items():
        tcls = t.get("class_names") or []
        if tcls and not any(c in tcls for c in classes):
            continue
        rec = my_record(key, sid)
        out.append({
            "task_key": key, "title": t.get("title", key),
            "status": t.get("status", "open"),
            "submitted": rec["submitted"], "latest": rec["latest"],
            "template_version": t.get("template", {}).get("version", 1),
            "materials": len([m for m in t.get("materials", []) if not m.get("hidden")]),
        })
    return out


# ---------------------------------------------------------------------------
# 附件 / 素材下载
# ---------------------------------------------------------------------------
def _may_act_as(auth, sid):
    """当前请求能否以 sid 这个学生的身份操作。

    · 老师：可以（批改/归档/代交）
    · 学生：只能是自己（会话里的学号与 sid 必须一致）
    以前这里只看 token，而 token 是「输入学号就发」的，等于谁都能领一张别人的身份证。
    """
    role = str((auth or {}).get("role") or "")
    if role == "teacher":
        return True
    if role == "student":
        return bool(sid) and str((auth or {}).get("sid") or "") == str(sid)
    return False


def resolve_file(task_key, sid, name, token, auth=None):
    """学生下载自己提交的作品（按 task+sid 限定目录，防止越权）。

    name 形如 v2/<upload_id>（即某版本的某个归档文件）。
    """
    if not _may_act_as(auth, sid):
        ident = check_token(token, "", sid)
        if not ident or str(ident.get("sid", "")) != str(sid):
            return None
    base = os.path.normpath(os.path.join(ENH_DIR, "subs_files", _safe(task_key), _safe(sid)))
    rel = str(name or "").replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    cand = os.path.normpath(os.path.join(base, rel))
    if cand != base and not cand.startswith(base + os.sep):
        return None
    if not os.path.isfile(cand):
        return None
    return cand


def resolve_material(task_key, mid, token, sid, auth=None):
    """学生下载任务素材（任务级可见）。

    素材允许「链接直接发群里」，但必须是登录用户：以前完全匿名，
    只要猜到 task + mid（都是可枚举的短串）就能把老师电脑上的文件拉走。
    """
    task = task_get(task_key)
    if not task:
        return None
    role = str((auth or {}).get("role") or "")
    ok = role in ("teacher", "student")
    if not ok and token:
        ident = check_token(token, "", sid)
        ok = bool(ident) and str(ident.get("sid", "")) == str(sid or "")
    if not ok:
        return None
    for m in task.get("materials", []):
        if m.get("id") == mid and not m.get("hidden"):
            # ref：老师电脑原路径；copy：enhance 副本
            if m.get("mode") == "copy" and m.get("copy_path") and os.path.isfile(m["copy_path"]):
                return m["copy_path"]
            if m.get("mode") != "copy" and m.get("path") and os.path.isfile(m["path"]):
                return m["path"]
    return None


def material_list(task_key, token, sid, admin=False):
    """素材清单。

    老师端（admin=1）看全部，包括失效的；学生端只看到可见且没隐藏的。
    字段名与老师端 JS / 学生门户一致：id / name / size / ext / kind / note /
    available / visible / dl。
    """
    task = ensure_task(task_key)
    out = []
    for m in (task.get("materials") or []):
        if not admin and (m.get("hidden") or m.get("visible") is False):
            continue
        available = _mat_available(m)
        mm = {
            "id": m.get("id"),
            "name": m.get("name", m.get("id")),
            "size": m.get("size", 0),
            "ext": m.get("ext") or (os.path.splitext(m.get("name", ""))[1].lstrip(".").upper() or "FILE"),
            "kind": "copy" if m.get("mode") == "copy" else "ref",
            "mode": m.get("mode", "ref"),
            "note": m.get("note", ""),
            "available": available,
            "dead": not available,
            "visible": m.get("visible", True) is not False,
            # 链接里不再拼 token：token 是身份凭证，拼进 URL 会进浏览器历史、
            # 进截图、进 Referer；下载接口改成认会话（见 server.py 的 /api/enh/dl）
            "dl": "/api/enh/dl?kind=material&task=%s&mid=%s&sid=%s" % (
                urllib.parse.quote(task_key), urllib.parse.quote(m.get("id", "")),
                urllib.parse.quote(sid or "")),
        }
        out.append(mm)
    return out


# ---------------------------------------------------------------------------
# 导出 CSV（老师端核对报告）
# ---------------------------------------------------------------------------
def export_csv(task_key, cls):
    task = ensure_task(task_key)
    idx = _load(INDEX_PATH, {})
    roster = [r for r in roster_all() if (not cls) or str(r.get("cls", "")) == str(cls)]
    snode = idx.get(task_key, {})
    rows = []
    header = ["学号", "姓名", "班级", "班级匹配", "已交版本", "最新提交时间",
              "核对级别", "问题", "作品文件"]
    for r in roster:
        sid = str(r.get("sid", ""))
        rec = snode.get(sid)
        if rec and rec.get("versions"):
            v = rec["versions"][-1]
            issues = "; ".join(i["msg"] for i in v.get("items", []) if i["lv"] != "info")
            files = "; ".join(f["name"] for f in v.get("files", []))
            # 班级用「学生实际提交/解析出来的」，不是花名册里的：
            # 不匹配模式下学生可能写了别的班，这时老师要看到学生写的那个
            sub_cls = v.get("cls") or r.get("cls", "")
            # 班级匹配状态：auto 且匹配上了=已匹配；auto 没匹配上=未匹配；none=未匹配(不处理)
            cm = v.get("class_match")
            if cm == "none":
                match_txt = "未匹配（不匹配模式）"
            elif v.get("class_matched"):
                match_txt = "已匹配"
            else:
                match_txt = "未匹配"
            rows.append([sid, r.get("name", ""), sub_cls, match_txt,
                         v["v"], v["ts"], v.get("level", "pass"), issues, files])
        else:
            rows.append([sid, r.get("name", ""), r.get("cls", ""), "", 0, "", "", "未提交", ""])
    # 标题行 + BOM 方便 Excel 打开中文
    out = "\ufeff" + ",".join(header) + "\n"
    for row in rows:
        out += ",".join('"%s"' % (str(c).replace('"', '""')) for c in row) + "\n"
    return out


# ---------------------------------------------------------------------------
# 入口分发（被 server.py 调用）
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 模板（老师端 F1）
# ---------------------------------------------------------------------------
def form_save(data):
    """保存模板：{task, name, sections} 或 {task, preset}。版本号 +1。"""
    key = data.get("task") or data.get("task_key")
    if not key:
        return {"ok": False, "msg": "缺少任务"}
    tasks = _load(TASKS_PATH, {})
    t = tasks.get(key) or ensure_task(key)
    tpl = t.get("template") or default_template()

    if data.get("preset"):
        p = PRESETS.get(str(data["preset"]).upper())
        if not p:
            return {"ok": False, "msg": "没有这个预设"}
        new_tpl = {"version": tpl.get("version", 0) + 1, "name": p["name"],
                   "sections": json.loads(json.dumps(p["sections"])), "updated_at": _now()}
    else:
        sections = data.get("sections")
        if not isinstance(sections, list) or not sections:
            return {"ok": False, "msg": "模板内容为空"}
        # 校验：字段必须有 key 和 label；系统字段 key 不允许自定义
        fields = []
        for sec in sections:
            for f in (sec.get("fields") or []):
                key_ = str(f.get("key") or "").strip()
                label = str(f.get("label") or "").strip()
                if not label:
                    return {"ok": False, "msg": "有字段没填名字"}
                if key_ in SYS_KEYS:
                    return {"ok": False, "msg": "「%s」是系统字段，不能自己添加" % label}
                f["key"] = key_ or ("f" + uuid.uuid4().hex[:8])
                f.setdefault("type", "text")
                f.setdefault("required", False)
                fields.append(f)
        if not fields:
            return {"ok": False, "msg": "模板里至少要有一个字段"}
        if len(fields) > 15:
            return {"ok": False, "msg": "字段太多（15 个上限），学生填不完"}
        new_tpl = {"version": tpl.get("version", 0) + 1,
                   "name": data.get("name") or tpl.get("name") or "作品提交",
                   "sections": json.loads(json.dumps(sections)), "updated_at": _now()}

    t["template"] = new_tpl

    # 班级匹配选项和模板一起保存（模板编辑器里那个单选）
    if "class_match" in data:
        pol = normalize_policy(t.get("policy"))
        pol["class_match"] = normalize_class_mode(data.get("class_match"))
        t["policy"] = pol

    t["updated_at"] = _now()
    tasks[key] = t
    _save(TASKS_PATH, tasks)
    return {"ok": True, "template": new_tpl,
            "class_match": normalize_class_match(t.get("policy"))}


# ---------------------------------------------------------------------------
# 素材（老师端 F3）
# ---------------------------------------------------------------------------
def _mat_id(name):
    return "m_" + uuid.uuid4().hex[:10]


def material_add(data):
    """下发素材：items 形如 {from:'mount', id, rel} 或 {from:'course', id, name}。"""
    key = data.get("task") or data.get("task_key")
    if not key:
        return {"ok": False, "msg": "缺少任务"}
    tasks = _load(TASKS_PATH, {})
    t = tasks.get(key) or ensure_task(key)
    mode = data.get("mode") or "ref"
    note = str(data.get("note") or "")[:40]
    items = data.get("items") or []
    if not items:
        return {"ok": False, "msg": "没勾要下发的文件"}

    added, skipped = [], []
    mats = list(t.get("materials") or [])
    for it in items:
        try:
            src = _resolve_material_source(it)
        except Exception as e:
            src = None
            skipped.append({"name": it.get("name") or it.get("id") or "?", "reason": str(e)})
        if not src:
            skipped.append({"name": it.get("name") or it.get("id") or "?", "reason": "文件找不到或没权限"})
            continue
        full, name = src
        if not os.path.isfile(full):
            skipped.append({"name": name, "reason": "文件不存在"})
            continue
        size = os.path.getsize(full)
        ext = (os.path.splitext(name)[1].lstrip(".") or "").upper()
        m = {"id": _mat_id(name), "name": name, "size": size, "ext": ext,
             "note": note, "visible": True, "hidden": False,
             "mode": mode, "path": full, "copy_path": ""}
        if mode == "copy":
            dst_dir = _ensure_dir(os.path.join(ENH_DIR, "materials", m["id"]))
            dst = os.path.join(dst_dir, _safe(name))
            try:
                shutil.copy2(full, dst)
                m["copy_path"] = dst
            except Exception as e:
                skipped.append({"name": name, "reason": "复制失败：" + str(e)})
                continue
        mats.append(m)
        added.append({"id": m["id"], "name": name})

    t["materials"] = mats
    t["updated_at"] = _now()
    tasks[key] = t
    _save(TASKS_PATH, tasks)
    return {"ok": True, "added": added, "skipped": skipped}


def _resolve_material_source(it):
    """把前端传来的素材条目解析成 (绝对路径, 文件名)。

    只认两种来源：课件库（course）与本地资料夹（mount）。
    以前还有一个「兜底：直接给绝对路径」的分支，于是
    `POST /api/enh/material/add {"items":[{"path":"C:/Windows/win.ini"}]}`
    就能把服务器上任意文件登记成素材，再用 /api/enh/dl 下载走 —— 任意文件读取。
    课件库/资料夹本身都有 commonpath 边界，不需要这个后门。
    """
    frm = it.get("from")
    if frm == "mount":
        mid, rel = it.get("id"), it.get("rel") or ""
        full = CW.mount_abs(mid, rel)
        if not full:
            return None
        return full, os.path.basename(rel) or os.path.basename(full)
    if frm == "course":
        cid = it.get("id")
        full = CW.abs_of(cid, must_exist=True)
        if not full:
            return None
        return full, it.get("name") or os.path.basename(full)
    return None


def material_update(data):
    """素材操作：visible（显示/隐藏）、to_copy（改为副本）、remove（移除）、move（排序）。"""
    key = data.get("task") or data.get("task_key")
    mid = data.get("id")
    action = data.get("action")
    if not key or not mid:
        return {"ok": False, "msg": "缺少参数"}
    tasks = _load(TASKS_PATH, {})
    t = tasks.get(key) or ensure_task(key)
    mats = list(t.get("materials") or [])

    if action == "move":
        order = data.get("order") or []
        pos = {m.get("id"): i for i, m in enumerate(mats)}
        mats.sort(key=lambda m: pos.get(m.get("id"), 999))
        if order:
            rank = {str(x): i for i, x in enumerate(order)}
            mats.sort(key=lambda m: rank.get(str(m.get("id")), 999))
        t["materials"] = mats
        t["updated_at"] = _now()
        tasks[key] = t
        _save(TASKS_PATH, tasks)
        return {"ok": True, "items": material_list(key, "", "", admin=True)}

    hit = None
    for m in mats:
        if m.get("id") == mid:
            hit = m
            break
    if not hit:
        return {"ok": False, "msg": "找不到这个素材"}

    if action == "remove":
        mats = [m for m in mats if m.get("id") != mid]
    elif action == "visible":
        hit["visible"] = bool(data.get("visible", True))
        hit["hidden"] = not hit["visible"]
    elif action == "to_copy":
        src = hit.get("copy_path") if hit.get("mode") == "copy" else hit.get("path")
        if not src or not os.path.isfile(src):
            return {"ok": False, "msg": "原文件已经不在了，没法转副本"}
        dst_dir = _ensure_dir(os.path.join(ENH_DIR, "materials", hit["id"]))
        dst = os.path.join(dst_dir, _safe(hit.get("name") or "file"))
        try:
            shutil.copy2(src, dst)
        except Exception as e:
            return {"ok": False, "msg": "复制失败：" + str(e)}
        hit["mode"] = "copy"
        hit["copy_path"] = dst
        hit["path"] = src

    t["materials"] = mats
    t["updated_at"] = _now()
    tasks[key] = t
    _save(TASKS_PATH, tasks)
    return {"ok": True, "items": material_list(key, "", "", admin=True)}


def material_check(data):
    """素材体检：逐个检查文件还在不在，统计失效数量。"""
    key = data.get("task") or data.get("task_key")
    if not key:
        return {"ok": False, "msg": "缺少任务"}
    t = task_get(key) or ensure_task(key)
    mats = t.get("materials") or []
    dead = [m.get("name") for m in mats if not _mat_available(m)]
    alive = len(mats) - len(dead)
    msg = "%d 个正常" % alive + (("，%d 个失效：%s" % (len(dead), "、".join(dead[:3]))) if dead else "")
    return {"ok": True, "msg": msg, "total": len(mats), "dead": dead}


# ---------------------------------------------------------------------------
# 学生：撤回 / 回执
# ---------------------------------------------------------------------------
def withdraw(task_key, sid, token):
    """撤回最近一次提交（文件保留，索引标记 withdrawn）。"""
    ident = check_token(token, "", sid)
    if not ident:
        return {"ok": False, "code": "E_TOKEN", "msg": "身份已过期，刷新页面重新输学号"}
    pol = normalize_policy((task_get(task_key) or {}).get("policy"))
    if not pol["submit"].get("allow_withdraw", True):
        return {"ok": False, "code": "E_NO_WITHDRAW", "msg": "老师关闭了撤回功能"}
    idx = _load(INDEX_PATH, {})
    snode = idx.get(task_key, {}).get(sid)
    if not snode or not snode.get("versions"):
        return {"ok": False, "code": "E_NOTHING", "msg": "你还没有交过东西"}
    with _LOCK:
        snode["withdrawn"] = True
        _save(INDEX_PATH, idx)
    return {"ok": True, "msg": "已撤回"}


def receipt_html(task_key, sid, version):
    """提交回执：一页可打印的小票。"""
    t = task_get(task_key) or {}
    rec = my_record(task_key, sid)
    ver = None
    for v in (rec.get("versions") or []):
        if str(v.get("v")) == str(version):
            ver = v
    if not ver:
        ver = (rec.get("versions") or [{}])[-1]
    roster = roster_lookup("", sid) or {}
    name = roster.get("name", "")
    cls = roster.get("cls", "")
    files = "、".join(f.get("name", "") for f in (ver.get("files") or []))
    fields = ver.get("fields") or {}
    rows = []
    for k, v in fields.items():
        if v:
            # 字段名和值都是学生自己填的，必须转义：
            # 以前直接拼进 <td>，学生把答案写成 <img onerror=...> 就能在老师的回执页执行脚本
            rows.append("<tr><td>%s</td><td>%s</td></tr>" % (esc_html(k), esc_html(str(v)[:200])))
    late = "（补交）" if ver.get("late") else ""
    # 样式表单独拼，不参与下面的 % 格式化：
    # CSS 里有 width:100%; 这样的百分号，混在 % 模板里会被当成格式符，
    # 结果是这一整页每次都抛 ValueError → 打印回执永远 500。
    _css = """<style>
body{font:14px/1.9 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:640px;margin:0 auto;padding:28px;color:#1F1B16;background:#FFFDFA}
h1{font-size:19px;margin:0 0 4px}p.sub{color:#8A8178;margin:0 0 18px;font-size:12.5px}
table{border-collapse:collapse;width:100%;margin:8px 0 14px}
td{border:1px solid #E7E1D6;padding:7px 10px;font-size:13px}
td:first-child{background:#F7F3EC;width:120px;color:#6B6259}
.box{border:1px solid #E7E1D6;border-radius:10px;padding:14px 16px;background:#fff}
.ok{color:#2E7D32;font-weight:600}.warn{color:#B26A00;font-weight:600}
@media print{body{background:#fff;padding:0}}
</style>"""
    html = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>提交回执</title>""" + _css + """
<h1>提交回执</h1>
<p class="sub">%s · %s · %s%s</p>
<div class="box">
<div>作品标题：<b>%s</b></div>
<div>提交时间：%s（第 %s 版）</div>
<div>提交文件：%s</div>
<table>%s</table>
</div>
<p class="sub" style="margin-top:16px">此回执由班级工作台自动生成，可直接打印交给老师。</p>
</html>""" % (
        esc_html(t.get("title") or task_key), esc_html(cls), esc_html(sid), late,
        esc_html(str(fields.get("work_title") or "(未填)")),
        esc_html(str(ver.get("ts", ""))), esc_html(str(ver.get("v", ""))),
        esc_html(files or "(无文件)"),
        "".join(rows) or "<tr><td colspan='2'>（无附加内容）</td></tr>",
    )
    return html


def esc_html(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


# ---------------------------------------------------------------------------
# 入口分发（被 server.py 调用）
# ---------------------------------------------------------------------------
def _deny(msg="需要先登录", code=401):
    return {"ok": False, "msg": msg, "_code": code}


def _role_of(auth):
    return str((auth or {}).get("role") or "")


def _teacher_only(auth, what="这个操作"):
    if _role_of(auth) != "teacher":
        return _deny("只有教师端可以%s" % what, 403)
    return None


def _self_only(auth, sid, action="查看"):
    """学生只能看/改自己的；老师不受限。返回 None 表示放行。

    action 是一句完整的话（如「查看别的同学的记录」），拼进提示里。
    """
    role = _role_of(auth)
    if role == "teacher":
        return None
    if role == "student":
        if sid and str((auth or {}).get("sid") or "") == str(sid):
            return None
        return _deny("不能%s" % action, 403)
    return _deny("请先在 /s/ 登录", 401)


def get_json(path, qs, host="", client_ip="", auth=None):
    """GET /api/enh/* 的分发。返回 dict；特殊键 __csv / __html 由 server 处理。"""
    arg = lambda k: (qs.get(k) or [""])[0]
    role = _role_of(auth)

    if path == "/api/enh/meta":
        return {"ok": True, "version": 1, "name": "学生提交增强"}

    if path == "/api/enh/task":
        key = arg("key") or arg("task")
        t = task_get(key)
        if not t:
            # GET 不该有写副作用：以前这里会顺手建一个空任务，
            # 任何人访问一次 /api/enh/task?task=xxx 就在磁盘上多出一个任务
            if role != "teacher":
                return _deny("任务不存在", 404)
            t = ensure_task(key, api=arg("api"), class_names=arg("class_names"))
        pub = task_public(t)
        return {"ok": True, "task": pub, "portal": portal_url(t, host),
                "class_match": normalize_class_match(t.get("policy")),
                "class_options": roster_class_names() if role else []}

    if path == "/api/enh/history":
        bad = _self_only(auth, arg("sid"), "查看别人的记录")
        if bad:
            return bad
        t = task_get(arg("task")) or {}
        rec = my_record(arg("task"), arg("sid")) or {}
        return {"ok": True, "record": rec, "qf_base": qf_base(t)}

    if path == "/api/enh/my":
        bad = _self_only(auth, arg("sid"), "查看别人的记录")
        if bad:
            return bad
        return {"ok": True, "tasks": my_tasks(arg("cls"), arg("sid"))}

    if path == "/api/enh/materials":
        if not role:
            return _deny("请先在 /s/ 登录", 401)
        return {"ok": True, "materials": material_list(arg("task"), arg("token"), arg("sid"))}

    if path == "/api/enh/classes":
        # 班级名册属于内部信息，登录后才给（登录页下拉不需要它）
        if not role:
            return _deny("请先登录", 401)
        return {"ok": True, "classes": roster_class_names()}

    if path == "/api/enh/material/list":
        admin = arg("admin") == "1"
        if admin:
            bad = _teacher_only(auth, "查看素材清单")
            if bad:
                return bad
        elif not role:
            return _deny("请先登录", 401)
        return {"ok": True, "items": material_list(arg("task"), arg("token"), arg("sid"), admin=admin)}

    if path == "/api/enh/receipt":
        bad = _self_only(auth, arg("sid"), "查看别人的记录")
        if bad:
            return bad
        return {"__html": receipt_html(arg("task"), arg("sid"), arg("v"))}

    if path == "/api/enh/export":
        bad = _teacher_only(auth, "导出核对报告")
        if bad:
            return bad
        csv = export_csv(arg("task"), arg("cls"))
        return {"__csv": csv}

    return None


def post_json(path, data, host="", client_ip="", ua="", auth=None):
    """POST /api/enh/* 的分发。"""
    role = _role_of(auth)

    if path == "/api/enh/task/save":
        bad = _teacher_only(auth, "修改任务")
        if bad:
            return bad
        return task_save(data)

    if path == "/api/enh/form/save":
        bad = _teacher_only(auth, "修改模板")
        if bad:
            return bad
        return form_save(data)

    if path == "/api/enh/material/add":
        bad = _teacher_only(auth, "下发素材")
        if bad:
            return bad
        return material_add(data)

    if path == "/api/enh/material/update":
        bad = _teacher_only(auth, "调整素材")
        if bad:
            return bad
        return material_update(data)

    if path == "/api/enh/material/check":
        bad = _teacher_only(auth, "体检素材")
        if bad:
            return bad
        return material_check(data)

    if path == "/api/enh/student/verify":
        sid = str(data.get("sid") or "").strip()
        if not sid:
            return {"ok": False, "code": "E_SID", "msg": "请输入学号"}
        # 身份不再「填学号就发」：必须已经以这个学号登录（或老师代操作）
        bad = _self_only(auth, sid, "以别人的身份提交")
        if bad:
            return bad

        cls = (data.get("cls") or "").strip()
        task_key = data.get("task_key") or data.get("task") or ""
        task = task_get(task_key) or {}
        mode = normalize_class_match(task.get("policy"))

        # 先按模式解析出学生最终归属的班级
        final_cls, matched, cmsg = resolve_student_class(sid, cls, mode)

        # 姓名仍然按学号到花名册里取（这是身份核对，与班级匹配互相独立）
        rec = roster_lookup(final_cls or cls, sid)
        if not rec:
            return {"ok": False, "code": "E_NOT_IN_ROSTER",
                    "msg": "学号 %s 不在 %s 的花名册里" % (sid, final_cls or cls or "本班")}
        name = rec.get("name", "")

        tok = issue_token(task_key, final_cls, sid, name)
        out = {"ok": True, "sid": sid, "name": name, "cls": final_cls, "token": tok,
               "class_match": mode, "class_matched": matched}
        if mode == "auto":
            out["class_options"] = roster_class_names()
            if cmsg:
                out["class_msg"] = cmsg
        return out

    if path == "/api/enh/submit":
        sid = str(data.get("sid") or "").strip()
        bad = _self_only(auth, sid, "替别人提交")
        if bad:
            return bad
        return commit_submission(
            data.get("task"), sid, data.get("token"),
            data.get("fields", {}), data.get("uploads", []), data.get("tpl_version", 0),
            auth=auth)

    if path == "/api/enh/withdraw":
        sid = str(data.get("sid") or "").strip()
        bad = _self_only(auth, sid, "撤回别人的提交")
        if bad:
            return bad
        return withdraw(data.get("task"), sid, data.get("token"))

    return None
