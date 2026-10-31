#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 服务端数据仓（仅标准库）

设计要点
--------
1. **通用行存储**：不按 24 张业务表建 24 个 schema，统一放 rows(tbl, rid, data)。
   业务字段仍然原样保存（JSON），所以前端 3 万行的读写代码不用知道表结构。
   代价是不能用 SQL 做字段级约束，收益是加一张表/加一个字段零迁移。

2. **全局 rev**：每次写入在 changelog 追加一条自增记录，行上同时记 rev。
   增量同步只有一个条件：rev > since。不需要逐表维护时间戳，也不会漏。

3. **墓碑**：删除只写 deleted=1 并占一个新 rev，保留 tombstone_days 天。
   学生端收到墓碑即从本地移除，不会“删了又活过来”。

4. **行指纹**：sha1(规范化 JSON)。同一行重复推送直接丢弃，
   避免“老师点一下保存，整表重传”。
"""
import os
import json
import time
import hashlib
import sqlite3
import threading
from datetime import datetime

SCHEMA_VERSION = 1

# 与 index.html 的 SYNC_TABLES 对应的 24 张表 + 课件链接
TABLES = [
    "todos", "classes", "students", "seats", "periods", "courses", "lessons",
    "attendance", "homeworks", "homework_records", "morality_items",
    "morality_records", "scores", "exams", "exam_scores", "groups", "dutys",
    "duty_areas", "duty_positions", "course_progress", "progress_cols",
    "seat_tpl", "student_notes", "quick_links", "courseware_links",
    "leave_requests",   # 学生请假申请（教师端考勤页/控制台审批）
    "materials",        # 教师下发的学习资料（按班级），学生端「资料」页签只读
]

# 这些字段是前端临时态，不进指纹也不落库（否则同一行会无限“变脏”）
TRANSIENT = {"_sel", "_open", "_tmp", "_hover", "__v"}


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _num(v, default=0.0):
    try:
        f = float(v)
        return f
    except Exception:
        return default


def ts_key(v):
    """把各种写法的时间戳统一成可比较的 epoch 秒。

    前端两条路混着用：有的地方 new Date().toISOString() → "2026-10-30T11:53:46.337Z"（UTC），
    有的地方自己拼 "2026-10-30 20:20:00"（本地时间）。
    直接比较字符串会得出荒唐结论（'T' > ' '，UTC 看起来永远比本地“新”），
    于是正常的保存被判成冲突、真冲突又放过。这里统一成绝对时间再比。
    """
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v or "").strip()
    if not s:
        return 0.0
    try:
        t = s if "T" in s else s.replace(" ", "T", 1)
        if t.endswith("Z"):
            t = t[:-1] + "+00:00"
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            return dt.timestamp()            # 无时区：按本机时区理解
        return dt.astimezone().timestamp()   # 带时区：换成绝对时间
    except Exception:
        return 0.0


def norm_ts(v, fallback=None):
    """统一存成本地 "YYYY-MM-DD HH:MM:SS"，库里不再混格式。"""
    k = ts_key(v)
    if not k:
        return fallback if fallback is not None else _now()
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(k))


def row_id(tbl, row):
    """稳定行 ID。与前端 rowIdOf() 完全一致的规则，两端指向同一条记录。"""
    if not isinstance(row, dict):
        return None
    rid = row.get("id")
    if rid is not None and rid != "":
        return str(rid)
    if tbl == "lessons":
        v = _num(row.get("day_of_week")) * 1000000 + _num(row.get("period_id")) * 1000 + _num(row.get("class_id"))
        return str(int(v))
    if tbl == "exam_scores":
        v = _num(row.get("exam_id")) * 10000000 + _num(row.get("student_id")) * 100 + _num(row.get("course_id"))
        return str(int(v))
    # 兜底：内容指纹。改内容会变成一条新行（旧行留墓碑），但绝不会丢失。
    return "h" + _fingerprint(row)[:16]


def _clean(row):
    if not isinstance(row, dict):
        return {}
    return {k: v for k, v in row.items() if k not in TRANSIENT}


def _is_tombstone(row):
    """客户端推来的“删除”行：deleted 为真（1/true/'1'）即视为墓碑。

    前端删一行时本地先移除，同步时补发 {原行, deleted:1}，
    服务端必须把它当删除处理——否则墓碑会被 upsert 当成普通行写回，
    已删的学生/座位在换设备后会“复活”。
    """
    if not isinstance(row, dict):
        return False
    v = row.get("deleted")
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y")
    return False


def _fingerprint(row):
    try:
        s = json.dumps(_clean(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        s = str(row)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def _tomb_hash(rid):
    """墓碑行专用指纹。

    不能沿用原内容的 hash：否则离线设备把同一条内容再推一次，
    “内容没变就跳过”的分支会把它认成恢复导入，孤儿行就复活了。
    """
    return _fingerprint({"__deleted__": True, "id": str(rid)})


class Store(object):
    def __init__(self, db_path):
        self.path = db_path
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()
        healed = self.heal_empty_rows()
        if healed:
            self.audit("system", "heal-empty-rows", "清理 %d 条空行" % healed)

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _init(self):
        with self.lock:
            c = self.conn
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.executescript("""
            CREATE TABLE IF NOT EXISTS rows (
              tbl         TEXT NOT NULL,
              rid         TEXT NOT NULL,
              data        TEXT NOT NULL,
              rev         INTEGER NOT NULL,
              updated_at  TEXT NOT NULL,
              deleted     INTEGER NOT NULL DEFAULT 0,
              hash        TEXT NOT NULL,
              PRIMARY KEY (tbl, rid)
            );
            CREATE INDEX IF NOT EXISTS idx_rows_rev ON rows(rev);
            CREATE INDEX IF NOT EXISTS idx_rows_tbl ON rows(tbl);

            CREATE TABLE IF NOT EXISTS changelog (
              rev   INTEGER PRIMARY KEY AUTOINCREMENT,
              tbl   TEXT NOT NULL,
              rid   TEXT NOT NULL,
              op    TEXT NOT NULL,
              ts    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meta (
              k TEXT PRIMARY KEY,
              v TEXT
            );

            CREATE TABLE IF NOT EXISTS audit (
              id     INTEGER PRIMARY KEY AUTOINCREMENT,
              ts     TEXT NOT NULL,
              actor  TEXT,
              action TEXT,
              detail TEXT
            );

            CREATE TABLE IF NOT EXISTS devices (
              sid        TEXT PRIMARY KEY,
              role       TEXT,
              student_id TEXT,
              ip         TEXT,
              ua         TEXT,
              last_seen  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);

            -- 学生登录口令。故意不放进行数据表：
            -- 口令哈希永远不参与同步，也不会出现在任何接口响应里。
            CREATE TABLE IF NOT EXISTS student_creds (
              student_id  TEXT PRIMARY KEY,
              pwd_hash    TEXT NOT NULL,
              must_change INTEGER DEFAULT 0,
              updated_at  TEXT
            );
            """)
            c.commit()
            row = c.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
            if not row:
                c.execute("INSERT INTO meta(k,v) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
                c.commit()

    # ------------------------------------------------------------------
    # 基础读
    # ------------------------------------------------------------------
    def rev(self):
        with self.lock:
            r = self.conn.execute("SELECT MAX(rev) AS m FROM changelog").fetchone()
            return int(r["m"] or 0)

    def is_empty(self):
        with self.lock:
            r = self.conn.execute("SELECT COUNT(*) AS c FROM rows WHERE deleted=0").fetchone()
            return int(r["c"]) == 0

    def stats(self):
        with self.lock:
            out = {}
            for r in self.conn.execute(
                    "SELECT tbl, COUNT(*) AS c FROM rows WHERE deleted=0 GROUP BY tbl"):
                out[r["tbl"]] = int(r["c"])
            total = sum(out.values())
        return {"tables": out, "total": total, "rev": self.rev()}

    def table_rows(self, tbl, include_deleted=False):
        q = "SELECT rid, data, rev, updated_at, deleted FROM rows WHERE tbl=?"
        if not include_deleted:
            q += " AND deleted=0"
        with self.lock:
            rows = self.conn.execute(q, (tbl,)).fetchall()
        out = []
        for r in rows:
            try:
                d = json.loads(r["data"])
            except Exception:
                d = {}
            if not isinstance(d, dict):
                d = {}
            d = dict(d)
            d.setdefault("id", r["rid"])
            d["_rev"] = int(r["rev"])
            d["updated_at"] = r["updated_at"]
            if r["deleted"]:
                d["deleted"] = 1
            out.append(d)
        return out

    def heal_empty_rows(self):
        """把历史遗留的“只有 id、没有任何业务字段”的空行打成墓碑。

        旧版 upsert() 不识别 deleted 标记，把客户端的 {id, deleted:1}
        当成普通行写了进去，于是数据库里留下一批 deleted=0、data={"id":…} 的行：
        前端会把它渲染成没有姓名、没有班级的“幽灵学生”，还会把学生总数抬高。
        这里启动时自愈一次（幂等），并占一个新 rev 让各端跟着清掉。
        """
        n = 0
        with self.lock:
            rows = self.conn.execute(
                "SELECT tbl, rid, data FROM rows WHERE deleted=0").fetchall()
            for r in rows:
                try:
                    d = json.loads(r["data"])
                except Exception:
                    continue
                if not isinstance(d, dict):
                    continue
                # 两种幽灵行：data 里带 deleted 标记（旧版把墓碑当普通行写进来），
                # 或者除了 id/时间戳之外没有任何业务字段
                ghost = _is_tombstone(d) or not (
                    set(d.keys()) - {"id", "updated_at", "deleted"})
                if not ghost:
                    continue
                rev = self._next_rev()
                self.conn.execute(
                    "UPDATE rows SET deleted=1, rev=?, updated_at=? WHERE tbl=? AND rid=?",
                    (rev, _now(), r["tbl"], r["rid"]))
                n += 1
            if n:
                self.conn.commit()
        return n

    def get_row(self, tbl, rid):
        """按主键取单行（不含墓碑）。取不到返回 None。"""
        with self.lock:
            r = self.conn.execute(
                "SELECT data, rev, updated_at, deleted FROM rows WHERE tbl=? AND rid=?",
                (tbl, str(rid))).fetchone()
        if not r or r["deleted"]:
            return None
        try:
            d = json.loads(r["data"])
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        d = dict(d)
        d.setdefault("id", str(rid))
        d["_rev"] = int(r["rev"])
        d["updated_at"] = r["updated_at"]
        return d

    def snapshot(self):
        """全量快照（教师端首次加载 / 迁移备份用）"""
        return {t: self.table_rows(t) for t in TABLES}

    # ------------------------------------------------------------------
    # 增量拉取
    # ------------------------------------------------------------------
    def changes_since(self, since, limit=800):
        """返回 (rev, {tbl: [rows]}, has_more)。

        只按 rev 切，不按表切：跨表一次取完，保证“作业 + 提交记录”这种
        关联数据在同一个游标窗口里，不会出现半套数据。
        """
        rev = self.rev()
        with self.lock:
            rows = self.conn.execute(
                "SELECT tbl, rid, data, rev, updated_at, deleted FROM rows "
                "WHERE rev > ? ORDER BY rev ASC LIMIT ?", (int(since), int(limit))).fetchall()
            more = self.conn.execute(
                "SELECT 1 FROM rows WHERE rev > ? LIMIT 1 OFFSET ?",
                (int(since), int(limit))).fetchone() is not None
        out = {}
        last = int(since)
        for r in rows:
            try:
                d = json.loads(r["data"])
            except Exception:
                d = {}
            if not isinstance(d, dict):
                d = {}
            d = dict(d)
            d.setdefault("id", r["rid"])
            d["_rev"] = int(r["rev"])
            d["updated_at"] = r["updated_at"]
            if r["deleted"]:
                d["deleted"] = 1
            out.setdefault(r["tbl"], []).append(d)
            last = max(last, int(r["rev"]))
        return (last if rows else rev), out, more

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def _next_rev(self):
        c = self.conn.execute(
            "INSERT INTO changelog(tbl,rid,op,ts) VALUES('_','_','_',?)", (_now(),))
        return int(c.lastrowid)

    def upsert(self, tbl, row, actor="teacher", force=False):
        """单行 upsert。返回 (rev, changed, conflict)。

        force=True 只在“恢复备份”这类管理操作里用：备份里写了什么就是什么，
        不再按时间戳判“客户端太旧”（否则恢复一份比墓碑更早的备份，
        已删的行永远回不来）。
        """
        rid = row_id(tbl, row)
        if rid is None:
            return 0, False, False
        if _is_tombstone(row):
            # 墓碑走删除路径：绝不能把 deleted 当普通字段写回 0
            rev, changed = self.tombstone(tbl, row, actor)
            return rev, changed, False
        if tbl in self.STUDENT_REF_TABLES and not force:
            sid = row.get("student_id")
            if sid not in (None, ""):
                with self.lock:
                    alive = self._student_exists(sid)
                if not alive:
                    # 学生已经删了，还推他的考勤/成绩/作业/座位 → 拒收。
                    # 不拦的话，离线设备一上线就把刚清掉的孤儿数据推回来。
                    return self.rev(), False, True
        data = _clean(row)
        data.pop("_rev", None)
        updated = norm_ts(row.get("updated_at"))
        if "updated_at" in data:
            data["updated_at"] = updated        # 库里只留一种时间格式
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        fp = _fingerprint(data)
        with self.lock:
            cur = self.conn.execute(
                "SELECT hash, updated_at, deleted FROM rows WHERE tbl=? AND rid=?",
                (tbl, rid)).fetchone()
            if cur and cur["hash"] == fp:
                if cur["deleted"]:
                    # 内容一样但行是墓碑：说明这是“恢复导入”（导入前先把整表
                    # 打成删除，再按原样写回），必须把墓碑翻回来。
                    # 正常同步不会走到这里——墓碑的 hash 是 __deleted__ 指纹。
                    rev = self._next_rev()
                    self.conn.execute(
                        "UPDATE rows SET deleted=0, rev=?, updated_at=? WHERE tbl=? AND rid=?",
                        (rev, updated, tbl, rid))
                    self.conn.commit()
                    return rev, True, False
                return int(self.rev()), False, False
            conflict = False
            if not force and cur and ts_key(cur["updated_at"]) > ts_key(updated) + 0.5:
                # 服务端更新的版本比客户端这条还新 → 让客户端 rebase
                conflict = True
                return self._rev_of(tbl, rid), False, conflict
            rev = self._next_rev()
            self.conn.execute(
                "INSERT INTO rows(tbl,rid,data,rev,updated_at,deleted,hash) VALUES(?,?,?,?,?,0,?) "
                "ON CONFLICT(tbl,rid) DO UPDATE SET data=excluded.data, rev=excluded.rev, "
                "updated_at=excluded.updated_at, deleted=0, hash=excluded.hash",
                (tbl, rid, payload, rev, updated, fp))
            self.conn.commit()
        return rev, True, False

    def _rev_of(self, tbl, rid):
        r = self.conn.execute("SELECT rev FROM rows WHERE tbl=? AND rid=?", (tbl, rid)).fetchone()
        return int(r["rev"]) if r else 0

    def delete(self, tbl, rid, actor="teacher"):
        cascade = {}
        with self.lock:
            cur = self.conn.execute(
                "SELECT rev, deleted FROM rows WHERE tbl=? AND rid=?", (tbl, rid)).fetchone()
            if not cur:
                return 0
            rev = self._next_rev()
            self.conn.execute(
                "UPDATE rows SET deleted=1, rev=?, updated_at=?, hash=? WHERE tbl=? AND rid=?",
                (rev, _now(), _tomb_hash(rid), tbl, rid))
            if tbl == "students":
                cascade = self._cascade_student(rid)
            self.conn.commit()
        self._audit_cascade(tbl, rid, cascade, actor)
        return rev

    # 学生名下的从表：删学生时必须连带清掉，否则留下指向不存在学生的孤儿行
    STUDENT_CHILD_TABLES = ("attendance", "homework_records", "scores",
                            "morality_records", "leave_requests",
                            "student_notes", "exam_scores")

    # 这些表带 student_id 引用；指向不存在的学生时应当拒收（见 upsert）
    STUDENT_REF_TABLES = STUDENT_CHILD_TABLES + ("seats",)

    def _student_exists(self, sid):
        r = self.conn.execute(
            "SELECT deleted FROM rows WHERE tbl='students' AND rid=?", (str(sid),)).fetchone()
        return bool(r) and not r["deleted"]

    def _cascade_student(self, sid):
        """清理挂在某个学生名下的全部数据。返回 {表: 条数}。

        为什么必须放在服务端：前端删学生时会自己清一遍，但“接口直接写墓碑”
        （换客户端、脚本导入、恢复备份）不会。孤儿行不报错、页面也看不出来，
        只会悄悄把统计口径拖歪——班均把不存在的人算进分母、出勤率永远差几个、
        作业提交率对不上在册人数。

        调用方负责加锁与 commit（这里只改行、不提交）。
        """
        sid = str(sid)
        stats = {}

        def _bump(tbl, rid, data):
            """写回一行（更新内容），占一个新 rev。"""
            payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            rev = self._next_rev()
            self.conn.execute(
                "UPDATE rows SET data=?, rev=?, updated_at=?, hash=? WHERE tbl=? AND rid=?",
                (payload, rev, _now(), _fingerprint(data), tbl, rid))

        for tbl in self.STUDENT_CHILD_TABLES:
            n = 0
            rows = self.conn.execute(
                "SELECT rid, data FROM rows WHERE tbl=? AND deleted=0", (tbl,)).fetchall()
            for r in rows:
                try:
                    d = json.loads(r["data"])
                except Exception:
                    continue
                if not isinstance(d, dict) or str(d.get("student_id")) != sid:
                    continue
                rev = self._next_rev()
                self.conn.execute(
                    "UPDATE rows SET deleted=1, rev=?, updated_at=?, hash=? WHERE tbl=? AND rid=?",
                    (rev, _now(), _tomb_hash(r["rid"]), tbl, r["rid"]))
                n += 1
            if n:
                stats[tbl] = n

        # 座位：座位本身还要留着，只把这个人腾出去
        n = 0
        for r in self.conn.execute(
                "SELECT rid, data FROM rows WHERE tbl='seats' AND deleted=0").fetchall():
            try:
                d = json.loads(r["data"])
            except Exception:
                continue
            if not isinstance(d, dict) or str(d.get("student_id")) != sid:
                continue
            d["student_id"] = None
            d["updated_at"] = _now()
            _bump("seats", r["rid"], d)
            n += 1
        if n:
            stats["seats"] = n

        # 小组：组还在，把人从名单里摘掉
        n = 0
        for r in self.conn.execute(
                "SELECT rid, data FROM rows WHERE tbl='groups' AND deleted=0").fetchall():
            try:
                d = json.loads(r["data"])
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            ids = d.get("student_ids")
            if not isinstance(ids, list):
                continue
            keep = [x for x in ids if str(x) != sid]
            if len(keep) == len(ids):
                continue
            d["student_ids"] = keep
            d["updated_at"] = _now()
            _bump("groups", r["rid"], d)
            n += 1
        if n:
            stats["groups"] = n

        # 登录口令：人删了就不该还能登进来（否则登录成功、同步才报“档案不存在”）
        c = self.conn.execute("DELETE FROM student_creds WHERE student_id=?", (sid,))
        if c.rowcount:
            stats["student_creds"] = c.rowcount
        return stats

    def _audit_cascade(self, tbl, rid, stats, actor="teacher"):
        if not stats:
            return
        detail = "%s#%s 级联清理 " % (tbl, rid) + "、".join(
            "%s=%d" % (k, v) for k, v in sorted(stats.items()))
        self.audit(actor, "cascade-delete", detail)

    def tombstone(self, tbl, row, actor="teacher"):
        """按客户端推来的墓碑行写删除标记。返回 (rev, changed)。

        与 delete() 的区别：行在本机还不存在（别的设备建的）时也补一条墓碑，
        否则“A 设备删、B 设备从没同步过这一行”就会出现删除丢失。
        """
        rid = row_id(tbl, row)
        if rid is None:
            return 0, False
        data = _clean(row)
        data.pop("_rev", None)
        data.pop("deleted", None)
        if "updated_at" in data:
            data["updated_at"] = norm_ts(data.get("updated_at"))
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        fp = _fingerprint({"__deleted__": True, "id": rid})
        cascade = {}
        changed = False
        with self.lock:
            cur = self.conn.execute(
                "SELECT deleted FROM rows WHERE tbl=? AND rid=?", (tbl, rid)).fetchone()
            if cur and cur["deleted"]:
                # 已经删过：不刷 rev，但仍补一次级联（幂等），
                # 兜住老版本留在库里的孤儿行
                if tbl == "students":
                    cascade = self._cascade_student(rid)
                    if cascade:
                        self.conn.commit()
                rev = self._rev_of(tbl, rid)
            else:
                rev = self._next_rev()
                self.conn.execute(
                    "INSERT INTO rows(tbl,rid,data,rev,updated_at,deleted,hash) VALUES(?,?,?,?,?,1,?) "
                    "ON CONFLICT(tbl,rid) DO UPDATE SET deleted=1, rev=excluded.rev, "
                    "updated_at=excluded.updated_at, hash=excluded.hash",
                    (tbl, rid, payload, rev, _now(), fp))
                if tbl == "students":
                    cascade = self._cascade_student(rid)
                self.conn.commit()
                changed = True
        self._audit_cascade(tbl, rid, cascade, actor)
        return rev, changed

    def apply_changes(self, changes, base_rev=0, actor="teacher"):
        """批量应用客户端变更。changes = [{table, rows:[...]}, ...]

        返回 {rev, applied, conflicts:[{table,id}]}。
        设计成“尽力而为”：一条坏数据不影响整批（线上最常见的坑就是
        一条 null 让整表 upsert 全红）。
        """
        applied = 0
        conflicts = []
        errs = []
        # 先写 students：同一批里可能有“新建学生 + 他的考勤”，
        # 顺序反了会被上面的存在性校验当成孤儿行拒掉。sorted 是稳定排序，
        # 表内顺序不变。
        ordered = sorted([c for c in (changes or []) if isinstance(c, dict)],
                         key=lambda c: 0 if str(c.get("table")) == "students" else 1)
        for ch in ordered:
            tbl = str(ch.get("table") or "")
            if tbl not in TABLES:
                errs.append({"table": tbl, "msg": "unknown table"})
                continue
            for row in (ch.get("rows") or []):
                if not isinstance(row, dict):
                    continue
                try:
                    rev, changed, conflict = self.upsert(tbl, row, actor)
                    if conflict:
                        conflicts.append({"table": tbl, "id": row_id(tbl, row)})
                    if changed:
                        applied += 1
                except Exception as e:
                    errs.append({"table": tbl, "id": row_id(tbl, row), "msg": str(e)[:120]})
        return {"rev": self.rev(), "applied": applied, "conflicts": conflicts, "errors": errs}

    def import_snapshot(self, tables, replace=True, actor="teacher"):
        """一次性导入（老数据迁移 / 备份恢复）。返回每张表写入条数。"""
        counts = {}
        if replace:
            with self.lock:
                # 整表打删除必须逐行占 rev：否则别的设备完全不知道这些行没了，
                # 恢复一份旧备份后，各端还留着一堆“备份里没有”的数据。
                # 故意不动 updated_at——紧接着的 upsert 会拿它做冲突比较，
                # 这里若写成 now，原样恢复的行会被判成“客户端太旧”而被拒。
                stale = self.conn.execute(
                    "SELECT tbl, rid FROM rows WHERE deleted=0").fetchall()
                for r in stale:
                    self.conn.execute(
                        "UPDATE rows SET deleted=1, rev=? WHERE tbl=? AND rid=?",
                        (self._next_rev(), r["tbl"], r["rid"]))
                self.conn.commit()
        for tbl, rows in (tables or {}).items():
            if tbl not in TABLES or not isinstance(rows, list):
                continue
            n = 0
            for row in rows:
                if isinstance(row, dict):
                    _, changed, _ = self.upsert(tbl, row, actor, force=True)
                    n += 1 if changed else 0
            counts[tbl] = n
        self.audit(actor, "import", json.dumps(counts, ensure_ascii=False))
        return counts

    # ------------------------------------------------------------------
    # 学生口令
    # ------------------------------------------------------------------
    def get_cred(self, student_id):
        with self.lock:
            r = self.conn.execute(
                "SELECT student_id, pwd_hash, must_change FROM student_creds WHERE student_id=?",
                (str(student_id),)).fetchone()
        return dict(r) if r else None

    def set_cred(self, student_id, pwd_hash, must_change=0):
        with self.lock:
            self.conn.execute(
                "INSERT INTO student_creds(student_id,pwd_hash,must_change,updated_at) "
                "VALUES(?,?,?,?) ON CONFLICT(student_id) DO UPDATE SET "
                "pwd_hash=excluded.pwd_hash, must_change=excluded.must_change, "
                "updated_at=excluded.updated_at",
                (str(student_id), pwd_hash, int(must_change), _now()))
            self.conn.commit()

    def cred_count(self):
        with self.lock:
            r = self.conn.execute("SELECT COUNT(*) AS c FROM student_creds").fetchone()
        return int(r["c"])

    def clear_creds(self, student_ids=None):
        with self.lock:
            if student_ids is None:
                self.conn.execute("DELETE FROM student_creds")
            else:
                for sid in student_ids:
                    self.conn.execute("DELETE FROM student_creds WHERE student_id=?", (str(sid),))
            self.conn.commit()

    # ------------------------------------------------------------------
    # 运维
    # ------------------------------------------------------------------
    def audit(self, actor, action, detail=""):
        with self.lock:
            self.conn.execute("INSERT INTO audit(ts,actor,action,detail) VALUES(?,?,?,?)",
                              (_now(), str(actor), str(action), str(detail)[:2000]))
            self.conn.commit()

    def recent_audit(self, limit=100):
        with self.lock:
            rows = self.conn.execute(
                "SELECT ts,actor,action,detail FROM audit ORDER BY id DESC LIMIT ?",
                (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def prune_tombstones(self, days=90):
        cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - days * 86400))
        with self.lock:
            c = self.conn.execute("DELETE FROM rows WHERE deleted=1 AND updated_at < ?", (cutoff,))
            self.conn.commit()
            return c.rowcount

    def vacuum_into(self, target):
        with self.lock:
            self.conn.execute("VACUUM INTO ?", (target,))
        return target

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass
