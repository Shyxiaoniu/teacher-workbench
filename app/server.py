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
import secrets
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
import accounts as ACC                 # noqa: E402  管理员 / 多教师账号 + 工作区隔离
import account_config as ACFG          # noqa: E402  账号级配置代理（config.json 不再全服务共享）
import pages as P                      # noqa: E402
import projection as PJ                # noqa: E402
import ports as PORTX                  # noqa: E402
from store import Store, TABLES, ImportRefused   # noqa: E402
from activity import ActivityStore, parse_multipart   # noqa: E402  课堂活动（自研）
from typing_store import TypingStore   # noqa: E402
import studio_data as STUDIO           # noqa: E402  学生设计作品（宠物/蛋/主题）
from quiz_store import QuizStore       # noqa: E402  测验/检测（试卷含答案，独立存储）
from quiz_api import (QuizAPI, EPOCH, last_event, bump as quiz_bump,
                      set_account_provider as _quiz_set_account)   # noqa: E402
from design_store import DesignStore   # noqa: E402  H5 网页设计草稿（2.0.39）
from pixel_store import PixelStore     # noqa: E402  像素画草稿（2.0.47）
from updater import Updater            # noqa: E402  在线更新（检查/下载/安装/回滚）
import ooxml_preview as OOX            # noqa: E402  课件在线预览（纯 Python 解析 OOXML）
import enc_ctx as ENC                  # noqa: E402  静态加密上下文（P1+）
import migrate_enc as MIG              # noqa: E402  存量数据后台迁移（P4）
from entry_token import EntryToken     # noqa: E402  学生专属入口签名令牌（账号隔离）

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

# ---- 账号与工作区（2.0.13）------------------------------------------------
# 原单账号升级时映射成 id="default" 的教师账号，工作区就是原 DATA_DIR，
# workbench.db / courseware / submissions 的路径一字不改，老数据零迁移。
WS = ACC.Workspace(DATA_DIR)
# 2026-11-25：配置也按账号隔离。CFG 不再是全局 dict，而是按当前请求账号
# 路由的代理；端口/更新/备份等部署设置仍读原 config.json。
CFG = ACFG.AccountConfig(CONFIG_PATH, WS)
ACCOUNTS = ACC.AccountStore(DATA_DIR, legacy_teacher=CFG.get("teacher") or {})
# 存量账号首次启动时把当时共享的配置快照成自己的初值，之后互不影响。
CFG.migrate_existing(ACCOUNTS.teacher_ids())
SESS = A.Sessions()
# 学生专属入口签名令牌（2026-11-25 账号隔离）：密钥落数据目录、重启不变。
# 老师端入口地址带 e=令牌，学生端所有公开接口/登录都验它，杜绝跨账号串班。
ET = EntryToken(DATA_DIR)

# 资料包导入/导出进度任务（2026-11-25）：打包/解压在后台线程跑，
# 前端拿一次性 job id 轮询进度。任务只存在内存里，重启后老 id 自动失效；
# job id 绑定登录会话 cookie，别人拿到 id 也读不到进度/下载不到文件。
class PackCancelled(Exception):
    """后台打包/解压被用户取消：只用于安全中断，不留半成品。"""


PACK_JOBS = {}
PACK_JOBS_LOCK = threading.Lock()
PACK_JOB_TTL = 30 * 60


def _pack_job_new(kind, sess, owner, **extra):
    jid = secrets.token_urlsafe(18)
    now = time.time()
    with PACK_JOBS_LOCK:
        for k, v in list(PACK_JOBS.items()):
            if now - v.get("created", 0) > PACK_JOB_TTL and v.get("phase") in ("ready", "done", "error"):
                PACK_JOBS.pop(k, None)
        j = {"id": jid, "kind": kind, "owner": owner,
             "account": (sess or {}).get("account"), "created": now, "phase": "starting",
             "done": 0, "total": 0, "filesDone": 0, "filesTotal": 0,
             "percent": 0, "error": "", "result": None}
        j.update(extra)
        PACK_JOBS[jid] = j
    return jid


def _pack_job_set(jid, **fields):
    with PACK_JOBS_LOCK:
        j = PACK_JOBS.get(jid)
        if j is not None:
            j.update(fields)


def _pack_job_cancel(jid, owner):
    """给后台打包/解压任务打取消标记（2026-11-25）。

    只认同会话 owner；只有还在跑（packing/extracting/starting）的任务能取消，
    已经 ready/done 的包不动——那种情况直接告诉前端「已经完成了」。
    后台线程每读一块检查一次 cancel 标记，自己清理临时文件并收尾。
    """
    with PACK_JOBS_LOCK:
        j = PACK_JOBS.get(str(jid or ""))
        if not j or j.get("owner") != owner:
            return None, "任务不存在或已过期"
        if j.get("phase") not in ("starting", "packing", "extracting"):
            return None, "任务已结束，无需取消"
        j["cancel"] = True
        return dict(j), ""


def _pack_job_cancelled(jid):
    with PACK_JOBS_LOCK:
        j = PACK_JOBS.get(jid)
        return bool(j and j.get("cancel"))


def _pack_job_active(owner):
    """找当前会话还在跑的打包/解压任务（刷新页面后恢复进度用）。

    只认 packing/extracting/ready 三种状态：ready 的包还没下载/加密完，
    刷新后也要能接着走；done/cancelled/error 都已结束，不再恢复。
    """
    with PACK_JOBS_LOCK:
        cands = [j for j in PACK_JOBS.values()
                 if j.get("owner") == owner and
                 j.get("phase") in ("starting", "packing", "extracting", "ready") and
                 time.time() - j.get("created", 0) <= PACK_JOB_TTL]
        if not cands:
            return None
        cands.sort(key=lambda x: x.get("created", 0), reverse=True)
        j = cands[0]
        return {"job": j.get("id"), "kind": j.get("kind"), "phase": j.get("phase"),
                "percent": j.get("percent") or 0, "done": j.get("done") or 0,
                "total": j.get("total") or 0, "filesDone": j.get("filesDone") or 0,
                "filesTotal": j.get("filesTotal") or 0, "error": j.get("error") or ""}


def _pack_job_get(jid, owner):
    with PACK_JOBS_LOCK:
        j = PACK_JOBS.get(str(jid or ""))
        if not j or j.get("owner") != owner:
            return None
        return dict(j)


# 2.0.71 账号隔离：测验 SSE 事件按当前工作区账号分桶，
# 避免 A 教师的提交实时推给 B 教师（事件里带班级 / 学号 / 窗口名）。
_quiz_set_account(ACC.current_account)

def _store_for(a):
    WS.ensure(a)
    return Store(WS.paths_for(a)["db"])

def _act_for(a):
    WS.ensure(a)
    p = WS.paths_for(a)
    return ActivityStore(p["db"], p["activity"])

def _typing_for(a):
    WS.ensure(a)
    return TypingStore(WS.paths_for(a)["typing"])

def _quiz_for(a):
    p = WS.ensure(a)
    return QuizStore(p["quiz"])


def _design_for(a):
    """H5 网页设计草稿：按账号隔离的 design.json（2.0.39）。"""
    WS.ensure(a)
    return DesignStore(WS.paths_for(a)["design"])


def _pixel_for(a):
    """像素画草稿：按账号隔离的 pixel.json（2.0.47）。"""
    WS.ensure(a)
    return PixelStore(WS.paths_for(a)["pixel"])


# 模块级单例改成「按当前请求账号懒加载的代理」：业务代码里几百处
# STORE.xxx / ACT.xxx / TYPING.xxx 一行都不用改，数据自动按账号隔离。
# 课堂活动附件落 <工作区>/activity/<活动号>/，URL 仍是 /static/uploads/…。
STORE = ACC.WorkspaceProxy(_store_for)
ACT = ACC.WorkspaceProxy(_act_for)
TYPING = ACC.WorkspaceProxy(_typing_for)
# 测验/检测：独立 quiz.json，不含在学生端同步投影里（防泄答案）
QUIZ = ACC.WorkspaceProxy(_quiz_for)
# H5 网页设计：只有学生草稿，提交记录统一走 ACT（大屏 / 作品墙 / 归档同一份）
DESIGN = ACC.WorkspaceProxy(_design_for)
# 像素画（2.0.47）：同样只有草稿，作品提交进 ACT + 自动归档
PIXEL = ACC.WorkspaceProxy(_pixel_for)


def _enc_unlock_reload(acc, fek):
    """服务重启后 FEK 首次解锁：重载此前无法解密的独立 JSON Store。

    登录请求里 ACC 已切到该账号，直接访问 proxy 会懒创建实例（此刻线程 FEK
    已由 cache_account_fek 临时注入），_load() 才能读到密文的真实内容。
    不能只用 instances().get() —— 实例尚未创建时会返回 None，重载被跳过，
    之后首次业务访问在匿名请求线程（FEK=None）里建实例 → 读到空壳。
    """
    for proxy in (TYPING, QUIZ, DESIGN, PIXEL):
        try:
            obj = proxy.for_account(acc)   # 懒创建/取回该账号实例，此刻线程 FEK 已注入
            if hasattr(obj, "_load"):
                obj._load()
        except Exception:
            pass
    # FEK 首次解锁 = 教师登录（或重启后第一次带会话的请求）：后台迁移存量明文。
    MIG.start(WS, acc, fek)


ENC.register_unlock_hook(_enc_unlock_reload)


def _quiz_cross(handler, path, body):
    """测验的跨账号操作（试卷分享）。只处理白名单里的路径，未匹配返回 None
    交给 QuizAPI 自己分发。跨账号能力集中在 server 层，这里能拿到全部全局。"""
    if path != "/api/v2/quiz/teacher/share":
        return None
    if not handler._is_teacher():
        handler._json({"ok": False, "msg": "未登录"}, 401)
        return True
    pid = str(body.get("paper") or body.get("id") or "").strip()
    targets = body.get("targets") or []
    if not isinstance(targets, list):
        targets = [targets]
    if not pid or not targets:
        handler._json({"ok": False, "msg": "请先选试卷和要分享的老师"}, 400)
        return True
    # 源卷在当前账号工作区；没有就拒绝
    src = QUIZ.paper(pid) if hasattr(QUIZ, "paper") else None
    if not isinstance(src, dict) or not src.get("id"):
        handler._json({"ok": False, "msg": "试卷不存在"}, 404)
        return True
    valid = set(ACCOUNTS.teacher_ids()) - {ACC.current_account()}
    targets = [str(x) for x in targets if str(x) in valid]
    if not targets:
        handler._json({"ok": False, "msg": "没有可用的目标教师账号"}, 400)
        return True
    copies = []
    for tid in targets:
        try:
            # 目标账号自动生成新 id，不覆盖对方已有试卷；题目保留原答案
            newp = QUIZ.for_account(tid).save_paper({
                k: v for k, v in src.items() if k != "id"
            })
            if newp:
                copies.append({"target": tid, "id": newp.get("id"),
                               "title": newp.get("title")})
        except Exception as e:
            STORE.audit("teacher", "share-quiz-fail", "%s %s" % (tid, e))
    if not copies:
        handler._json({"ok": False, "msg": "分享失败，目标账号可能不可写"}, 500)
        return True
    STORE.audit("teacher", "share-quiz", "%s → %s" % (pid, ",".join(t for t in targets)))
    handler._json({"ok": True, "copies": copies})
    return True


def _quiz_api(handler):
    """按当前请求账号构造接口层；路径也随账号走，多教师互不串卷。"""
    p = WS.ensure(ACC.current_account())
    return QuizAPI(handler, QUIZ, STORE, ACT, p["quiz_files"],
                   student_row, handler._class_name, notify,
                   cross_account=_quiz_cross)

# 旧提交系统 / 课件库 / 增强模块原本各有一套模块级全局路径常量，
# 注入路径函数后它们也跟着当前账号走（模块内改为 PATH_FN() 取值）。
LEG.PATH_FN = WS.legacy_paths
LEG.CW.PATH_FN = WS.courseware_paths
LEG.ENH.PATH_FN = WS.enhance_paths


def _archive_after_submit(task, who, res):
    """学生提交成功后立刻归档到「资料」对应文件夹（2026-11-05）。

    为什么放在服务端：以前是「大屏刷新时顺带归档」，老师没打开大屏、或页面
    被关掉，学生交的文件就一直进不去资料夹（表现为「传了文件但资料里没有」）。

    落点：<课程>/<班级>/<学号+姓名>/<原文件名>
      · 课程 = act_task.course（大屏选课程时写入）；没有就不猜，避免在资料里
        凭空建一个以活动标题命名的文件夹；
      · 班级 = 学生自己登录时带的班级，比大屏当前选中的班更准；
      · 纯数据提交（没有文件）直接返回，不建任何文件夹。

    失败绝不影响提交：异常全部吞掉，只是这次没归档，老师仍可在大屏点「归档」。
    """
    try:
        files = (res or {}).get("files") or []
        if not files:
            return None
        t = ACT.get_task(task) or {}
        title = str(t.get("title") or "").strip() or str(task)
        course = str(t.get("course") or "").strip()
        if not course:
            try:
                courses, _err = LEG.CW.list_courses()
                names = {str(c.get("name") or "") for c in (courses or [])}
            except Exception:
                names = set()
            if title in names:
                course = title
        if not course:
            return None
        # 身份多来源兜底（2026-11-06）：学习单页面没注入身份 / 用了英文别名时，
        # 以前会落成「未命名学生」甚至平铺在班级目录里，老师认不出是谁交的。
        first = lambda *vals: next((str(v).strip() for v in vals if v not in (None, "")), "")
        name = first((who or {}).get("姓名"), (who or {}).get("name"))
        no = first((who or {}).get("学号"), (who or {}).get("student_no"),
                   (who or {}).get("sid"))
        cls_name = first((who or {}).get("班级"), (who or {}).get("cls"),
                         (who or {}).get("class_name"))
        # 还缺就从刚落库的这条提交记录里反查（同一学生票 / 中文字段都认）
        if not (name and no):
            try:
                for s in reversed(ACT.submissions(task) or []):
                    if not isinstance(s, dict):
                        continue
                    name = name or first(s.get("姓名"), s.get("name"))
                    no = no or first(s.get("学号"), s.get("student_no"), s.get("sid"))
                    cls_name = cls_name or first(s.get("班级"), s.get("cls"))
                    if name and no:
                        break
            except Exception:
                pass
        items = [{"name": str(f.get("name") or ""), "url": str(f.get("url") or ""),
                  "student": name, "no": no, "class": cls_name} for f in files]
        out = LEG.CW.archive_items(course, title, "", items, cls=cls_name)
        if not (out or {}).get("ok"):
            # 归档失败不影响提交，但要留一条痕迹，方便老师反馈「文件没进资料」时排查
            try:
                STORE.audit("system", "auto-archive-fail",
                            "%s → %s：%s" % (task, course, (out or {}).get("msg") or "未知原因"))
            except Exception:
                pass
        return out
    except Exception as e:
        try:
            STORE.audit("system", "auto-archive-fail", "%s：%s" % (task, e))
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# H5 网页设计（2.0.39）
# ---------------------------------------------------------------------------
# 定位：教师端「其他 · 网页设计」一键启停的小服务，学生在学生端页签里
# 左边写 HTML/CSS、右边实时预览；点一次「提交作品」= 截图 + 存档 + 归档
# + 进大屏看板，老师不用再做任何搬运。
#
# 数据分两处，各管一段，不重复存：
#   · 草稿 → DESIGN（design.json，按学生存，可跨设备续写）
#   · 提交 → ACT（活动库；大屏作品墙、按班统计、自动归档都读这一份）
# ---------------------------------------------------------------------------
def design_cfg():
    """取设计功能配置（缺字段就地补默认值，老配置不用迁移）。"""
    d = CFG.get("design")
    if not isinstance(d, dict):
        d = {}
        CFG["design"] = d
    d.setdefault("enabled", False)
    d.setdefault("allow_add", True)
    d.setdefault("allow_submit", True)
    d.setdefault("course", "网页设计")
    d.setdefault("window", "网页设计作品")
    d.setdefault("intro", "")
    # 每人最多提交几次（2.0.53）：0 = 不限。默认 1，和像素画同一套口径。
    d.setdefault("max_submit", 1)
    return d


def studio_cfg():
    """学生设计作品配置：designers（设计者信息）+ 免费孵化额度。

    2.0.68：每个账号默认送 1 次学生作品蛋免费孵化（教师演示 2 次），
    额度也存 config.json，教师端可改；实际使用次数记在 typing.json 的学生档案里，
    改额度不影响已经用过的次数。"""
    c = CFG.get("studio")
    if not isinstance(c, dict):
        c = {}
        CFG["studio"] = c
    ds = c.get("designers")
    if not isinstance(ds, dict):
        ds = STUDIO.default_designers() if STUDIO else {}
        c["designers"] = ds
    # 补齐后来新增作品的设计者缺省项，老配置平滑升级
    if STUDIO:
        for wid, d in STUDIO.default_designers().items():
            ds.setdefault(wid, d)
    for key, default in (("free_student", 1), ("free_teacher", 2)):
        try:
            n = int(c.get(key, default))
        except (TypeError, ValueError):
            n = default
        c[key] = max(0, min(9, n))
    return c


def studio_free_cfg():
    """给前端 / 兑换接口用的免费孵化额度（教师与学生两套）。"""
    c = studio_cfg()
    return {"student": c["free_student"], "teacher": c["free_teacher"]}


def design_public_cfg():
    """给前端的安全副本：开关 / 权限 / 课程 / 窗口 / 任务说明 / 提交上限。"""
    d = design_cfg()
    return {"enabled": bool(d.get("enabled")),
            "allow_add": bool(d.get("allow_add")),
            "allow_submit": bool(d.get("allow_submit")),
            "course": str(d.get("course") or ""),
            "window": str(d.get("window") or ""),
            "intro": str(d.get("intro") or ""),
            "max_submit": _max_submit_of(d)}


def design_task(course, create=False):
    """找（或建）这门课在大屏里的收集任务。

    act_task.course 是「资料」里的课程文件夹名，自动归档只认这个字段；
    title 与课程同名，老师在大屏上方选课程时就能直接选中。
    """
    course = str(course or "").strip()
    if not course:
        return None
    try:
        tasks = ACT.tasks(include_archived=True) or []
    except Exception:
        tasks = []
    for t in tasks:
        c = str(t.get("course") or "").strip()
        ti = str(t.get("title") or "").strip()
        if c == course or (not c and ti == course):
            if c != course:
                try:
                    ACT.update_task(t["task_id"], course=course)
                    t["course"] = course
                except Exception:
                    pass
            return t
    if not create:
        return None
    try:
        return ACT.create_task(course,
                               "H5 网页设计作品（系统自动创建；学生提交的图片与源码在这里）",
                               0, course)
    except Exception:
        return None


def design_ensure_course(course):
    """确保「资料」里有这门课程文件夹（老师直接填新名字也能用）。"""
    name = str(course or "").strip()
    if not name:
        return ""
    try:
        safe = LEG.CW.safe_course(name)
        full = LEG.CW.abs_of(safe)
        if full:
            os.makedirs(full, exist_ok=True)
            return safe
    except Exception:
        pass
    return name


def design_full_html(html, css, title):
    """把学生的 HTML/CSS 拼成一份能独立打开的网页（随作品一起归档）。

    学生只写 body 里的片段 + CSS，这里补上文档骨架，老师双击 .html
    就能看到和学生屏幕上一样的画面。
    """
    t = _html.escape(str(title or "我的网页"), quote=False)
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            "<meta charset=\"UTF-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n"
            "<title>%s</title>\n<style>\n%s\n</style>\n</head>\n<body>\n%s\n</body>\n</html>\n"
            % (t, str(css or ""), str(html or "")))


def _design_student_last(course, no, name):
    """学生最近一次设计提交（用于页面上显示「已提交」状态）。"""
    t = design_task(course)
    if not t:
        return None
    try:
        subs = ACT.submissions(t["task_id"]) or []
    except Exception:
        return None
    no = str(no or "").strip()
    name = str(name or "").strip()
    for s in reversed(subs):
        if not isinstance(s, dict):
            continue
        a = str(s.get("学号") or s.get("student_no") or "").strip()
        b = str(s.get("姓名") or s.get("name") or "").strip()
        if (no and a == no) or (not no and name and b == name):
            return s
    return None


# ---------------------------------------------------------------------------
# 像素画（2.0.47）
# ---------------------------------------------------------------------------
# 与 H5 网页设计同构，别合并成一套：两者提交物、编辑器、归档命名都不一样，
# 硬凑一个「通用作品」只会让两边都长满 if。共用的是更下层已有的东西——
#   · 鉴权：同一个学生会话 / 教师会话
#   · 提交：同一个 ACT.add_submission + _archive_after_submit
#   · 归档：<课程>/<班级>/<学号-姓名>/ 同一套目录
# 数据同样分两处：草稿进 PIXEL（pixel.json），提交进 ACT。
# ---------------------------------------------------------------------------
PIXEL_SIZES = (16, 24, 32, 48, 64)


def pixel_cfg():
    """取像素画配置（缺字段就地补默认值，老配置不用迁移）。"""
    d = CFG.get("pixel")
    if not isinstance(d, dict):
        d = {}
        CFG["pixel"] = d
    d.setdefault("enabled", False)
    d.setdefault("allow_add", True)
    d.setdefault("allow_submit", True)
    d.setdefault("course", "像素画")
    d.setdefault("window", "像素画作品")
    d.setdefault("intro", "")
    d.setdefault("size", 32)
    # 每人最多提交几次（2.0.53）：0 = 不限。默认 1 —— 一节课交两三次，
    # 大屏和归档里全是同一个人的重复版本，白白拖慢列表。
    d.setdefault("max_submit", 1)
    return d


def _max_submit_of(d, default=1):
    """每人最多提交几次，0 = 不限（网页设计 / 像素画共用，2.0.53）。

    脏值（负数、字符串、None）一律夹到 0~20，缺字段用 default。
    """
    d = d if isinstance(d, dict) else {}
    try:
        n = int(d.get("max_submit"))
    except Exception:
        n = default
    return max(0, min(n, 20))


def _student_subs_of(task, no, name):
    """某个收集任务下这名学生的全部提交（保持原顺序），用来算「已交几次」。

    认人规则与 `_pixel_student_last` / 大屏的按人去重一致：有学号认学号，
    没学号才退回姓名 —— 同名同姓的学生不会互相占额度。
    """
    if not task:
        return []
    try:
        subs = ACT.submissions(task["task_id"]) or []
    except Exception:
        return []
    no = str(no or "").strip()
    name = str(name or "").strip()
    out = []
    for s in subs:
        if not isinstance(s, dict):
            continue
        a = str(s.get("学号") or s.get("student_no") or "").strip()
        b = str(s.get("姓名") or s.get("name") or "").strip()
        if (no and a == no) or (not no and name and b == name):
            out.append(s)
    return out


def _pixel_student_subs(course, no, name):
    """像素画专用：先按课程找到收集任务，再数这个人的提交。"""
    return _student_subs_of(pixel_task(course), no, name)


def pixel_public_cfg():
    """给前端的安全副本：开关 / 权限 / 课程 / 窗口 / 任务说明 / 默认画布。"""
    d = pixel_cfg()
    try:
        size = int(d.get("size") or 32)
    except Exception:
        size = 32
    if size not in PIXEL_SIZES:
        size = 32
    return {"enabled": bool(d.get("enabled")),
            "allow_add": bool(d.get("allow_add")),
            "allow_submit": bool(d.get("allow_submit")),
            "course": str(d.get("course") or ""),
            "window": str(d.get("window") or ""),
            "intro": str(d.get("intro") or ""),
            "size": size,
            "max_submit": _max_submit_of(d),
            "sizes": list(PIXEL_SIZES)}


def pixel_task(course, create=False):
    """找（或建）这门课在大屏里的收集任务（口径同 design_task）。"""
    course = str(course or "").strip()
    if not course:
        return None
    try:
        tasks = ACT.tasks(include_archived=True) or []
    except Exception:
        tasks = []
    for t in tasks:
        c = str(t.get("course") or "").strip()
        ti = str(t.get("title") or "").strip()
        if c == course or (not c and ti == course):
            if c != course:
                try:
                    ACT.update_task(t["task_id"], course=course)
                    t["course"] = course
                except Exception:
                    pass
            return t
    if not create:
        return None
    try:
        return ACT.create_task(course,
                               "像素画作品（系统自动创建；学生提交的图片与工程文件在这里）",
                               0, course)
    except Exception:
        return None


def pixel_ensure_course(course):
    """确保「资料」里有这门课程文件夹（老师直接填新名字也能用）。"""
    name = str(course or "").strip()
    if not name:
        return ""
    try:
        safe = LEG.CW.safe_course(name)
        full = LEG.CW.abs_of(safe)
        if full:
            os.makedirs(full, exist_ok=True)
            return safe
    except Exception:
        pass
    return name


def _pixel_student_last(course, no, name):
    """学生最近一次像素画提交（用于页面上显示「已提交」状态）。"""
    t = pixel_task(course)
    if not t:
        return None
    try:
        subs = ACT.submissions(t["task_id"]) or []
    except Exception:
        return None
    no = str(no or "").strip()
    name = str(name or "").strip()
    for s in reversed(subs):
        if not isinstance(s, dict):
            continue
        a = str(s.get("学号") or s.get("student_no") or "").strip()
        b = str(s.get("姓名") or s.get("name") or "").strip()
        if (no and a == no) or (not no and name and b == name):
            return s
    return None


if not isinstance(CFG.get("typing"), dict):
    CFG["typing"] = {}
CFG["typing"].setdefault("enabled", False)
CFG["typing"].setdefault("grades", [])
# H5 网页设计（2.0.39）：教师端「其他 · 网页设计」一键启停的小服务。
#   enabled      总开关：关掉后学生端入口实时消失、提交接口拒收
#   allow_add    允许学生在学生端「添加」这个功能页（导航里出现「网页」页签）
#   allow_submit 允许学生提交作品（关掉只能自己练、不能交）
#   course       归档课程：作品进「资料」的 <课程>/<班级>/<学号-姓名>/，与学习单归档同一套目录
#   window       大屏窗口名：提交按这个窗口进「上课」页的实时看板
#   intro        老师写给学生的任务说明（显示在设计页顶部）
if not isinstance(CFG.get("design"), dict):
    CFG["design"] = {}
CFG["design"].setdefault("enabled", False)
CFG["design"].setdefault("allow_add", True)
CFG["design"].setdefault("allow_submit", True)
CFG["design"].setdefault("course", "网页设计")
CFG["design"].setdefault("window", "网页设计作品")
CFG["design"].setdefault("intro", "")
# 像素画（2.0.47）：与网页设计同一套开关口径，字段名一致，老师两端不用重新学。
#   enabled / allow_add / allow_submit / course / window / intro
#   size 默认画布边长（16/24/32/48/64），学生可在页面里自己换
if not isinstance(CFG.get("pixel"), dict):
    CFG["pixel"] = {}
CFG["pixel"].setdefault("enabled", False)
CFG["pixel"].setdefault("allow_add", True)
CFG["pixel"].setdefault("allow_submit", True)
CFG["pixel"].setdefault("course", "像素画")
CFG["pixel"].setdefault("window", "像素画作品")
CFG["pixel"].setdefault("intro", "")
CFG["pixel"].setdefault("size", 32)
# 课堂签到（2026-11-05）：教师端可整体关闭（默认开启）；
# 默认按课表当节课时间自动签到，填了 manual_time（HH:MM）就改按这个时间执行。
if not isinstance(CFG.get("checkin"), dict):
    CFG["checkin"] = {}
CFG["checkin"].setdefault("enabled", True)
CFG["checkin"].setdefault("manual_time", "")
# 课堂活动配置（提交有效时长等；后端只有自研一种，不再有开关）
if not isinstance(CFG.get("activity"), dict):
    CFG["activity"] = {}
# 在线更新配置（2026-11-04）：更新源地址 / 自动检查。
# 仓库已公开，不再使用访问令牌（老配置里遗留的 token 字段会被忽略）。
if not isinstance(CFG.get("update"), dict):
    CFG["update"] = {}
CFG["update"].setdefault("url", "")            # 空 = 默认更新源（Gitee raw version.json）
CFG["update"].setdefault("auto", True)         # 后台定时检查
CFG["update"].setdefault("interval_hours", 24) # 检查间隔（小时）
# 宠物图鉴 id（必须与 typing-data.js 的 PETS 一一对应，顺序即图鉴顺序）。
# 2026-11-17：原来只列了 8 只，后加的仓鼠 / 狐狸 / 青蛙 / 绵羊孵不出来 ——
# 学生攒到蛋只会拿到「图鉴已集齐，蛋化作 50 积分」，看着就是「位置满了」。
# 这次补齐到 22 只（新增 10 只），以后加宠物**必须同步这里**。
PET_IDS = ["cat", "dog", "rabbit", "penguin", "panda", "dino", "unicorn", "dragon",
           "hamster", "fox", "frog", "sheep",
           "koala", "pig", "tiger", "seal", "deer", "bat", "whale", "owl",
           "duck", "bear"]
COND = threading.Condition()
STARTED = time.time()
VERSION = "2.1.19"
ONLINE_WINDOW = 300      # 5 分钟内活跃算在线
# 可内联直出的附件类型（其余一律按下载处理，防止同源执行脚本）
_INLINE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "pdf",
                "mp4", "webm", "ogg", "mp3", "wav", "m4a", "txt"}
# 在线更新器（2026-11-04）：状态/下载/备份放数据目录；ROOT 即 app 代码目录
UPD = Updater(DATA_DIR, VERSION, ROOT)

# 存量配置清理（2026-11-20）：老版本前端把「默认更新源」预填进自定义源输入框，
# 老师一保存就落了盘，于是配置里的 url 等于默认源 —— 程序把它当「老师指定
# 单源」，多源回退失效，Gitee 一 403 就再也检查不了更新。
# 这里把这类「等价默认源」的地址清成空（= 走默认多源），只改配置不动代码。
try:
    _upd_url = str((CFG.get("update") or {}).get("url") or "").strip()
    if _upd_url:
        from updater import _is_default_source as _is_dflt_upd
        if _is_dflt_upd(_upd_url):
            CFG["update"]["url"] = ""
            CFG.save()
except Exception:
    pass


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
# 2.0.71：由 200 上调到 600，支撑 10 班/近千人同时在线；
# 仍可用环境变量 WB_SSE_MAX 或 config.json 的 sse.max 覆盖，便于按服务器内存调整。
try:
    _SSE_MAX = int(os.environ.get("WB_SSE_MAX") or
                   (CFG.get("sse") or {}).get("max") or 600)
except Exception:
    _SSE_MAX = 600
_SSE_MAX = max(50, min(5000, _SSE_MAX))
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
    """对外访问根地址，形如 https://192.168.x.x:20020（不带结尾斜杠）。

    2.1.15：协议跟随服务实际运行模式。离线包以 HTTPS（内置自签证书）启动时，
    浏览器直连没有 X-Forwarded-Proto，原来一律拼 http，学生入口点开是错的。
    """
    # 服务自身跑 HTTPS（WB_HTTPS=1，离线包默认开）时，默认协议就该是 https。
    scheme = "https" if (os.environ.get("WB_HTTPS", "").strip().lower()
                         in ("1", "true", "yes")) else "http"
    env = (os.environ.get("WB_PUBLIC_URL") or "").strip()
    if env:
        low = env.lower()
        if not (low.startswith("http://") or low.startswith("https://")):
            env = scheme + "://" + env
        return env.rstrip("/")

    if headers is not None:
        fwd = _safe_host((headers.get("X-Forwarded-Host") or "").split(",")[0])
        if fwd:
            # 反代没带 X-Forwarded-Proto 时，回落到服务自身协议（HTTPS 模式下为 https）
            proto = (headers.get("X-Forwarded-Proto") or scheme).split(",")[0].strip().lower()
            return "%s://%s" % ("https" if proto == "https" else "http", fwd)
        host = _safe_host(headers.get("Host"))
        if host and _host_only(host) not in _LOOPBACK_HOSTS:
            return "%s://%s" % (scheme, host)

    # 兜底：主机网卡 IP + 进程端口。教师从 127.0.0.1:<port> 打开时端口在 Host 里，
    # 跟着用它，避免学生入口给出错误端口（预览端口被写成配置里的 20020）。
    port = _port()
    if headers is not None:
        host = _safe_host(headers.get("Host"))
        if host and _host_only(host) in _LOOPBACK_HOSTS and host.count(":") == 1:
            try:
                hp = int(host.rsplit(":", 1)[1])
                if 1 <= hp <= 65535:
                    port = hp
            except Exception:
                pass
    return "%s://%s:%d" % (scheme, best_ip(), port)


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
    # 账号级配置：每个老师写自己的工作区，不再覆盖别人的设置。
    CFG.save()


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


def _split_acc_id(v):
    """拆「账号id|真实id」复合键（2026-11-05，多账号班级聚合用）。

    登录页的班级/学生 id 现在带账号前缀（不同教师账号可能有同 id 的班），
    形如 `t2|1`。老页面/老缓存里是不带前缀的 `1`，这里一并兼容：
    返回 (账号id, 真实id)，不带前缀时账号部分为空字符串。
    """
    s = str(v or "").strip()
    if "|" in s:
        a, _, r = s.partition("|")
        return a.strip(), r.strip()
    return "", s


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


def security_of(account=None):
    """教师密保：{question, answer_hash}。

    2.0.13 多教师账号：可传账号 id 或用户名（忘记密码时按用户名定位），
    不传就用当前请求绑定的教师账号。账号权威在 accounts.json，
    CFG["teacher"]["security"] 只保留作旧备份的兼容镜像。
    """
    t = None
    if account:
        t = ACCOUNTS.teacher(str(account)) or ACCOUNTS.teacher_by_username(str(account))
    if t is None:
        t = ACCOUNTS.current_teacher()
    sec = (t or {}).get("security")
    if not isinstance(sec, dict):
        # 2026-11-25 跨账号排查：旧兼容镜像只能给 default 账号兜底。
        # 否则新建一个教师账号时若没设密保，会继承 default 的密保问题与答案哈希，
        # 别人拿 default 的答案就能走密保改 t2 的密码。非 default 一律空密保。
        sec = {}
        if not t or str(t.get("id")) == str(ACC.DEFAULT_TEACHER_ID):
            sec = (CFG.get("teacher") or {}).get("security") or {}
    if not isinstance(sec, dict):
        sec = {}
    return {"question": str(sec.get("question") or ""),
            "answer_hash": str(sec.get("answer_hash") or "")}


def has_qa(account=None):
    s = security_of(account)
    return bool(s["question"] and s["answer_hash"])


def new_ticket(account=None):
    import secrets as _s
    tok = _s.token_urlsafe(24)
    with QA_LOCK:
        now = time.time()
        for k in [k for k, v in QA_TICKETS.items() if v["exp"] < now]:
            QA_TICKETS.pop(k, None)
        # 票据绑定账号：多教师下密保改密必须改到「答对问题那个人」的账号上
        QA_TICKETS[tok] = {"exp": now + 600,
                           "account": str(account or ACC.DEFAULT_TEACHER_ID)}
    return tok


def take_ticket(tok):
    with QA_LOCK:
        v = QA_TICKETS.pop(str(tok or ""), None)
    if not v or v["exp"] < time.time():
        return None
    return v.get("account") or ACC.DEFAULT_TEACHER_ID


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
    """预览缓存目录（按账号隔离），返回 (dir, err)。

    静态加密：缓存是文档正文的明文 HTML，不能放在全局共享目录 —— 那等于
    把课件内容绕过 FEK 摊在磁盘上，还可能跨账号串味。"""
    d = os.path.join(WS.paths_for(ACC.current_account())["dir"], "preview_cache")
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
                raw = f.read()
            return ENC.dec_text(raw, domain="preview") if raw.startswith("WB64.") else raw
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
                f.write(ENC.enc_text(html, domain="preview") if ENC.get_fek() else html)
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

    def _read_json(self, limit=None):
        """按路径决定请求体上限：导入接口放宽，其余沿用通用限制。

        通用上限是 32MB，而全量备份里的附件 base64 后很容易超过它——
        不放开的话「一键恢复」会在带附件时莫名 413，用户只看到「失败」。
        limit 由调用方传入（如课件上传 440MB）；不传就交给基类按默认规则处理。
        """
        path = urllib.parse.urlparse(self.path).path
        if path not in ("/api/v2/admin/import", "/api/v2/admin/import-preauth"):
            if limit is None:
                return LEG.Handler._read_json(self)
            return LEG.Handler._read_json(self, limit)
        try:
            raw = self._read_body(self.MAX_IMPORT_BODY,
                                  drain_cap=LEG.MAX_CW_UPLOAD_BODY)
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

    def _teacher_name(self, s=None):
        """当前会话教师的显示名（账号级，昵称优先，其次账号名）。
        绝不能读全局 CFG["teacher"]["username"] —— 那只是 default 账号的
        旧镜像，多账号下会把 t2 老师显示成 Shyxiaoniu。
        """
        s = s or self._session() or {}
        tid = s.get("account") or ACC.DEFAULT_TEACHER_ID
        name = ACCOUNTS.nickname_of(tid)
        if not name:
            name = str((ACCOUNTS.teacher(tid) or {}).get("username") or "").strip()
        return name or "老师"

    def _bind_account(self):
        """每个请求开头绑定工作区账号：教师/学生会话带 account，
        匿名请求（登录页、图标、闸门）一律用 default，保证老数据可见。
        LEG 的静态/提交/课件接口走 super().do_*，同线程上下文已绑定。"""
        s = self._session()
        ACC.set_account((s or {}).get("account") or ACC.DEFAULT_TEACHER_ID)
        # 静态加密：按会话角色注入 FEK。
        # teacher=会话FEK(并常驻账号缓存)；student=按账号取缓存(能看下发课件)；
        # admin/匿名=None(看不到任何明文)。每请求重设，线程复用不串味。
        try:
            ENC.bind_request((s or {}).get("account"), (s or {}).get("fek"),
                             (s or {}).get("role"))
        except Exception:
            ENC.set_fek(None)
        return s

    def _is_teacher(self):
        s = self._session()
        return bool(s and s["role"] == "teacher")

    def _is_admin(self):
        s = self._session()
        return bool(s and s["role"] == "admin")

    def _data_unlocked(self):
        """资料包导入/导出前的二次验证：验证通过后在本次会话里开一个短窗口。
        只放 180 秒，避免老师离开座位后别人拿同一会话把课件整包拖走。"""
        s = self._session() or {}
        try:
            return float(s.get("dw_until") or 0) > time.time()
        except Exception:
            return False

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
        # 老页面兼容（2026-11-05 深夜）：2.0.34 及以前的页面提交时会带 __archive
        # （整页 HTML 快照）。现在不再存整页存档，直接丢弃 —— 留下来会变成一列
        # 几百 KB 的文本塞进 data，既撑库又在大屏表格里冒出一列乱码。身份用票里的认。
        fields.pop("__archive", None)
        # 任务类型（2026-11-05）：纯数据任务丢掉误带的文件，与带票提交口保持一致
        _allow, _kind = self._accept_files(task_key, fields)
        _ignored = 0
        if _allow is False and files:
            _ignored = len(files)
            files = []
        try:
            res = ACT.add_submission(task_key, fields, files,
                                     ip=self._client_ip_safe(), kind=_kind)
        except ValueError as e:
            return self._json_cors({"ok": False, "message": str(e)}, 413)
        except LookupError:
            return self._json_cors({"ok": False, "message": "任务不存在"}, 404)
        # 提交成功立刻就归档（匿名口没有票，身份按提交里带的认）
        _who = {"学号": str(fields.get("学号") or fields.get("sid") or ""),
                "姓名": str(fields.get("姓名") or fields.get("name") or ""),
                "班级": str(fields.get("班级") or fields.get("cls") or "")}
        # 2026-11-17：模拟提交（__test=1）不归档 —— 「测试同学」不该在资料里建文件夹
        _is_test = str(fields.get("__test") or "").strip().lower() in ("1", "true", "yes")
        _arc = None if _is_test else _archive_after_submit(task_key, _who, res)
        # 2.0.38：学习单按钮提交也走同一条实时通道，带上所属窗口 →
        # 大屏只刷新那一个窗口，不再等 5 秒轮询整块重绘。
        _win = " ".join(str(fields.get("按钮") or "").split())
        if _is_test:
            _win = "测试提交"
        if _win:
            quiz_bump({"kind": "submit", "window": _win, "from": "sheet",
                       "task": task_key, "student_no": _who.get("学号")})
            notify()
        _out = {"message": "提交成功", "id": res["id"], "kind": _kind or "legacy",
                "attachment": res.get("attachment")}
        if _arc and _arc.get("ok"):
            _out["archived"] = len(_arc.get("saved") or [])
        if _ignored:
            _out["ignored_files"] = _ignored
        return self._json_cors_success(_out)

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
        # 静态加密：WBF1 密文附件按会话 FEK 解密（有 FEK 才可能是密文）
        try:
            if ENC.get_fek() and len(data) >= 4 and data[:4] == b"WBF1":
                data = ENC.dec_file(data)
        except Exception as e:
            return self._json({"ok": False, "msg": "文件解密失败，请重新登录"}, 500)
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
        self._bind_account()
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
            # 登录页「数据导入」要用的两份公开脚本（2026-11-05）：
            # · cipher.js  = AES/SHA256 纯 JS 实现，与教师端同一份文件（唯一实现），
            #   登录页解密 .cwbackup 与教师端加密必须逐字节兼容；
            # · login-import.js = 导入对话框 UI。
            # 都是纯代码不含任何数据 / 口令，公开可取，no-store 保证改版即生效。
            if path in ("/core/cipher.js", "/login-import.js"):
                return self._login_asset(path)
            if path == "/teacher/forgot":
                return self._html(P.forgot_page())
            # 管理员控制台：只管教师账号，不承载任何业务设置
            if path in ("/admin", "/admin/"):
                if not self._is_admin():
                    return self._redirect("/teacher/login")
                return self._html(P.admin_page(VERSION))
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
                return self._courseware_gate(path, qs)
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
            # H5 网页设计（2.0.39）：静态页公开（页面无敏感信息），
            # 权限 / 草稿 / 提交全部走 /api/v2/design/* 鉴权接口。
            if path == "/design":
                return self._redirect("/design/")
            if path.startswith("/design/"):
                return self._design_static(path[len("/design/"):])
            # 像素画（2.0.47）：学生画板 /pixel/、教师管理页 /pixel/teacher.html
            if path == "/pixel":
                return self._redirect("/pixel/")
            if path.startswith("/pixel/"):
                return self._pixel_static(path[len("/pixel/"):])
            # 截图工具（2.1.7）：纯前端静态页，教师端工具 + 学生端菜单共用。
            # 页面无敏感信息，公开可开；无需后端接口。
            if path == "/shot":
                return self._redirect("/shot/")
            if path.startswith("/shot/"):
                return self._asset_static("shot", path[len("/shot/"):])
            # 课堂活动接口：/api/act/*（自研）
            if path.startswith("/api/act/") or path == "/api/act":
                return self._act_api(path, u.query)
            # 课堂活动附件：URL 就是 /static/uploads/<活动>/<文件>，
            # 大屏、课件库归档、自定义页面都按这个前缀取，所以必须可访问。
            if path.startswith("/static/uploads/"):
                return self._act_file(path)
            if path.startswith("/teacher/"):
                return self._teacher_static(path[len("/teacher/"):])
            # 学习单页面（2026-11-17 重做闸门）：
            #   · 教师打开 = 预览，带跨账号兜底与默认模板兜底 ——「查看」绝不
            #     再落到「老师还没有配置自定义提交页」的白页（多教师账号、
            #     127.0.0.1 打开而预览链接走局域网 IP 时，旧逻辑会读错工作区）；
            #   · 学生打开 = 原闸门（票兜底 / 关闭提示），不放行未配置任务。
            if path == "/submit/custom.html" and self._is_teacher():
                _sh = self._sheet_html_for((qs.get("task") or [""])[0])
                if _sh is not None:
                    return self._html(_sh)
                return super().do_GET()
            # 学习单开关：活动被「关闭」后，非教师直接打开学生页会被拦下；
            # 学生端仍能看到条目（projection 会下发 closed 行），只是点开时打不开。
            if path == "/submit/custom.html" and not self._is_teacher():
                q2 = urllib.parse.parse_qs(u.query)
                _task = (q2.get("task") or [""])[0]
                _cname = (q2.get("class") or [""])[0]
                # 身份兜底（2026-11-05 牛哥反馈「重新登录后打开学习单，提示没从
                # 教师端打开、学生信息也读不出来」）：学生明明已经登录，可链接里
                # 没有票 / 票已过期 / 是别人的票（收藏夹、老师发的裸链接、重新登录
                # 后回到旧标签、从 /cw/ 直接点进来）——页面就只能停在「请输学号」，
                # 模板等不到注入的提交按钮，还会弹「本页未从教师端打开」的预览提示。
                # 这里按会话里的真实身份重新签一张票，302 到带参数的同一页。
                if _task and "/" not in _task and ".." not in _task:
                    _s0 = self._session()
                    _r0 = student_row(_s0.get("student_id")) if (
                        _s0 and _s0.get("role") == "student") else None
                    _tok0 = (q2.get("wbT") or [""])[0]
                    _gt0 = get_gticket(_tok0) if _tok0 else None
                    # 票是自己的、且就是这份学习单 → 不再折腾（避免重定向循环）
                    _own = bool(_gt0
                                and str(_gt0.get("task") or "") == _task
                                and str(_gt0.get("no") or "")
                                == str(_r0.get("student_no") or "")) if _r0 else False
                    if _r0 and not _own:
                        _cls0 = self._class_name(_r0.get("class_id"))
                        _qs0 = {k: (v or [""])[0] for k, v in q2.items()}
                        _qs0.update({
                            "wbT": new_gticket({
                                "sid": str(_r0.get("id") or ""),
                                "no": str(_r0.get("student_no") or ""),
                                "name": str(_r0.get("name") or ""),
                                "cls": _cls0, "gid": "", "idx": 0, "task": _task,
                            }),
                            "wbSid": str(_r0.get("student_no") or ""),
                            "wbName": str(_r0.get("name") or ""),
                            "wbCls": _cls0,
                            "wbTask": _task,
                            "wbBase": public_base(self.headers),
                        })
                        return self._redirect(public_base(self.headers)
                                              + "/submit/custom.html?"
                                              + urllib.parse.urlencode(_qs0))
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

    # ------------------------------------------------------------------
    # 学习单页面：教师的「查看 / 预览」兜底（2026-11-17）
    # ------------------------------------------------------------------
    _SHEET_MISS_MARK = "老师还没有配置自定义提交页"

    def _sheet_default_html(self, task):
        """教师预览兜底页：这个任务还没部署过学习单时给一份默认模板，
        让「用默认模板 → 生成页面 → 查看」这条链路永远看得到页面，
        而不是一句「老师还没有配置」。提交按钮直接走活动接口 /api/act/<task>。"""
        course = ""
        try:
            course = str((ACT.get_task(task) or {}).get("course") or "").strip()
        except Exception:
            course = ""
        course = course or "课堂"
        import html as _htmlmod
        title = _htmlmod.escape(course)
        return (
            "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s · 课堂学习单（预览）</title>"
            "<style>body{font-family:-apple-system,\"PingFang SC\",\"Microsoft YaHei\",sans-serif;"
            "max-width:640px;margin:24px auto;padding:0 16px;color:#1A2E28;line-height:1.75}"
            ".act-q{border:1px solid #DDE5E1;border-radius:14px;padding:16px 18px;margin:16px 0;background:#FBFDFC}"
            ".hint{background:#E1F5EE;border:1px dashed #0F6E56;border-radius:12px;padding:11px 14px;"
            "margin:14px 0;font-size:13.5px}"
            "label{display:block;font-weight:600;margin:12px 0 5px}"
            "input,textarea{width:100%%;padding:10px;border:1px solid #cfe3dc;border-radius:10px;"
            "box-sizing:border-box;font-size:14px;font-family:inherit}"
            ".tip{font-size:12px;color:#5F6E68;margin-top:4px}"
            ".pv-bar{background:#FFF6D9;border:1px solid #E7C76B;border-radius:12px;"
            "padding:10px 14px;font-size:13px;margin:14px 0}</style></head><body>"
            "<h1>%s · 课堂学习单</h1>"
            "<div class=\"pv-bar\">👀 这是教师预览（默认模板兜底）。学生端看到这份页面的前提是："
            "你在大屏点了「下发」，或在这里点过「生成页面」。下发后本页会自动接上学生身份与提交逻辑。</div>"
            "<p>完成后请在下方<b>选择文件提交</b>；系统会自动附上班级、学号与姓名。</p>"
            "<div class=\"hint\">💡 支持 作业文档、图片、演示文稿等常见格式；提交后老师会在大屏上看到记录。</div>"
            "<div class=\"act-q\" data-btn=\"提交作业\" data-allow-empty=\"1\">"
            "<label>课堂作业文件</label>"
            "<input type=\"file\" name=\"作业文件\" multiple>"
            "<div class=\"tip\">可按住 Ctrl 一次选择多个文件</div>"
            "<label>备注（选填）</label>"
            "<input name=\"备注\" placeholder=\"有问题或想对老师说的话，可以写在这里\">"
            "</div>"
            "<div style=\"margin-top:16px\"><button class=\"act-submit-btn\" id=\"pvSubmit\" "
            "style=\"background:#0F6E56;color:#fff;border:0;padding:11px 22px;border-radius:12px;"
            "font-size:15px;cursor:pointer\">提交：提交作业</button> "
            "<span id=\"pvSt\" class=\"tip\" style=\"margin-left:10px\"></span></div>"
            "<script>document.getElementById('pvSubmit').onclick=function(){"
            "var fd=new FormData();fd.append('按钮','提交作业');fd.append('测试内容',"
            "'教师预览测试 '+new Date().toLocaleTimeString());"
            "var st=document.getElementById('pvSt');st.textContent='提交中…';"
            "fetch('/api/act/%s',{method:'POST',body:fd,mode:'cors'})"
            ".then(function(r){return r.json().catch(function(){return{}})})"
            ".then(function(j){st.textContent=(j&&j.ok)?'✅ 测试提交成功，请到大屏查看':'❌ '+(j&&j.msg||'提交失败');})"
            ".catch(function(e){st.textContent='网络错误：'+e.message;});};</script>"
            "</body></html>" % (title, title, urllib.parse.quote(str(task or ""))))

    def _sheet_html_for(self, task):
        """教师打开 /submit/custom.html 时的页面来源（按优先级）：
        ① 当前教师工作区里部署过的 custom_templates[task]；
        ② 其它教师工作区里同 task 的部署（多账号 / 跨域打开时读错工作区的兜底）；
        ③ 默认模板兜底页（绝不给一句「老师还没有配置」就完事）。
        找不到任务参数时返回 None → 交回旧处理器。"""
        task = str(task or "").strip()
        if not task or "/" in task or ".." in task:
            return None
        try:
            cur = self._custom_html(task)
        except Exception:
            cur = ""
        if cur and self._SHEET_MISS_MARK not in cur:
            return cur
        # ② 跨账号兜底：直接读各工作区的 submissions/config.json，不动 ACC 状态
        me = ACC.current_account()
        for tid in ACCOUNTS.teacher_ids():
            if str(tid) == str(me):
                continue
            try:
                cfgp = os.path.join(WS.paths_for(tid)["submissions"], "config.json")
                with open(cfgp, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                continue
            tpls = cfg.get("custom_templates") if isinstance(cfg, dict) else None
            html = (tpls or {}).get(task) if isinstance(tpls, dict) else ""
            if html:
                cc = cfg.get("custom_course") if isinstance(cfg, dict) else None
                course = str((cc or {}).get(task) or "") if isinstance(cc, dict) else ""
                course = course or "课堂"
                import html as _htmlmod
                safe = _htmlmod.escape(course)
                return (html.replace("{{COURSE}}", safe)
                            .replace("{{course}}", safe))
        # ③ 默认模板兜底（教师预览专用）
        return self._sheet_default_html(task)

    def _entry(self):
        ua = (self.headers.get("User-Agent") or "").lower()
        mobile = any(k in ua for k in ("iphone", "android", "mobile", "ipad", "harmony"))
        return self._redirect("/s/" if mobile else "/teacher/")

    def _teacher_page(self):
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
        # 2.0.13：登录页默认就是管理员登录界面，教师账号由管理员统一注册，
        # 不再有「首次设置 / 注册」这一步（login_page_setup 已停用）。
        return self._html(P.login_page())

    def _login_asset(self, path):
        """登录页公开静态脚本（无登录态）：白名单只放这两个文件。"""
        rel = {"/core/cipher.js": os.path.join("core", "cipher.js"),
               "/login-import.js": "login-import.js"}[path]
        full = os.path.normpath(os.path.join(ROOT, rel))
        if not os.path.isfile(full):
            return self._json({"ok": False, "msg": "not found"}, 404)
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

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
        """根路径图标：快捷方式 / 添加到主屏时系统来取的那两个文件。

        2026-11-04 牛哥反馈：学生端链接拖到桌面，快捷方式图标不对。
        原因：Windows 把链接拖到桌面建 .url 快捷方式时，只会来取
        「根路径」的 /favicon.ico（不带 Referer、也不看页面 <head> 里的
        <link rel="icon">）——原来这里永远给教师端「牛师」图标。

        现在的口径：
        · 无 Referer（拖链接建快捷方式就是这种）→ 学生端「学」图标
          （学生机拖桌面的场景远多于教师机，默认给学生）；
        · Referer 来自学生侧页面（/s/、/student/ 等）→ 学生图标；
        · Referer 来自教师端（/、/teacher/）→ 教师图标。
        浏览器页签的图标走各页面 <head> 的 <link rel="icon">，
        与这里无关，所以教师端页签仍是牛师、学生端页签仍是学。
        """
        name = "favicon.ico" if path == "/favicon.ico" else "apple-touch-icon-180.png"
        raw_ref = self.headers.get("Referer") or ""
        # 无 Referer（拖链接建快捷方式就是这种）→ 学生图标；
        # 有 Referer 才按页面来源区分教师 / 学生。
        ref = urllib.parse.urlparse(raw_ref).path if raw_ref else ""
        teacher_ref = bool(ref) and (ref == "/" or ref.startswith("/teacher")
                                     or ref.startswith("/index"))
        full = os.path.join(ROOT, "icons" if teacher_ref else "student", name)
        if not os.path.isfile(full):
            full = os.path.join(ROOT, "icons", name)
        if not os.path.isfile(full):
            return self._html("<h1>页面不存在</h1>", 404)
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type",
                         "image/x-icon" if name.endswith(".ico") else "image/png")
        self.send_header("Content-Length", str(len(body)))
        # 2026-11-05：图标不再让浏览器缓存 1 小时 —— 换图标 / 重建桌面快捷方式时
        # 必须回源取到最新的那份，否则建出来的 .url 会一直挂着旧图标。
        # （Windows Explorer 自己的图标缓存是客户端的事，改这里管不到，见 README。）
        self.send_header("Cache-Control", "no-cache, max-age=0, must-revalidate")
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

    def _design_static(self, rel):
        """H5 网页设计的静态资源：/design/ → 学生设计器，/design/teacher.html → 教师管理页。"""
        return self._asset_static("design", rel)

    def _pixel_static(self, rel):
        """像素画的静态资源：/pixel/ → 学生画板，/pixel/teacher.html → 教师管理页。"""
        return self._asset_static("pixel", rel)

    def _asset_static(self, sub, rel):
        """前端子应用的静态托管（design / pixel 共用，2.0.47 从 _design_static 抽出）。

        与打字页同一套做法：静态文件本身不含任何学生数据（老师写的任务说明
        与开关都在鉴权接口里），未登录也能打开，页面自己进「演示模式」。
        """
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel:
            rel = "student.html"
        if ".." in rel.split("/"):
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        base = os.path.normpath(os.path.join(ROOT, sub))
        full = os.path.normpath(os.path.join(base, rel))
        try:
            if os.path.commonpath([base, full]) != base:
                return self._json({"ok": False, "msg": "forbidden"}, 403)
        except ValueError:
            return self._json({"ok": False, "msg": "forbidden"}, 403)
        if not os.path.isfile(full):
            return self._html("<h1>页面不存在</h1>", 404)
        if not full.lower().endswith((".html", ".js", ".css", ".png", ".svg",
                                      ".ico", ".json", ".webmanifest")):
            return self._html("<h1>页面不存在</h1>", 404)
        import mimetypes
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",
                                                  "application/json"):
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
        if not s:
            return None, self._redirect("/s/")
        if s.get("role") == "teacher":
            # 教师端预览学生作品（2.0.41）：作品 HTML 里的图片是 /cw/<文件id>，
            # 页面在教师浏览器里加载时会带教师会话 —— 教师本来就能读整个课件库，
            # 这里直接放行，否则预览页里的图片全变成破图。
            try:
                full = LEG.CW.abs_of(fid, must_exist=True)
            except Exception:
                full = None
            if not full or not os.path.isfile(full):
                return None, self._html(_tip_page("课件文件不存在",
                                            "老师可能已经移动或删除了这个文件。"), 404)
            return full, None
        if s["role"] != "student":
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

    def _task_of_course(self, course):
        """课程名 → 课堂活动号（标题或 course 字段对上就算）。"""
        name = str(course or "").strip()
        if not name:
            return ""
        for t in ACT.tasks():
            if (str(t.get("title") or "").strip() == name
                    or str(t.get("course") or "").strip() == name):
                return str(t.get("task_id") or "")
        return ""

    def _sheet_task_of_file(self, fid, class_id="", full=""):
        """这个课件文件是不是某门课的「学习单」？→ (课程名, 活动号)。

        2026-11-05：学生直接打开 /cw/<学习单.html>（收藏夹、老师发的文件链接、
        旧标签）时，以前给的是**原始网页**：没有平台注入的登录/提交按钮，
        模板等 900ms 等不到 .act-submit-btn 就弹「本页未从教师端打开」的
        预览提示，学生也读不到自己的班级姓名。这里认出它是学习单，
        统一转去带身份的注入页。

        两条判据（与资料入口一致）：
          ① 资料里给这门课「设为学习单」的就是它（正式标记）；
          ② 没设过标记：它是网页，而且已经推给这个班、本课程又有部署好的
             学习单页 —— 老师只在大屏下发、没点「设为学习单」是常态。
        """
        fid = str(fid or "").strip("/")
        if not fid:
            return "", ""
        try:
            courses, _err = LEG.CW.list_courses()
        except Exception:
            courses = []
        for c in (courses or []):
            name = str((c or {}).get("name") or "").strip()
            if not name:
                continue
            try:
                if str(LEG.CW.sheet_of(name) or "").strip("/") != fid:
                    continue
            except Exception:
                continue
            task = self._task_of_course(name)
            if task and self._custom_page_ready(task):
                return name, task
        # ② 没设标记：按「资料里推给本班的这条网页」认
        mid = self._material_of_file(fid, class_id)
        if mid:
            row = None
            for m in STORE.table_rows("materials"):
                if str(m.get("id") or "") == str(mid):
                    row = m
                    break
            if row:
                course = str(row.get("lesson") or row.get("subject") or "").strip()
                task = self._task_of_course(course)
                # 还得对得上「就是这一份网页」（标题/正文指纹），或者这门课
                # 本来就只有这一份网页资料 —— 否则同课程别的 html 会被误当学习单。
                if (task and self._custom_page_ready(task)
                        and (self._page_from_file(task, full)
                             or self._only_html_material(fid, class_id, course))):
                    return course, task
        return "", ""

    def _material_of_file(self, fid, class_id):
        """学生在「资料」里对应这份文件的条目 id（没有就返回空串）。"""
        want = str(fid or "").strip("/")
        for m in STORE.table_rows("materials"):
            if m.get("deleted"):
                continue
            if str(m.get("class_id") or "") != str(class_id or ""):
                continue
            u = str(m.get("url") or "")
            if not u.startswith("/cw/"):
                continue
            try:
                rel = urllib.parse.unquote(u[len("/cw/"):]).strip("/")
            except Exception:
                rel = u[len("/cw/"):].strip("/")
            if rel == want:
                return str(m.get("id") or "")
        return ""

    def _courseware_gate(self, path, qs=None):
        # do_GET 入口已对整个 path 做过一次 unquote，课件 id 里的「课程/文件名」
        # 斜杠已被还原 —— 不能再 split/unquote，否则 id 会被从斜杠处切断。
        fid = path[len("/cw/"):].strip("/") if path.startswith("/cw/") else ""
        full, err = self._courseware_resolve(fid)
        if err:
            return err
        # 2026-11-25：不支持在线预览的格式（zip/rar/7z/exe…）学生端显示「下载」，
        # 走 ?download=1 强制 attachment，否则浏览器多数内联打开、点了像没反应。
        # 鉴权已在上面 _courseware_resolve 完成，这里只切换响应头。
        if qs and str((qs.get("download") or [""])[0]) in ("1", "true", "yes"):
            return self._serve_abs(full, download=True, name=os.path.basename(full))
        # 学生打开网页学习单 → 转到带票的注入页（见 _sheet_task_of_file）。
        # 老师预览不受影响（role=teacher）；图片/PDF 等非网页也不动。
        s = self._session()
        if (s and s.get("role") == "student"
                and _ext_of(full) in ("html", "htm")):
            r = student_row(s.get("student_id"))
            course, task = self._sheet_task_of_file(
                fid, (r or {}).get("class_id") or "", full)
            if task and r:
                mid = self._material_of_file(fid, r.get("class_id"))
                if mid:
                    return self._redirect(
                        "/g/mat/" + urllib.parse.quote(mid, safe=""))
                return self._redirect(self._sheet_entry_url(r, task, course))
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
        # 资料一键打开：/g/mat/<资料id>（2026-11-04）
        # 老师下发的网页资料，学生点一下就能打开，身份自动挂在地址上。
        if len(parts) >= 2 and parts[1] == "mat":
            return self._material_gate(r, parts[2] if len(parts) > 2 else "")
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

    def _signed_today(self, r):
        """今天是否有有效签到（出勤/迟到/请假都算；缺勤不算）。

        学习单入口（/g/act）与资料入口打开学习单（/g/mat）共用这一条闸门，
        两个入口的判断标准必须完全一致，否则学生会发现「从资料点进去
        不用签到、从今日点进去要签到」这种自相矛盾的行为。

        2026-11-05 牛哥反馈「老师设了无需签到，学生打开学习单还是被拦」：
        老师关掉课堂签到后，学生端连签到卡都不显示（app.js 按
        checkin.enabled 隐藏），可这条闸门还要求「今天必须有签到记录」，
        结果学生既没有入口签到、也永远打不开学习单 —— 死锁。
        签到关闭 = 不存在签到要求，这里直接放行，两个入口一起生效。
        """
        if not self._checkin_enabled():
            return True
        d = time.strftime("%Y-%m-%d")
        ok_st = ("checked", "late", "sick", "personal", "present", "early_leave")
        return any(
            str(a.get("status") or "") in ok_st
            for a in STORE.table_rows("attendance")
            if str(a.get("student_id")) == str(r.get("id"))
            and str(a.get("date") or "") == d and not a.get("deleted"))

    def _task_page_html(self, task):
        """该活动已部署的学习单页面 HTML（没有就空串）。"""
        try:
            cfg = LEG.get_config()
            tpls = cfg.get("custom_templates") if isinstance(cfg, dict) else None
            return str(tpls.get(str(task)) or "") if isinstance(tpls, dict) else ""
        except Exception:
            return ""

    def _page_from_file(self, task, full):
        """这份网页是不是就是「该活动部署的那一页」？

        为什么需要：老师常常只在「大屏」下发过、没在资料里点「设为学习单」，
        这时没有显式标记。不能因此把同一课程里**任何** html 都当学习单打开
        （一门课可能有好几份网页资料），所以拿标题 / 正文前 40 个有效字符
        做指纹，比对部署页里是否含着这份文件的内容（2026-11-05）。
        """
        page = self._task_page_html(task)
        if not page or not full:
            return False
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                raw = fh.read(400000)
        except Exception:
            return False
        norm = lambda s: re.sub(r"\s+", "", s or "")
        m = re.search(r"<title[^>]*>([\s\S]*?)</title>", raw, re.I)
        key = norm(m.group(1))[:60] if m else ""
        if key and key in norm(page):
            return True
        body = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", raw, flags=re.I)
        txt = norm(re.sub(r"<[^>]+>", "", body))[:40]
        return bool(txt) and txt in norm(page)

    def _only_html_material(self, fid, class_id, course):
        """本班这门课里，网页资料只有这一份吗？（只有一份时按学习单处理）"""
        n, hit = 0, False
        want = str(fid or "").strip("/")
        for m in STORE.table_rows("materials"):
            if m.get("deleted"):
                continue
            if str(m.get("class_id") or "") != str(class_id or ""):
                continue
            lesson = str(m.get("lesson") or m.get("subject") or "").strip()
            if lesson and course and lesson != course:
                continue
            u = str(m.get("url") or "")
            if not u.lower().split("?")[0].endswith((".html", ".htm")):
                continue
            try:
                rel = urllib.parse.unquote(u).strip("/")
            except Exception:
                rel = u.strip("/")
            if rel.startswith("cw/"):
                rel = rel[3:]
            n += 1
            if rel.strip("/") == want:
                hit = True
        return n == 1 and hit

    def _custom_page_ready(self, task):
        """该活动是否已经部署过学习单页面（custom_templates 里有非空 HTML）。

        为什么资料入口要先问这一句：学生最终打开的是
        /submit/custom.html?task=…，服务端按 task 取这份已注入运行时的页面。
        没部署过就只能拿到「老师还没有配置自定义提交页」的占位串 —— 那还不如
        退回原始网页（虽然会有预览模式黄条，至少内容是对的）。
        """
        try:
            cfg = LEG.get_config()
            tpls = cfg.get("custom_templates") if isinstance(cfg, dict) else None
            return bool(isinstance(tpls, dict) and str(tpls.get(task) or "").strip())
        except Exception:
            return False

    def _task_layout(self, task):
        """这份学习单有几个提交按钮、每题有哪些选项（2026-11-05）。

        下发（actDeploySheet）时前端把页面扫一遍，结果随 /api/custom 存进
        config.json 的 custom_layout[task]；这里读出来交给数据大屏 ——
        大屏据此为每个提交按钮预建一个收集窗口，并给选择题算选项分布。
        老页面（从没经过新下发）没有这份数据 → 返回 {}，大屏退回
        「按已提交数据里的按钮分组」，行为与旧版一致，不会白屏。
        """
        try:
            cfg = LEG.get_config()
            lays = cfg.get("custom_layout") if isinstance(cfg, dict) else None
            lay = lays.get(str(task)) if isinstance(lays, dict) else None
            return lay if isinstance(lay, dict) else {}
        except Exception:
            return {}

    def _sheet_entry_url(self, r, task, title=""):
        """带身份票的 /submit/custom.html 链接（资料入口直通学习单用）。

        和 _activity_gate 的跳转保持同一套参数：票由服务端签，页面凭票
        提交（/api/v2/student/submit?t=…），学号姓名只用来显示。api 不带，
        让页面自己问 /api/v2/act-api 拿本活动的提交地址。
        """
        cls_name = self._class_name(r.get("class_id"))
        tok = new_gticket({
            "sid": str(r.get("id") or ""),
            "no": str(r.get("student_no") or ""),
            "name": str(r.get("name") or ""),
            "cls": cls_name,
            "gid": "", "idx": 0, "task": task,
        })
        qs = urllib.parse.urlencode({
            "class": cls_name,
            "task": task,
            "title": title or task,
            "wbT": tok,
            "wbSid": str(r.get("student_no") or ""),
            "wbName": str(r.get("name") or ""),
            "wbCls": cls_name,
            "wbTask": task,
            "wbBase": public_base(self.headers),
        })
        return public_base(self.headers) + "/submit/custom.html?" + qs

    def _activity_gate(self, r, push_id):
        """课堂活动学习单入口：/g/act/<push_id>（2026-11-03）。

        学生端打开学习单统一走这里，三件事：
        ① 身份注入：把「票 + 学号/姓名/班级」挂到学习单 URL 上——以前学生端
           直接 window.open 裸链接，页面拿不到身份，教师端导入的学生
           班级/姓名/学号在学习单上永远显示不出来；
        ② 签到闸门：今天一笔有效签到都没有（或缺勤）就不放行，
           先回学生端签到（牛哥要求：不签到不能打开课程对应的学习单）；
           老师在设置里关掉课堂签到时，_signed_today 直接放行，不再拦。
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
        if not self._signed_today(r):
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

    def _material_gate(self, r, mid):
        """资料一键打开：/g/mat/<资料id>（2026-11-04）。

        学生端「资料」「今日」里点一条资料，就走这里，做三件事：
        ① 校验：这条 materials 属于本班、没被老师收回；
        ② 身份注入：签一张票，把票 + 学号/姓名/班级挂到资料地址上——
           生成过的学习单网页会自己读这些参数，登录浮层直接摘掉、显示
           「已识别：X 班 · 姓名」，学生不用再填一遍；
        ③ .url（Windows 网页快捷方式）不是网页，不能内嵌预览：读出里面的
           URL= 行直接跳过去——原来学生点开只能看到一段文件文本。
        """
        row = None
        for m in STORE.table_rows("materials"):
            if str(m.get("id")) == str(mid):
                row = m
                break
        if (not row or row.get("deleted")
                or str(row.get("class_id") or "") != str(r.get("class_id") or "")):
            return self._html(_tip_page("找不到这份资料",
                "它可能已被老师收回，或者不属于你所在的班级。"), 404)
        url = str(row.get("url") or "").strip()
        fid = ""
        if url.startswith("/cw/"):
            fid = urllib.parse.unquote(url[len("/cw/"):].strip("/"))
        if not fid:
            return self._html(_tip_page("这份资料没有可打开的地址",
                "请让老师重新下发一次。"), 404)
        full, err = self._courseware_resolve(fid)
        if err:
            return err

        ext = _ext_of(full)
        if ext in ("url", "website"):
            target = ""
            try:
                with open(full, "r", encoding="utf-8-sig", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if line.upper().startswith("URL="):
                            target = line[4:].strip()
                            break
            except Exception:
                target = ""
            if target.startswith("//"):
                target = "https:" + target
            if target.startswith("http://") or target.startswith("https://"):
                return self._redirect(target)
            return self._html(_tip_page("这个快捷方式里没有网址",
                "它是 Windows 的网页快捷方式，但里面没写地址。请让老师换一份资料。"), 400)

        # 课程收集任务：网页学习单提交后要能进数据大屏（按课程资料名找任务）
        course = str(row.get("lesson") or row.get("subject") or "").strip()
        task = ""
        if course:
            for t in ACT.tasks():
                if (str(t.get("title") or "").strip() == course
                        or str(t.get("course") or "").strip() == course):
                    task = str(t.get("task_id") or "")
                    break

        # 学习单优先（2026-11-04 / 2026-11-05 补 draft 直通）：这份文件如果是
        # 本课程「设为学习单」的那一份，就绝不能再回 /cw 原始文件 ——
        # 那个页面自带「预览模式」兜底（没有平台注入的提交按钮就显示黄条
        # 「提交仅作演示、不会真正上报」），学生看到会以为学习单坏了。
        #   ① 该班有已发放（published）的记录 → 走 /g/act，签到 + 发放校验齐全；
        #   ② 已关闭（closed）→ 明确告诉学生被老师关了；
        #   ③ 从没发放过 / 已收回（draft）→ 网页既然推给了本班，就该能答题，
        #      直通 /submit/custom.html（带票，真提交按钮、提交进数据大屏）。
        #      这种情况在「老师只推送了资料、没在学习单入口下发」时很常见。
        if course and task:
            try:
                sheet_rel = str(LEG.CW.sheet_of(course) or "").strip("/")
            except Exception:
                sheet_rel = ""
            # 标记指向的文件被删 / 改名 → 视为没设过，别再拿它比（否则这份网页
            # 永远匹配不上，学生只能拿到原始页）。2026-11-05
            if sheet_rel:
                try:
                    if not LEG.CW.abs_of(sheet_rel, must_exist=True):
                        sheet_rel = ""
                except Exception:
                    pass
            is_html = ext in ("html", "htm")
            # 走注入页的条件（2026-11-05 放宽）：
            #   · 设过学习单 → 只有这份文件本身算；
            #   · 没设过 / 标记失效 → 只要它是网页且本课程已有部署好的学习单页，
            #     就按学习单打开。老师常常只在大屏「下发」过、没在资料里点
            #     「设为学习单」，以前这种网页点开就是原始 /cw 页：没有身份、
            #     没有提交按钮，模板还会弹「本页未从教师端打开」。
            route = bool(sheet_rel and sheet_rel == fid.strip("/")) \
                or (not sheet_rel and is_html and self._custom_page_ready(task)
                    and (self._page_from_file(task, full)
                         or self._only_html_material(fid, r.get("class_id"), course)))
            if route:
                push = None
                for p in STORE.table_rows("activity_pushes"):
                    if (not p.get("deleted")
                            and str(p.get("class_id") or "") == str(r.get("class_id") or "")
                            and str(p.get("task_id") or "") == task):
                        push = p
                        break
                st = str(push.get("status") or "published") if push else ""
                if push and st == "published":
                    return self._activity_gate(r, str(push.get("id") or ""))
                if st == "closed":
                    return self._html(_tip_page(
                        "这份学习单已关闭",
                        "老师暂时关闭了这份学习单，等老师重新开启后再打开。"), 403)
                if self._custom_page_ready(task):
                    if not self._signed_today(r):
                        return self._html(_tip_page(
                            "请先签到",
                            "今天还没有你的签到记录，先回到学生端完成签到，再打开这门课的学习单。"), 403)
                    return self._redirect(self._sheet_entry_url(r, task, course))

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
        return self._redirect(public_base(self.headers) + url + sep + qs)

    # ------------------------------------------------------------------
    # 学生答题提交中转：POST /api/v2/student/submit?t=<票>
    # ------------------------------------------------------------------
    # 为什么不让页面直接 POST 到活动数据服务：
    #   ① 页面大多在另一个端口（5055），直连要它开 CORS、还得把地址写进页面；
    #   ② 地址一旦写进页面，老师换机器就要全班改一遍；
    #   ③ 身份字段如果由页面自己填，学生改个数字就能冒充别人。
    # 走这里：学生只带一张票，身份由服务端补，活动服务只需要在内网可达。

    def _accept_files(self, task, fields):
        """这次提交收不收文件（2026-11-05）。

        判断顺序（先细后粗，保证老页面也能用）：
          ① 前端提交时按「当前题目块里有没有 file 控件」写进来的 __kind：
             file = 文件任务，data = 纯数据任务 —— 最贴近学生实际操作的这一块；
          ② 没有 __kind（2.0.33 之前下发的页面）→ 查下发时扫描的 layout.file_buttons；
          ③ 两者都没有（更老的模板）→ None：保持「收到什么存什么」的旧行为。

        返回 (allow, kind)；allow=False 时调用方必须把 files 丢掉 ——
        纯数据任务不写盘、不产生 attachment，也不占 act_file 行。
        """
        f = fields if isinstance(fields, dict) else {}
        kind = str(f.pop("__kind", "") or "").strip().lower()
        if kind in ("file", "data"):
            return kind == "file", kind
        lay = self._task_layout(task) or {}
        fb = lay.get("file_buttons")
        if isinstance(fb, list):
            btn = str(f.get("按钮") or "").strip()
            names = [str(x or "").strip() for x in fb]
            hit = btn in names
            return hit, ("file" if hit else "data")
        return None, ""

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
        # 老页面兼容（2026-11-05 深夜）：不再保存整页 HTML 快照，带票口同样直接丢弃
        # __archive。它不是一道题的答案，留着会变成一列几百 KB 的文本。
        fields.pop("__archive", None)
        # 身份以服务端签的票为准，页面自己填的一律覆盖（防冒名）
        fields.update(who)
        # 任务类型：纯数据任务即使前端误带了文件，也在这里丢掉（不写盘、不产生 attachment）
        allow_files, kind = self._accept_files(task, fields)
        ignored = 0
        if allow_files is False and files:
            ignored = len(files)
            files = []
        try:
            res = ACT.add_submission(task, fields, files, ip=self._client_ip_safe(),
                                     sid=who.get("学号"), name=who.get("姓名"),
                                     cls=who.get("班级"), kind=kind)
        except ValueError as e:
            return self._json_cors({"ok": False, "msg": str(e)}, 413)
        except LookupError:
            return self._json_cors({"ok": False, "msg": "任务不存在"}, 404)
        # 提交成功立刻就归档：文件直接进「资料」的课程/班级/学号+姓名 文件夹
        # 2026-11-17：模拟提交（__test=1）不归档，别让「测试同学」在资料里建文件夹
        _is_test = str(fields.get("__test") or "").strip().lower() in ("1", "true", "yes")
        arc = None if _is_test else _archive_after_submit(task, who, res)
        _win = " ".join(str(fields.get("按钮") or "").split())
        if _is_test:
            _win = "测试提交"          # 预览页真实按钮点出来的模拟数据也归到测试窗口
        if _win:
            quiz_bump({"kind": "submit", "window": _win, "from": "sheet",
                       "task": task, "student_no": who.get("学号")})
            notify()
        out = {"ok": True, "message": "提交成功", "id": res["id"],
               "kind": kind or "legacy",
               "attachment": res.get("attachment")}
        if arc and arc.get("ok"):
            out["archived"] = len(arc.get("saved") or [])
        if ignored:
            out["ignored_files"] = ignored
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
        self._bind_account()
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
        # 测验/检测（2.0.38）：试卷含答案，走独立鉴权接口，不进同步投影
        if path.startswith("/api/v2/quiz/"):
            r = _quiz_api(self).get(path, qs)
            return r if r is not None else self._json({"ok": False, "msg": "未知接口"}, 404)
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
                               # 管理员账号随服务初始化即存在，恒为 true
                               "has_password": True,
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
            # H5 网页设计（2.0.39）：学生端据此决定「今日」入口卡与导航页签。
            # add = 允许添加该功能页；submit = 允许提交作品（练习不受影响）。
            _dg = CFG.get("design") or {}
            out["design"] = {"enabled": bool(_dg.get("enabled")),
                             "add": bool(_dg.get("allow_add", True)),
                             "submit": bool(_dg.get("allow_submit", True)),
                             "window": str(_dg.get("window") or "")}
            # 像素画（2.0.47）：字段与 design 一一对应，学生端同一套判断
            _pg = CFG.get("pixel") or {}
            out["pixel"] = {"enabled": bool(_pg.get("enabled")),
                            "add": bool(_pg.get("allow_add", True)),
                            "submit": bool(_pg.get("allow_submit", True)),
                            "window": str(_pg.get("window") or ""),
                            "size": int(_pg.get("size") or 32)}
            if s["role"] == "teacher":
                t = ACCOUNTS.current_teacher()
                out["username"] = (t or {}).get("username") or "teacher"
                out["account"] = s.get("account") or ACC.DEFAULT_TEACHER_ID
                out["avatar"] = (t or {}).get("avatar") or ""   # 像素画头像（服务端权威）
                # 教师账号登录后不再提示改初始密码（由管理员统一管理账号）；
                # 只有管理员账号仍走 need_setup 引导。学生端走独立的 suggest_pwd。
                out["need_setup"] = False
            if s["role"] == "admin":
                out["username"] = ACCOUNTS.admin().get("username") or "teacher"
                out["need_setup"] = ACCOUNTS.need_setup("admin", "admin")
            if s["role"] == "student":
                r = student_row(s.get("student_id"))
                out["student"] = PJ.student_context(r)
                # 头像等本机偏好按「教师账号 + 班级 + 学生 id」分键，
                # 同一台机房电脑上换人登录不会看到上一位同学的头像。
                out["student"]["account"] = s.get("account") or ACC.DEFAULT_TEACHER_ID
                out["student"]["name"] = (r or {}).get("name", "")
                out["student"]["student_no"] = (r or {}).get("student_no", "")
                out["student"]["class_name"] = self._class_name((r or {}).get("class_id"))
                out["student"]["avatar"] = (r or {}).get("avatar", "")   # 像素画头像（服务端权威）
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
                               "layout": self._task_layout(task),
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
        if path == "/api/v2/school":
            # 学校名称：教师设置页读写，创意宠物作品署名下发时取用。
            # 不含任何敏感信息；任何人都能读（和 entry 里的 school 同源）。
            return self._json({"ok": True, "school": CFG.get("school_name") or ""})
        if path == "/api/v2/checkin/config":
            # 签到配置：教师设置页 / 学生端今日页都要读（不含任何敏感信息）
            return self._json(dict({"ok": True}, **self._checkin_cfg()))
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
        if path == "/api/v2/admin/teachers":
            # 管理员控制台：只列教师账号，绝不返回口令哈希 / 密保答案
            if not self._is_admin():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json({"ok": True,
                               "teachers": ACCOUNTS.public_teachers(),
                               "admin_username": ACCOUNTS.admin().get("username"),
                               "version": VERSION})
        if path == "/api/v2/teacher/peers":
            # 资料共享：可选的目标教师（不含自己）
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json({"ok": True, "me": ACC.current_account(),
                               "peers": ACCOUNTS.peers(ACC.current_account())})
        if path == "/api/v2/teacher/tt-clash":
            # 跨账号同班冲突检测（2026-11-05）：多教师账号可能教同一个班
            #（各自工作区里建了同名班级），同一时段同一班会真实撞车。
            # 按班级名跨工作区比对：
            #   单格模式 ?dow=&pid=&class_name=&wp=  —— 保存排课前确认用；
            #   全表模式 ?all=1                      —— 课表渲染角标用。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            me = ACC.current_account()

            def _wp_clash(a, b):
                a = str(a or "all")
                b = str(b or "all")
                return not ((a == "odd" and b == "even")
                            or (a == "even" and b == "odd"))

            def _fmt(t, l, courses):
                oc = next((x for x in courses
                           if not x.get("deleted")
                           and str(x.get("id")) == str(l.get("course_id"))), None)
                w = str(l.get("week_pattern") or "all")
                return {"teacher": t.get("name") or t.get("username") or t["id"],
                        "course": (oc or {}).get("name") or "（课程已删）",
                        "week_pattern": w}

            # 其它账号：班级名 → [(dow, pid, week_pattern, lesson, courses)]
            other = {}
            for t in ACCOUNTS.teachers():
                if t["id"] == me:
                    continue
                try:
                    st = STORE.for_account(t["id"])
                    courses = [c for c in st.table_rows("courses")
                               if not c.get("deleted")]
                    classes = {}
                    for c in st.table_rows("classes"):
                        if c.get("deleted"):
                            continue
                        classes[str(c.get("id"))] = \
                            str(c.get("name") or "").strip()
                    for l in st.table_rows("lessons"):
                        if l.get("deleted"):
                            continue
                        cn = classes.get(str(l.get("class_id")))
                        if not cn:
                            continue
                        other.setdefault(cn, []).append(
                            (int(l.get("day_of_week") or 0),
                             int(l.get("period_id") or 0), l, courses, t))
                except Exception:
                    continue        # 某个账号的工作区坏了不影响别人

            if (qs.get("all") or [""])[0]:
                # 全表：我每节课的位置，看别处同名班有没有同时段安排
                my_classes = {str(c.get("id")): str(c.get("name") or "").strip()
                              for c in STORE.table_rows("classes")
                              if not c.get("deleted")}
                cells = {}
                for l in STORE.table_rows("lessons"):
                    if l.get("deleted"):
                        continue
                    cn = my_classes.get(str(l.get("class_id")))
                    if not cn:
                        continue
                    d = int(l.get("day_of_week") or 0)
                    p = int(l.get("period_id") or 0)
                    wp = str(l.get("week_pattern") or "all")
                    my_courses = [c for c in STORE.table_rows("courses")
                                  if not c.get("deleted")]
                    for (d2, p2, l2, courses2, t) in other.get(cn, []):
                        if d2 == d and p2 == p and _wp_clash(wp, l2.get("week_pattern")):
                            cells.setdefault("%d-%d-%s" % (d, p, l.get("class_id")), []) \
                                .append(_fmt(t, l2, courses2))
                return self._json({"ok": True, "cells": cells})

            # 单格
            try:
                dow = int((qs.get("dow") or [""])[0])
                pid = int((qs.get("pid") or [""])[0])
            except Exception:
                return self._json({"ok": False, "msg": "参数不合法"}, 400)
            cname = str((qs.get("class_name") or [""])[0]).strip()
            wp = str((qs.get("wp") or ["all"])[0]) or "all"
            out = []
            if cname and 1 <= dow <= 7 and pid > 0:
                for (d2, p2, l2, courses2, t) in other.get(cname, []):
                    if d2 == dow and p2 == pid and _wp_clash(wp, l2.get("week_pattern")):
                        out.append(_fmt(t, l2, courses2))
            return self._json({"ok": True, "clashes": out})
        if path == "/api/v2/teacher/entry":
            # 首页「学生入口」卡：给出学生机该打开的局域网地址。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            # 地址跟着「老师当前打开工作台用的地址」走：反向代理（NAS 上的
            # 统一入口）会把 Host / X-Forwarded-* 带过来，学生端链接自然是
            # http://<NAS>:<入口端口>/s/，不再写死容器内的 20020。
            port = _port()
            base = public_base(self.headers)
            # 2026-11-25 账号隔离：学生入口绑定当前教师账号的签名令牌。
            # 学生端只认这张令牌，看不到、也登不进别的老师账号的班级。
            acc = (self._session() or {}).get("account") or ACC.current_account()
            tok = ET.issue(acc)
            urls = [base + "/s/?e=" + urllib.parse.quote(tok)]
            return self._json({"ok": True, "urls": urls, "port": port,
                               "school": CFG.get("school_name") or "",
                               "account": str(acc)})
        if path == "/api/v2/teacher/nickname":
            # 教师称呼昵称（账号级，写 accounts.json）：设置页「称呼昵称」用。
            # 之前只存本机 localStorage，换设备/重装后昵称丢失，这里改成服务端权威。
            s = self._session()
            if not s or s["role"] != "teacher":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json({"ok": True, "nickname": ACCOUNTS.nickname_of(s.get("account"))})
        if path == "/api/v2/teacher/activity":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json({"ok": True,
                               "submit_hours": (CFG.get("activity") or {}).get("submit_hours", 12)})
        if path == "/api/v2/teacher/security":
            return self._security_status()
        if path == "/api/v2/teacher/enc/status":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json({"ok": True,
                               **ACCOUNTS.enc_status(ACC.current_account())})
        if path == "/api/v2/teacher/console":
            return self._console()
        if path == "/api/v2/teacher/export":
            return self._export(qs)
        if path == "/api/v2/teacher/export-files":
            return self._export_files()
        if path == "/api/v2/teacher/export-files/progress":
            return self._export_files_progress(qs)
        if path == "/api/v2/teacher/export-files/download":
            return self._export_files_download(qs)
        if path == "/api/v2/teacher/pack-active":
            # 刷新页面后恢复进度：验证会话 + 数据密码窗口仍有效才告诉前端
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            if not self._data_unlocked():
                return self._json({"ok": True, "active": None, "expired": True})
            j = _pack_job_active(A.parse_cookie(self.headers.get("Cookie")))
            return self._json({"ok": True, "active": j})
        if path == "/api/v2/admin/import-files/progress":
            return self._import_files_progress(qs)
        if path == "/api/v2/cw/courses":
            # 大屏「课程」下拉（2.0.41）：不再只依赖 /api/cw/config。
            # 那个旧接口一旦 500，前端把异常吞掉就显示「资料里还没有文件夹」，
            # 老师看着明明有课却选不出来。这里由工作台自己汇总：
            # 资料库课程 + 网页设计归档课程 + 测验发布里出现的课程，去重。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            names, seen = [], set()

            def _add(n):
                n = str(n or "").strip()
                if n and n != "未分类" and n not in seen:
                    seen.add(n)
                    names.append(n)

            try:
                courses, _err = LEG.CW.list_courses()
                for c in (courses or []):
                    if not c.get("loose"):
                        _add(c.get("name"))
            except Exception:
                pass
            try:
                _add((CFG.get("design") or {}).get("course"))
            except Exception:
                pass
            try:
                _add((CFG.get("pixel") or {}).get("course"))
            except Exception:
                pass
            try:
                for p in (QUIZ.publishes() or []):
                    _add(p.get("course"))
            except Exception:
                pass
            return self._json({"ok": True, "courses": names})
        if path == "/api/v2/design/assets":
            # 图片素材（2.0.41）：老师从「资料」下发给本班的图片，学生可直接插进网页。
            # 只列图片、只列本班、只列没被收回的；地址统一走 /cw/<文件id> 闸门，
            # 学生用自己的会话就能取到，导出 PNG 时前端也能内联成 dataURL。
            s = self._session()
            if not s or s.get("role") not in ("student", "teacher"):
                return self._json({"ok": False, "msg": "未登录"}, 401)
            cid = ""
            if s["role"] == "student":
                stu = student_row(s.get("student_id"))
                if not stu:
                    return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
                cid = str(stu.get("class_id") or "")
            IMG_EXT = ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif")
            if s["role"] == "teacher":
                # 教师练习（2.0.41）：教师看到的应该是**课件库里全部图片**——
                # 老师是先挑图、再下发，不能要求他"先下发给班级"才能自己用。
                rows = []
                try:
                    courses, _err = LEG.CW.list_courses()
                except Exception:
                    courses = []
                for c in (courses or []):
                    cname = str(c.get("name") or "").strip()
                    if not cname:
                        continue
                    try:
                        files = LEG.CW.list_files(cname) or []
                    except Exception:
                        files = []
                    for f in files:
                        nm = str(f.get("name") or "")
                        if _ext_of(nm) not in IMG_EXT:
                            continue
                        # 学生交上来的作品截图（归档目录）不放进素材库：
                        # 那些是作业结果，不是可复用的图片资源，而且常是空白页。
                        if f.get("archived"):
                            continue
                        fid = str(f.get("id") or "")
                        if not fid:
                            continue
                        rows.append({
                            "id": fid, "title": nm,
                            "url": "/cw/" + urllib.parse.quote(fid),
                            "course": cname,
                            "date": str(f.get("mtime") or ""),
                        })
                rows.sort(key=lambda x: (x["course"], x["title"]))
                return self._json({"ok": True, "assets": rows, "scope": "teacher",
                                   "courses": sorted({x["course"] for x in rows if x["course"]})})
            rows, seen = [], set()
            for m in STORE.table_rows("materials"):
                if m.get("deleted"):
                    continue
                if str(m.get("from") or "") != "courseware":
                    continue
                if cid and str(m.get("class_id") or "") != cid:
                    continue
                fid = str(m.get("src_id") or "").strip()
                if not fid:
                    u = str(m.get("url") or "")
                    if u.startswith("/cw/"):
                        fid = urllib.parse.unquote(u[len("/cw/"):].strip("/"))
                if not fid or fid in seen:
                    continue
                title = str(m.get("title") or "").strip() or fid.rsplit("/", 1)[-1]
                if _ext_of(title) not in IMG_EXT:
                    continue
                seen.add(fid)
                rows.append({
                    "id": fid,
                    "title": title,
                    "url": "/cw/" + urllib.parse.quote(fid),
                    "course": str(m.get("lesson") or m.get("subject") or "").strip(),
                    "date": str(m.get("date") or ""),
                })
            rows.sort(key=lambda x: (x["course"], x["title"]))
            return self._json({"ok": True, "assets": rows,
                               "courses": sorted({x["course"] for x in rows if x["course"]})})
        if path == "/api/v2/design/me":
            # H5 网页设计：学生打开设计器时拿初始状态（权限 / 草稿 / 上次提交）
            s = self._session()
            out = {"ok": True, "cfg": design_public_cfg()}
            if not s:
                out["role"] = None
                return self._json(out)
            if s["role"] == "teacher":
                out["role"] = "teacher"
                # 教师练习模式（2.0.40）：?practice=1 时把老师自己的草稿给他，
                # 下次点「进入练习」接着上次的代码和显示设置继续。
                if str((qs.get("practice") or [""])[0]).lower() in ("1", "true"):
                    acc = ACC.current_account()
                    t = ACCOUNTS.current_teacher() or {}
                    out["practice"] = True
                    out["me"] = {"id": "t:" + str(acc),
                                 "name": str(t.get("username") or "老师"),
                                 "student_no": "", "class_name": ""}
                    out["draft"] = DESIGN.draft("t:" + str(acc))
                    out["last"] = None
                return self._json(out)
            if s["role"] != "student":
                return self._json({"ok": False, "msg": "只有学生有设计草稿"}, 403)
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            uid = str(r.get("id"))
            cfg = design_public_cfg()
            last = _design_student_last(cfg["course"], r.get("student_no"), r.get("name"))
            files = (last or {}).get("_files") or []
            out["role"] = "student"
            out["me"] = {"id": uid, "name": r.get("name", ""),
                         "student_no": r.get("student_no", ""),
                         "class_name": self._class_name(r.get("class_id"))}
            out["draft"] = DESIGN.draft(uid)
            # 已交几次 / 还能交几次（-1 = 老师没限次数），设计器据此提示与拦按钮
            used_n = len(_student_subs_of(design_task(cfg["course"]),
                                          r.get("student_no"), r.get("name")))
            lim = _max_submit_of(cfg)
            out["used"] = used_n
            out["left"] = (max(0, lim - used_n) if lim > 0 else -1)
            out["last"] = ({"at": str(last.get("submitted_at") or ""),
                            "url": files[0].get("url") if files else "",
                            "name": files[0].get("name") if files else ""}
                           if last else None)
            return self._json(out)
        if path == "/api/v2/design/config":
            # 教师管理页：开关状态 + 课程列表 + 提交概览 + 最近作品
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            cfg = design_public_cfg()
            courses, _err = LEG.CW.list_courses()
            names = [str(c.get("name") or "") for c in (courses or [])
                     if not c.get("loose") and c.get("name")]
            t = design_task(cfg["course"])
            stats = {"people": 0, "files": 0, "last_at": ""}
            recent = []
            if t:
                try:
                    subs = ACT.submissions(t["task_id"]) or []
                except Exception:
                    subs = []
                # 一人一条（留最新），与作品墙 / 大屏口径一致
                by = {}
                for sub in subs:
                    key = str(sub.get("学号") or sub.get("姓名") or "")
                    if not key:
                        continue
                    old = by.get(key)
                    if old is None or str(sub.get("submitted_at") or "") >= str(old.get("submitted_at") or ""):
                        by[key] = sub
                rows = sorted(by.values(), key=lambda x: str(x.get("submitted_at") or ""))
                for sub in rows:
                    fs = sub.get("_files") or []
                    stats["files"] += len(fs)
                    if sub.get("submitted_at") and sub["submitted_at"] > stats["last_at"]:
                        stats["last_at"] = sub["submitted_at"]
                stats["people"] = len(rows)
                for sub in reversed(rows[-12:]):
                    fs = sub.get("_files") or []
                    img = next((f for f in fs if str(f.get("name") or "").lower().endswith(".png")),
                               fs[0] if fs else None)
                    recent.append({"name": sub.get("姓名") or "",
                                   "no": sub.get("学号") or "",
                                   "cls": sub.get("班级") or "",
                                   "at": sub.get("submitted_at") or "",
                                   "url": (img or {}).get("url") or "",
                                   "file": (img or {}).get("name") or ""})
            return self._json({"ok": True, "cfg": cfg, "courses": names,
                               "task": ({"task_id": t["task_id"], "title": t.get("title") or "",
                                         "course": t.get("course") or ""} if t else None),
                               "stats": stats, "recent": recent})
        if path == "/api/v2/pixel/me":
            # 像素画（2.0.47）：学生打开画板时拿初始状态（权限 / 草稿 / 上次提交）
            s = self._session()
            out = {"ok": True, "cfg": pixel_public_cfg()}
            if not s:
                out["role"] = None
                return self._json(out)
            if s["role"] == "teacher":
                out["role"] = "teacher"
                # 教师练习：与网页设计同一套，草稿 key 用 "t:<账号>"
                if str((qs.get("practice") or [""])[0]).lower() in ("1", "true"):
                    acc = ACC.current_account()
                    t = ACCOUNTS.current_teacher() or {}
                    out["practice"] = True
                    out["me"] = {"id": "t:" + str(acc),
                                 "name": str(t.get("username") or "老师"),
                                 "student_no": "", "class_name": ""}
                    out["draft"] = PIXEL.draft("t:" + str(acc))
                    out["last"] = None
                return self._json(out)
            if s["role"] != "student":
                return self._json({"ok": False, "msg": "只有学生有像素画草稿"}, 403)
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            uid = str(r.get("id"))
            cfg = pixel_public_cfg()
            last = _pixel_student_last(cfg["course"], r.get("student_no"), r.get("name"))
            files = (last or {}).get("_files") or []
            out["role"] = "student"
            out["me"] = {"id": uid, "name": r.get("name", ""),
                         "student_no": r.get("student_no", ""),
                         "class_name": self._class_name(r.get("class_id"))}
            out["draft"] = PIXEL.draft(uid)
            # 已交几次 / 还能交几次（-1 = 老师没限次数）：学生页据此提示与禁用按钮
            used_n = len(_pixel_student_subs(cfg["course"], r.get("student_no"), r.get("name")))
            lim = _max_submit_of(cfg)
            out["used"] = used_n
            out["left"] = (max(0, lim - used_n) if lim > 0 else -1)
            out["last"] = ({"at": str(last.get("submitted_at") or ""),
                            "url": files[0].get("url") if files else "",
                            "name": files[0].get("name") if files else ""}
                           if last else None)
            return self._json(out)
        if path == "/api/v2/pixel/config":
            # 教师管理页：开关状态 + 课程列表 + 提交概览 + 最近作品
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            cfg = pixel_public_cfg()
            courses, _err = LEG.CW.list_courses()
            names = [str(c.get("name") or "") for c in (courses or [])
                     if not c.get("loose") and c.get("name")]
            t = pixel_task(cfg["course"])
            stats = {"people": 0, "files": 0, "last_at": ""}
            recent = []
            if t:
                try:
                    subs = ACT.submissions(t["task_id"]) or []
                except Exception:
                    subs = []
                # 一人一条（留最新），与作品墙 / 大屏口径一致
                by = {}
                for sub in subs:
                    key = str(sub.get("学号") or sub.get("姓名") or "")
                    if not key:
                        continue
                    old = by.get(key)
                    if old is None or str(sub.get("submitted_at") or "") >= str(old.get("submitted_at") or ""):
                        by[key] = sub
                rows = sorted(by.values(), key=lambda x: str(x.get("submitted_at") or ""))
                for sub in rows:
                    stats["files"] += len(sub.get("_files") or [])
                    if sub.get("submitted_at") and sub["submitted_at"] > stats["last_at"]:
                        stats["last_at"] = sub["submitted_at"]
                stats["people"] = len(rows)
                for sub in reversed(rows[-12:]):
                    fs = sub.get("_files") or []
                    img = next((f for f in fs if str(f.get("name") or "").lower().endswith(".png")),
                               fs[0] if fs else None)
                    recent.append({"name": sub.get("姓名") or "",
                                   "no": sub.get("学号") or "",
                                   "cls": sub.get("班级") or "",
                                   "at": sub.get("submitted_at") or "",
                                   "url": (img or {}).get("url") or "",
                                   "file": (img or {}).get("name") or ""})
            return self._json({"ok": True, "cfg": cfg, "courses": names,
                               "task": ({"task_id": t["task_id"], "title": t.get("title") or "",
                                         "course": t.get("course") or ""} if t else None),
                               "stats": stats, "recent": recent})
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
        if path == "/api/v2/studio/works":
            # 创意宠物（只读）：作品清单 + 价格 + 设计者信息（教师改、学生看）。
            # 2.0.58：改为公开只读 —— 未登录的演示页 / 教师预览也要能拿到完整
            # 作品与动作配置，否则只剩 3 只兜底数据。这里不含任何学生数据与可写配置。
            if not STUDIO:
                return self._json({"ok": False, "msg": "作品数据未就绪"}, 500)
            # 2.0.56：不再按教师身份「全部解锁」。教师练习页看到的创意馆
            # 与学生端一致（真实作品 + 设计者信息 + 兑换按钮），避免误以为
            # 教师账号拥有全部作品。unlocked 默认 False。
            c = studio_cfg()
            return self._json({"ok": True,
                               "studio": STUDIO.public_cfg(c.get("designers"),
                                                           free=studio_free_cfg(),
                                                           school=CFG.get("school_name", ""))})
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
                             "pet": d.get("pet", ""), "decos": d.get("decos") or [],
                             "bg": d.get("bg", ""), "fx": d.get("fx", ""),
                             # 2.1.7：头像上排行榜——创意宠物头像 + 像素画头像（students.avatar）
                             "studio_pet": d.get("studio_pet", ""),
                             "avatar": st.get("avatar", "")})
            rows.sort(key=lambda x: -x["value"])
            # 教师练习成绩（2026-11-03）：开启「计入班级排行榜」后，以「老师」
            # 一行参与排序；本周星为 0 时不进周榜（与学生口径一致）。
            tv = TYPING.teacher_lb_value(class_id, scope)
            if tv and not (scope == "week" and tv["value"] <= 0):
                # 教师行显示名：必须按当前会话账号从 accounts.json 取。
                # 2026-11-25 牛哥反馈「t2 班排行榜里老师显示 Shyxiaoniu」——
                # 原来读全局 config.json 的 teacher.username，那是 default 账号
                # 的镜像值，改昵称也是账号级写 accounts.json，导致多账号串名。
                # 成绩本身来自账号隔离的 typing.json，没有串；只是这一行名字错。
                tv["name"] = self._teacher_name(s)
                rows.append({"student_id": "__teacher__", "name": tv["name"],
                             "student_no": "", "value": tv["value"],
                             "stars": tv["stars"], "pet": tv.get("pet", ""),
                             "decos": tv.get("decos") or [],
                             "bg": tv.get("bg", ""), "fx": tv.get("fx", ""),
                             # 2.1.9：教师出战创意宠物也要上排行榜（与学生行同口径）
                             "studio_pet": tv.get("studio_pet", "")})
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
        if path == "/api/v2/typing/points-admin":
            # 教师端积分管理数据：各班学生余额 + 最近操作记录
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            classes = []
            for c in STORE.table_rows("classes"):
                if c.get("deleted"):
                    continue
                uids = [(st.get("id"), st.get("name", ""), st.get("student_no", ""))
                        for st in STORE.table_rows("students")
                        if not st.get("deleted") and str(st.get("class_id")) == str(c.get("id"))]
                classes.append({"id": str(c.get("id")), "name": c.get("name", ""),
                                "students": TYPING.students_points(uids)})
            return self._json({"ok": True, "classes": classes,
                               "log": TYPING.points_log(100)})
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
        sess = self._session() or {}
        # 2.0.38：测验提交不写 STORE.rev（试卷不参与同步），必须走独立事件。
        # 只推给教师会话——事件里带窗口 / 班级 / 学号，学生端不该收到别班名单。
        quiz_epoch = EPOCH[0]
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
                if EPOCH[0] != quiz_epoch:
                    quiz_epoch = EPOCH[0]
                    # 教师拿完整事件（窗口 / 班级 / 学号）用于只刷新对应窗口；
                    # 学生只拿一个版本号，用来让列表缓存失效 ——
                    # 绝不把别班名单或试卷答案推给学生端。
                    ev = last_event() if sess.get("role") == "teacher" else {"epoch": EPOCH[0]}
                    payload = json.dumps(ev, ensure_ascii=False)
                    self.wfile.write(("event: quiz\ndata: %s\n\n" % payload).encode())
                    self.wfile.flush()
                if time.time() - last_ping > 20:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
                # 会话过期 / 被踢就断开（2026-11-17：被踢要先告一声原因，
                # 学生端收到 kick 立刻弹提示并回到登录页，而不是白屏或停在旧数据上）
                _tok = A.parse_cookie(self.headers.get("Cookie"))
                if not SESS.get(_tok):
                    _why = SESS.kick_reason(_tok) or ""
                    if _why:
                        self.wfile.write(("event: kick\ndata: %s\n\n"
                                          % json.dumps({"reason": _why},
                                                       ensure_ascii=False)).encode())
                        self.wfile.flush()
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
            # 2.0.71：所属全部班级（主班 + 兼属班），供学生端显示/切换
            "class_ids": PJ._as_int_list(r.get("class_ids")) or [int(r.get("class_id") or 0)],
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
        t = ACCOUNTS.current_teacher() or {}
        return self._json({
            "ok": True,
            "username": t.get("username") or "teacher",
            "account": ACC.current_account(),
            "has_qa": bool(sec["question"] and sec["answer_hash"]),
            "question": sec["question"],
            "qa_hint": ("密保问题：" + sec["question"]) if sec["question"] else "",
            "min_len": 8,
            "config_rev": CFG_VER[0],
        })

    def _public_classes(self, qs):
        """登录页的班级下拉。只给 id + 名称，不含任何学生信息。

        2026-11-25 账号隔离：不再遍历全部教师账号。学生入口地址带签名令牌 e，
        这里只认令牌里的账号 —— 学生只能看到「发入口那位老师」的班级，改地址
        也看不到别的老师账号。老的无令牌入口一律拒绝，提示换新入口。
        """
        tid = ET.verify((qs.get("e") or [""])[0])
        if not tid:
            return self._json({"ok": False, "need_entry": True,
                               "msg": "请用老师发的新入口链接进入（旧链接已失效）"}, 403)
        rows = []
        try:
            ACC.set_account(tid)
            # 静态加密：登录页是匿名请求，线程 FEK 为 None。已加密账号的
            # rows.data 是密文，取该账号缓存的 FEK（教师登录时解锁并常驻）
            # 临时注入当前线程，读完恢复，避免班名退化成默认值「班级」。
            old_fek = ENC.get_fek()
            ENC.set_fek(ENC.cached_account_fek(tid))
            try:
                cur = STORE.table_rows("classes")
            finally:
                ENC.set_fek(old_fek)
        except Exception:
            ENC.set_fek(None)
            cur = []
        for c in cur:
            cid = str(c.get("id") or "").strip()
            if not cid:
                continue
            # 加密账号未解锁时该行只剩 id/_rev（name 字段没了），解不开就
            # 直接跳过，别在下拉里显示「班级」这种误导性假名。
            if "name" not in c:
                continue
            def _so(v):
                try:
                    return int(v)
                except (TypeError, ValueError):
                    return 10 ** 9  # 未设顺序的排最后
            rows.append({"id": "%s|%s" % (tid, cid),
                         "name": c.get("name", "班级"),
                         "acc": str(tid),
                         "_so": _so(c.get("sort_order"))})
        # 2.1.7：班级下拉顺序统一按班级页设定的 sort_order（未设的排最后，再按名称兜底）。
        rows.sort(key=lambda x: (x["_so"], str(x["name"])))
        for r in rows:
            r.pop("_so", None)
        return self._json({"ok": True, "classes": rows})

    def _public_students(self, qs):
        """选定班级后拉该班名单（学号 + 姓名），供下拉选择。

        2026-11-25 账号隔离：必须带入口令牌 e，只查令牌账号的名单；
        复合键里的账号必须与令牌一致，改 class_id 前缀也串不到别的账号。
        只暴露“这个班有哪些人”，不暴露联系方式；密码仍然必须。
        """
        tid = ET.verify((qs.get("e") or [""])[0])
        if not tid:
            return self._json({"ok": False, "need_entry": True,
                               "msg": "请用老师发的新入口链接进入（旧链接已失效）"}, 403)
        want_acc, cid = _split_acc_id((qs.get("class_id") or [""])[0])
        if not cid:
            return self._json({"ok": False, "msg": "缺少 class_id"}, 400)
        # 复合键里的账号若带了，必须和令牌账号一致——否则学生手改 class_id
        # 就能读别的老师账号的名单。
        if want_acc and str(want_acc) != str(tid):
            return self._json({"ok": False, "msg": "这个班级不属于当前入口，请重新打开老师发来的链接"}, 403)
        out = []
        try:
            ACC.set_account(tid)
            # 同班级下拉：用该账号缓存的 FEK 读加密名单，读完恢复线程上下文。
            old_fek = ENC.get_fek()
            ENC.set_fek(ENC.cached_account_fek(tid))
            try:
                rows = STORE.table_rows("students")
            finally:
                ENC.set_fek(old_fek)
        except Exception:
            ENC.set_fek(None)
            rows = []
        for r in rows:
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
        # 2026-11-25 账号隔离：概览里的学生入口同样带账号令牌（与首页入口一致）。
        _acc = (self._session() or {}).get("account") or ACC.current_account()
        _entry = base + "/s/?e=" + urllib.parse.quote(ET.issue(_acc))
        return self._json({
            "ok": True, "rev": STORE.rev(),
            "school": CFG.get("school_name", ""),
            "entry_url": _entry,
            "online": SESS.online("student", ONLINE_WINDOW),
            "students": stats.get("students", 0),
            "open_homework": open_hw,
            "pending_leaves": len(leaves),
            "leaves": leaves[:20],
            "modules": PJ.module_flags(CFG),
            "visibility": CFG.get("visibility", {}),
            "teacher_username": (ACCOUNTS.current_teacher() or {}).get("username") or "teacher",
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
            # 导出当前账号视角的配置（含 teacher 旧镜像；口令是哈希不是明文）。
            # CFG 是账号级代理，直接 json.dumps 拿不到内容，要用 snapshot()。
            data["config"] = CFG.snapshot()
            # 2026-11-25 账号级配置：每个老师的工作区设置（模块开关 / 签到 /
            # 打字 / 设计 / 座位图等）也在 <账号>/config.json，换机恢复要一起搬，
            # 否则 t2 等账号的设置会退回出厂默认。default 的已包含在 config 里。
            data["account_configs"] = {
                tid: CFG.for_account(tid) for tid in ACCOUNTS.teacher_ids()
                if str(tid) != ACC.DEFAULT_TEACHER_ID}
            # 2.0.13：备份标记所属教师账号，并带上教师账号表（只有哈希）。
            # 恢复时按当前账号工作区写回，不会串到别的教师账号。
            data["account"] = ACC.current_account()
            data["accounts"] = ACCOUNTS.snapshot()
            data["creds"] = STORE.export_creds()
            data["sub"] = _sub_snapshot()                    # 提交系统：题目/花名册/提交记录
            # 2026-11-03（换机测试 F3/F4）：打字练习成绩与提交附件都不在 29 表里，
            # 以前不打包 —— 换机后「打字进度积分装扮全归零」「提交记录在、作品文件没了」。
            data["typing"] = _typing_snapshot()
            # 课堂活动（2026-11-03 方案 B）：活动/提交/附件已并入工作台自己的库，
            # 备份口径随之合一——活动数据也在本库里，导出导入一并处理。
            data["activity"] = _act_snapshot()
            # 测验/检测（2.0.38）：quiz.json 不在 29 表里，换设备必须单独搬，
            # 否则试卷、发布设置和成绩全丢。图片附件仍走资料包迁移。
            data["quiz"] = _quiz_snapshot()
            # H5 网页设计草稿（2.0.39）：学生写到一半的设计稿，换机不该丢
            data["design"] = _design_snapshot()
            # 像素画草稿（2.0.47）：同上，独立 pixel.json
            data["pixel"] = _pixel_snapshot()
            # 附件（学生作品）2.0.23 起默认不进全量备份：
            #   · 它们动辄几十上百 MB，是备份又大又慢的主因（导出要序列化 + 加密，
            #     导入要解密，纯 JS 只有 0.5MB/s）；
            #   · 真正的二进制迁移有更合适的通道 ——「导出资料包」走 zip，不占内存。
            # 需要连附件一起带的场景（老调用 / 手工排查）传 nofiles=0 即可，
            # 行为与以前完全一致；默认 nofiles=1 只回清单（files_meta），
            # 恢复后提示"附件未包含，请用资料包另行迁移"。
            nofiles = (qs.get("nofiles") or ["1"])[0] not in ("0", "false")
            files, meta = _sub_files_snapshot()
            if nofiles:
                meta = dict(meta or {})
                meta["omitted"] = True
                meta["count"] = len(files or {})
                try:
                    meta["bytes"] = sum(len(v) for v in (files or {}).values())
                except Exception:
                    meta["bytes"] = 0
            elif files:
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

    def _export_files_guard(self):
        root = self._cw_root()
        if not root or not os.path.isdir(root):
            return None, ({"ok": False,
                           "msg": "课件库目录不存在（还没有上传过资料）"}, 404)
        return root, None

    def _pack_courseware(self, jid, root, account=None):
        """后台打包：逐文件、逐块上报进度，供前端进度条实时显示。"""
        if account:
            ACC.set_account(account)
            ENC.set_fek(ENC.cached_account_fek(account))  # 后台线程显式注入 FEK
        bdir = os.path.join(DATA_DIR, "backup")
        os.makedirs(bdir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = os.path.join(bdir, "courseware-%s.zip" % stamp)
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if fn.startswith("."):
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(full)
                except Exception:
                    size = 0
                files.append((full, os.path.relpath(full, root).replace(os.sep, "/"), size))
        total = sum(x[2] for x in files)
        _pack_job_set(jid, phase="packing", total=total, filesTotal=len(files),
                      done=0, filesDone=0, percent=0)
        n = done = 0
        try:
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for full, rel, size in files:
                    try:
                        # 流式写入而不是 z.write()：大视频也能按 MB 报进度
                        with open(full, "rb") as src, z.open(rel, "w", force_zip64=True) as dst:
                            # 静态加密：导出的备份必须是明文（换设备/前端密码加密），
                            # 后台线程已注入该账号 FEK，WBF1 源流式解密写出。
                            head = src.read(4)
                            src.seek(0)
                            chunks = (ENC.dec_file_stream(src) if head == b"WBF1" else
                                      iter(lambda: src.read(1 << 20), b""))
                            for chunk in chunks:
                                if _pack_job_cancelled(jid):
                                    raise PackCancelled()
                                dst.write(chunk)
                                done += len(chunk)
                                _pack_job_set(jid, done=done,
                                              percent=min(99, int(done * 100 / total)) if total else 99,
                                              filesDone=n)
                        n += 1
                        _pack_job_set(jid, done=done, filesDone=n)
                    except PackCancelled:
                        raise
                    except Exception:
                        pass
        except PackCancelled:
            _pack_job_set(jid, phase="cancelled", error="已取消导出", percent=0)
            try:
                os.remove(out)          # 半成品 zip 一定删掉，不给下载留后门
            except Exception:
                pass
            return
        except Exception as e:
            _pack_job_set(jid, phase="error", error="打包失败：%s" % e, errorCode=500)
            try:
                os.remove(out)
            except Exception:
                pass
            return
        if not n:
            try:
                os.remove(out)
            except Exception:
                pass
            _pack_job_set(jid, phase="error", error="课件库是空的，没有可导出的文件", errorCode=404)
            return
        # 只留最近 3 个包，别把数据卷撑满
        try:
            olds = sorted([os.path.join(bdir, f) for f in os.listdir(bdir)
                           if f.startswith("courseware-") and f.endswith(".zip")])
            for p in olds[:-3]:
                os.remove(p)
        except Exception:
            pass
        name = "资料包-%s.zip" % stamp
        _pack_job_set(jid, phase="ready", path=out, name=name, files=n,
                      size=os.path.getsize(out), done=total, percent=100)

    def _export_files(self):
        """兼容老调用：同步打包并直接下载。前端新流程走 start/progress/download。"""
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        if not self._data_unlocked():
            return self._json({"ok": False, "msg": "请先验证教师账号密码",
                               "need_password": True}, 401)
        root, err = self._export_files_guard()
        if err:
            return self._json(*err)
        jid = _pack_job_new("export-sync", self._session(), "")
        self._pack_courseware(jid, root, (self._session() or {}).get("account"))
        j = _pack_job_get(jid, "")
        PACK_JOBS.pop(jid, None)
        if j["phase"] == "error":
            return self._json({"ok": False, "msg": j.get("error") or "打包失败"},
                              j.get("errorCode") or 500)
        self._serve_abs(j["path"], download=True,
                        ctype="application/zip", name=j["name"])
        return None

    def _export_files_start(self):
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        if not self._data_unlocked():
            return self._json({"ok": False, "msg": "请先验证教师账号密码",
                               "need_password": True}, 401)
        root, err = self._export_files_guard()
        if err:
            return self._json(*err)
        s = self._session()
        jid = _pack_job_new("export", s, A.parse_cookie(self.headers.get("Cookie")))
        threading.Thread(target=self._pack_courseware,
                         args=(jid, root, (s or {}).get("account")),
                         name="pack-courseware", daemon=True).start()
        return self._json({"ok": True, "job": jid})

    def _pack_cancel(self):
        """取消后台打包/解压任务（2026-11-25）：只认本会话的任务。"""
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        body = self._read_json() or {}
        jid = str(body.get("job") or "")
        owner = A.parse_cookie(self.headers.get("Cookie"))
        j, err = _pack_job_cancel(jid, owner)
        if err:
            return self._json({"ok": False, "msg": err}, 400)
        return self._json({"ok": True, "msg": "已取消，正在清理临时文件"})

    def _pack_job_reply(self, qs, kind, need_ready=False):
        if not self._is_teacher():
            return None, ({"ok": False, "msg": "未登录"}, 401)
        j = _pack_job_get((qs.get("job") or [""])[0],
                          A.parse_cookie(self.headers.get("Cookie")))
        if not j or j.get("kind") != kind:
            return None, ({"ok": False, "msg": "任务不存在或已过期，请重新操作"}, 404)
        if need_ready and j.get("phase") != "ready":
            return None, ({"ok": False, "msg": "资料包还没准备好"}, 409)
        return j, None

    def _export_files_progress(self, qs):
        j, err = self._pack_job_reply(qs, "export")
        if err:
            return self._json(*err)
        return self._json({"ok": True, "status": j["phase"],
                           "percent": j.get("percent") or 0,
                           "done": j.get("done") or 0, "total": j.get("total") or 0,
                           "filesDone": j.get("filesDone") or 0,
                           "filesTotal": j.get("filesTotal") or 0,
                           "error": j.get("error") or ""})

    def _export_files_download(self, qs):
        j, err = self._pack_job_reply(qs, "export", need_ready=True)
        if err:
            return self._json(*err)
        self._serve_abs(j["path"], download=True,
                        ctype="application/zip", name=j["name"])
        return None

    def _update_offline(self):
        """离线安装包上传（2026-11-20）：服务端完全没有外网时的更新途径。

        老师在能上网的电脑下载 dist/ 里的发行包 → U 盘/共享盘拷进内网 →
        教师端「系统设置 → 软件更新 → 离线安装」上传。服务端只收 .zip，
        换装、备份、自动回滚与在线更新走同一段逻辑（updater._install_zip），
        区别只是完整性校验用 zip 自带的 CRC32 代替 manifest 里的 sha256。
        """
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "未登录"}, 401)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return self._json({"ok": False, "msg": "没有收到安装包内容"}, 400)
        if length > 800 * 1024 * 1024:
            return self._json({"ok": False, "msg": "安装包太大（上限 800MB）"}, 413)
        # 文件名走自定义头：HTTP 头不能放非 ASCII，前端 encodeURIComponent 过、这里还原
        name = ""
        try:
            name = urllib.parse.unquote(str(self.headers.get("X-WB-Filename") or ""))
        except Exception:
            name = ""
        name = os.path.basename(name.replace("\\", "/").strip())[:120]
        if not name.lower().endswith(".zip"):
            return self._json({"ok": False, "msg": "只接受 .zip 格式的离线安装包"}, 400)
        dst = os.path.join(DATA_DIR, "update", "offline-upload.zip")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        remain = length
        try:
            with open(dst, "wb") as f:
                while remain > 0:
                    chunk = self.rfile.read(min(1 << 20, remain))
                    if not chunk:
                        break
                    f.write(chunk)
                    remain -= len(chunk)
        except Exception as e:
            return self._json({"ok": False, "msg": "接收安装包失败：%s" % e}, 500)
        if remain > 0:
            return self._json({"ok": False,
                               "msg": "安装包上传不完整（差 %d 字节），请重试。" % remain}, 400)
        r = UPD.install_local(dst, name)
        STORE.audit("teacher", "update-offline", "%s %s" % (name, r.get("msg") or ""))
        return self._json(r, 200 if r.get("ok") else 400)

    def _extract_courseware(self, jid, tmp, root, account=None):
        """把资料包解压回课件库；逐块上报进度（打包/解压共用一套进度任务）。"""
        if account:
            ACC.set_account(account)
        n = skipped = done = 0
        try:
            with zipfile.ZipFile(tmp) as z:
                infos = z.infolist()
                total = sum(i.file_size for i in infos if not i.is_dir())
                _pack_job_set(jid, phase="extracting", total=total,
                              filesTotal=len(infos), done=0, filesDone=0, percent=0)
                for info in infos:
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
                    copied = 0
                    if account:
                        ENC.set_fek(ENC.cached_account_fek(account))
                    if ENC.get_fek():
                        # 导出包是明文；导入到已加密账号时按该账号 FEK 流式加密落盘。
                        tmp_dest = dest + ".enc.tmp"
                        state = {"n": 0}

                        class _Counting(object):
                            def read(self, size=-1):
                                if _pack_job_cancelled(jid):
                                    raise PackCancelled()
                                chunk = src.read(min(size if size and size > 0 else (1 << 20), 1 << 20))
                                if chunk:
                                    state["n"] += len(chunk)
                                    _pack_job_set(jid,
                                                  done=done + state["n"],
                                                  percent=min(99, int((done + state["n"]) * 100 / total)) if total else 99,
                                                  filesDone=n)
                                return chunk

                        with z.open(info) as src, open(tmp_dest, "wb") as dst:
                            ENC.enc_file_stream_limited(_Counting(), dst, info.file_size)
                        os.replace(tmp_dest, dest)
                        copied = state["n"]
                    else:
                        with z.open(info) as src, open(dest, "wb") as dst:
                            while True:
                                if _pack_job_cancelled(jid):
                                    raise PackCancelled()
                                chunk = src.read(1 << 20)
                                if not chunk:
                                    break
                                dst.write(chunk)
                                copied += len(chunk)
                                _pack_job_set(jid,
                                              done=done + copied,
                                              percent=min(99, int((done + copied) * 100 / total)) if total else 99,
                                              filesDone=n)
                    done += max(copied, info.file_size)
                    n += 1
                    _pack_job_set(jid, done=done, filesDone=n)
        except PackCancelled:
            # 已经解压出来的文件保留（同名覆盖语义），但不再继续写剩下的；
            # 临时 zip 在 finally 里删掉，任务状态标 cancelled。
            _pack_job_set(jid, phase="cancelled", error="已取消导入", percent=0)
            return
        except Exception as e:
            _pack_job_set(jid, phase="error", error="解压失败：%s" % e, errorCode=400)
            return
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
        _pack_job_set(jid, phase="done", percent=100, done=done, filesDone=n,
                      result={"ok": True, "files": n, "skipped": skipped, "root": root})

    def _import_files(self, async_mode=False):
        """接收课程资料包（zip），解压回课件库目录。
        ?async=1 时后台解压并返回 job id，前端轮询进度；老调用保持同步。"""
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "只有教师端可以导入"}, 403)
        if not self._data_unlocked():
            return self._json({"ok": False, "msg": "请先验证教师账号密码",
                               "need_password": True}, 403)
        s = self._session()
        owner = A.parse_cookie(self.headers.get("Cookie"))
        jid = _pack_job_new("import" if async_mode else "import-sync", s, owner)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            PACK_JOBS.pop(jid, None)
            return self._json({"ok": False, "msg": "没有收到文件内容"}, 400)
        if length > 2 * 1024 * 1024 * 1024:
            PACK_JOBS.pop(jid, None)
            return self._json({"ok": False, "msg": "资料包太大（上限 2GB）"}, 413)
        root = self._cw_root()
        if not root:
            PACK_JOBS.pop(jid, None)
            return self._json({"ok": False, "msg": "课件库目录不可用"}, 500)
        os.makedirs(root, exist_ok=True)
        tmp = os.path.join(DATA_DIR, "backup",
                           "_import-%s.zip" % jid if async_mode else "_import-files.zip")
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
            PACK_JOBS.pop(jid, None)
            try:
                os.remove(tmp)
            except Exception:
                pass
            return self._json({"ok": False, "msg": "接收文件失败：%s" % e}, 500)
        if remain > 0:
            PACK_JOBS.pop(jid, None)
            try:
                os.remove(tmp)
            except Exception:
                pass
            return self._json({"ok": False, "msg": "文件上传不完整，请重试"}, 400)
        account = (s or {}).get("account")
        if async_mode:
            threading.Thread(target=self._extract_courseware,
                             args=(jid, tmp, root, account),
                             name="extract-courseware", daemon=True).start()
            return self._json({"ok": True, "job": jid})
        self._extract_courseware(jid, tmp, root, account)
        j = _pack_job_get(jid, owner)
        PACK_JOBS.pop(jid, None)
        if j["phase"] == "error":
            return self._json({"ok": False, "msg": j.get("error") or "解压失败"},
                              j.get("errorCode") or 400)
        return self._json(j["result"])

    def _import_files_progress(self, qs):
        j, err = self._pack_job_reply(qs, "import")
        if err:
            return self._json(*err)
        result = {"ok": True, "status": j["phase"],
                  "percent": j.get("percent") or 0,
                  "done": j.get("done") or 0, "total": j.get("total") or 0,
                  "filesDone": j.get("filesDone") or 0,
                  "filesTotal": j.get("filesTotal") or 0,
                  "error": j.get("error") or ""}
        if j["phase"] == "done":
            result["result"] = j.get("result")
        return self._json(result)

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
        # 资料包导出任务：无请求体，但不能落进 _read_json 的 JSON 解析
        if path == "/api/v2/teacher/export-files/start":
            return self._export_files_start()
        if path == "/api/v2/teacher/pack-cancel":
            return self._pack_cancel()
        # 课程资料包是 zip 二进制，必须在这里截住：下面 _read_json 会把它当 JSON 读坏
        if path == "/api/v2/admin/import-files":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return self._import_files((qs.get("async") or [""])[0] in ("1", "true"))
        # 离线安装包同样是 17MB+ 的二进制：不能让它进 _read_json（会按 JSON 解析
        # 并撞上体积上限），必须在最前面截住（2026-11-20）。
        if path == "/api/v2/update/offline":
            return self._update_offline()
        # 活动教学资料是 multipart 二进制，同理必须在 _read_json 之前截住
        if path == "/api/v2/teacher/activity/upload":
            return self._act_upload()
        body = self._read_json()
        if body is None:
            return          # 请求体过大，_read_json 已经回过 413 了
        # 测验/检测（2.0.38）：试卷 CRUD / 发布 / 学生提交 / 人工批阅
        if path.startswith("/api/v2/quiz/"):
            r = _quiz_api(self).post(path, body)
            return r if r is not None else self._json({"ok": False, "msg": "未知接口"}, 404)
        # 数据大屏：只清「模拟 / 测试提交」（2026-11-17）
        # 大屏的「🧪 测试提交」窗口上有这个按钮：老师课前验完链路一键清干净，
        # 绝不会像「清空全部提交」那样把真实数据一起删掉。
        if path == "/api/v2/act-test-clear":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            q2 = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            task = str((q2.get("task") or [body.get("task") or ""])[0] or "").strip()
            if not task:
                return self._json({"ok": False, "msg": "缺少 task"}, 400)
            ids = ACT.delete_test_submissions(task)
            if ids:
                STORE.audit("teacher", "activity-test-clear",
                            "%s 清掉 %d 条测试数据" % (task, len(ids)))
                notify()
            return self._json({"ok": True, "deleted": len(ids),
                               "message": ("已清掉 %d 条测试数据" % len(ids))
                               if ids else "这个活动没有测试数据"})
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
        if path == "/api/v2/teacher/activity/normalize-archive":
            # 2026-11-06：老数据里平铺在班级目录、认不出提交者的作品，
            # 按「提交记录里的原文件名 → 学号/姓名/班级」反查后移进学生文件夹。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            mapping = {}
            try:
                with ACT.lock:
                    rows = ACT.conn.execute(
                        "SELECT f.orig, s.sid, s.name, s.cls, s.data FROM act_file f "
                        "LEFT JOIN act_submission s ON s.id=f.sub_id").fetchall()
                for r in rows:
                    orig = str(r[0] or "").strip()
                    if not orig:
                        continue
                    no = str(r[1] or "").strip()
                    nm = str(r[2] or "").strip()
                    cls = str(r[3] or "").strip()
                    try:
                        d = ACT._dec_payload(r[4] or "{}")
                        if isinstance(d, dict):
                            no = no or str(d.get("学号") or d.get("student_no") or "").strip()
                            nm = nm or str(d.get("姓名") or d.get("name") or "").strip()
                            cls = cls or str(d.get("班级") or d.get("cls") or "").strip()
                    except Exception:
                        pass
                    mapping.setdefault(orig.lower(), {"class": cls, "no": no, "student": nm})
            except Exception as e:
                return self._json({"ok": False, "msg": "读取提交记录失败：%s" % e}, 500)
            moved, skipped, details = 0, [], []
            try:
                courses, _err = LEG.CW.list_courses()
            except Exception:
                courses = []
            for c in (courses or []):
                name = str(c.get("name") or "")
                if not name:
                    continue
                res = LEG.CW.normalize_student_dirs(name, mapping)
                n = len(res.get("moved") or [])
                moved += n
                skipped.extend(res.get("skipped") or [])
                if n:
                    details.append({"course": name, "n": n})
            return self._json({"ok": True, "moved": moved, "skipped": skipped[:20],
                               "details": details,
                               "message": "已按「学号-姓名」整理 %d 个文件" % moved})

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
        if path == "/api/v2/update/cancel":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = UPD.cancel()
            STORE.audit("teacher", "update-cancel", r.get("msg", ""))
            return self._json(r, 200 if r.get("ok") else 400)
        if path == "/api/v2/update/rollback":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = UPD.rollback()
            STORE.audit("teacher", "update-rollback", r.get("msg", ""))
            return self._json(r, 200 if r.get("ok") else 400)
        if path == "/api/v2/teacher/checkin-config":
            # 教师端「课堂签到」设置：整体开关 + 手动签到时间（空 = 按课表当节）
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            c = CFG["checkin"]
            if "enabled" in body:
                c["enabled"] = bool(body.get("enabled"))
            if "manual_time" in body:
                t = str(body.get("manual_time") or "").strip()
                if t:
                    try:
                        a = t.split(":")
                        if len(a) != 2:
                            raise ValueError()
                        h, m = int(a[0]), int(a[1])
                        if not (0 <= h <= 23 and 0 <= m <= 59):
                            raise ValueError()
                        t = "%02d:%02d" % (h, m)
                    except Exception:
                        return self._json({"ok": False, "msg": "签到时间要是 HH:MM 格式"}, 400)
                c["manual_time"] = t
            CFG.save()
            CFG_VER[0] += 1
            notify()                       # 学生端 SSE 立即感知开关变化
            STORE.audit("teacher", "checkin-config",
                        "enabled=%s time=%s" % (c.get("enabled"), c.get("manual_time") or "课表"))
            return self._json(dict({"ok": True}, **self._checkin_cfg()))
        if path == "/api/v2/teacher/school":
            # 学校名称：只写入 CFG["school_name"]，创意宠物 designer.school 从这里取。
            # 教师会话守卫；长度限 60，去掉首尾空白和换行。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            v = " ".join(str(body.get("school") or "").split())[:60]
            CFG["school_name"] = v
            CFG.save()
            CFG_VER[0] += 1
            notify()
            STORE.audit("teacher", "school", "name=%s" % (v or "（清空）"))
            return self._json({"ok": True, "school": v})
        if path == "/api/v2/teacher/nickname":
            # 教师称呼昵称：账号级写入 accounts.json（不是全局 CFG，多账号互不覆盖）。
            # 前端设置页保存昵称走这里，取代原来只存本机 localStorage 的老逻辑。
            s = self._session()
            if not s or s["role"] != "teacher":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = ACCOUNTS.set_nickname(s.get("account"), body.get("nickname"))
            if not r.get("ok"):
                return self._json(r, 400)
            return self._json(r)
        if path == "/api/v2/update/config":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            u = CFG["update"]
            # 更新源（2026-11-17 放开）：默认仍是官方 Gitee 仓库；
            # 内网 443 出不去 / 走镜像的场景允许填一个 http(s) 自定义源，
            # 填了非法值一律清空，防止填错地址导致更新失败。令牌依旧不收。
            _raw_url = str(body.get("url") or "").strip()
            _ok_url = _raw_url if re.match(r"^https?://\S+$", _raw_url, re.I) else ""
            # 2026-11-20：老前端会把默认地址预填进「自定义源」再保存，落盘后
            # 就成了「老师指定单源」，多源回退失效（Gitee 403 就没退路）。
            # 存量配置里这类等价默认源的地址，保存时一律清成空 = 走默认多源。
            if _ok_url and UPD._is_default_source(_ok_url):
                _ok_url = ""
            u["url"] = _ok_url
            u.pop("token", None)
            if "auto" in body:
                u["auto"] = bool(body.get("auto"))
            if "interval_hours" in body:
                try:
                    u["interval_hours"] = max(1, min(24 * 30, int(body.get("interval_hours"))))
                except Exception:
                    pass
            CFG.save()
            CFG_VER[0] += 1
            notify()
            STORE.audit("teacher", "update-config", "url=%s auto=%s" % (u.get("url") or "默认", u.get("auto")))
            return self._json({"ok": True})
        if path == "/api/v2/update/restart":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            STORE.audit("teacher", "update-restart", "version=%s" % VERSION)
            # 重启服务分两条路（2026-11-20 修「离线包更新后服务起不来」）：
            #   ① 有启动器看着（launch.py 会注入 WB_SUPERVISED=1 + WB_RESTART_FLAG）：
            #      落一个标记文件后**正常退出**，由启动器把新代码重新拉起来。
            #   ② 容器 / 计划任务直跑（没有启动器）：沿用 os.execv 换进程映像。
            #
            # ①为什么不能再用 execv：Windows 上 os.execv 是「起新进程 + 退旧进程」，
            # 旧进程一退，启动器那个 Popen.poll() 就返回 0 → 它以为服务崩了 →
            # 打印「已停止。」并 sys.exit；而新进程继承的正是启动器那条 PIPE，
            # 父进程一退出管道读端就关了，新进程启动时写一行日志立刻抛
            # WinError 232（管道的另一端已关闭）→ 新服务当场崩。
            # 牛哥在不联网的服务器上离线更新，看到的就是「服务已停止、新服务起不来」。
            flag = (os.environ.get("WB_RESTART_FLAG") or "").strip()
            supervised = (os.environ.get("WB_SUPERVISED") == "1") and bool(flag)
            if supervised:
                try:
                    with open(flag, "w", encoding="utf-8") as f:
                        f.write("%d\n" % int(time.time()))
                except Exception:
                    supervised = False        # 标记写不下去就退回 execv，别把服务卡死
            if supervised:
                try:
                    _heal_exec_bits()
                except Exception:
                    pass
                # 必须先把响应发出去再安排退出，否则前端拿到的是「连接被重置」
                self._json({"ok": True, "msg": "正在重启，几秒后自动恢复"})
                try:
                    self.wfile.flush()
                except Exception:
                    pass
                # ⚠️ 这里**不能**调 server_close()：Windows 上关掉监听 socket 后
                # 主线程的 serve_forever 会抛 OSError 10038（非套接字操作），
                # 进程以退出码 1 结束 → 启动器判定「崩了」，照样不打重启标记。
                # 进程整体 os._exit 时 OS 会回收所有句柄，端口自然放开。

                def _bye():
                    time.sleep(0.45)              # 留时间让上面的响应真的发完
                    try:
                        sys.stdout.flush()
                        sys.stderr.flush()
                    except Exception:
                        pass
                    os._exit(0)                   # 退出码 0 = 告诉启动器「这是重启，不是崩了」
                threading.Thread(target=_bye, daemon=True, name="restart-exit").start()
                return
            # 原地重启（execv 换进程映像，端口由新进程重新绑定）。
            # 前端发起后按「连接断开 → 轮询 /api/v2/state 恢复」处理。
            try:
                # ⚠️ Windows 上 os.execv 不是 Unix 那种「原地换映像」，而是
                # 「先 CreateProcess 起新进程 → 再退出旧进程」，两个进程会有
                # 一段并存窗口。新进程启动时 main() 会走 PORTX.pick_port 探测
                # 端口：此刻旧进程还在监听 → is_mine() 返回 True → 判定
                # 「已有一个本工作台实例在运行，不重复启动」→ 新进程直接
                # return 退出；随后旧进程也退了 → 服务彻底没了。这就是
                # 「自动更新并重启后起不来」的根因（2026-11-05 牛哥反馈）。
                # 处理：① 先主动关掉监听 socket，让端口立刻空出来；
                #       ② 给新进程打一个重启标记，端口若因 TIME_WAIT 短暂
                #          占用也不当作「重复启动」，见 main() 里的 restarting。
                try:
                    self.server.server_close()
                except Exception:
                    pass
                # 顺手把更新换装弄丢的可执行位补回来，免得重启完下次双击打不开
                try:
                    _heal_exec_bits()
                except Exception:
                    pass
                os.environ["WB_RESTARTING"] = "1"
                sys.stdout.flush()
                sys.stderr.flush()
                os.execv(sys.executable, [sys.executable] + list(sys.argv))
            except Exception as e:
                return self._json({"ok": False,
                                   "msg": "自动重启没成功（%s）。请手动重启：Windows 计划任务/直接关掉重开启动脚本；"
                                          "Docker 用 docker compose restart workbench。" % e}, 500)
        if path == "/api/v2/auth/setup":
            # 2.0.13：取消教师端账号注册。教师账号一律由管理员在
            # /admin/ 控制台创建；管理员账号在部署时用环境变量配置。
            # 路径保留只是为了让旧页面/旧书签得到明确说明，不再写任何账号。
            return self._json({"ok": False,
                               "msg": "账号注册已关闭：教师账号由管理员统一创建，请联系管理员。"}, 403)
        if path == "/api/v2/auth/teacher":
            ip = A.client_ip(self)
            if not A.rate_ok("t:" + ip, 20, 60):
                return self._json({"ok": False, "msg": "尝试太频繁，请 1 分钟后再试"}, 429)
            # role: admin（默认登录页） / teacher（切换后的教师登录）
            role = str(body.get("role") or "teacher").strip()
            if role not in ("admin", "teacher"):
                role = "teacher"
            user = str(body.get("username") or "").strip()
            pwd = str(body.get("password") or "")
            ok_login, acc_id, acc = ACCOUNTS.verify(role, user, pwd)
            if not ok_login:
                STORE.audit("?", "login-fail", "%s role=%s user=%s" % (ip, role, user))
                return self._json({"ok": False, "msg": "账号或密码不正确"}, 401)
            hours = CFG["teacher"].get("session_hours", 12)
            tok = SESS.create(role, hours, account=acc_id)
            ACC.set_account(acc_id)
            # 2026-11-25 静态加密：登录时用明文密码解出 FEK，存会话内存态。
            # 服务重启 / 会话过期 → FEK 随之消失，必须重新登录。
            if role == "teacher":
                fek = ACCOUNTS.enc_unlock(acc_id, pwd)
                if fek:
                    SESS.set(tok, "fek", fek)
                    ENC.cache_account_fek(acc_id, fek)   # 常驻账号缓存，供该班学生解密下发文件
            STORE.audit(role, "login", ip)
            # 教师账号不再提示改初始密码；只有管理员账号保留引导。
            # 学生端走独立 suggest_pwd，与这里无关。
            _need_setup = ACCOUNTS.need_setup("admin", acc_id) if role == "admin" else False
            return self._set_session_headers(
                tok, extra={"username": str((acc or {}).get("username") or user),
                            "role": role, "account": acc_id,
                            "need_setup": _need_setup,
                            "landing": "/admin/" if role == "admin" else "/teacher/"},
                hours=hours)
        if path == "/api/v2/auth/student":
            ip = A.client_ip(self)
            # 机房/代理上网时同一个出口 IP 后面可能是整班 45 人，
            # IP 维度只做“异常刷接口”的粗拦截；防爆破靠下面的学号维度。
            if not A.rate_ok("s:" + ip, 120, 60):
                return self._json({"ok": False, "msg": "登录请求太多，请 1 分钟后再试"}, 429)
            # 2026-11-25 账号隔离：登录必须带入口令牌 e，只在令牌账号的
            # 工作区里找学生。改 class_id 账号前缀 / 换学号都串不到别的老师。
            tid = ET.verify(body.get("e"))
            if not tid:
                return self._json({"ok": False, "need_entry": True,
                                   "msg": "请用老师发的新入口链接进入（旧链接已失效）"}, 401)
            want_acc, cls_id = _split_acc_id(body.get("class_id"))
            if want_acc and str(want_acc) != str(tid):
                return self._json({"ok": False, "msg": "这个入口不是你这个班的，请重新打开老师发来的链接"}, 403)
            sid = body.get("student_id")
            no = str(body.get("no") or "").strip()
            pwd = str(body.get("password") or "")
            # 与名单接口一致：登录请求是匿名的（线程 FEK=None）。已加密账号
            # （如 WB64.）读 students / student_row 必须注入该账号缓存的 FEK，
            # 否则读到解不开的空壳 → 匹配不到学生 → "没找到这个学生"。
            # 教师登录后 FEK 进账号缓存，这里取用；整个登录处理段保持，
            # 保证 auto_checkin 里的 student_row 也能解密。_bind_account 每
            # 请求开头都会重置 FEK，所以不会串味。
            ENC.set_fek(ENC.cached_account_fek(tid))
            hit = None
            found_acc = tid
            try:
                ACC.set_account(tid)
                rows = STORE.table_rows("students")
            except Exception:
                rows = []
            for r in rows:
                # 2.0.71 多班：兼属班也能登录。主班 class_id 或 class_ids
                # 任一命中即视为本班学生，档案仍只有一份（不复制）。
                if cls_id not in (None, ""):
                    in_cls = str(r.get("class_id")) == str(cls_id)
                    if not in_cls:
                        in_cls = any(
                            str(x) == str(cls_id)
                            for x in (r.get("class_ids") or []))
                    if not in_cls:
                        continue
                if sid not in (None, "") and str(r.get("id")) == str(sid):
                    hit = r
                    break
                if sid in (None, "") and no and str(r.get("student_no") or "").strip() == no:
                    hit = r
                    break
            ACC.set_account(found_acc or ACC.DEFAULT_TEACHER_ID)
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
                              student_id=hit.get("id"), account=found_acc)
            # 单设备登录（2026-11-17 牛哥要求）：
            # 同一名学生只允许一个设备在线 —— 新设备登录成功，其它设备立刻下线。
            # 学生共用机房里最常见的场景就是「登错账号」，让他换一台机器接着登，
            # 旧机器必须自己退回登录页，否则两台上各自签到/提交，数据全乱。
            _kicked = 0
            try:
                _kicked = SESS.drop_student(hit.get("id"), account=found_acc,
                                            except_token=tok,
                                            reason="这个账号已在另一台设备登录，本设备已退出")
                if _kicked:
                    notify()          # SSE 立刻醒，把 kick 事件推给旧设备
            except Exception:
                _kicked = 0
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
            # 2.1.8：用全班初始密码登录（且本人没设独立密码）时，前端弹
            # 「建议修改密码」——不是强制改密（那是 force_change/must_change），
            # 只是提醒，学生可点「稍后」先进去。
            _init_pwd = str(CFG["student"].get("initial_password") or "123456")
            _suggest = bool(not STORE.get_cred(hit.get("id")) and pwd == _init_pwd)
            return self._set_session_headers(tok, extra={
                "must_change": bool(must), "name": hit.get("name"),
                "suggest_pwd": _suggest, "checkin": auto_ck or None},
                hours=CFG["student"].get("session_hours", 8))
        if path == "/api/v2/auth/logout":
            tok = A.parse_cookie(self.headers.get("Cookie"))
            SESS.drop(tok)
            return self._set_session_headers("", expire=True)

        # ---------------- 密保问题：忘记密码时自助改密（按账号定位） ----------------
        if path == "/api/v2/auth/qa/question":
            ip = A.client_ip(self)
            if not A.rate_ok("qa:" + ip, 20, 60):
                return self._json({"ok": False, "msg": "请求太频繁，请 1 分钟后再试"}, 429)
            user = str(body.get("username") or "").strip()
            t = (ACCOUNTS.teacher_by_username(user) if user
                 else ACCOUNTS.teacher(ACC.DEFAULT_TEACHER_ID))
            if not t:
                return self._json({"ok": False, "msg": "账号不存在"}, 401)
            sec = security_of(t["id"])
            ok_qa = bool(sec["question"] and sec["answer_hash"])
            return self._json({
                "ok": True, "has_qa": ok_qa, "username": t["username"],
                "account": t["id"],
                "question": sec["question"] if ok_qa else "",
                "msg": "" if ok_qa else "这个账号还没设置密保问题，请管理员重置密码",
            })
        if path == "/api/v2/auth/qa/verify":
            ip = A.client_ip(self)
            if not A.rate_ok("qav:" + ip, 8, 60):
                return self._json({"ok": False, "msg": "尝试太频繁，请 1 分钟后再试"}, 429)
            user = str(body.get("username") or "").strip()
            t = (ACCOUNTS.teacher_by_username(user) if user
                 else ACCOUNTS.teacher(ACC.DEFAULT_TEACHER_ID))
            if not t or not has_qa(t["id"]):
                return self._json({"ok": False, "msg": "没有设置密保问题"}, 400)
            ans = str(body.get("answer") or "")
            if not A.verify_answer(ans, security_of(t["id"])["answer_hash"]):
                STORE.audit("?", "qa-fail", ip)
                return self._json({"ok": False, "msg": "密保答案不正确"}, 401)
            tok = new_ticket(t["id"])
            STORE.audit("teacher", "qa-verify", ip)
            return self._json({"ok": True, "ticket": tok, "expires_in": 600})
        if path == "/api/v2/auth/qa/reset":
            ip = A.client_ip(self)
            if not A.rate_ok("qar:" + ip, 10, 60):
                return self._json({"ok": False, "msg": "请求太频繁，请 1 分钟后再试"}, 429)
            pwd = str(body.get("password") or "")
            bad = A.weak_password(pwd)
            if bad:
                return self._json({"ok": False, "msg": bad}, 400)
            acc = take_ticket(body.get("ticket"))
            if not acc:
                return self._json({"ok": False,
                                   "msg": "验证已过期，请重新回答密保问题"}, 400)
            # 2026-11-25 静态加密：启用文件加密的账号忘记密码必须走恢复码，
            # 不能只凭密保重置 —— 否则 FEK 解不开、账号下所有文件永久锁死。
            if ACCOUNTS.enc_requires_old(acc):
                return self._json({"ok": False,
                                   "msg": "该账号已启用文件加密，忘记密码请用恢复码重置（登录页「用恢复码重置」）"}, 400)
            ACCOUNTS.set_password(acc, pwd)
            ACC.set_account(acc)
            # 旧密码可能已泄露：把所有教师会话踢下线，再给本机发新会话
            SESS.drop_role("teacher")
            notify_cfg()
            STORE.audit("teacher", "reset-password-qa", ip)
            hours = CFG["teacher"].get("session_hours", 12)
            tok = SESS.create("teacher", hours, account=acc)
            t = ACCOUNTS.teacher(acc) or {}
            return self._set_session_headers(
                tok, extra={"username": t.get("username") or "teacher", "via": "qa"},
                hours=hours)

        if path == "/api/v2/auth/recover":
            """2026-11-25 静态加密：忘记密码时用恢复码重置（解包 FEK + 重设密码）。"""
            ip = A.client_ip(self)
            if not A.rate_ok("rc:" + ip, 10, 60):
                return self._json({"ok": False, "msg": "尝试太频繁，请 1 分钟后再试"}, 429)
            user = str(body.get("username") or "").strip()
            code = str(body.get("recovery_code") or "").strip()
            pwd = str(body.get("password") or "")
            bad = A.weak_password(pwd)
            if bad:
                return self._json({"ok": False, "msg": bad}, 400)
            t = ACCOUNTS.teacher_by_username(user)
            if not t:
                return self._json({"ok": False, "msg": "账号或恢复码不正确"}, 401)
            tid = t.get("id")
            if not ACCOUNTS.enc_recover(tid, code, pwd):
                STORE.audit("?", "recover-fail", "%s user=%s" % (ip, user))
                return self._json({"ok": False, "msg": "账号或恢复码不正确"}, 401)
            ACC.set_account(tid)
            SESS.drop_role("teacher")
            notify_cfg()
            STORE.audit("teacher", "recover-password", "%s user=%s" % (ip, user))
            hours = CFG["teacher"].get("session_hours", 12)
            tok = SESS.create("teacher", hours, account=tid)
            fek = ACCOUNTS.enc_unlock(tid, pwd)
            if fek:
                ENC.cache_account_fek(tid, fek)
            # FEK 只能留在服务端会话 / 账号缓存里，绝不能随响应头或 JSON 返回前端。
            return self._set_session_headers(
                tok, extra={"username": t.get("username") or user, "via": "recovery"},
                hours=hours)

        if path == "/api/v2/teacher/enc/migrate-status":
            """存量加密迁移进度（教师本人，只读）。"""
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            st = MIG.status(WS, ACC.current_account())
            st["ok"] = True
            return self._json(st)

        if path == "/api/v2/teacher/enc/init":
            """首次启用文件加密：验当前密码 → 生成 FEK + 恢复码，恢复码仅此一次返回。"""
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            tid = ACC.current_account()
            pwd = str(body.get("password") or "")
            rec = ACCOUNTS.enc_init(tid, pwd)
            if not rec:
                return self._json({"ok": False,
                                   "msg": "当前密码不正确，或加密已启用"}, 401)
            fek = ACCOUNTS.enc_unlock(tid, pwd)
            if fek:
                SESS.set(A.parse_cookie(self.headers.get("Cookie")), "fek", fek)
                ENC.cache_account_fek(tid, fek)
            STORE.audit("teacher", "enc-init", tid)
            return self._json({"ok": True, "recovery_code": rec})

        if path == "/api/v2/teacher/enc/recovery/reset":
            """重置恢复码（用户定规：账号密码 + 密保问题双重验证）。"""
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            tid = ACC.current_account()
            t = ACCOUNTS.teacher(tid) or {}
            pwd = str(body.get("password") or "")
            qa = str(body.get("answer") or "")
            if not A.verify_password(pwd, t.get("password_hash")):
                return self._json({"ok": False, "msg": "账号密码不正确"}, 401)
            sec = t.get("security") or {}
            if not (sec.get("question") and sec.get("answer_hash")):
                return self._json({"ok": False,
                                   "msg": "该账号未设置密保问题，请先在账号安全里设置"}, 400)
            if not A.verify_answer(qa, sec.get("answer_hash")):
                return self._json({"ok": False, "msg": "密保答案不正确"}, 401)
            rec = ACCOUNTS.enc_reset_recovery(tid, pwd)
            if not rec:
                return self._json({"ok": False, "msg": "重置失败，请重试"}, 400)
            STORE.audit("teacher", "enc-recovery-reset", tid)
            return self._json({"ok": True, "recovery_code": rec})

        if path == "/api/v2/teacher/security":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            t = ACCOUNTS.current_teacher() or {}
            if t.get("password_hash"):
                if not A.verify_password(str(body.get("password") or ""),
                                         t.get("password_hash")):
                    return self._json({"ok": False, "msg": "当前密码不正确"}, 401)
            q = str(body.get("question") or "").strip()
            a = str(body.get("answer") or "")
            if q and len(q) < 4:
                return self._json({"ok": False, "msg": "密保问题至少 4 个字"}, 400)
            if q and not a.strip():
                return self._json({"ok": False, "msg": "请填写密保答案"}, 400)
            ACCOUNTS.set_security(ACC.current_account(), q[:60],
                                  A.hash_answer(a) if q else "")
            notify_cfg()
            STORE.audit("teacher", "security-update",
                        (q[:30] if q else "清除密保问题"))
            return self._json({"ok": True, "has_qa": bool(q), "question": q})
        if path == "/api/v2/teacher/data-password-verify":
            """2.1.14：资料包导入/导出前的二次验证。
            用当前教师账号密码（不是备份口令），验证通过只在本次会话开 180 秒窗口；
            失败限流，不写密码明文进审计。"""
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            ip = A.client_ip(self)
            if not A.rate_ok("dw:" + ip, 8, 300):
                return self._json({"ok": False, "msg": "尝试太频繁，请 5 分钟后再试"}, 429)
            t = ACCOUNTS.current_teacher() or {}
            pwd = str(body.get("password") or "")
            if not A.verify_password(pwd, t.get("password_hash")):
                STORE.audit("teacher", "data-password-fail", ip)
                return self._json({"ok": False, "msg": "教师账号密码不正确"}, 401)
            s = self._session()
            if s is not None:
                s["dw_until"] = time.time() + 180
            STORE.audit("teacher", "data-password-verify", ip)
            return self._json({"ok": True, "seconds": 180})

        if path == "/api/v2/teacher/password":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            ip = A.client_ip(self)
            if not A.rate_ok("tpw:" + ip, 10, 60):
                return self._json({"ok": False, "msg": "尝试太频繁，请 1 分钟后再试"}, 429)
            old = str(body.get("old") or "")
            new = str(body.get("new") or "")
            t = ACCOUNTS.current_teacher() or {}
            if not A.verify_password(old, t.get("password_hash")):
                return self._json({"ok": False, "msg": "当前密码不正确"}, 401)
            bad = A.weak_password(new)
            if bad:
                return self._json({"ok": False, "msg": bad}, 400)
            if new == old:
                return self._json({"ok": False, "msg": "新密码和原密码一样"}, 400)
            if not ACCOUNTS.set_password(ACC.current_account(), new, old=old):
                return self._json({"ok": False,
                                   "msg": "文件加密已启用，原密码验证失败，无法更新"}, 400)
            cur = A.parse_cookie(self.headers.get("Cookie"))
            SESS.drop_role("teacher", except_token=cur)
            notify_cfg()
            STORE.audit("teacher", "change-password", ip)
            return self._json({"ok": True})

        if path == "/api/v2/account/profile":
            """2.1.8：当前登录者（管理员 / 教师）自助改自己的账号名和密码。
            账号名与管理端改名走同一套唯一性校验；改完踢掉其它设备的会话。"""
            s = self._session()
            if not s or s.get("role") not in ("admin", "teacher"):
                return self._json({"ok": False, "msg": "未登录"}, 401)
            role = s["role"]
            acc_id = "admin" if role == "admin" else (
                s.get("account") or ACC.DEFAULT_TEACHER_ID)
            ip = A.client_ip(self)
            if not A.rate_ok("ap:" + ip, 10, 60):
                return self._json({"ok": False, "msg": "尝试太频繁，请 1 分钟后再试"}, 429)
            old = str(body.get("old") or "")
            new_user = str(body.get("username") or "").strip()
            new_pwd = str(body.get("password") or "")
            # 2.1.8：登录账号名只有管理员能改；教师端只改密码和密保。
            if role == "teacher" and new_user:
                return self._json({"ok": False,
                                   "msg": "登录账号由管理员在管理控制台统一设置，这里不能改"}, 403)
            if not new_user and not new_pwd:
                return self._json({"ok": False, "msg": "没有要修改的内容"}, 400)
            acc = ACCOUNTS.admin() if role == "admin" else (ACCOUNTS.teacher(acc_id) or {})
            if not A.verify_password(old, acc.get("password_hash")):
                return self._json({"ok": False, "msg": "当前密码不正确"}, 401)
            if new_pwd:
                bad = A.weak_password(new_pwd)
                if bad:
                    return self._json({"ok": False, "msg": bad}, 400)
                if A.verify_password(new_pwd, acc.get("password_hash")):
                    return self._json({"ok": False, "msg": "新密码和当前密码一样"}, 400)
            if new_user and new_user != str(acc.get("username") or ""):
                r = ACCOUNTS.set_own_username(role, acc_id, new_user)
                if not r.get("ok"):
                    return self._json(r, 400)
            if new_pwd:
                if role == "admin":
                    ACCOUNTS.set_admin_password(new_pwd)
                else:
                    if not ACCOUNTS.set_password(acc_id, new_pwd, old=old):
                        return self._json({"ok": False,
                                           "msg": "文件加密已启用，原密码验证失败，无法更新"}, 400)
            # 让其它设备上的旧会话失效；当前这次保留，前端收到成功提示后再自行跳转。
            cur = A.parse_cookie(self.headers.get("Cookie"))
            SESS.drop_role(role, except_token=cur)
            notify_cfg()
            STORE.audit(role, "profile-update", "%s%s" % (
                "user " if new_user else "", "password" if new_pwd else ""))
            return self._json({"ok": True,
                               "username": ACCOUNTS.username_of(role, acc_id)})

        # ---------------- 学生设计作品 · 设计者信息（教师可改，学生只读）----
        if path == "/api/v2/studio/designer":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            wid = str(body.get("id") or "")
            if not STUDIO or wid not in STUDIO.BY_ID:
                return self._json({"ok": False, "msg": "没有这个作品"}, 404)
            ds = studio_cfg().setdefault("designers", {})
            entry = ds.setdefault(wid, {"pen": ""})
            # 2.0.76（隐私脱敏）：只接受自愿填写的「笔名」，不再接收/展示
            # 真实姓名 / 班级 / 学校 / 介绍。老配置里的这些字段保留不删，
            # 但公开接口永不回传，改写也只动 pen，不会把真实信息重新写回去。
            if "pen" in body:
                entry["pen"] = str(body.get("pen") or "")[:60]
            cfg_save()
            STORE.audit("teacher", "studio-designer",
                        "%s:%s" % (wid, entry.get("pen", "")))
            return self._json({"ok": True, "studio":
                               STUDIO.public_cfg(ds, free=studio_free_cfg(),
                                                 school=CFG.get("school_name", ""))})

        # 2.0.70：教师端「免费孵化额度」设置已删除。默认额度（学生 1 / 教师 2）
        # 仍在 studio_cfg() 里补齐，兑换接口照用；不再提供改写入口。

        # ---------------- 像素画头像存服务端（2.0.56） ----------------
        # 学生头像写 students 行的 avatar 字段（自动随 config/tables 备份导入导出）；
        # 教师头像写 accounts.json 的 teacher.avatar（随 account 段同步）。
        # 身份一律由服务端 session 判定，前端不传 uid，防越权。
        if path == "/api/v2/avatar":
            s = self._session()
            if not s:
                return self._json({"ok": False, "msg": "未登录"}, 401)
            png = str(body.get("png") or "")
            # 2.1.7（#4）：传空 png = 清除服务端头像 —— 学生在设置里改选预设头像时
            # 调这个把宠物/像素画头像清掉，否则服务端头像永远盖住预设，切不了。
            if not png:
                if s["role"] == "student":
                    r = student_row(s.get("student_id"))
                    if not r:
                        return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
                    r["avatar"] = ""
                    r["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    STORE.upsert("students", r, actor="student", force=True)
                elif s["role"] == "teacher":
                    t = ACCOUNTS.raw_teacher(ACC.current_account())
                    if not t:
                        return self._json({"ok": False, "msg": "教师账号不存在"}, 404)
                    t["avatar"] = ""
                    ACCOUNTS.flush()
                else:
                    return self._json({"ok": False, "msg": "当前账号不能设置头像"}, 403)
                return self._json({"ok": True, "avatar": ""})
            if not png.startswith("data:image/png;base64,"):
                return self._json({"ok": False, "msg": "头像格式不对"}, 400)
            try:
                raw = base64.b64decode(png.split(",", 1)[1])
            except Exception:
                return self._json({"ok": False, "msg": "头像数据损坏"}, 400)
            if not raw or len(raw) > 512 * 1024:
                return self._json({"ok": False, "msg": "头像图片过大（上限 512KB）"}, 413)
            if s["role"] == "student":
                r = student_row(s.get("student_id"))
                if not r:
                    return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
                r["avatar"] = png
                r["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                STORE.upsert("students", r, actor="student", force=True)
                STORE.audit("student", "avatar", str(r.get("id") or ""))
            elif s["role"] == "teacher":
                t = ACCOUNTS.raw_teacher(ACC.current_account())
                if not t:
                    return self._json({"ok": False, "msg": "教师账号不存在"}, 404)
                t["avatar"] = png
                ACCOUNTS.flush()
                STORE.audit("teacher", "avatar", str(ACC.current_account()))
            else:
                return self._json({"ok": False, "msg": "当前账号不能设置头像"}, 403)
            return self._json({"ok": True, "avatar": png})

        # ---------------- H5 网页设计（2.0.39） ----------------
        if path == "/api/v2/design/config":
            # 教师端一键启停 + 学生端权限（添加 / 提交）+ 归档课程 + 大屏窗口
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            d = design_cfg()
            if "enabled" in body:
                d["enabled"] = bool(body["enabled"])
            if "allow_add" in body:
                d["allow_add"] = bool(body["allow_add"])
            if "allow_submit" in body:
                d["allow_submit"] = bool(body["allow_submit"])
            if "course" in body:
                c = " ".join(str(body.get("course") or "").split())[:40]
                if not c:
                    return self._json({"ok": False, "msg": "请填一个归档课程名"}, 400)
                # 填了新名字就顺手在「资料」里建好文件夹，学生交上来立刻有地方放
                d["course"] = design_ensure_course(c)
                if not str(d.get("window") or "").strip():
                    d["window"] = d["course"]
            if "window" in body:
                w = " ".join(str(body.get("window") or "").split())[:30]
                d["window"] = w or d["course"]
            if "intro" in body:
                d["intro"] = str(body.get("intro") or "")[:500]
            if "max_submit" in body:
                try:
                    d["max_submit"] = max(0, min(int(body.get("max_submit") or 0), 20))
                except Exception:
                    d["max_submit"] = 1
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "design-config",
                        json.dumps(design_public_cfg(), ensure_ascii=False))
            return self._json({"ok": True, "cfg": design_public_cfg()})
        if path == "/api/v2/design/draft":
            # 草稿即使老师关了功能也照收：关的那一刻最需要留住学生正在写的东西。
            # 教师端练习（2.0.40）也走这里，key 用 "t:<账号>"，与学生 id 分开存。
            s = self._session()
            if not s:
                return self._json({"ok": False, "msg": "未登录"}, 401)
            if s["role"] == "teacher":
                uid = "t:" + str(ACC.current_account())
            elif s["role"] == "student":
                r = student_row(s.get("student_id"))
                if not r:
                    return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
                uid = str(r.get("id"))
            else:
                return self._json({"ok": False, "msg": "这个账号没有设计草稿"}, 403)
            if body.get("clear"):
                return self._json(DESIGN.clear_draft(uid))
            res = DESIGN.save_draft(uid, body.get("html"), body.get("css"),
                                    body.get("title"), body.get("mode"), body.get("width"))
            return self._json(res)
        if path == "/api/v2/design/submit":
            # 一键提交：预览图（PNG）+ 完整源码（HTML）→ 活动库 → 自动归档 → 大屏
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "请先在学生端登录"}, 401)
            d = design_cfg()
            if not d.get("enabled"):
                return self._json({"ok": False, "msg": "老师还没有开启网页设计"}, 403)
            if not d.get("allow_submit"):
                return self._json({"ok": False, "msg": "老师暂未开放提交，先自己练习、保存草稿"}, 403)
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            png = str(body.get("png") or "")
            if not png.startswith("data:image/png;base64,"):
                return self._json({"ok": False, "msg": "预览图没有生成成功，请点「重新生成预览」后再提交"}, 400)
            try:
                raw = base64.b64decode(png.split(",", 1)[1])
            except Exception:
                return self._json({"ok": False, "msg": "预览图数据损坏，请重新截图"}, 400)
            if not raw:
                return self._json({"ok": False, "msg": "预览图是空的，请重新截图"}, 400)
            if len(raw) > 8 * 1024 * 1024:
                return self._json({"ok": False, "msg": "作品图片超过 8MB，先把图片换成小一点的再交"}, 413)
            html_src = str(body.get("html") or "")[:200 * 1024]
            css_src = str(body.get("css") or "")[:100 * 1024]
            title = " ".join(str(body.get("title") or "").split())[:60] or "我的网页"
            course = str(d.get("course") or "").strip() or "网页设计"
            win = str(d.get("window") or "").strip() or course
            t = design_task(course, create=True)
            if not t:
                return self._json({"ok": False, "msg": "创建收集任务失败，请让老师检查课程设置"}, 500)
            page = design_full_html(html_src, css_src, title)
            who = {"学号": str(r.get("student_no") or ""),
                   "姓名": str(r.get("name") or ""),
                   "班级": self._class_name(r.get("class_id"))}
            # 提交次数闸门（2.0.53）：和像素画同一套。一人交十几版，大屏作品墙
            # 和归档目录全被同一个人占满，课堂浏览翻不动。0 = 不限。
            limit = _max_submit_of(d)
            used = len(_student_subs_of(t, who["学号"], who["姓名"]))
            if limit > 0 and used >= limit:
                return self._json({"ok": False, "used": used, "max_submit": limit,
                                   "msg": ("老师设置了每人最多交 %d 版，你已经交了 %d 版。"
                                           "想再交就先把上一版改好，或者找老师放开次数。"
                                           % (limit, used))}, 409)
            # 按钮名就是大屏窗口名：设计提交在「上课」页单独占一个窗口，
            # 不会和学习单的其它窗口混在一起。
            fields = {"按钮": win, "作品": title, "类型": "H5 网页设计", "__kind": "file"}
            files = [{"name": "网页作品.png", "data": raw, "mime": "image/png"},
                     {"name": "网页源码.html", "data": page.encode("utf-8"),
                      "mime": "text/html; charset=utf-8"}]
            try:
                res = ACT.add_submission(t["task_id"], fields, files,
                                         ip=self._client_ip_safe(),
                                         sid=who["学号"], name=who["姓名"],
                                         cls=who["班级"], kind="file")
            except ValueError as e:
                return self._json({"ok": False, "msg": str(e)}, 413)
            except LookupError:
                return self._json({"ok": False, "msg": "收集任务不存在，请让老师重新保存一次设置"}, 404)
            arc = _archive_after_submit(t["task_id"], who, res)
            try:
                quiz_bump({"kind": "submit", "window": win, "from": "design",
                           "task": t["task_id"], "student_no": who["学号"]})
                notify()
            except Exception:
                pass
            # 交完把当前版本留成草稿：学生关掉页面再打开，接着改的还是这一版
            try:
                DESIGN.save_draft(str(r.get("id")), html_src, css_src, title)
            except Exception:
                pass
            fs = res.get("files") or []
            STORE.audit("student:%s" % who["姓名"], "design-submit",
                        "%s → %s（%d 个文件）" % (title, course, len(fs)))
            return self._json({"ok": True, "at": res.get("submitted_at") or now_str(),
                               "url": (fs[0].get("url") if fs else ""),
                               "files": len(fs),
                               "archived": len((arc or {}).get("saved") or []),
                               "task": t["task_id"], "window": win,
                               "course": course,
                               "used": used + 1, "max_submit": limit})
        # ---------------- 像素画（2.0.47） ----------------
        if path == "/api/v2/pixel/config":
            # 教师端一键启停 + 学生端权限 + 归档课程 + 大屏窗口 + 默认画布
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            d = pixel_cfg()
            if "enabled" in body:
                d["enabled"] = bool(body["enabled"])
            if "allow_add" in body:
                d["allow_add"] = bool(body["allow_add"])
            if "allow_submit" in body:
                d["allow_submit"] = bool(body["allow_submit"])
            if "course" in body:
                c = " ".join(str(body.get("course") or "").split())[:40]
                if not c:
                    return self._json({"ok": False, "msg": "请填一个归档课程名"}, 400)
                d["course"] = pixel_ensure_course(c)
                if not str(d.get("window") or "").strip():
                    d["window"] = d["course"]
            if "window" in body:
                w = " ".join(str(body.get("window") or "").split())[:30]
                d["window"] = w or d["course"]
            if "intro" in body:
                d["intro"] = str(body.get("intro") or "")[:500]
            if "size" in body:
                try:
                    sz = int(body.get("size") or 0)
                except Exception:
                    sz = 0
                if sz in PIXEL_SIZES:
                    d["size"] = sz
            if "max_submit" in body:
                try:
                    d["max_submit"] = max(0, min(int(body.get("max_submit") or 0), 20))
                except Exception:
                    d["max_submit"] = 1
            cfg_save()
            notify_cfg()
            STORE.audit("teacher", "pixel-config",
                        json.dumps(pixel_public_cfg(), ensure_ascii=False))
            return self._json({"ok": True, "cfg": pixel_public_cfg()})
        if path == "/api/v2/pixel/draft":
            # 草稿即使老师关了功能也照收：关的那一刻最需要留住学生正在画的东西。
            # 教师练习也走这里，key 用 "t:<账号>"，与学生 id 分开存。
            s = self._session()
            if not s:
                return self._json({"ok": False, "msg": "未登录"}, 401)
            if s["role"] == "teacher":
                uid = "t:" + str(ACC.current_account())
            elif s["role"] == "student":
                r = student_row(s.get("student_id"))
                if not r:
                    return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
                uid = str(r.get("id"))
            else:
                return self._json({"ok": False, "msg": "这个账号没有像素画草稿"}, 403)
            if body.get("clear"):
                return self._json(PIXEL.clear_draft(uid))
            res = PIXEL.save_draft(uid, body.get("w"), body.get("h"), body.get("px"),
                                   body.get("pal"), body.get("title"), body.get("grid"))
            return self._json(res)
        if path == "/api/v2/pixel/submit":
            # 一键提交：作品图（PNG，前端按格放大导出）+ 工程文件（json，可再编辑）
            # → 活动库 → 自动归档 → 大屏
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "请先在学生端登录"}, 401)
            d = pixel_cfg()
            if not d.get("enabled"):
                return self._json({"ok": False, "msg": "老师还没有开启像素画"}, 403)
            if not d.get("allow_submit"):
                return self._json({"ok": False, "msg": "老师暂未开放提交，先自己画、保存草稿"}, 403)
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            png = str(body.get("png") or "")
            if not png.startswith("data:image/png;base64,"):
                return self._json({"ok": False, "msg": "作品图没有生成成功，请稍后重试"}, 400)
            try:
                raw = base64.b64decode(png.split(",", 1)[1])
            except Exception:
                return self._json({"ok": False, "msg": "作品图数据损坏，请重试"}, 400)
            if not raw:
                return self._json({"ok": False, "msg": "作品图是空的，先画几笔再交"}, 400)
            if len(raw) > 8 * 1024 * 1024:
                return self._json({"ok": False, "msg": "作品图超过 8MB，请把画布调小一点再交"}, 413)
            try:
                w = int(body.get("w") or 0)
                h = int(body.get("h") or 0)
            except Exception:
                w = h = 0
            if w not in PIXEL_SIZES or h not in PIXEL_SIZES:
                return self._json({"ok": False, "msg": "画布尺寸不对，请刷新页面重画"}, 400)
            px = str(body.get("px") or "")
            if len(px) != w * h:
                return self._json({"ok": False, "msg": "像素数据长度对不上，请刷新页面重画"}, 400)
            if not px.replace(".", ""):
                return self._json({"ok": False, "msg": "画布还是空的，先画几笔再交"}, 400)
            pal = [str(c or "")[:9] for c in (body.get("pal") or []) if str(c or "").startswith("#")][:32]
            title = " ".join(str(body.get("title") or "").split())[:60] or "我的像素画"
            course = str(d.get("course") or "").strip() or "像素画"
            win = str(d.get("window") or "").strip() or course
            t = pixel_task(course, create=True)
            if not t:
                return self._json({"ok": False, "msg": "创建收集任务失败，请让老师检查课程设置"}, 500)
            who = {"学号": str(r.get("student_no") or ""),
                   "姓名": str(r.get("name") or ""),
                   "班级": self._class_name(r.get("class_id"))}
            # 提交次数闸门（2.0.53）：一人交五六版，大屏作品墙和归档目录全是
            # 同一个人，列表越拖越长、课堂上也翻不动。0 = 不限。
            limit = _max_submit_of(d)
            used = len(_pixel_student_subs(course, who["学号"], who["姓名"]))
            if limit > 0 and used >= limit:
                return self._json({"ok": False, "used": used, "max_submit": limit,
                                   "msg": ("老师设置了每人最多交 %d 版，你已经交了 %d 版。"
                                           "想再交就先把上一版改好，或者找老师放开次数。"
                                           % (limit, used))}, 409)
            proj = {"app": "班级工作台 · 像素画", "format": "wb-pixel", "v": 1,
                    "w": w, "h": h, "px": px, "pal": pal, "title": title,
                    "author": {"no": who["学号"], "name": who["姓名"], "class": who["班级"]}}
            fields = {"按钮": win, "作品": title,
                      "画布": "%d×%d" % (w, h), "类型": "像素画", "__kind": "file"}
            files = [{"name": "像素画.png", "data": raw, "mime": "image/png"},
                     {"name": "像素画工程.json",
                      "data": json.dumps(proj, ensure_ascii=False, indent=1).encode("utf-8"),
                      "mime": "application/json; charset=utf-8"}]
            try:
                res = ACT.add_submission(t["task_id"], fields, files,
                                         ip=self._client_ip_safe(),
                                         sid=who["学号"], name=who["姓名"],
                                         cls=who["班级"], kind="file")
            except ValueError as e:
                return self._json({"ok": False, "msg": str(e)}, 413)
            except LookupError:
                return self._json({"ok": False, "msg": "收集任务不存在，请让老师重新保存一次设置"}, 404)
            arc = _archive_after_submit(t["task_id"], who, res)
            try:
                quiz_bump({"kind": "submit", "window": win, "from": "pixel",
                           "task": t["task_id"], "student_no": who["学号"]})
                notify()
            except Exception:
                pass
            # 交完把这一版留成草稿：回去接着改的还是刚交的那张
            try:
                PIXEL.save_draft(str(r.get("id")), w, h, px, pal, title,
                                 body.get("grid"))
            except Exception:
                pass
            fs = res.get("files") or []
            STORE.audit("student:%s" % who["姓名"], "pixel-submit",
                        "%s → %s（%d 个文件）" % (title, course, len(fs)))
            return self._json({"ok": True, "at": res.get("submitted_at") or now_str(),
                               "url": (fs[0].get("url") if fs else ""),
                               "files": len(fs),
                               "archived": len((arc or {}).get("saved") or []),
                               "task": t["task_id"], "window": win,
                               "course": course, "w": w, "h": h,
                               "used": used + 1, "max_submit": limit})
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
            tname = self._teacher_name(s)
            res = TYPING.teacher_record(body.get("class_id"), tname, body or {})
            if res.get("ok"):
                STORE.audit("teacher", "typing-teacher-record",
                            "class=%s s%sc%sl%s %s星" % (body.get("class_id"),
                                                         body.get("stage"), body.get("chapter"),
                                                         body.get("level"), res.get("stars")))
            return self._json(res)
        if path == "/api/v2/typing/teacher-equip":
            # 老师换装不上榜也要让排行榜那行跟上（2026-11-05 晚）
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            return self._json(TYPING.teacher_equip(body.get("class_id"),
                                                   body.get("equip") or body))
        if path == "/api/v2/typing/claim":
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            studio_ids = STUDIO.PET_IDS if STUDIO else []
            return self._json(TYPING.claim_egg(str(s.get("student_id")),
                                               PET_IDS, studio_ids))
        if path == "/api/v2/typing/equip":
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            # bg / fx / 学生作品缺省不传 = 保持原样（None）；传 '' = 取下来
            kw = {}
            if "bg" in body:
                kw["bg"] = str(body.get("bg") or "")
            if "fx" in body:
                kw["fx"] = str(body.get("fx") or "")
            if "studio_pet" in body:
                kw["studio_pet"] = str(body.get("studio_pet") or "")
            if "studio_theme" in body:
                kw["studio_theme"] = str(body.get("studio_theme") or "")
            return self._json(TYPING.equip(str(s.get("student_id")),
                                           str(body.get("pet") or ""),
                                           body.get("decos") or [], **kw))
        if path == "/api/v2/typing/buy":
            # 积分商店：kind = deco（装扮）/ item（道具）/ bg（背景）/ effect（特效）
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
            elif kind == "bg":
                res = TYPING.buy_bg(uid, oid)
            elif kind == "effect":
                res = TYPING.buy_effect(uid, oid)
            elif kind == "egg":
                # 积分兑换宠物蛋（2026-11-19）：价格只认服务端 EGG_PRICE，
                # 客户端传什么价都不看；图鉴集齐后直接拒售。
                res = TYPING.buy_egg(uid, PET_IDS)
            elif kind == "studio_theme":
                # 界面主题：唯一直兑入口，价格只认服务端。
                res = TYPING.buy_studio(uid, oid)
            elif kind == "studio_egg":
                # 2.0.56：作品宠物只能通过宠物蛋兑换。
                # 客户端传 egg_id，服务端查表决定出哪只宠物、扣多少积分。
                res = TYPING.buy_studio_egg(uid, oid,
                                            free_quota=studio_free_cfg()["student"])
            elif kind == "studio_pet":
                # 老客户端 / 伪造请求：明确拒绝直兑，避免绕过蛋入口。
                res = {"ok": False, "msg": "作品宠物不能直接兑换，请兑换对应的宠物蛋"}
            else:
                res = {"ok": False, "msg": "未知的商品类型"}
            if res.get("ok"):
                STORE.audit("student", "typing-buy", "%s:%s" % (kind, oid))
            return self._json(res)
        if path == "/api/v2/typing/draft":
            # 练习草稿（2026-11-05）：学生端练习中自动保存当前关卡进度。
            # 注意这里**不能**校验 enabled —— 教师把练习关掉的那一瞬间
            # 恰恰是学生最需要把进度落盘的时刻（record 那时已拒收）。
            s = self._session()
            if not s or s["role"] != "student":
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = student_row(s.get("student_id"))
            if not r:
                return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
            return self._json(TYPING.save_draft(str(r.get("id")), body))
        if path == "/api/v2/typing/admin-points":
            # 教师对学生/全班发放或扣除积分：
            # 全局实时生效（学生端 12 秒内轮询到新余额），
            # 操作记录进 typing.json 的 pts_log，随快照导入导出。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            try:
                delta = int(body.get("delta"))
            except Exception:
                return self._json({"ok": False, "msg": "积分数不合法"})
            class_id = str(body.get("class_id") or "")
            sid = str(body.get("student_id") or "")
            note = str(body.get("note") or "")[:80]
            if not class_id:
                return self._json({"ok": False, "msg": "请先选择班级"})
            targets = []
            for st in STORE.table_rows("students"):
                if st.get("deleted") or str(st.get("class_id")) != class_id:
                    continue
                if sid and str(st.get("id")) != sid:
                    continue
                targets.append((st.get("id"), st.get("name", "")))
            if sid and not targets:
                return self._json({"ok": False, "msg": "这个学生不在所选班级里"})
            by = self._teacher_name(s)
            res = TYPING.adjust_points(targets, delta, note, by)
            if res.get("ok"):
                STORE.audit("teacher", "typing-points",
                            "class=%s %s%s %s%d (%s)" % (
                                class_id, ("uid=" + sid) if sid else "全班",
                                (targets[0][1] if sid else "%d人" % res.get("count", 0)),
                                "+" if delta > 0 else "", delta, note))
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
        if path == "/api/v2/teacher/clear-lessons":
            # 每周跨周清空课表：服务端按数据库真实 rid 逐行打墓碑，
            # 避免前端逐行墓碑对「老 id 数据」算错 rid 导致删不干净（幽灵课表）。
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            try:
                n = STORE.clear_table("lessons", "teacher")
            except Exception as e:
                return self._json({"ok": False, "msg": str(e)[:120]}, 500)
            if n:
                notify()
            return self._json({"ok": True, "deleted": n, "rev": STORE.rev()})
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
                # 全部重置 = 全班口令作废：所有学生会话一起踢下线，
                # 学生端自动回到登录页（2026-11-17）。
                try:
                    n_kick = 0
                    for _sid in [r.get("id") for r in STORE.table_rows("students")
                                 if not r.get("deleted")]:
                        n_kick += SESS.drop_student(
                            _sid, account=ACC.current_account(),
                            reason="老师重置了班级密码，请用新密码重新登录")
                    if n_kick:
                        notify()
                except Exception:
                    pass
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
                # 重置口令 → 旧会话立即失效（2026-11-17）：学生端自动退回登录页，
                # 用新密码登录。不这么做，学生在别的机器上还能接着用旧身份。
                try:
                    if SESS.drop_student(row.get("id"), account=ACC.current_account(),
                                         reason="老师重置了你的密码，请用新密码重新登录"):
                        notify()
                except Exception:
                    pass
                STORE.audit("teacher", "reset-password",
                            "%s（指定新密码%s）" % (who, "，首次登录须修改" if must else ""))
                return self._json({"ok": True, "mode": "password", "name": who,
                                   "must_change": bool(must)})
            STORE.clear_creds([row.get("id")])
            try:
                if SESS.drop_student(row.get("id"), account=ACC.current_account(),
                                     reason="老师重置了你的密码，请用班级新密码重新登录"):
                    notify()
            except Exception:
                pass
            STORE.audit("teacher", "reset-password", "%s（回落班级初始密码）" % who)
            return self._json({"ok": True, "mode": "uniform", "name": who})
        # ---------------- 管理员：只管教师账号 ----------------
        if path == "/api/v2/admin/teacher/add":
            if not self._is_admin():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = ACCOUNTS.add_teacher(body.get("username"), body.get("password"),
                                     body.get("name"))
            if r.get("ok"):
                # 新账号的工作区立刻建好，第一次登录就能看到自己的空资料库
                WS.ensure(r["teacher"]["id"])
                STORE.audit("admin", "teacher-add", r["teacher"]["username"])
                notify_cfg()
            return self._json(r)
        if path == "/api/v2/admin/teacher/update":
            if not self._is_admin():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = ACCOUNTS.update_teacher(body.get("id"),
                                        username=body.get("username"),
                                        password=body.get("password"),
                                        name=body.get("name"))
            if r.get("ok"):
                STORE.audit("admin", "teacher-update", str(body.get("id") or ""))
                notify_cfg()
            return self._json(r)
        if path == "/api/v2/admin/teacher/delete":
            if not self._is_admin():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            r = ACCOUNTS.delete_teacher(body.get("id"))
            if r.get("ok"):
                STORE.audit("admin", "teacher-delete", str(body.get("id") or ""))
                notify_cfg()
            return self._json(r)
        if path == "/api/v2/admin/password":
            if not self._is_admin():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            old = str(body.get("old") or "")
            new = str(body.get("new") or "")
            adm = ACCOUNTS.admin()
            if not A.verify_password(old, adm.get("password_hash")):
                return self._json({"ok": False, "msg": "当前密码不正确"}, 401)
            bad = A.weak_password(new)
            if bad:
                return self._json({"ok": False, "msg": bad}, 400)
            ACCOUNTS.set_admin_password(new)
            cur = A.parse_cookie(self.headers.get("Cookie"))
            SESS.drop_role("admin", except_token=cur)
            STORE.audit("admin", "change-password", "")
            return self._json({"ok": True})

        # ---------------- 资料共享：文件复制到目标教师账号的课件目录 ----------------
        if path == "/api/v2/teacher/share-material":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            files = body.get("files")
            if not isinstance(files, list):
                files = [body.get("file")] if body.get("file") else []
            targets = body.get("targets") or []
            if not isinstance(targets, list):
                targets = [targets]
            if not files or not targets:
                return self._json({"ok": False, "msg": "请先选资料和要共享的老师"}, 400)
            valid_ids = set(ACCOUNTS.teacher_ids()) - {ACC.current_account()}
            targets = [str(x) for x in targets if str(x) in valid_ids]
            if not targets:
                return self._json({"ok": False, "msg": "没有可用的目标教师账号"}, 400)
            copied, skipped = [], []
            for rel in files:
                rel = str(rel or "").replace("\\", "/").strip("/")
                if not rel or ".." in rel.split("/"):
                    skipped.append(str(rel) + "（路径不合法）")
                    continue
                src = LEG.CW.abs_of(rel, must_exist=True)
                if not src or not os.path.isfile(src):
                    skipped.append(rel + "（源文件不存在）")
                    continue
                for tid in targets:
                    p = WS.ensure(tid)
                    dst = os.path.normpath(os.path.join(
                        p["courseware"], *rel.split("/")))
                    # 目标必须仍在该账号的课件根内
                    try:
                        if os.path.commonpath([os.path.normpath(p["courseware"]), dst]) \
                                != os.path.normpath(p["courseware"]):
                            skipped.append(rel + "（目标路径越界）")
                            continue
                    except ValueError:
                        skipped.append(rel + "（目标路径越界）")
                        continue
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    stem, ext = os.path.splitext(dst)
                    n = 1
                    while os.path.exists(dst):
                        dst = "%s_共享%d%s" % (stem, n, ext)
                        n += 1
                    # 静态加密：源按当前账号 FEK 解密，目标按目标账号 FEK 加密。
                    # 直接 copy2 会把 A 的密文放进 B 的目录，B 永远打不开。
                    try:
                        with open(src, "rb") as f:
                            blob = f.read()
                        if blob[:4] == b"WBF1":
                            blob = ENC.dec_file(blob)
                    except Exception as e:
                        skipped.append(rel + "（读取失败：%s）" % str(e)[:40])
                        continue
                    tgt_fek = ENC.cached_account_fek(tid)
                    if tgt_fek:
                        old_fek = ENC.get_fek()
                        ENC.set_fek(tgt_fek)
                        try:
                            blob = ENC.enc_file(blob)
                        finally:
                            ENC.set_fek(old_fek)
                    elif ACCOUNTS.enc_status(tid).get("ready"):
                        skipped.append(rel + "（目标老师尚未登录，密钥未解锁）")
                        continue
                    with open(dst, "wb") as f:
                        f.write(blob)
                    copied.append({"target": tid, "file": rel,
                                   "to": os.path.relpath(dst, p["courseware"])
                                         .replace(os.sep, "/")})
            STORE.audit("teacher", "share-material",
                        "%d 份 → %s" % (len(copied), ",".join(targets)))
            return self._json({"ok": True, "copied": len(copied),
                               "skipped": skipped, "items": copied})

        if path == "/api/v2/teacher/account":
            if not self._is_teacher():
                return self._json({"ok": False, "msg": "未登录"}, 401)
            user = str(body.get("username") or "").strip()
            if user:
                # 2.0.13：账号名（注册 / 改名 / 删除）统一归管理员
                return self._json({"ok": False,
                                   "msg": "账号名由管理员统一管理，请到管理员控制台修改"}, 403)
            old = str(body.get("old") or "")
            new = str(body.get("new") or "")
            if not new:
                return self._json({"ok": False, "msg": "没有要修改的内容"}, 400)
            t = ACCOUNTS.current_teacher() or {}
            if not A.verify_password(old, t.get("password_hash")):
                return self._json({"ok": False, "msg": "原密码不正确"}, 401)
            bad = A.weak_password(new)
            if bad:
                return self._json({"ok": False, "msg": bad}, 400)
            if not ACCOUNTS.set_password(ACC.current_account(), new, old=old):
                return self._json({"ok": False,
                                   "msg": "文件加密已启用，原密码验证失败，无法更新"}, 400)
            notify_cfg()
            STORE.audit("teacher", "account-update", "change-password")
            return self._json({"ok": True,
                               "username": t.get("username") or "teacher"})
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
        if path == "/api/v2/teacher/leave":
            return self._teacher_leave(body)
        if path == "/api/v2/admin/import":
            return self._import(body)
        # 登录页「数据导入」（换设备 / 重新部署）：见 _import_preauth 的说明
        if path == "/api/v2/admin/import-preauth":
            return self._import_preauth(body)
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
    def _checkin_cfg(self):
        """签到配置对外统一口径（两端显示 / 设置页都读这一份）。"""
        t = _manual_checkin_min()
        return {"enabled": self._checkin_enabled(),
                "manual_time": str((CFG.get("checkin") or {}).get("manual_time") or "")
                               if t is not None else "",
                "mode": "manual" if t is not None else "schedule"}

    def _checkin_enabled(self):
        return bool((CFG.get("checkin") or {}).get("enabled", True))

    def _checkin_base_min(self, prow):
        """签到 / 迟到判定的时间基准：手动时间优先，否则课表本节开始时间。"""
        manual = _manual_checkin_min()
        if manual is not None:
            return manual
        return _mins_of((prow or {}).get("start_time"))

    def _current_period(self):
        """按作息表判断“现在第几节”。

        找不到（课间 / 没排作息 / 放学后）就返回 None，让前端按第 1 节处理，
        绝不猜一个错的节次把签到记到别的课上去。

        2.0.49：改成只看「今天」的作息（_periods_today），与前端 periodList 同口径。
        """
        rows = _periods_today()
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
        # 2.0.52：课间（上一节下课 ~ 下一节上课）算「下一节的课前」。
        # 原来取「已经过去的最近一节」→ 课间学生签到被记到刚下课的节次，
        # 时间窗早已结束，整个课间 9 分钟谁都签不了（牛哥 11-20 反馈）。
        # 与 _class_period_now / 前端 curPeriodSeq 同一口径。
        nxt = None
        for r in rows:
            s = mins(r.get("start_time"))
            if s > cur:
                nxt = r
                break
        if nxt is not None:
            return nxt
        # 放学后（当天已经没有下一节）：退回最后一节，交给时间窗去拒
        past = [r for r in rows if mins(r.get("start_time")) >= 0
                and mins(r.get("start_time")) <= cur]
        return past[-1] if past else rows[0]

    def _class_period_now(self, cid):
        """这个班「现在」该记哪一节（登录自动签到 / 手动签到 / 学生端显示共用）。

        2026-11-04 牛哥反馈：一个班签完到，下一个班提前进机房登录，
        整班被误判「迟到」。根因：课间（两节之间）_current_period 取的是
        「已经过去的最近一节」——新班级被记到上一节，还按上一节的开始
        时间算迟到（差了整整一个课间，10+ 分钟 → 全班 late）。

        口径（2026-11-04 牛哥定版）：
        · 整点在某一节的时间窗里 → 就是那一节（各班一样）；
        · 课间先看「下一节」：上一节下课后（+1 分钟起）就算下一节的「课前」，
          且本班这一节有排课 → 记下一节（提前到机房 = 准时），不再限 45 分钟；
        · 当天第一节之前没有「上一节」，保留开始前 15 分钟的提前量；
        · 其余（放学后 / 本班课表没在用）→ 保持旧行为（上一节补签）。
        cid 为 None（无班级上下文）时等同 _current_period。
        """
        rows = _periods_today()          # 2.0.49：只看今天，与前端同一套
        if not rows:
            return None
        lt = time.localtime()
        cur = lt.tm_hour * 60 + lt.tm_min
        dow = lt.tm_wday

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
        # 2026-11-25 牛哥反馈：教师端把第9节改到 18:30，但第10节还是默认的 17:10，
        # 17:27 学生端却显示「第10节未排课」。根因是选节只看时间窗——没排课的
        # 第10节时间窗正好罩住当前时刻，排了课的第9节根本轮不到。
        # 定版：本班今天有排课时，没排课的节次即使时间窗命中也不算当前节。
        def scheduled(r):
            if cid is None:
                return True
            try:
                p = int(float(r.get("seq") if r.get("seq") not in (None, "") else r.get("id")))
            except Exception:
                return False
            return self._class_lesson_at(cid, dow, p) is True
        has_sched = any(scheduled(r) for r in rows)
        for r in rows:
            if cid is not None and has_sched and not scheduled(r):
                continue
            s, e = mins(r.get("start_time")), mins(r.get("end_time"))
            if s >= 0 and e > s and s <= cur <= e:
                return r
        nxt = None
        for r in rows:
            if cid is not None and has_sched and not scheduled(r):
                continue
            s = mins(r.get("start_time"))
            if s > cur:
                nxt = r
                break
        if nxt is not None and cid is not None:
            # 2026-11-04 定版：上一节下课后 1 分钟起就算下一节的课前，
            # 不再限「45 分钟内开上」——9:40 下课 → 9:41 起可签下一节。
            # cur 已在上一节结束之后（分钟为整数，cur > end 即 cur >= end+1）；
            # 当天第一节之前没有「上一节」，保留开始前 15 分钟的提前量。
            idx = rows.index(nxt)
            if idx > 0:
                pe = mins(rows[idx - 1].get("end_time"))
                too_early = pe >= 0 and cur < pe + 1   # 保险，正常到不了
            else:
                s_n = mins(nxt.get("start_time"))
                too_early = s_n >= 0 and cur < s_n - 15
            if not too_early:
                try:
                    p = int(float(nxt.get("seq") or nxt.get("id") or 0))
                except Exception:
                    p = 0
                # 2.0.52：工作日的课间一律算「下一节的课前」，不再要求本班
                # 下一节排过课。否则课表没排到这一节时，课间登录会被判成
                # 上一节（时间窗早已结束）→ 整个课间谁都签不了到。
                if p and (self._class_lesson_at(cid, dow, p) is True or dow < 5):
                    return nxt
        past = [r for r in rows if mins(r.get("start_time")) >= 0
                and mins(r.get("start_time")) <= cur]
        return past[-1] if past else rows[0]

    def _period_time_ok(self, r):
        """签到时间窗校验（2026-11-04 牛哥定版）。

        窗口 = 「上一节下课 + 1 分钟」~「本节下课」：
        · 上一节 9:40 下课 → 9:41 起就算这一节的课前，可以签到（提前到 = 准时）；
        · 本节一下课立即截止，课后不再开放补签（需要补签让老师在考勤页登记）；
        · 当天第一节之前没有「上一节」，保留开始前 15 分钟的提前量；
        · 作息表里没填时间的节次不拦（老师没排作息就不约束）。
        返回 (ok, msg)。
        """
        if not r:
            return True, ""
        # 老师手动设了签到时间：窗口就以它为准，不再看课表节次的上下课。
        manual = _manual_checkin_min()
        if manual is not None:
            cur_m = time.localtime().tm_hour * 60 + time.localtime().tm_min
            tstr = self._checkin_cfg()["manual_time"] or ("%d:%02d" % (manual // 60, manual % 60))
            if cur_m < manual - 15:
                return False, "还没到签到时间（老师设定 %s，提前 15 分钟才能签）" % tstr
            if cur_m > manual + 45:
                return False, "签到时间已过（老师设定 %s），如需补签请让老师在考勤页登记" % tstr
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
        # 找本节之前最近一个有下课时间的节次：它的下课时间 +1 分钟 = 本节课前起点
        try:
            seq = float(r.get("seq") if r.get("seq") not in (None, "") else r.get("id"))
        except Exception:
            seq = -1.0
        prev_end = None
        try:
            # 2026-11-20：上一节下课时间必须取「今天」的，全表遍历会拿到
            # 别个星期那套（周一 08:00 / 周二~周六 08:55），窗口就差一小时。
            rows = _periods_today()
        except Exception:
            rows = []
        for x in rows:
            try:
                xseq = float(x.get("seq")
                             if x.get("seq") not in (None, "") else x.get("id"))
            except Exception:
                continue
            if xseq >= seq:
                break
            pe = mins(x.get("end_time"))
            if pe >= 0:
                prev_end = pe
        # 2026-11-25：倒挂节次的兜底。教师把第9节改到 18:30、第10节还是 17:10，
        # 「上一节下课+1」会算出 16:06 就放行第9节 → 17:27 就签成第9节且算准时。
        # 窗口起点改成两者取晚：既保留正常课间「上一节下课即可签」的宽松，
        # 又保证节次被大幅后移后，仍按本节开始前 15 分钟才开放。
        early = s - 15
        start_at = max(prev_end + 1, early) if prev_end is not None else early
        if cur < start_at:
            if prev_end is not None and prev_end + 1 >= early:
                pe_str = "%d:%02d" % (prev_end // 60, prev_end % 60)
                return False, "%s 还没到签到时间（上一节 %s 下课，之后才算%s的课前）" % (
                    label, pe_str, label)
            return False, "还没到%s（%s 开始），先不用签" % (label, r.get("start_time"))
        if cur > e:
            return False, "%s 已下课（%s），不能再签这一节了；如需补签请让老师在考勤页登记" % (
                label, r.get("end_time"))
        return True, ""

    def _now_payload(self, cid=None):
        """服务端当前节次（2026-11-04 牛哥反馈：签到节数两端不一致）。

        服务端时间 = 唯一时间源（三端校时定版）：学生机 / 教师机时钟不准时，
        各端自己算出来的「第几节」会和实际签到记录对不上。
        统一由服务端算好发给两端显示；带 cid 时顺带给出本班该节是否排课
        （lesson：True 有课 / False 没课 / "no_table" 本班课表没在用）。
        """
        p = self._class_period_now(cid) if cid is not None else self._current_period()
        period = 0
        label = ""
        if p:
            try:
                period = int(float(p.get("seq") or p.get("id") or 0))
            except Exception:
                period = 0
            label = str(p.get("label") or ("第 %s 节" % (p.get("seq") or p.get("id") or "?")))
        out = {"ok": True, "date": today_str(), "time": time.strftime("%H:%M"),
               "period": period, "label": label,
               "checkin": self._checkin_cfg()}
        # 2026-11-25 牛哥定版：学生端「一键签到」必须只在服务端确认
        # 「开关开着 + 本节有排课 + 在签到时间窗内」时才可点。以前前端靠
        # 字段不等于 false 兜底，服务端还没算出节次 / 网络抖动时按钮会默认
        # 出现，点了又被拒。现在统一发 can_checkin，前端只认显式 true。
        can = self._checkin_enabled()
        why = "" if can else "老师已关闭课堂签到"
        if cid is not None:
            # 没算出节次时也把闸门字段补齐，避免前端把 undefined 当允许。
            out.setdefault("lesson", False)
            out.setdefault("checkin_ok", False)
            out.setdefault("checkin_hint", "现在没有可签到的节课")
            out.setdefault("checkin_time_ok", False)
            out.setdefault("checkin_time_hint", "现在没有可签到的节课")
        if cid is not None and period:
            dow = time.localtime().tm_wday
            lesson = self._class_lesson_at(cid, dow, period)
            out["lesson"] = lesson
            # 2026-11-25 牛哥定版：签到按「今天这一节有没有排课」判定，
            # 不再用「今天任意一节有课」放行。没排课的节次不写迟到，
            # 学生端提示等这一节下课后再签下一节。
            out["checkin_ok"] = lesson is True
            out["checkin_hint"] = (
                (label or ("第 %s 节" % period)) + "没有排课，下课后签到下一节"
            ) if lesson is False else ""
            if lesson is not True:
                can = False
                why = out["checkin_hint"] or "这一节没有排课"
            # 2026-11-25：节次被大幅后移（倒挂）时，选节已指向本节但时间窗未开，
            # 把窗口判定一并发给学生端，界面直接显示「还没到签到时间」，
            # 不要再挂一个点了被拒的「一键签到」按钮。
            if p:
                t_ok, t_msg = self._period_time_ok(p)
                out["checkin_time_ok"] = bool(t_ok)
                out["checkin_time_hint"] = "" if t_ok else t_msg
                if not t_ok:
                    can = False
                    why = t_msg or "还没到签到时间"
        elif cid is not None:
            can = False
            why = "现在没有可签到的节课"
        out["can_checkin"] = bool(can)
        out["checkin_disabled_reason"] = "" if can else (why or "现在还不能签到")
        return out

    def _student_checkin(self, body):
        s = self._session()
        if not s or s["role"] != "student":
            return self._json({"ok": False, "msg": "未登录"}, 401)
        if not self._checkin_enabled():
            return self._json({"ok": False, "msg": "老师已关闭课堂签到，本节课不用签到"}, 403)
        sid = s.get("student_id")
        st = student_row(sid)
        if not st:
            return self._json({"ok": False, "msg": "学生档案不存在"}, 404)
        # 机房里整班同学共用一个出口 IP，按学号限速（同一人反复点也不怕）
        if not A.rate_ok("ck:%s" % sid, 20, 60):
            return self._json({"ok": False, "msg": "签到太频繁了，稍等一下"}, 429)

        p = self._current_period()
        prow = None
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
            # 时间窗校验：没到课前 / 已下课都明确拒绝（窗口见 _period_time_ok）
            # 2.0.49：取今天的行，否则同一节会拿到别的星期那套时间。
            for r in _periods_today():
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
            # 按班选节（课间优先记本班的下一节，见 _class_period_now）
            p = self._class_period_now(st.get("class_id")) or self._current_period()
            period = (int(float(p.get("seq") or p.get("id") or 1)) if p else 1)
            ok, msg = self._period_time_ok(p)
            if not ok:
                return self._json({"ok": False, "msg": msg}, 400)
        d = today_str()
        ok2, info = self._write_checkin(sid, st, prow or p, period, d, "student")
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

    def _class_has_lesson_today(self, cid, dow):
        """本班这个星期几有没有课（不看具体第几节）。

        2.0.52 牛哥反馈「课表设了课，学生却签到不了」：签到闸门原来按
        「此刻这一节有没有排课」卡，而课表常常只排了个别节次、或老师临时
        调课补课，于是整班被堵在门外；偏偏自动记缺勤（absent_sweep_once）
        是按「今天这个班有课」扫的 —— 一边不让人签到，一边把全班记旷课。
        两边必须同口径：今天有课就允许签，今天完全没课才拦。
        周末 / 无课日下午的假出勤仍然拦得住（今天一条课都没有）。
        """
        for l in STORE.table_rows("lessons"):
            if l.get("deleted"):
                continue
            if str(l.get("class_id") or "") != str(cid):
                continue
            try:
                if int(float(l.get("day_of_week"))) == int(dow):
                    return True
            except Exception:
                continue
        return False

    def _write_checkin(self, sid, st, p, period, d, source):
        """登录自动签到 / 学生手动签到共用的落库核心。

        v2 考勤判定规则（服务端时间为唯一时间源）：
        · 课表开始 5 分钟内签到 → 出勤（checked）；超过 5 分钟 → 迟到（late）
        · 教师已登记病假 / 事假 → 保留请假状态，不被签到覆盖（请假优先级最高）
        · 已被自动记缺勤的学生到堂 → 改判迟到，reason 留痕「缺勤后到堂」
        · 首次签到时间留住：反复签到不会把迟到「刷」成准时
        """
        if not self._checkin_enabled():
            return False, "老师已关闭课堂签到"
        # 2.1.7（#1 无课签到）：本班当天课表一条课都没有 → 不落考勤。
        # 之前只卡周末、工作日无条件放行，于是「切到没排课的班」照样签成功。
        # 放在落库核心里，登录自动签到与手动签到一律拦；口径与前端 checkin_ok 一致。
        try:
            dow_of_d = time.strptime(d, "%Y-%m-%d").tm_wday
        except Exception:
            dow_of_d = time.localtime().tm_wday
        if not self._class_has_lesson_today(st.get("class_id"), dow_of_d):
            return False, "今天这个班没有课，不用签到"
        rid = "ck%s-%s-%s" % (sid, d, period)
        old = STORE.get_row("attendance", rid) or {}
        first = str(old.get("checked_at") or "")
        old_status = str(old.get("status") or "")

        # 教师强制锁（2026-11-04）：老师在座位表/学生面板手动强设的考勤状态
        # （attendance.force=1）优先级最高——学生再怎么登录/签到都不改写，
        # 直到教师解除强制。请假优先逻辑也一并被涵盖（强制就是最终态）。
        if old.get("force"):
            # 返回结构与正常签到一致（调用方 _student_checkin 会 **info 展开），
            # 前端据 already=True 提示「本节课已经签过到了」，状态仍是老师那一笔。
            return True, {"date": d, "period": period,
                          "status": str(old.get("status") or ""),
                          "period_label": (p or {}).get("label") or ("第 %s 节" % period),
                          "checked_at": first, "already": True, "forced": True}

        # 课表闸门（2026-11-03 牛哥反馈）：本班这个时段没排课就不允许签到。
        # 以前只校验节次合法性与时间窗：课表没排课的日子（周末/无课日下午）
        # 登录照样被记一笔出勤，考勤统计出现假全勤。
        # 2.0.52（牛哥 11-20「课表设了课却签到不了」）：
        #   · 工作日一律放行 —— 课表常常只排了个别节次、临时调课补课更是常态，
        #     按「这一节有没有排课」卡人会把整班堵在门外；老师开着签到开关
        #     本身就是在收签到。
        #   · 周末保留闸门：本班周末没排课就别记考勤，防在家登录刷出假出勤。
        cid = st.get("class_id")
        dow = time.localtime().tm_wday
        lesson = self._class_lesson_at(cid, dow, period)
        if lesson is False:
            # 2026-11-25 牛哥定版：这一节没排课就不记迟到，也不记出勤。
            # 之前工作日按「今天有任意一节课」放行，老师没排到的节次
            # 登录后照样写一笔迟到；现在明确等这一节下课再签下一节。
            label = (p or {}).get("label") or ("第 %s 节" % period)
            return False, "%s没有排课，这一节下课后签到下一节" % label

        now_min = time.localtime().tm_hour * 60 + time.localtime().tm_min
        def _mins(v):
            a = str(v or "").split(":")
            try:
                return int(a[0]) * 60 + int(a[1] or 0)
            except Exception:
                return -1
        start_min = self._checkin_base_min(p)
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
        if not self._checkin_enabled():
            return False, "签到功能已关闭"
        st = student_row(sid)
        if not st:
            return False, "学生档案不存在"
        if not A.rate_ok("ck:%s" % sid, 20, 60):
            return False, "签到太频繁了，稍等一下"
        p = self._class_period_now(st.get("class_id"))
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
        """全量恢复入口之一（教师端「从备份一键恢复」）。

        2026-11-03 加固（换机测试 F1/F2/F5）：
          · F1 先校验后落库 —— 空表 / 缺表 / 数据量骤减一律在删数据之前拒绝；
            需要强导时前端会带 force=1 再提交一次。拒绝时本机数据零改动。
          · F2 学生口令表改成「整体替换」：带了 creds 字段就先清空再写，
            不再出现「备份 2 条、目标机剩 3 条」的残留。
          · F5 未知表名不再静默丢弃，回包告诉调用方跳过了哪些。
        """
        if not self._is_teacher():
            return self._json({"ok": False, "msg": "只有教师端可以导入"}, 403)
        return self._do_import(body, actor="teacher")

    def _import_preauth(self, body):
        """登录页「数据导入」（2026-11-05）：换设备 / 重新部署后，
        还没登录（甚至还没有账号）就要把旧机的全部数据搬进来。

        免登录不等于不设防 —— 按两种情况分别把关：
          · 还没有管理员账号：沿用首次设置页同一套窗口限制（_setup_allowed），
            启动 24 小时内 + 内网来源才放行。导入的备份里带着教师账号，
            恢复完账号就回来了；
          · 已有账号：必须验证教师账号密码（强度等同登录本身），防止
            机房里学生随手把一份备份灌进服务端覆盖全班数据。
        """
        ip = A.client_ip(self)
        if not A.rate_ok("imp:" + ip, 12, 300):
            return self._json({"ok": False, "msg": "操作太频繁，请 5 分钟后再试"}, 429)
        # 2.0.13：管理员/教师账号随服务初始化即存在，一律要求验证凭据。
        # 管理员或任一教师账号的密码都能通过（防止机房学生一键覆盖全班数据）。
        user = str(body.get("teacher_username") or "").strip()
        pwd = str(body.get("teacher_password") or "")
        ok_admin, _, _ = ACCOUNTS.verify("admin", user, pwd)
        ok_teacher, tid, _ = ACCOUNTS.verify("teacher", user, pwd)
        if not (ok_admin or ok_teacher):
            STORE.audit("?", "preauth-import-fail", ip)
            return self._json({"ok": False,
                               "msg": "管理员或教师账号密码不正确 —— 导入数据前"
                                      "要先验证密码（防止学生误导入）"}, 401)
        if ok_teacher:
            ACC.set_account(tid)
        return self._do_import(body, actor="admin" if ok_admin else "teacher")

    def _do_import(self, body, actor="teacher"):
        """导入的真正实现：校验 → 写库 → 汇总范围，被登录态和登录页两条入口共用。"""
        tables = body.get("tables")
        if tables is None:
            return self._json({
                "ok": False, "code": "bad_format",
                "msg": "这不是一份完整备份：缺少数据表段。请在原设备用「一键导出」重新导出后再导入。"}, 400)
        if not isinstance(tables, dict):
            return self._json({"ok": False, "code": "bad_format",
                               "msg": "备份格式不对（数据表段不是键值结构），文件可能已损坏。"}, 400)

        mode = str(body.get("mode") or "replace")
        if mode not in ("replace", "merge", "skip"):
            return self._json({"ok": False, "code": "bad_mode",
                               "msg": "不认识的冲突处理策略：%s" % mode}, 400)

        force = bool(body.get("force"))
        try:
            res = STORE.import_snapshot(tables, replace=(mode == "replace"),
                                        force=force, mode=mode, actor=actor)
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
                # 账号（管理员 / 教师）权威在 accounts.json，不让旧备份的
                # config.teacher 段把账号表覆盖掉；其余部署设置照旧恢复。
                if k in ("port", "port_migrated", "bind", "teacher"):
                    continue
                CFG[k] = v
            cfg_save()
            notify_cfg()

        # 全量备份里的教师账号表：仅「覆盖」策略才恢复，且保留本机管理员凭据
        if str(body.get("mode") or "") == "replace":
            try:
                ACCOUNTS.restore(body.get("accounts"))
            except Exception:
                pass

        # 账号级配置（2026-11-25）：按账号 id 写回各自工作区，只认本机已存在的
        # 教师账号，避免备份里一个不存在的账号在目标机凭空生成配置。
        acc_cfg_in = body.get("account_configs")
        if isinstance(acc_cfg_in, dict):
            known = set(ACCOUNTS.teacher_ids())
            for aid, acfg in acc_cfg_in.items():
                aid = str(aid or "")
                if (aid == ACC.DEFAULT_TEACHER_ID or aid not in known
                        or not isinstance(acfg, dict)):
                    continue
                target = CFG.for_account(aid)
                target.clear()
                target.update(acfg)
                CFG.save(aid)

        creds = body.get("creds")
        if "creds" in body and isinstance(creds, list):
            # 整体替换：只做 import_creds（纯新增）会留下目标机独有的旧密码
            if mode == "replace":
                STORE.clear_creds()
                counts["student_creds"] = STORE.import_creds(creds)
            elif mode == "skip":
                # 只补本机没有的口令；已设过密码的学生一律不动
                fresh = [c for c in creds
                         if isinstance(c, dict) and not STORE.get_cred(c.get("student_id"))]
                counts["student_creds"] = STORE.import_creds(fresh)
            else:
                # merge：按学号 upsert，双方都保留
                counts["student_creds"] = STORE.import_creds(creds)

        # 打字冒险岛（独立 typing.json，不进 29 表）
        if "typing" in body:
            n = _typing_restore(body.get("typing"))
            if n:
                counts["typing_students"] = n

        # 测验/检测（独立 quiz.json，2.0.38）
        if isinstance(body.get("quiz"), dict) and body["quiz"]:
            counts["quiz"] = _quiz_restore(body.get("quiz"))

        # H5 网页设计草稿（独立 design.json，2.0.39）
        if isinstance(body.get("design"), dict) and body["design"]:
            nd = _design_restore(body.get("design"))
            if nd:
                counts["design_drafts"] = nd

        # 像素画草稿（独立 pixel.json，2.0.47）
        if isinstance(body.get("pixel"), dict) and body["pixel"]:
            np_ = _pixel_restore(body.get("pixel"))
            if np_:
                counts["pixel_drafts"] = np_

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

        if res.get("skipped_rows"):
            warnings.append("按「跳过」策略让过已有记录 %s 条"
                            % res.get("skipped_rows"))
        if res.get("skipped_tables"):
            warnings.append("以下表名本机不认识，已跳过：%s" % "、".join(res["skipped_tables"][:8]))
        if res.get("kept_tables"):
            warnings.append("备份里没有这些表，本机原有数据已保留：%s"
                            % "、".join(res["kept_tables"][:8]))

        notify()
        return self._json({"ok": True, "counts": counts, "rev": STORE.rev(),
                           "rows": res.get("rows", 0),
                           "mode": mode,
                           "skipped_rows": res.get("skipped_rows", 0),
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


def _quiz_snapshot():
    """测验/检测快照（试卷 / 发布 / 提交）。读失败不阻塞导出。"""
    try:
        return QUIZ.snapshot()
    except Exception:
        return {}


def _quiz_restore(data):
    """恢复测验数据，返回恢复的对象总数（0 表示没恢复）。"""
    try:
        return QUIZ.restore(data)
    except Exception:
        return 0


def _design_snapshot():
    """H5 网页设计草稿快照。读失败不阻塞整份备份导出。"""
    try:
        return DESIGN.snapshot()
    except Exception:
        return {}


def _design_restore(data):
    """恢复设计草稿，返回恢复的学生数（0 表示没恢复）。"""
    try:
        return DESIGN.restore(data)
    except Exception:
        return 0


def _pixel_snapshot():
    """像素画草稿快照。读失败不阻塞整份备份导出。"""
    try:
        return PIXEL.snapshot()
    except Exception:
        return {}


def _pixel_restore(data):
    """恢复像素画草稿，返回恢复的学生数（0 表示没恢复）。"""
    try:
        return PIXEL.restore(data)
    except Exception:
        return 0


def _typing_restore(data):
    """整份替换打字成绩，返回恢复的学生数（0 表示没恢复）。

    2.1.7 修复（数据丢失 #6/#11）：TYPING 是 WorkspaceProxy，直接 `TYPING.data = ...`
    只改了代理对象自身的属性，真正的 TypingStore 实例的 data 没变，`_save()` 存的
    还是旧数据 —— 导入后打字进度/宠物/积分/星星全丢。必须用 .current() 拿真实实例。
    """
    if not isinstance(data, dict) or not isinstance(data.get("students"), dict):
        return 0
    try:
        blank = {"students": {}}
        if not data.get("students"):
            data = blank
        real = TYPING.current()          # 当前账号工作区的真实 TypingStore
        real.data = json.loads(json.dumps(data, ensure_ascii=False))
        real._save()
        return len(real.data.get("students") or {})
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


def _periods_today():
    """今天该用哪套作息：按 day_of_week 过滤 + 同 seq 去重（2.0.49 定版）。

    2026-11-20 牛哥反馈：「教师端座位表上面的时间和课表设置的时间不一致，
    学生端签到也跟着出问题」。根因是两端取的不是同一批行 ——
    前端 periodList() 按「今天」过滤，服务端这边却遍历全表 171 行：
    库里同一个 seq 在周一是 08:00、周二~周六是 08:55（历史重排留下的），
    按 seq 排序后谁先命中就返回谁，判出来的节次跟页面上显示的差一节，
    签到时间窗自然全错。

    口径与前端 periodList() 严格对齐：
      · 先取今天的行；今天一套都没建过 → 退回周一那套（前端也这么兜底）；
      · 同 seq 多行只留 id 最小的那条（前端也是这个规则）。
    返回按 seq 升序，调用方不用再排。
    """
    rows = [r for r in STORE.table_rows("periods") if not r.get("deleted")]
    if not rows:
        return []
    dow = time.localtime().tm_wday          # 0=周一，与 lessons.day_of_week 同口径

    def dow_of(r):
        try:
            return int(float(r.get("day_of_week") or 0))
        except Exception:
            return 0

    def seq_of(r):
        try:
            return float(r.get("seq") if r.get("seq") not in (None, "") else r.get("id"))
        except Exception:
            return 999.0

    def id_of(r):
        try:
            return float(r.get("id") or 0)
        except Exception:
            return 0.0

    base = [r for r in rows if dow_of(r) == 0]      # 课表格子用的那套（模板）
    mine = [r for r in rows if dow_of(r) == dow]
    if not mine:
        mine = base
    seen, out = set(), []
    for r in sorted(mine, key=lambda x: (seq_of(x), id_of(x))):
        k = seq_of(r)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(r)
    # 今天缺某个节次 → 用周一那套的时间补出来（前端 periodList 也这么补，只读不写库）。
    # 不补的话：页面上下拉里看得到第 1 节，服务端却判不出第 1 节，又差一节。
    if dow != 0 and base:
        for r in sorted(base, key=lambda x: seq_of(x)):
            k = seq_of(r)
            if not k or k in seen:
                continue
            seen.add(k)
            out.append(dict(r, day_of_week=dow, id=dow * 100 + int(k)))
        out.sort(key=seq_of)
    return out


def _lesson_ids_for_period(period):
    """把作息节次号（seq）映射回课表格子用的 period_id（作息表 id）。
    与 _class_lesson_at 同一口径：id / seq 双比，查不到就按号直比。"""
    ids = set()
    for r in STORE.table_rows("periods"):
        if r.get("deleted"):
            continue
        try:
            if int(float(r.get("seq") if r.get("seq") not in (None, "") else r.get("id"))) == int(period):
                for k in ("id", "seq"):
                    try:
                        ids.add(int(float(r.get(k))))
                    except Exception:
                        pass
                break
        except Exception:
            continue
    if not ids:
        try:
            ids.add(int(period))
        except Exception:
            pass
    return ids


def _classes_with_lesson_at(dow, period):
    """今天这一节真正排了课的班级集合（节次级，不是「当天有课」）。"""
    ids = _lesson_ids_for_period(period)
    out = set()
    for l in STORE.table_rows("lessons"):
        if l.get("deleted") or l.get("class_id") in (None, ""):
            continue
        try:
            if int(float(l.get("day_of_week"))) != int(dow):
                continue
            if int(float(l.get("period_id"))) in ids:
                out.add(str(l.get("class_id")))
        except Exception:
            continue
    return out


def _manual_checkin_min():
    """老师手动设置的签到时间 → 当天分钟数；没填 / 格式不对返回 None。"""
    t = str((CFG.get("checkin") or {}).get("manual_time") or "").strip()
    try:
        a = t.split(":")
        if len(a) != 2:
            return None
        h, m = int(a[0]), int(a[1])
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return None


def absent_sweep_once():
    # 老师把签到关了：连自动记缺勤一起停，否则关了功能还在后台刷旷课
    if not bool((CFG.get("checkin") or {}).get("enabled", True)):
        return 0
    rows = _periods_today()          # 2.0.49：按今天的作息判「本节结束」
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
    manual = _manual_checkin_min()
    if manual is not None:
        # 手动签到时间：到点 15 分钟后开始扫，45 分钟后停止
        if not (manual + 15 <= cur <= manual + 45):
            return 0
    else:
        s = _mins_of(curp.get("start_time"))
        e = _mins_of(curp.get("end_time"))
        if not (s + 15 <= cur <= e + 30):
            return 0                      # 15 分钟线之前 / 下课 30 分钟后不扫
    try:
        period = int(float(curp.get("seq") or curp.get("id") or 1))
    except Exception:
        return 0
    d = today_str()
    # 2026-11-25 牛哥定版：缺勤扫描也要按「今天这一节有没有排课」判定，
    # 跟登录签到同一口径。旧逻辑只看当天任意一节有课，老师没排的节次
    # 也会把全班扫成缺勤。只有整个课表都没建过时才退化为「本节有人签到」。
    lesson_cls = _classes_with_lesson_at(dow, period)
    if not lesson_cls and not [l for l in STORE.table_rows("lessons") if not l.get("deleted")]:
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


def _heal_exec_bits():
    """把更新换装弄丢的可执行位补回来（2026-11-05，macOS / Linux）。

    老版本 updater 解压时用 open(target, "wb") 落地，zip 里存的 755 全丢了；
    换装后 app/启动教师端.command 变成 0644，老师下次双击启动就是「没有权限」。
    updater 已修（解压时 chmod），这里再兜一次：服务每次启动都检查并补上，
    已经在用坏版本更新过、卡在「打不开」的机器升级后能自动恢复。
    """
    if os.name == "nt":
        return
    base = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(base)
    for rel in ("app/启动教师端.command", "app/启动教师端.sh",
                "app/submission-system/run.command", "docker/entrypoint.sh",
                "启动工作台.command"):
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(p):
            continue
        try:
            st = os.stat(p)
            if not (st.st_mode & 0o111):
                os.chmod(p, (st.st_mode & 0o777) | 0o755)
                print("[workbench] 已补回可执行权限：%s" % rel)
        except Exception:
            pass


def _print_admin_hint():
    """管理员账号/密码每次启动都在终端显示（2026-11-25 牛哥要求：方便现场查看登录）。

    登录页不再展示（2026-11-05 定版），但终端每次都打印：
      · 密码仍是部署默认 / 初始密码 → 明文打印；
      · 已改过 → 库里只剩哈希还原不出来，明确提示账号名与重置办法，
        绝不打印一个已经失效的默认值误导用户。
    """
    try:
        user = ACCOUNTS.admin_username()
        # 只走哈希校验：admin_initial_password() 在改密后仍返回旧默认值，
        # 直接用会把已失效的密码打到终端。
        pwd = ACCOUNTS.admin_password_if_default()
    except Exception:
        return
    if pwd:
        print("[workbench] 管理员账号 %s ／ 密码 %s" % (user, pwd))
        print("            当前密码仍是默认值；登录后请尽快修改。")
    elif user:
        print("[workbench] 管理员账号 %s（密码已修改，终端无法还原；忘记请用 launch.py --reset-password）"
              % user)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0,
                    help="端口（默认读 data/config.json，首次为 20020）")
    ap.add_argument("--bind", default=CFG.get("bind", "0.0.0.0"))
    ap.add_argument("--no-dialog", action="store_true", help="端口被占时不弹窗")
    args = ap.parse_args()
    global PORT
    _heal_exec_bits()
    # 自动更新重启标记（2026-11-05）：/api/v2/update/restart 用 os.execv 换进程，
    # Windows 上会有一段「新旧进程并存」的窗口，端口还被旧进程占着。
    # 这个标记让本进程知道「端口是我自己的，等它空出来即可，别判成重复启动」。
    # pop 掉：只对这一次启动有效，避免影响后续手动启动。
    restarting = os.environ.pop("WB_RESTARTING", "") == "1"
    # 一次性迁移：老配置里写死 8080（旧默认值）→ 换成不常用的 20020。
    # 只在“用户没显式指定 --port”时做，且只做一次（port_migrated 标记）。
    if not args.port:
        try:
            raw = PORTX.read_cfg(CONFIG_PATH)
            if not raw.get("port_migrated") and int(raw.get("port") or 0) in (0, 8080):
                CFG["port"] = PORTX.DEFAULT_PORT
                CFG["port_migrated"] = True
                CFG.save()
                print("[workbench] 默认端口已从 8080 改为 %d（避免和常用软件冲突）"
                      % PORTX.DEFAULT_PORT)
        except Exception:
            pass
    want = args.port or PORTX.config_port(CONFIG_PATH, PORTX.DEFAULT_PORT)

    def mine(p):
        # 探活要跟当前协议一致：HTTPS 模式下用 http 探会失败、误判端口空闲
        _https = os.environ.get("WB_HTTPS", "").strip() in ("1", "true", "yes")
        _url = ("https" if _https else "http") + "://127.0.0.1:%d/api/v2/ping" % p
        try:
            if _https:
                import ssl
                _c = ssl._create_unverified_context()
                with urllib.request.urlopen(_url, timeout=1.0, context=_c) as r:
                    return json.loads(r.read().decode()).get("service") == "workbench"
            with urllib.request.urlopen(_url, timeout=1.0) as r:
                return json.loads(r.read().decode()).get("service") == "workbench"
        except Exception:
            return False

    if restarting:
        # 自动更新重启（2026-11-05 修 macOS「更新后起不来」）：
        # 这个端口是我自己刚放开的，但旧进程可能还没退干净，浏览器那边的
        # SSE / keep-alive 连接也会让端口短暂停在 TIME_WAIT。这期间：
        #   ① 绝不弹端口设置窗 —— 那个 Tk 窗就是 macOS 上看到的「选择端口号」；
        #   ② 绝不换端口 —— 换了以后老师的书签、桌面图标全指着老地址，等于没起来。
        # 先等它变成「起服务能绑上」的状态（带 SO_REUSEADDR 判断），最多 20 秒。
        _t0 = time.time()
        while time.time() - _t0 < 20:
            # trust=True：这个端口上一刻还是本进程自己的监听口，不再做
            # 「有没有别人接着」的探测（Windows 关掉监听后端口会挂起，
            # 探测只会一直超时，把重启拖死）。
            if PORTX.can_bind(want, trust=True):
                break
            time.sleep(0.2)
    port, state = PORTX.pick_port(want, cfg_path=CONFIG_PATH, is_mine=mine,
                                  no_dialog=args.no_dialog or restarting,
                                  reuse_ok=True, trust_own=restarting)
    if restarting and state == "mine":
        # 新的进程映像起来了，但旧进程还没把 socket 放掉（Windows 上 execv 是
        # 「起新进程 → 退旧进程」，有一段并存窗口）。等它放掉；
        # 8 秒还占着 = 旧进程根本没退，服务其实还在跑，保持 mine 走「已有实例」。
        t0 = time.time()
        while time.time() - t0 < 8 and not PORTX.can_bind(want, trust=True):
            time.sleep(0.15)
        if PORTX.can_bind(want, trust=True):
            port, state = want, "free"
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

    # HTTPS（2026-11-25）：截图工具的 getDisplayMedia / showDirectoryPicker
    # 只在安全上下文开放，纯 HTTP 下截图功能废掉。离线包默认开 HTTPS，
    # 用随包自签证书（app/certs/），用户首次点「继续访问」即可，无需装证书。
    # Docker 路径不设 WB_HTTPS（nginx 网关已做 TLS），保持原样。
    USE_TLS = os.environ.get("WB_HTTPS", "").strip() in ("1", "true", "yes")
    if USE_TLS:
        try:
            import ssl
            _cert_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")
            _ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            _ctx.load_cert_chain(os.path.join(_cert_dir, "server.crt"),
                                 os.path.join(_cert_dir, "server.key"))
            httpd.socket = _ctx.wrap_socket(httpd.socket, server_side=True)
            print("[workbench] HTTPS 已启用（内置自签证书，浏览器首次需点「继续访问」）")
        except Exception as e:
            # 证书缺失/损坏不能让整个服务起不来——退回 HTTP，截图功能不可用但其他功能正常
            print("[workbench] HTTPS 启用失败，退回 HTTP：%s" % e)
            USE_TLS = False

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
    _scheme = "https" if USE_TLS else "http"
    print("[workbench] 服务已启动 · 教师端 %s://127.0.0.1:%d/teacher/（本机地址）" % (_scheme, port))
    for ip in lan_ips()[:3]:
        print("[workbench] 学生端 %s://%s:%d/s/" % (_scheme, ip, port))
    _print_admin_hint()
    # 2026-11-25 静态加密能力自检（失败不影响启动，仅提示降级）
    try:
        import fec as _fec
        _ok, _msg = _fec.self_test()
        print("[workbench] 文件加密能力：%s（%s）" % ("可用" if _ok else "不可用", _msg))
    except Exception as _e:
        print("[workbench] 文件加密能力：不可用（%s）" % _e)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
