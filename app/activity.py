#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""课堂活动数据仓（自研内嵌）

职责边界
--------
只做「活动 + 提交 + 附件」三件事，**不做登录**：
教师身份来自工作台的会话（wb_sid），学生身份来自服务端签发的票（wbT），
两者都在 server.py 里判定完再调用这里。数据模型认识「班级 / 学号」——
身份落成列（sid / name / cls），提交率、缺交名单、按班统计可以直接查。

对外契约（前端与自定义页面按此解析）
------------------------------------
  GET  /api/act/tasks            → {success, tasks:[{task_id,title,api}]}
  GET  /api/act/<task>           → 等同 /all
  GET  /api/act/<task>/all       → {submissions, total_submissions, task_id, task_title}
  GET  /api/act/<task>/count     → {total_submissions}
  POST /api/act/<task>           → 收提交（JSON / 表单 / multipart 文件）
附件 URL 用 `/static/uploads/<task>/<file>`，课件库归档、自定义大屏
（window.ACT_DATA）都按这个格式解析，所以格式不能改。

仅标准库：sqlite3 + email（解析 multipart）+ csv。
"""
import csv
import io
import json
import os
import random
import re
import sqlite3
import string
import threading
import time
import uuid

# 单个文件与整份提交的上限：与 server.py 里的学生提交中转保持一致
MAX_FILE = 40 * 1024 * 1024
MAX_TOTAL = 40 * 1024 * 1024
# 允许直出的类型（其余一律按附件下载，避免在站点同源下执行脚本）
_INLINE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg",
               "pdf", "mp4", "webm", "ogg", "mp3", "wav", "m4a", "txt"}
_SID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def gen_task_id(n=11):
    """11 位活动号（小写字母 + 数字，与历史活动号风格一致）。"""
    alpha = string.ascii_lowercase + string.digits
    return "".join(random.choice(alpha) for _ in range(n))


def _safe_ext(name):
    ext = os.path.splitext(str(name or ""))[1].lstrip(".").lower()
    if not ext or not re.match(r"^[a-z0-9]{1,8}$", ext):
        return ""
    return ext


def parse_multipart(raw, ctype):
    """解析 multipart/form-data → (fields, files)。

    Python 3.13 已删除 cgi 模块，这里用 email 包解析（它本来就实现 RFC 2046，
    中文文件名、RFC2231 编码都能认）。standard library only。
    """
    from email.parser import BytesParser
    from email.policy import default as _policy

    fields, files = {}, []
    if not raw:
        return fields, files
    head = ("Content-Type: %s\r\nMIME-Version: 1.0\r\n\r\n" % ctype).encode("utf-8", "replace")
    try:
        msg = BytesParser(policy=_policy).parsebytes(head + raw)
    except Exception:
        return fields, files
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        cd = str(part.get("Content-Disposition") or "")
        if "form-data" not in cd.lower():
            continue
        name = part.get_param("name", header="content-disposition") or ""
        filename = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if filename:
            files.append({"field": name, "name": filename,
                          "mime": part.get_content_type() or "application/octet-stream",
                          "data": payload})
            continue
        try:
            val = payload.decode("utf-8")
        except Exception:
            val = payload.decode("utf-8", "replace")
        if name in fields:                       # 同名多值（复选框）：收成列表
            cur = fields[name]
            fields[name] = (cur if isinstance(cur, list) else [cur]) + [val]
        else:
            fields[name] = val
    return fields, files


class ActivityStore(object):
    """活动与提交的 SQLite 存储。与工作台共用同一个库文件（WAL）。"""

    def __init__(self, db_path, files_dir):
        self.path = db_path
        self.files_dir = files_dir
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        os.makedirs(files_dir, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self._init()

    # ------------------------------------------------------------------
    # 表结构
    # ------------------------------------------------------------------
    def _init(self):
        with self.lock:
            c = self.conn
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=8000")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS act_task (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              task_id     TEXT UNIQUE NOT NULL,
              title       TEXT NOT NULL,
              description TEXT DEFAULT '',
              class_id    INTEGER DEFAULT 0,
              course      TEXT DEFAULT '',
              archived    INTEGER DEFAULT 0,
              created_at  TEXT,
              updated_at  TEXT
            );
            CREATE TABLE IF NOT EXISTS act_submission (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              task_key     TEXT NOT NULL,
              data         TEXT NOT NULL,
              sid          TEXT DEFAULT '',
              name         TEXT DEFAULT '',
              cls          TEXT DEFAULT '',
              ip           TEXT DEFAULT '',
              files        TEXT DEFAULT '[]',
              submitted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_act_sub_task ON act_submission(task_key);
            CREATE INDEX IF NOT EXISTS idx_act_sub_sid ON act_submission(task_key, sid);
            CREATE TABLE IF NOT EXISTS act_file (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              task_key   TEXT,
              sub_id     INTEGER DEFAULT 0,
              stored     TEXT,
              orig       TEXT,
              size       INTEGER DEFAULT 0,
              mime       TEXT DEFAULT '',
              created_at TEXT
            );
            """)
            c.commit()

    # ------------------------------------------------------------------
    # 活动 CRUD
    # ------------------------------------------------------------------
    def _task_row(self, r, counts=None):
        return {
            "task_id": r["task_id"],
            "title": r["title"],
            "description": r["description"] or "",
            "class_id": int(r["class_id"] or 0),
            "course": r["course"] or "",
            "archived": int(r["archived"] or 0),
            "created_at": r["created_at"] or "",
            "updated_at": r["updated_at"] or "",
            "count": int((counts or {}).get(r["task_id"], 0)),
        }

    def tasks(self, include_archived=False):
        with self.lock:
            q = "SELECT * FROM act_task"
            if not include_archived:
                q += " WHERE archived=0"
            q += " ORDER BY id DESC"
            rows = self.conn.execute(q).fetchall()
            cnt = {r["task_key"]: int(r["c"]) for r in self.conn.execute(
                "SELECT task_key, COUNT(*) AS c FROM act_submission GROUP BY task_key")}
        return [self._task_row(r, cnt) for r in rows]

    def get_task(self, task_key):
        with self.lock:
            r = self.conn.execute("SELECT * FROM act_task WHERE task_id=?",
                                  (str(task_key),)).fetchone()
            if not r:
                return None
            n = self.conn.execute("SELECT COUNT(*) AS c FROM act_submission WHERE task_key=?",
                                  (str(task_key),)).fetchone()
        return self._task_row(r, {r["task_id"]: int(n["c"] or 0)})

    def create_task(self, title, description="", class_id=0, course=""):
        title = str(title or "").strip()
        if not title:
            raise ValueError("活动标题不能为空")
        for _ in range(20):
            tid = gen_task_id()
            with self.lock:
                hit = self.conn.execute("SELECT 1 FROM act_task WHERE task_id=?", (tid,)).fetchone()
            if not hit:
                break
        else:
            tid = uuid.uuid4().hex[:11]
        ts = _now()
        with self.lock:
            self.conn.execute(
                "INSERT INTO act_task(task_id,title,description,class_id,course,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (tid, title[:200], str(description or "")[:4000], int(class_id or 0),
                 str(course or "")[:100], ts, ts))
            self.conn.commit()
        return self.get_task(tid)

    def update_task(self, task_key, **fields):
        allow = {"title", "description", "class_id", "course", "archived"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allow:
                continue
            if k == "title":
                v = str(v or "").strip()[:200]
                if not v:
                    raise ValueError("活动标题不能为空")
            elif k == "description":
                v = str(v or "")[:4000]
            elif k == "class_id":
                v = int(v or 0)
            elif k == "course":
                v = str(v or "")[:100]
            elif k == "archived":
                v = 1 if v else 0
            sets.append("%s=?" % k)
            vals.append(v)
        if not sets:
            return self.get_task(task_key)
        sets.append("updated_at=?")
        vals.append(_now())
        vals.append(str(task_key))
        with self.lock:
            self.conn.execute("UPDATE act_task SET %s WHERE task_id=?" % ", ".join(sets), vals)
            self.conn.commit()
        return self.get_task(task_key)

    def delete_task(self, task_key):
        """删活动：连同提交记录与附件目录一起清掉（不可恢复，前端要二次确认）。"""
        task_key = str(task_key)
        with self.lock:
            self.conn.execute("DELETE FROM act_submission WHERE task_key=?", (task_key,))
            self.conn.execute("DELETE FROM act_file WHERE task_key=?", (task_key,))
            c = self.conn.execute("DELETE FROM act_task WHERE task_id=?", (task_key,))
            self.conn.commit()
        self._rm_tree(task_key)
        return c.rowcount > 0

    def _rm_tree(self, task_key):
        d = os.path.join(self.files_dir, str(task_key))
        if not SID_OK(task_key):
            return
        try:
            for name in os.listdir(d):
                try:
                    os.remove(os.path.join(d, name))
                except OSError:
                    pass
            os.rmdir(d)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 附件
    # ------------------------------------------------------------------
    def save_file(self, task_key, filename, blob, mime=""):
        """落盘一个附件，返回 (相对 URL, 文件信息)。"""
        if len(blob) > MAX_FILE:
            raise ValueError("文件 %s 超过 40MB 上限" % filename)
        ext = _safe_ext(filename)
        name = uuid.uuid4().hex[:12] + (("." + ext) if ext else "")
        d = os.path.join(self.files_dir, str(task_key))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "wb") as f:
            f.write(blob)
        info = {"stored": name, "orig": str(filename or name)[:200],
                "size": len(blob), "mime": str(mime or "")[:100]}
        return "/static/uploads/%s/%s" % (task_key, name), info

    def file_path(self, url):
        """把 `/static/uploads/<task>/<file>` 还原成本机路径（严格校验，防穿越）。"""
        u = str(url or "").strip()
        for pre in ("/qf/static/uploads/", "/static/uploads/"):
            # /qf/ 前缀只是历史数据兼容：旧备份里可能存有带前缀的附件地址，
            # 对外路由已不再提供 /qf/*，这里识别后按同一条本机路径还原。
            if u.startswith(pre):
                u = u[len(pre):]
                break
        else:
            return None
        parts = [p for p in u.split("/") if p not in ("", ".")]
        if len(parts) != 2:
            return None
        task, name = parts
        if not SID_OK(task) or not _SID_RE.match(name) or ".." in name:
            return None
        p = os.path.join(self.files_dir, task, name)
        return p if os.path.isfile(p) else None

    # ------------------------------------------------------------------
    # 提交
    # ------------------------------------------------------------------
    def add_submission(self, task_key, data, files=None, ip="", sid="", name="", cls=""):
        """收一条提交。files = [{name, data, mime}]，写盘后把 URL 挂进 data['attachment']。"""
        task_key = str(task_key)
        if not self.get_task(task_key):
            raise LookupError("活动不存在")
        data = dict(data or {})
        urls, infos, total = [], [], 0
        for f in (files or []):
            blob = f.get("data") or b""
            total += len(blob)
            if len(blob) > MAX_FILE or total > MAX_TOTAL:
                raise ValueError("附件超过大小上限（单文件 40MB / 合计 40MB）")
            url, info = self.save_file(task_key, f.get("name"), blob, f.get("mime"))
            urls.append(url)
            infos.append(info)
        if urls:
            data["attachment"] = urls[0] if len(urls) == 1 else urls
        # 身份：显式列 + JSON 里的中文字段都写，前端/导出/归档各处都认
        sid = str(sid or data.get("学号") or "").strip()
        name = str(name or data.get("姓名") or "").strip()
        cls = str(cls or data.get("班级") or "").strip()
        if sid:
            data.setdefault("学号", sid)
        if name:
            data.setdefault("姓名", name)
        if cls:
            data.setdefault("班级", cls)
        if ip:
            data.setdefault("_ip", ip)
        ts = _now()
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO act_submission(task_key,data,sid,name,cls,ip,files,submitted_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (task_key, json.dumps(data, ensure_ascii=False), sid, name, cls, ip,
                 json.dumps([u for u in urls], ensure_ascii=False), ts))
            sub_id = int(cur.lastrowid)
            for info, url in zip(infos, urls):
                self.conn.execute(
                    "INSERT INTO act_file(task_key,sub_id,stored,orig,size,mime,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (task_key, sub_id, info["stored"], info["orig"], info["size"],
                     info["mime"], ts))
            self.conn.commit()
        return {"id": sub_id, "attachment": data.get("attachment"), "submitted_at": ts}

    def submissions(self, task_key):
        """全量提交（`/all` 的标准返回）。"""
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM act_submission WHERE task_key=? ORDER BY id ASC",
                (str(task_key),)).fetchall()
        out = []
        for r in rows:
            try:
                d = json.loads(r["data"])
            except Exception:
                d = {"raw": r["data"]}
            if not isinstance(d, dict):
                d = {"data": d}
            d["submitted_at"] = r["submitted_at"] or ""
            d["_submission_id"] = int(r["id"])
            if r["sid"] and not d.get("学号"):
                d["学号"] = r["sid"]
            if r["name"] and not d.get("姓名"):
                d["姓名"] = r["name"]
            if r["cls"] and not d.get("班级"):
                d["班级"] = r["cls"]
            out.append(d)
        return out

    def count(self, task_key):
        with self.lock:
            r = self.conn.execute("SELECT COUNT(*) AS c FROM act_submission WHERE task_key=?",
                                  (str(task_key),)).fetchone()
        return int(r["c"] or 0)

    # ------------------------------------------------------------------
    # 活动本身的教学资料（sub_id=0 的附件行）
    # ------------------------------------------------------------------
    def add_attachment(self, task_key, filename, blob, mime=""):
        url, info = self.save_file(task_key, filename, blob, mime)
        with self.lock:
            self.conn.execute(
                "INSERT INTO act_file(task_key,sub_id,stored,orig,size,mime,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (str(task_key), 0, info["stored"], info["orig"], info["size"],
                 info["mime"], _now()))
            self.conn.commit()
        return {"url": url, "name": info["orig"], "size": info["size"]}

    def attachments(self, task_key):
        with self.lock:
            rows = self.conn.execute(
                "SELECT stored, orig, size, mime, created_at FROM act_file "
                "WHERE task_key=? AND sub_id=0 ORDER BY id ASC", (str(task_key),)).fetchall()
        return [{"url": "/static/uploads/%s/%s" % (task_key, r["stored"]),
                 "name": r["orig"], "size": int(r["size"] or 0),
                 "created_at": r["created_at"] or ""} for r in rows]

    def delete_attachment(self, task_key, url):
        p = self.file_path(url)
        name = os.path.basename(p) if p else ""
        if not name:
            return False
        with self.lock:
            c = self.conn.execute(
                "DELETE FROM act_file WHERE task_key=? AND sub_id=0 AND stored=?",
                (str(task_key), name))
            self.conn.commit()
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except OSError:
                pass
        return c.rowcount > 0

    def delete_submission(self, sub_id):
        with self.lock:
            r = self.conn.execute("SELECT task_key, files FROM act_submission WHERE id=?",
                                  (int(sub_id),)).fetchone()
            if not r:
                return False
            self.conn.execute("DELETE FROM act_submission WHERE id=?", (int(sub_id),))
            self.conn.execute("DELETE FROM act_file WHERE sub_id=?", (int(sub_id),))
            self.conn.commit()
        try:
            for url in json.loads(r["files"] or "[]"):
                p = self.file_path(url)
                if p and os.path.isfile(p):
                    os.remove(p)
        except Exception:
            pass
        return True

    def clear(self, task_key):
        with self.lock:
            ids = [int(x["id"]) for x in self.conn.execute(
                "SELECT id FROM act_submission WHERE task_key=?", (str(task_key),))]
            c = self.conn.execute("DELETE FROM act_submission WHERE task_key=?", (str(task_key),))
            self.conn.execute("DELETE FROM act_file WHERE task_key=?", (str(task_key),))
            self.conn.commit()
        self._rm_tree(task_key)
        return int(c.rowcount or 0), ids

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def export_rows(self, task_key):
        """扁平化成二维表：固定列 + 提交里出现过的所有字段。"""
        subs = self.submissions(task_key)
        fixed = ["序号", "提交时间", "学号", "姓名", "班级", "IP"]
        extra = []
        for s in subs:
            for k in s:
                if k in ("_submission_id", "_ip", "submitted_at") or k in fixed:
                    continue
                if k not in extra:
                    extra.append(k)
        head = fixed + extra
        rows = []
        for i, s in enumerate(subs, 1):
            line = [i, s.get("submitted_at") or "", s.get("学号") or "",
                    s.get("姓名") or "", s.get("班级") or "", s.get("_ip") or ""]
            for k in extra:
                v = s.get(k, "")
                if isinstance(v, (list, dict)):
                    v = json.dumps(v, ensure_ascii=False)
                line.append("" if v is None else str(v))
            rows.append(line)
        return head, rows

    def export_csv(self, task_key):
        head, rows = self.export_rows(task_key)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(head)
        for r in rows:
            w.writerow(r)
        # BOM：Excel 双击打开中文不乱码（老师最常用的动作）
        return ("\ufeff" + buf.getvalue()).encode("utf-8")

    # ------------------------------------------------------------------
    # 备份 / 迁移
    # ------------------------------------------------------------------
    def snapshot(self, file_cap=40 * 1024 * 1024, file_max=10 * 1024 * 1024):
        """全量快照：{tasks, submissions, files:{相对路径: base64}, meta}。

        体积口径与工作台既有附件备份一致（单文件 10MB / 合计 40MB），
        超限的点名跳过，不静默丢。
        """
        import base64
        with self.lock:
            tasks = [dict(r) for r in self.conn.execute(
                "SELECT * FROM act_task ORDER BY id ASC").fetchall()]
            subs = [dict(r) for r in self.conn.execute(
                "SELECT * FROM act_submission ORDER BY id ASC").fetchall()]
            files_tbl = [dict(r) for r in self.conn.execute(
                "SELECT * FROM act_file ORDER BY id ASC").fetchall()]
        blobs, skipped, total = {}, [], 0
        for f in files_tbl:
            rel = "%s/%s" % (f["task_key"], f["stored"])
            full = os.path.join(self.files_dir, f["task_key"], f["stored"])
            if not os.path.isfile(full):
                continue
            size = os.path.getsize(full)
            if size > file_max:
                skipped.append({"name": rel, "size": size, "why": "single"})
                continue
            if total + size > file_cap:
                skipped.append({"name": rel, "size": size, "why": "cap"})
                continue
            try:
                with open(full, "rb") as fh:
                    blobs[rel] = base64.b64encode(fh.read()).decode("ascii")
                total += size
            except OSError:
                skipped.append({"name": rel, "size": size, "why": "read"})
        return {"tasks": tasks, "submissions": subs, "files": blobs,
                "meta": {"files": len(blobs), "bytes": total, "skipped": skipped,
                         "cap": file_cap, "single_max": file_max}}

    def restore(self, snap, replace=True):
        """从快照恢复。replace=True 时先清空现有活动数据（与工作台备份语义一致）。"""
        import base64
        if not isinstance(snap, dict):
            return {"tasks": 0, "submissions": 0, "files": 0}
        tasks = snap.get("tasks") or []
        subs = snap.get("submissions") or []
        blobs = snap.get("files") or {}
        n_t = n_s = n_f = 0
        with self.lock:
            if replace:
                self.conn.execute("DELETE FROM act_task")
                self.conn.execute("DELETE FROM act_submission")
                self.conn.execute("DELETE FROM act_file")
            for t in tasks:
                if not isinstance(t, dict) or not t.get("task_id"):
                    continue
                self.conn.execute(
                    "INSERT INTO act_task(task_id,title,description,class_id,course,archived,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                    "title=excluded.title, description=excluded.description, "
                    "class_id=excluded.class_id, course=excluded.course, "
                    "archived=excluded.archived, updated_at=excluded.updated_at",
                    (str(t.get("task_id")), str(t.get("title") or "未命名")[:200],
                     str(t.get("description") or "")[:4000], int(t.get("class_id") or 0),
                     str(t.get("course") or "")[:100], int(t.get("archived") or 0),
                     str(t.get("created_at") or _now()), str(t.get("updated_at") or _now())))
                n_t += 1
            for s in subs:
                if not isinstance(s, dict) or not s.get("task_key"):
                    continue
                self.conn.execute(
                    "INSERT INTO act_submission(task_key,data,sid,name,cls,ip,files,submitted_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (str(s.get("task_key")), str(s.get("data") or "{}"),
                     str(s.get("sid") or ""), str(s.get("name") or ""),
                     str(s.get("cls") or ""), str(s.get("ip") or ""),
                     str(s.get("files") or "[]"), str(s.get("submitted_at") or _now())))
                n_s += 1
            self.conn.commit()
        for rel, b64 in blobs.items():
            parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".")]
            if len(parts) != 2 or ".." in parts or not SID_OK(parts[0]):
                continue
            try:
                blob = base64.b64decode(b64)
            except Exception:
                continue
            d = os.path.join(self.files_dir, parts[0])
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, parts[1]), "wb") as fh:
                fh.write(blob)
            n_f += 1
        return {"tasks": n_t, "submissions": n_s, "files": n_f}

    def import_task(self, task_id, title, description="", created_at=None,
                    class_id=0, course="", archived=0):
        """迁移用：按原 task_id 写入（已存在则更新标题，不覆盖提交）。"""
        ts = str(created_at or _now())
        with self.lock:
            self.conn.execute(
                "INSERT INTO act_task(task_id,title,description,class_id,course,archived,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                "title=excluded.title, description=excluded.description, updated_at=excluded.updated_at",
                (str(task_id), str(title or "未命名")[:200], str(description or "")[:4000],
                 int(class_id or 0), str(course or "")[:100], int(archived or 0), ts, ts))
            self.conn.commit()
        return self.get_task(task_id)

    def import_submission(self, task_key, data, submitted_at=None, sid="", name="", cls="", ip=""):
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO act_submission(task_key,data,sid,name,cls,ip,files,submitted_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (str(task_key), json.dumps(data, ensure_ascii=False), str(sid or ""),
                 str(name or ""), str(cls or ""), str(ip or ""), "[]",
                 str(submitted_at or _now())))
            self.conn.commit()
            return int(cur.lastrowid)


def SID_OK(v):
    """活动号只允许字母数字（拼路径前必须过这一关）。"""
    return bool(re.match(r"^[A-Za-z0-9._-]{1,64}$", str(v or "")))
