#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口令、会话、配置（仅标准库）"""
import os
import json
import time
import hmac
import base64
import hashlib
import secrets
import threading

DEFAULT_CONFIG = {
    "port": 20020,
    "bind": "0.0.0.0",
    "class_code": "",
    "school_name": "",
    "teacher": {"username": "teacher", "password_hash": "", "session_hours": 12,
                "allow_lan": True,
                # 密保问题：忘了密码时用它自助改密（答案是加盐哈希，不存明文）
                "security": {"question": "", "answer_hash": ""}},
    "student": {"session_hours": 8, "password_mode": "uniform",
                "initial_password": "123456", "force_change": False},
    # 学生端定位为「课堂使用」：不开放请假入口（病假/事假由教师在教师端登记），
    # 学习资料默认开放，方便课堂下发。
    "modules": {"homework": True, "scores": True, "courseware": True,
                "morality": False, "duty": True, "leave": False},
    "visibility": {"rank": False, "morality_reason": False, "contact": False,
                   "leave_reason": False},
    "sync": {"poll_seconds": 5, "tombstone_days": 90, "page_size": 800},
    "backup": {"auto_daily": True, "keep": 14},
}

_LOCK = threading.RLock()


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = _deep_merge(cfg, json.load(f))
    except Exception:
        pass
    # 一次性迁移（2026-10-31）：学生端重定位为「课堂使用」——
    # 学习资料默认开放（新功能，旧配置里的 False 只是旧默认值，不是老师主动关的）；
    # 请假默认关闭（病假 / 事假改由教师在教师端登记）。
    if not cfg.get("_flags_migrated_v2"):
        m = cfg.setdefault("modules", {})
        m["courseware"] = True
        m["leave"] = False
        cfg["_flags_migrated_v2"] = True
        save_config(path, cfg)
    if not cfg.get("class_code"):
        cfg["class_code"] = new_class_code()
        save_config(path, cfg)
    return cfg


def save_config(path, cfg):
    with _LOCK:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def new_class_code():
    # 去掉容易看错的 0/O/1/I
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(4))


# ---------------------------------------------------------------------------
# 口令强度
# ---------------------------------------------------------------------------
# 教师账号＝这个班全部数据的钥匙（名单、成绩、家长电话都在里面）。
# 以前只要求 4 位，`1234`「abcd」都能设；部署到公网后，
# 4 位纯数字在 20 次/分钟的限速下几小时就能撞开。
WEAK_PASSWORDS = {
    "12345678", "123456789", "1234567890", "password", "password1", "passw0rd",
    "admin", "admin123", "administrator", "11111111", "00000000", "88888888",
    "66666666", "abc12345", "qwerty", "qwerty123", "1qaz2wsx", "iloveyou",
    "teacher", "teacher123", "class123", "woaini1314", "a1234567", "abcd1234",
}


def weak_password(pwd, min_len=8):
    """返回「为什么不行」，空串表示通过。"""
    p = str(pwd or "")
    if len(p) < min_len:
        return "密码至少 %d 位（这个账号能看到全班数据，别用短密码）" % min_len
    if len(p) > 128:
        return "密码太长（128 位以内就够）"
    low = p.lower()
    if low in WEAK_PASSWORDS:
        return "这个密码太常见了，换一个"
    if len(set(p)) == 1:
        return "密码不能是同一个字符重复"
    if low.isdigit():
        return "密码不能全是数字，掺几个字母更安全"
    return ""


# ---------------------------------------------------------------------------
# 口令：PBKDF2-HMAC-SHA256
# ---------------------------------------------------------------------------
ITERATIONS = 120000


def hash_password(pwd):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode("utf-8"), ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (ITERATIONS, salt, base64.b16encode(dk).decode().lower())


def verify_password(pwd, stored):
    try:
        algo, iters, salt, want = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode("utf-8"), int(iters))
        got = base64.b16encode(dk).decode().lower()
        return hmac.compare_digest(got, want)
    except Exception:
        return False


def norm_answer(s):
    """密保答案归一化：去空白、全角转半角、统一小写。

    老师答题时多打一个空格、用中文标点、全角括号，都会被判错——
    那种“我明明答对了”的挫败感比忘记密码本身更糟。
    """
    s = str(s or "")
    s = s.replace("\u3000", " ").strip()
    out = []
    for ch in s:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:          # 全角 ASCII → 半角
            out.append(chr(o - 0xFEE0))
        elif ch == "\u00a0":
            out.append(" ")
        else:
            out.append(ch)
    s = "".join(out)
    s = "".join(s.split())                  # 去掉所有空白（含中间空格）
    return s.lower()


def hash_answer(ans):
    return hash_password(norm_answer(ans))


def verify_answer(ans, stored):
    if not stored:
        return False
    return verify_password(norm_answer(ans), stored)


# ---------------------------------------------------------------------------
# 会话（内存）。重启后需要重新登录——故意如此：
# 教室机器经常整夜开着，不应留下一个永久有效的 cookie。
# ---------------------------------------------------------------------------
class Sessions(object):
    def __init__(self):
        self._d = {}
        self._lock = threading.RLock()

    def create(self, role, hours=12, student_id=None):
        token = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._d[token] = {
                "role": role,
                "student_id": student_id,
                "exp": now + hours * 3600,
                "seen": now,
            }
        return token

    def touch(self, token):
        with self._lock:
            s = self._d.get(token)
            if s:
                s["seen"] = time.time()

    def online(self, role, window=300):
        """最近 window 秒内有请求的会话数（按学生去重，多标签页算一个人）"""
        now = time.time()
        sids = set()
        with self._lock:
            for s in self._d.values():
                if s["role"] != role or s["exp"] < now:
                    continue
                if now - s.get("seen", 0) > window:
                    continue
                sids.add(s.get("student_id") or id(s))
        return len(sids)

    def get(self, token):
        if not token:
            return None
        with self._lock:
            s = self._d.get(token)
            if not s:
                return None
            if s["exp"] < time.time():
                self._d.pop(token, None)
                return None
            return s

    def drop(self, token):
        with self._lock:
            self._d.pop(token, None)

    def drop_role(self, role, except_token=None):
        with self._lock:
            for t in [t for t, s in self._d.items() if s["role"] == role and t != except_token]:
                self._d.pop(t, None)

    def gc(self):
        """清掉过期会话。

        以前只在 get() 里惰性删除：没人再拿着旧 cookie 来访问，那条记录就永远留着。
        一学期下来（加上每次登录都新建）内存里会堆几万条死会话。
        """
        now = time.time()
        with self._lock:
            for t in [t for t, s in self._d.items() if s.get("exp", 0) < now]:
                self._d.pop(t, None)
            return len(self._d)


def parse_cookie(header, name="wb_sid"):
    if not header:
        return ""
    for part in header.split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part[len(name) + 1:]
    return ""


def client_ip(handler):
    """真实客户端 IP。

    只有在「直连方是本机」时才信 X-Forwarded-For —— 那说明前面挂着同机的
    反向代理（nginx/Caddy 反代到 127.0.0.1）。其它情况一律用 TCP 对端地址：
    否则任何人都能伪造 XFF 绕过按 IP 的限速，或者把登录失败记录栽给别人。
    """
    try:
        peer = (handler.client_address or ["", ""])[0]
    except Exception:
        return ""
    if peer in ("127.0.0.1", "::1"):
        try:
            xff = handler.headers.get("X-Forwarded-For") or ""
        except Exception:
            xff = ""
        first = xff.split(",")[0].strip()
        if first:
            return first
    return peer


# 登录限速：同 IP 10 次/分钟
_RL = {}
_RL_LOCK = threading.RLock()
_RL_MAX_KEYS = 5000      # 表本身也要有上限，否则可以被海量 key 撑爆内存


def rate_ok(key, limit=10, window=60):
    now = time.time()
    with _RL_LOCK:
        arr = [t for t in _RL.get(key, []) if now - t < window]
        if len(arr) >= limit:
            _RL[key] = arr
            return False
        arr.append(now)
        _RL[key] = arr
        if len(_RL) > _RL_MAX_KEYS:
            # 先丢已经过窗的，再不行就丢掉最旧的一批（限速宁可短暂放宽，也不能把内存吃光）
            for k in [k for k, v in _RL.items() if not v or now - v[-1] > window]:
                _RL.pop(k, None)
            if len(_RL) > _RL_MAX_KEYS:
                for k in sorted(_RL, key=lambda x: _RL[x][-1] if _RL.get(x) else 0)[:1000]:
                    _RL.pop(k, None)
        return True
