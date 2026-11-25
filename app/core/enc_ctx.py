#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态加密上下文（P1）。

FEK 存在请求线程的线程局部变量里，由 Handler._bind_account() 在每请求开头
从会话注入（登录时解出的 FEK 存 sess["fek"]）。所有加解密读写都从这里取 FEK。

关键安全口径：
  · 没有 FEK（未登录 / 管理员 / 后台线程）→ 解密拒绝，绝不返回明文；
  · 加密写入无 FEK → 拒绝（避免把明文误当密文落盘）；
  · 只加密内容，hash/rev/deleted 等同步元数据保持明文。
"""
import json
import base64
import os
import threading

try:
    import fec as FEC
except Exception:
    FEC = None

_ctx = threading.local()

# 账号级 FEK 常驻缓存：教师登录后解锁 FEK 填进来，学生请求按所属账号取用解密
# （学生要访问教师下发的加密课件 / 附件，但学生会话本身没有 FEK）。
# 服务重启 → 缓存清空 → 需教师重新登录解锁，与「重启失效」语义一致。
# 管理员 / 未登录会话永不填入，拿不到任何 FEK。
_account_feks = {}
_account_feks_lock = threading.RLock()
_unlock_hooks = []


def register_unlock_hook(fn):
    """注册 FEK 解锁回调 fn(account, fek)：用于重载启动时未能解密的缓存。"""
    with _account_feks_lock:
        if fn not in _unlock_hooks:
            _unlock_hooks.append(fn)


class NoFekError(RuntimeError):
    """没有可用的文件密钥：未登录教师会话 / 管理员 / 后台线程。"""


def set_fek(fek):
    _ctx.fek = fek if fek else None


def get_fek():
    return getattr(_ctx, "fek", None)


def clear_fek():
    _ctx.fek = None


def cache_account_fek(account, fek):
    """教师登录 / 启用加密后：把该账号 FEK 存入服务端常驻缓存。"""
    if not fek:
        return
    with _account_feks_lock:
        first_unlock = str(account) not in _account_feks
        _account_feks[str(account)] = fek
        hooks = list(_unlock_hooks) if first_unlock else []
    # 服务重启后各独立 JSON Store 在启动时读不到密文（无 FEK），保留了空内存。
    # 只在「本进程首次解锁」时通知它们重新装载；每个请求都重载会冲掉未保存草稿。
    # 关键：登录请求开头 _bind_account 时用户尚未登录、线程 FEK=None，而 hook
    # 里的 _load() → dec_json_file() → require_fek() 依赖线程 FEK。若不临时注入，
    # 重载会全部静默失败 → 打字/测验/设计/像素 JSON 一直是空内存（数据加载不出来）。
    # 用 try/finally 恢复原 FEK，避免影响本请求后续逻辑。
    if hooks:
        prev = get_fek()
        set_fek(fek)
        try:
            for fn in hooks:
                try:
                    fn(str(account), fek)
                except Exception:
                    pass
        finally:
            set_fek(prev)


def drop_account_fek(account):
    with _account_feks_lock:
        _account_feks.pop(str(account), None)


def cached_account_fek(account):
    with _account_feks_lock:
        return _account_feks.get(str(account))


def bind_request(account, sess_fek, role):
    """每请求开头调用：按会话角色把正确的 FEK 注入当前线程。

    teacher → 会话 FEK（并刷新账号缓存）；student → 按所属账号取缓存 FEK；
    admin / 匿名 → 一律 None（看不到任何明文）。
    """
    if role == "teacher" and sess_fek:
        cache_account_fek(account, sess_fek)
        set_fek(sess_fek)
    elif role == "student":
        set_fek(cached_account_fek(account))
    else:
        set_fek(None)


def available():
    return bool(FEC and FEC.available())


def require_fek():
    fek = get_fek()
    if not fek:
        raise NoFekError("文件密钥不可用（请重新登录）")
    return fek


# ---------------------------------------------------------------- 结构化数据
# rows.data / 独立 JSON：整体 AES-CBC+HMAC，存成 ascii 字符串（WBE1.<...>）
_WB64 = "WB64."     # base64 包装前缀，与 WBE1 明文 blob 区分


def enc_text(obj, domain="row"):
    """把 dict/list/str 加密成可存盘的字符串（base64 包一层，SQLite TEXT 安全）。无 FEK 抛 NoFekError。"""
    fek = require_fek()
    if not isinstance(obj, (str, bytes)):
        obj = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    if isinstance(obj, str):
        obj = obj.encode("utf-8")
    blob = FEC.encrypt_blob(obj, fek, domain=domain)
    return _WB64 + base64.b64encode(blob).decode("ascii")


def dec_text(blob, domain="row"):
    """解密 enc_text 的字符串，返回 dict/list/str。明文（迁移期）原样解析。"""
    if blob is None:
        return None
    if isinstance(blob, bytes):
        blob = blob.decode("utf-8", "replace")
    if blob.startswith(_WB64):
        fek = require_fek()
        raw_blob = base64.b64decode(blob[len(_WB64):])
        raw = FEC.decrypt_blob(raw_blob, fek, domain=domain)
        if raw is None:
            raise ValueError("数据解密失败（密钥错误或数据被篡改）")
        txt = raw.decode("utf-8", "replace")
    elif FEC.is_encrypted(blob):
        fek = require_fek()
        raw = FEC.decrypt_blob(blob.encode("ascii"), fek, domain=domain)
        if raw is None:
            raise ValueError("数据解密失败（密钥错误或数据被篡改）")
        txt = raw.decode("utf-8", "replace")
    else:
        # 存量明文：直接按 JSON 解析（迁移期兼容）
        try:
            return json.loads(blob)
        except Exception:
            return blob
    try:
        return json.loads(txt)
    except Exception:
        return txt


def enc_json_file(obj, domain="json"):
    """独立 JSON 文件整包加密 → bytes（可直接 open(...,'wb') 落盘）。"""
    fek = require_fek()
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return FEC.encrypt_blob(raw, fek, domain=domain)


def dec_json_file(blob, domain="json"):
    """独立 JSON 文件解密 → dict。明文（迁移期）兼容。"""
    if not blob:
        return {}
    if isinstance(blob, str):
        blob = blob.encode("utf-8")
    if not FEC.is_encrypted(blob):
        try:
            return json.loads(blob.decode("utf-8", "replace"))
        except Exception:
            return {}
    fek = require_fek()
    raw = FEC.decrypt_blob(blob, fek, domain=domain)
    # 兼容历史：存量迁移统一用 domain="json" 加密，早期代码曾按 "typing"/"quiz"
    # 读取导致解不开、数据全空。指定 domain 失败时回退试 "json"，双向可读，
    # 避免因 domain 不一致把积分/测验数据读成空壳。
    if raw is None and domain != "json":
        raw = FEC.decrypt_blob(blob, fek, domain="json")
    if raw is None:
        raise ValueError("JSON 解密失败（密钥错误或数据被篡改）")
    return json.loads(raw.decode("utf-8", "replace"))


def is_encrypted_path(path):
    """磁盘文件是否已是密文（WBE1）—— 未解锁时绝不写明文覆盖。"""
    try:
        with open(path, "rb") as f:
            head = f.read(4)
        return bool(FEC and FEC.is_encrypted(head))
    except OSError:
        return False


def read_json_file(path, domain="json"):
    """读取独立 JSON（明文兼容）；密文且无 FEK → NoFekError。"""
    with open(path, "rb") as f:
        raw = f.read()
    return dec_json_file(raw, domain=domain)


def write_json_file(path, obj, domain="json"):
    """原子写独立 JSON：有 FEK 落密文；磁盘已是密文而无 FEK → 拒绝覆盖。"""
    if is_encrypted_path(path) and not get_fek():
        raise NoFekError("数据密钥不可用（请重新登录后再保存）")
    raw = enc_json_file(obj, domain=domain) if get_fek() else json.dumps(
        obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------- 二进制文件
def enc_file(plain, domain="file"):
    fek = require_fek()
    return FEC.encrypt_file_bytes(plain, fek)


def dec_file(wbf):
    """整块解密；明文（迁移期）原样返回。"""
    if not wbf:
        return wbf
    if not FEC.is_file_encrypted(wbf[:4]):
        return wbf                       # 存量明文
    fek = require_fek()
    out = FEC.decrypt_file_bytes(wbf, fek)
    if out is None:
        raise ValueError("文件解密失败（密钥错误或数据被篡改）")
    return out


def dec_file_range(wbf, start, length):
    """Range 随机访问解密；明文文件直接切片。"""
    if not wbf:
        return b""
    if not FEC.is_file_encrypted(wbf[:4]):
        return wbf[start:start + length]
    fek = require_fek()
    out = FEC.decrypt_range(wbf, fek, start, length)
    if out is None:
        raise ValueError("文件 Range 解密失败")
    return out


def dec_file_stream(fh, start=0, length=None):
    """流式解密（yield 明文块）。有 FEK 才解密文；明文文件直通。"""
    head = fh.read(4)
    fh.seek(0)
    if FEC.is_file_encrypted(head):
        fek = require_fek()
        yield from FEC.decrypt_file_stream(fh, fek, start, length)
    else:
        remain = length
        while remain is None or remain > 0:
            chunk = fh.read(65536 if remain is None else min(65536, remain))
            if not chunk:
                break
            yield chunk
            if remain is not None:
                remain -= len(chunk)


def enc_file_stream(src, dst):
    """流式加密（有 FEK 才加密，否则原样拷流）。返回明文字节数。"""
    fek = get_fek()
    if fek:
        return FEC.encrypt_file_stream(src, dst, fek)
    # 未启用加密：原样流式拷贝
    n = 0
    while True:
        chunk = src.read(262144)
        if not chunk:
            break
        dst.write(chunk)
        n += len(chunk)
    return n


def enc_file_stream_limited(src, dst, length):
    """流式加密「恰好 length 字节」（HTTP 请求体不能多读一个字节）。"""
    fek = require_fek()

    class _Limited(object):
        def __init__(self, s, n):
            self.s, self.left = s, int(n)

        def read(self, n=-1):
            if self.left <= 0:
                return b""
            if n is None or n < 0:
                n = self.left
            chunk = self.s.read(min(n, self.left))
            self.left -= len(chunk)
            return chunk

    return FEC.encrypt_file_stream(_Limited(src, length), dst, fek)


def file_size_on_disk(plain_size):
    return FEC.encrypted_size(plain_size)
