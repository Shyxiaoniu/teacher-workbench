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
                "morality": True, "duty": True},
    "visibility": {"rank": False, "morality_reason": False, "contact": False,
                   "leave_reason": False},
    "sync": {"poll_seconds": 5, "tombstone_days": 90, "page_size": 800},
    "backup": {"auto_daily": True, "keep": 14},
    # 课堂活动（自研内嵌）：活动 / 提交 / 附件都在工作台自己的库里。
    "activity": {"submit_hours": 12},
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
    # 学习资料默认开放（新功能，旧配置里的 False 只是旧默认值，不是老师主动关的）。
    if not cfg.get("_flags_migrated_v2"):
        cfg.setdefault("modules", {})["courseware"] = True
        cfg["_flags_migrated_v2"] = True
        save_config(path, cfg)
    # 学生自助请假已移除（2026-10-31）：旧配置里的遗留开关一并清掉
    cfg.get("modules", {}).pop("leave", None)
    # 一次性迁移（2026-10-31 v2.0.4）：学生端重构为「今日 / 我的」两个模块后，
    # 「我的」要展示课堂评价（积分）。旧的 False 是默认值不是老师主动关的，
    # 与上面 courseware 的迁移同理，翻成开放一次。
    if not cfg.get("_flags_migrated_v3"):
        cfg.setdefault("modules", {})["morality"] = True
        cfg["_flags_migrated_v3"] = True
        save_config(path, cfg)
    return cfg


def save_config(path, cfg):
    with _LOCK:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


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

# 2.1.20：口令校验结果短时缓存。
# 全班同时登录且多数学生设了独立密码时，PBKDF2 是 CPU 热点；同一份哈希+
# 同一口令在几分钟内重复校验（刷新、重试、同账号多标签）直接复用结果。
# key 里只放「哈希值 + 口令的 SHA256」，不存明文口令；口令或哈希一变自动 miss。
_PWD_VERIFY = {}
_PWD_VERIFY_LOCK = threading.RLock()
_PWD_VERIFY_MAX = 2048
_PWD_VERIFY_TTL = 300


def hash_password(pwd):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode("utf-8"), ITERATIONS)
    return "pbkdf2_sha256$%d$%s$%s" % (ITERATIONS, salt, base64.b16encode(dk).decode().lower())


def verify_password(pwd, stored):
    try:
        algo, iters, salt, want = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        try:
            pkey = (str(stored), hashlib.sha256(pwd.encode("utf-8")).hexdigest())
        except Exception:
            pkey = None
        now = time.time()
        if pkey is not None:
            with _PWD_VERIFY_LOCK:
                hit = _PWD_VERIFY.get(pkey)
                if hit and now - hit[1] < _PWD_VERIFY_TTL:
                    return bool(hit[0])
        dk = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode("utf-8"), int(iters))
        got = base64.b16encode(dk).decode().lower()
        ok = hmac.compare_digest(got, want)
        if pkey is not None:
            with _PWD_VERIFY_LOCK:
                _PWD_VERIFY[pkey] = (ok, now)
                if len(_PWD_VERIFY) > _PWD_VERIFY_MAX:
                    oldest = sorted(_PWD_VERIFY.items(), key=lambda kv: kv[1][1])[:512]
                    for k, _v in oldest:
                        _PWD_VERIFY.pop(k, None)
        return ok
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
        # 被踢下线的会话 → 原因（2026-11-17 单设备登录）：
        # 会话删掉后 SSE 还拿着这个 token，需要知道「为什么被踢」
        # 才能在学生端弹出能看懂的提示（在别处登录 / 密码被重置）。
        self._kicked = {}
        self._lock = threading.RLock()

    def create(self, role, hours=12, student_id=None, account=None):
        """account = 教师账号 id（多教师数据隔离用）。

        学生会话也要带 account：学生属于某位教师的班级，后续所有数据
        读写都必须落到同一个工作区，不能串到别的教师账号去。
        """
        token = secrets.token_urlsafe(24)
        now = time.time()
        with self._lock:
            self._d[token] = {
                "role": role,
                "student_id": student_id,
                "account": str(account or "default"),
                "exp": now + hours * 3600,
                "seen": now,
                "hours": hours,   # 2026-11-25：教师会话滑动续期用
            }
        return token

    def touch(self, token):
        with self._lock:
            s = self._d.get(token)
            if s:
                s["seen"] = time.time()

    def set(self, token, key, value):
        """往会话里塞内存态字段（如文件加密的 FEK）。重启即失效。"""
        with self._lock:
            s = self._d.get(token)
            if s:
                s[key] = value
                return True
        return False

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
            # 2026-11-25：教师会话「用着就续期」——只要还有请求进来就把到期时间
            # 往后顺延一个会话周期，避免老师上着课/改着作业突然被踢回登录页。
            # 学生会话仍保持绝对过期（8h），防止学生端长期挂机占用。
            if s.get("role") == "teacher":
                s["exp"] = time.time() + float(s.get("hours", 12)) * 3600
            return s

    def drop(self, token):
        with self._lock:
            self._d.pop(token, None)

    def drop_student(self, student_id, account=None, except_token=None, reason=""):
        """踢掉某个学生的所有会话（2026-11-17 单设备登录）。

        什么时候用：
          · 学生在其他设备登录 —— 旧设备必须退出（一台设备一个账号）；
          · 老师重置了他的密码 —— 旧会话立即失效，防止用旧身份继续操作。
        account 限定教师工作区（学生 id 只在工作区内唯一）；None = 不限。
        返回被踢掉的会话数。被踢原因记进 _kicked，SSE 断开前能读出来。
        """
        n = 0
        with self._lock:
            now = time.time()
            for t, s in list(self._d.items()):
                if s.get("role") != "student" or s.get("student_id") != student_id:
                    continue
                if s.get("exp", 0) < now:
                    continue
                if account is not None and str(s.get("account") or "") != str(account):
                    continue
                if t == except_token:
                    continue
                self._d.pop(t, None)
                if reason:
                    self._kicked[t] = str(reason)
                n += 1
            # 顺手清一次过期原因，避免字典慢慢变大
            if len(self._kicked) > 512:
                live = set(self._d)
                self._kicked = {t: r for t, r in self._kicked.items() if t not in live}
        return n

    def kick_reason(self, token):
        """被踢原因（读完即删）：SSE 用它给学生端一句能看懂的话。"""
        with self._lock:
            return self._kicked.pop(token, None)

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


# 是否信任内网反向代理发来的 X-Forwarded-For。
# 容器部署（compose 里的 gateway → workbench）时，TCP 对端是网关的内网地址，
# 不是回环地址；不打开这个开关的话，所有师生会共用同一个「IP」，
# 按 IP 的限速会互相拖累、登录失败记录也分不清是谁。
# 只在 compose 里显式设 WB_TRUST_PROXY=1 才生效：直连场景保持不信任，防伪造。
_TRUST_PROXY = (os.environ.get("WB_TRUST_PROXY") or "").strip().lower() in ("1", "true", "yes", "on")


def _is_private_peer(ip):
    """内网 / 回环地址判定（IPv4 私网段 + IPv6 本地地址）。"""
    ip = (ip or "").strip().lower()
    if not ip:
        return False
    if ip in ("127.0.0.1", "::1", "localhost"):
        return True
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    for pfx in ("10.", "192.168.", "127."):
        if ip.startswith(pfx):
            return True
    if ip.startswith("172."):
        try:
            return 16 <= int(ip.split(".")[1]) <= 31
        except Exception:
            return False
    return ip.startswith("fc") or ip.startswith("fd") or ip.startswith("fe80")


def client_ip(handler):
    """真实客户端 IP。

    只有在「直连方可信」时才信 X-Forwarded-For：
      · 直连方是回环地址 —— 前面挂着同机的反向代理（nginx/Caddy 反代到 127.0.0.1）；
      · 直连方是内网地址且设了 WB_TRUST_PROXY=1 —— 容器编排里的网关容器。
    其它情况一律用 TCP 对端地址：否则任何人都能伪造 XFF 绕过按 IP 的限速，
    或者把登录失败记录栽给别人。
    """
    try:
        peer = (handler.client_address or ["", ""])[0]
    except Exception:
        return ""
    if peer in ("127.0.0.1", "::1") or (_TRUST_PROXY and _is_private_peer(peer)):
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
