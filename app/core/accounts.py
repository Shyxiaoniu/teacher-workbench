#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""班级工作台 2.0 · 账号与工作区（2.0.13）

职责：
  · 账号表（accounts.json）：一个管理员 + 多个教师账号；
  · 请求级上下文（thread-local）：每个请求绑定当前账号；
  · 工作区路径：按账号给出 db / typing / activity / submissions / courseware 路径；
  · 懒加载代理：让原来模块级单例 STORE / ACT / TYPING 自动跟着上下文走，
    业务代码几百处 STORE.xxx 调用一行都不用改。

兼容性铁律：
  · 原单账号升级后映射为 id="default" 的教师账号，工作区就是原 DATA_DIR 本身，
    workbench.db / courseware / submissions 路径完全不变 —— 老数据零迁移、零丢失；
  · 管理员新建的教师账号 id 形如 t2 / t3，工作区落在 <DATA_DIR>/accounts/<id>/，
    与 default 及其它账号物理隔离。
"""
import json
import os
import threading
import time

import auth as A

DEFAULT_ADMIN_USERNAME = "teacher"
DEFAULT_ADMIN_PASSWORD = "t12345678"
DEFAULT_TEACHER_ID = "default"

_ctx = threading.local()


def current_account():
    """当前请求绑定的账号 id。未绑定（后台线程 / 启动阶段）一律用 default。"""
    return getattr(_ctx, "account", None) or DEFAULT_TEACHER_ID


def set_account(acc):
    _ctx.account = str(acc or DEFAULT_TEACHER_ID)


def clear_account():
    _ctx.account = DEFAULT_TEACHER_ID


class Workspace(object):
    """按账号算路径。注入给旧提交系统 / 课件模块，避免它们各自读全局常量。"""

    def __init__(self, data_dir, env=None):
        self.data_dir = os.path.abspath(data_dir)
        self.env = env if env is not None else os.environ

    def dir_of(self, acc):
        acc = str(acc or DEFAULT_TEACHER_ID)
        if acc == DEFAULT_TEACHER_ID:
            return self.data_dir
        return os.path.join(self.data_dir, "accounts", acc)

    def paths_for(self, acc):
        acc = str(acc or DEFAULT_TEACHER_ID)
        d = self.dir_of(acc)
        if acc == DEFAULT_TEACHER_ID:
            # default 沿用原来的环境变量口径，升级后路径一字不改
            sub = (self.env.get("WB_SUBMISSION_DATA_DIR")
                   or os.path.join(d, "submissions"))
            cw = (self.env.get("WB_COURSEWARE_DIR")
                  or os.path.join(d, "courseware"))
        else:
            sub = os.path.join(d, "submissions")
            cw = os.path.join(d, "courseware")
        return {
            "id": acc,
            "dir": d,
            "db": os.path.join(d, "workbench.db"),
            "typing": os.path.join(d, "typing.json"),
            "activity": os.path.join(d, "activity"),
            "submissions": os.path.normpath(sub),
            "courseware": os.path.normpath(cw),
        }

    def ensure(self, acc):
        p = self.paths_for(acc)
        for k in ("dir", "submissions", "courseware", "activity"):
            try:
                os.makedirs(p[k], exist_ok=True)
            except Exception:
                pass
        return p

    # ---- 注入给 app/submission-system/courseware.py ----
    def courseware_paths(self):
        p = self.paths_for(current_account())
        return {
            "data": p["submissions"],
            "cfg": os.path.join(p["submissions"], "courseware.json"),
            "root": p["courseware"],
        }

    # ---- 注入给 app/submission-system/server.py ----
    def legacy_paths(self):
        p = self.paths_for(current_account())
        d = p["submissions"]
        return {
            "data": d,
            "config": os.path.join(d, "config.json"),
            "roster": os.path.join(d, "roster.json"),
            "sub": os.path.join(d, "submissions.json"),
            "index": os.path.join(d, "uploads_index.json"),
            "upload": os.path.join(d, "uploads"),
        }

    # ---- 注入给 app/submission-system/enhance.py ----
    def enhance_paths(self):
        d = os.path.join(self.paths_for(current_account())["submissions"], "enh")
        return {"data": d, "enh": d,
                "uploads": os.path.join(d, "uploads"),
                "subs_files": os.path.join(d, "subs_files"),
                "materials": os.path.join(d, "materials")}


class WorkspaceProxy(object):
    """把「模块级单例」变成「按当前账号懒加载的代理」。

    所有 STORE / ACT / TYPING 的方法调用都经 __getattr__ 转发到当前账号的实例，
    因此业务代码不用改；实例按账号缓存，SQLite 连接自带 RLock +
    check_same_thread=False，多线程安全。
    """

    def __init__(self, factory):
        self._factory = factory
        self._cache = {}
        self._lock = threading.RLock()

    def for_account(self, acc):
        acc = str(acc or DEFAULT_TEACHER_ID)
        with self._lock:
            obj = self._cache.get(acc)
            if obj is None:
                obj = self._factory(acc)
                self._cache[acc] = obj
            return obj

    def current(self):
        return self.for_account(current_account())

    def __getattr__(self, name):
        return getattr(self.current(), name)

    def instances(self):
        """给需要遍历所有账号的场景（例如学生登录找账号）用。"""
        with self._lock:
            return dict(self._cache)


class AccountStore(object):
    """accounts.json：管理员 1 个 + 教师 N 个。口令一律 PBKDF2 哈希。"""

    def __init__(self, data_dir, legacy_teacher=None, env=None):
        self.env = env if env is not None else os.environ
        self.path = os.path.join(data_dir, "accounts.json")
        self.lock = threading.RLock()
        self.data = self._load()
        # 本进程是否刚刚建出管理员（首次启动）。只有这一次能拿到明文密码 ——
        # 之后 accounts.json 里只剩哈希，谁也还原不出来。
        # 登录页不再展示默认账号密码，改由启动终端打印（2026-11-05 牛哥要求）。
        self.bootstrapped = False
        if not isinstance(self.data, dict) or not self.data.get("admin"):
            self.data = self._bootstrap(legacy_teacher or {})
            self._save()
            self.bootstrapped = True
        # 部署时可用环境变量覆盖管理员账号；只在显式要求时重置口令，
        # 避免每次重启都把管理员密码悄悄改回默认值。
        self._apply_env_admin()
        # 2.0.19：账号表可能因为「全量恢复」丢了 default 条目，启动时自愈。
        self._ensure_default_teacher()

    def _ensure_default_teacher(self):
        """保证 default 教师条目一定在 teachers 里（2.0.19）。

        背景：`restore()` 会整体替换 teachers 列表，而备份可能来自
        · 2.0.12 之前的单账号版本（那时还没有 teachers 数组），或
        · 另一台机器（账号 id 是 t2 / t3，根本没有 default）。
        但业务数据（班级 / 学生 / 成绩）一律落在 **default 工作区** ——
        teachers 里少了 default，学生登录遍历 `teacher_ids()` 时就扫不到它，
        表现为「教师端能看到班级，学生端显示没有班级、登录不了」
        （2026-11-05 牛哥反馈的正是这个）。

        这里补一条 builtin 的 default：密码留空，管理员在 /admin/ 重置即可；
        用户名自动避让，不会顶掉已有的同名账号。
        """
        ts = self.data.get("teachers")
        if not isinstance(ts, list):
            ts = []
            self.data["teachers"] = ts
        if any(str(t.get("id")) == DEFAULT_TEACHER_ID for t in ts):
            return False
        names = {str(t.get("username") or "") for t in ts}
        uname, n = "teacher", 2
        while uname in names:
            uname = "teacher%d" % n
            n += 1
        ts.insert(0, {
            "id": DEFAULT_TEACHER_ID,
            "username": uname,
            "password_hash": "",
            "security": {"question": "", "answer_hash": ""},
            "name": "默认教师",
            "created": int(time.time()),
            "builtin": True,
        })
        try:
            self._save()
        except Exception:
            pass
        return True

    # ---------------- 底层 ----------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("admin"), dict) \
                    and isinstance(d.get("teachers"), list):
                return d
        except Exception:
            pass
        return {}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def _bootstrap(self, legacy):
        admin_user = (self.env.get("WB_ADMIN_USERNAME") or "").strip() \
            or DEFAULT_ADMIN_USERNAME
        admin_pwd = self.env.get("WB_ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD
        t = {
            "id": DEFAULT_TEACHER_ID,
            "username": str(legacy.get("username") or "teacher"),
            "password_hash": str(legacy.get("password_hash") or ""),
            "security": dict(legacy.get("security") or
                             {"question": "", "answer_hash": ""}),
            "name": "默认教师",
            "created": int(time.time()),
            "builtin": True,
        }
        return {
            "version": 1,
            "admin": {"username": admin_user,
                      "password_hash": A.hash_password(admin_pwd),
                      "created": int(time.time())},
            "next_id": 2,
            "teachers": [t],
        }

    def _apply_env_admin(self):
        changed = False
        u = (self.env.get("WB_ADMIN_USERNAME") or "").strip()
        if u and u != self.data["admin"].get("username"):
            self.data["admin"]["username"] = u
            changed = True
        p = self.env.get("WB_ADMIN_PASSWORD") or ""
        if p and self.env.get("WB_ADMIN_RESET") == "1":
            self.data["admin"]["password_hash"] = A.hash_password(p)
            changed = True
        if changed:
            self._save()

    # ---------------- 账号读取 ----------------
    def admin(self):
        with self.lock:
            return dict(self.data["admin"])

    def admin_username(self):
        """当前管理员账号名（登录页不再展示，仅供启动终端提示用）。"""
        try:
            with self.lock:
                return str((self.data.get("admin") or {}).get("username") or "")
        except Exception:
            return ""

    def admin_initial_password(self):
        """首次启动才知道的明文密码；账号已存在时返回空串。

        2026-11-05：登录页不再展示默认账号密码，改在启动终端打印一次。
        只有「本进程刚建出管理员」这一种情况能给出明文 —— 之后就只剩哈希，
        任何地方都还原不出来（也不该还原）。
        """
        if not getattr(self, "bootstrapped", False):
            return ""
        return self.env.get("WB_ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD

    def teachers(self):
        with self.lock:
            return [dict(t) for t in self.data["teachers"]]

    def teacher_ids(self):
        return [t["id"] for t in self.teachers()]

    def teacher(self, tid):
        for t in self.teachers():
            if t["id"] == str(tid):
                return t
        return None

    def teacher_by_username(self, username):
        username = str(username or "").strip()
        for t in self.teachers():
            if str(t.get("username") or "") == username:
                return t
        return None

    def raw_teacher(self, tid):
        """内部 dict 引用（仅供同进程内读写；外部请用 teacher()）。"""
        with self.lock:
            for t in self.data["teachers"]:
                if t["id"] == str(tid):
                    return t
        return None

    def flush(self):
        with self.lock:
            self._save()

    def snapshot(self):
        """全量备份用：账号表原文（只含哈希，不含明文口令）。"""
        with self.lock:
            return json.loads(json.dumps(self.data))

    def restore(self, data):
        """全量恢复：只在结构合法时整体替换，并保留当前管理员凭据，
        避免一份旧备份把部署时配置的管理员账号换掉。"""
        if not isinstance(data, dict) or not isinstance(data.get("admin"), dict) \
                or not isinstance(data.get("teachers"), list):
            return False
        with self.lock:
            keep_admin = dict(self.data["admin"])
            self.data = json.loads(json.dumps(data))
            self.data["admin"] = keep_admin
            self._save()
        # 备份里的 teachers 可能不含 default（旧版本 / 另一台机器），
        # 而业务数据都在 default 工作区 —— 不补回去学生会登录不了。
        self._ensure_default_teacher()
        return True

    def peers(self, exclude):
        """给「资料共享」用：其它教师账号的 id / 用户名 / 显示名。"""
        return [{"id": t["id"], "username": t.get("username") or "",
                 "name": t.get("name") or ""}
                for t in self.teachers() if t["id"] != str(exclude)]

    def public_teachers(self):
        """给管理员控制台用：绝不下发 password_hash / 密保答案。"""
        return [{"id": t["id"], "username": t.get("username") or "",
                 "name": t.get("name") or "",
                 "has_password": bool(t.get("password_hash")),
                 "created": t.get("created") or 0,
                 "builtin": bool(t.get("builtin"))}
                for t in self.teachers()]

    # ---------------- 校验 ----------------
    def verify(self, role, username, password):
        """返回 (ok, account_id, account_dict)。role: admin / teacher。"""
        username = str(username or "").strip()
        password = str(password or "")
        if not username or not password:
            return False, None, None
        if role == "admin":
            a = self.admin()
            if username == str(a.get("username") or "") and \
                    A.verify_password(password, a.get("password_hash")):
                return True, "admin", a
            return False, None, None
        for t in self.teachers():
            if username == str(t.get("username") or "") and \
                    A.verify_password(password, t.get("password_hash")):
                return True, t["id"], t
        return False, None, None

    def current_teacher(self):
        """当前会话绑定的教师账号（管理员会话返回 None）。"""
        return self.teacher(current_account())

    # ---------------- 教师账号管理（仅管理员可调用） ----------------
    def add_teacher(self, username, password, name=""):
        username = str(username or "").strip()
        password = str(password or "")
        if len(username) < 2:
            return {"ok": False, "msg": "账号至少 2 个字符"}
        bad = A.weak_password(password)
        if bad:
            return {"ok": False, "msg": bad}
        with self.lock:
            for t in self.data["teachers"]:
                if str(t.get("username") or "") == username:
                    return {"ok": False, "msg": "这个账号名已经被用了"}
            n = int(self.data.get("next_id") or 2)
            self.data["next_id"] = n + 1
            t = {"id": "t%d" % n, "username": username,
                 "password_hash": A.hash_password(password),
                 "security": {"question": "", "answer_hash": ""},
                 "name": str(name or "").strip() or username,
                 "created": int(time.time())}
            self.data["teachers"].append(t)
            self._save()
        return {"ok": True, "teacher": {"id": t["id"], "username": t["username"],
                                        "name": t["name"]}}

    def update_teacher(self, tid, username=None, password=None, name=None):
        with self.lock:
            for t in self.data["teachers"]:
                if t["id"] != str(tid):
                    continue
                if username is not None:
                    u = str(username).strip()
                    if len(u) < 2:
                        return {"ok": False, "msg": "账号至少 2 个字符"}
                    for x in self.data["teachers"]:
                        if x["id"] != t["id"] and \
                                str(x.get("username") or "") == u:
                            return {"ok": False, "msg": "这个账号名已经被用了"}
                    t["username"] = u
                if password is not None and str(password) != "":
                    bad = A.weak_password(str(password))
                    if bad:
                        return {"ok": False, "msg": bad}
                    t["password_hash"] = A.hash_password(str(password))
                if name is not None:
                    t["name"] = str(name).strip() or t["username"]
                self._save()
                return {"ok": True}
        return {"ok": False, "msg": "没有这个教师账号"}

    def delete_teacher(self, tid):
        tid = str(tid)
        if tid == DEFAULT_TEACHER_ID:
            return {"ok": False, "msg": "默认教师账号不能删除（它对应原有的全部数据）"}
        with self.lock:
            n = len(self.data["teachers"])
            self.data["teachers"] = [t for t in self.data["teachers"]
                                     if t["id"] != tid]
            if len(self.data["teachers"]) == n:
                return {"ok": False, "msg": "没有这个教师账号"}
            self._save()
        return {"ok": True, "msg": "账号已删除；该账号的数据目录保留，不会自动清空"}

    # ---------------- 教师自助改密 / 密保（写回账号表） ----------------
    def set_password(self, tid, password):
        with self.lock:
            for t in self.data["teachers"]:
                if t["id"] == str(tid):
                    t["password_hash"] = A.hash_password(str(password))
                    self._save()
                    return True
        return False

    def set_security(self, tid, question, answer_hash):
        with self.lock:
            for t in self.data["teachers"]:
                if t["id"] == str(tid):
                    t["security"] = {"question": str(question or ""),
                                     "answer_hash": str(answer_hash or "")}
                    self._save()
                    return True
        return False

    def set_admin_password(self, password):
        with self.lock:
            self.data["admin"]["password_hash"] = A.hash_password(str(password))
            self._save()
