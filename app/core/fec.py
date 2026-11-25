#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件静态加密核心（P0）。

设计（见 .workbuddy/静态加密方案-v1.md）：
  · 每个账号一把随机 FEK（32 字节），真正加解密文件。
  · 密码派生 KEK = PBKDF2(密码 + 独立盐 + 域标签)，切分出 enc/mac 两把子钥。
  · 用 AES-256-CBC + HMAC-SHA256 包裹 FEK（seal/unseal），存 accounts.json。
  · 恢复码同理解包同一把 FEK（另一组盐 + 域标签）。
  · 改密码 = 旧密码解包 → 新密码重包裹，FEK 不变（文件不用重加密）。

依赖：仅标准库 + ctypes 调系统 libcrypto（OpenSSL）的 EVP 接口。
  · Windows 内置运行时：runtime/win-x64/python/libcrypto-3.dll
  · Windows 系统 Python：libcrypto-3.dll（PATH / System32）
  · Docker python:3.12-slim：libcrypto.so.3
纯 .py 随 app/ 走在线更新下发，不改 Dockerfile / runtime / 离线包结构。

算法实现均用 ctypes 直接调 EVP（C 速度），避免纯 Python AES 的性能问题。
"""
import os
import sys
import ctypes
import ctypes.util
import hashlib
import hmac
import base64
import secrets
import threading
from collections import OrderedDict

_KDF_ROUNDS = 120000          # 与 auth.py 的口令哈希同强度
_KDF_CACHE = OrderedDict()    # FEK 子密钥缓存（高熵 FEK，派生结果可复用）
_KDF_CACHE_LOCK = threading.Lock()
_KDF_CACHE_MAX = 64
_SALT_LEN = 16
_IV_LEN = 16
_FEK_LEN = 32
_MAGIC = "WBE1"                # 密钥包裹 blob 的版本前缀

# ---------------------------------------------------------------- libcrypto
_LIB = None
_LIB_TRIED = []


def _candidates():
    out = []
    here = os.path.dirname(os.path.abspath(__file__))       # app/core
    app_dir = os.path.dirname(here)                          # app
    root = os.path.dirname(app_dir)                          # 项目根
    # Windows 内置运行时（优先，离线包/在线更新都带）
    out.append(os.path.join(root, "runtime", "win-x64", "python", "libcrypto-3.dll"))
    out.append(os.path.join(app_dir, "runtime", "win-x64", "python", "libcrypto-3.dll"))
    try:
        name = ctypes.util.find_library("crypto")
        if name:
            out.append(name)
    except Exception:
        pass
    # Linux / Docker
    out += ["libcrypto.so.3", "libcrypto.so.1.1",
            "/usr/lib/x86_64-linux-gnu/libcrypto.so.3",
            "/usr/lib64/libcrypto.so.3"]
    # Windows 系统
    out += ["libcrypto-3.dll", "libcrypto-1_1.dll"]
    seen, uniq = set(), []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def _lib():
    global _LIB
    if _LIB is not None:
        return _LIB
    last = None
    for c in _candidates():
        try:
            _LIB_TRIED.append(c)
            lib = ctypes.CDLL(c)
            # 探测一个 EVP 符号，确认是 libcrypto 不是同名空壳
            if not hasattr(lib, "EVP_aes_256_cbc"):
                last = RuntimeError("%s 无 EVP 接口" % c)
                continue
            _LIB = lib
            return lib
        except Exception as e:
            last = e
    raise RuntimeError("无法加载 libcrypto（OpenSSL）：%s；已尝试 %s"
                       % (last, _LIB_TRIED))


def available():
    """加密能力是否可用（启动自检 / 降级判断用）。"""
    try:
        _lib()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- EVP 低层
def _bind_evp(lib):
    c_void = ctypes.c_void_p
    c_int = ctypes.c_int
    lib.EVP_CIPHER_CTX_new.restype = c_void
    lib.EVP_CIPHER_CTX_free.argtypes = [c_void]
    for nm in ("EVP_aes_256_cbc", "EVP_aes_256_ctr"):
        getattr(lib, nm).restype = c_void
    lib.EVP_EncryptInit_ex.argtypes = [c_void, c_void, c_void, c_void, c_void]
    lib.EVP_EncryptUpdate.argtypes = [c_void, c_void, ctypes.POINTER(c_int), c_void, c_int]
    lib.EVP_EncryptFinal_ex.argtypes = [c_void, c_void, ctypes.POINTER(c_int)]
    lib.EVP_DecryptInit_ex.argtypes = [c_void, c_void, c_void, c_void, c_void]
    lib.EVP_DecryptUpdate.argtypes = [c_void, c_void, ctypes.POINTER(c_int), c_void, c_int]
    lib.EVP_DecryptFinal_ex.argtypes = [c_void, c_void, ctypes.POINTER(c_int)]


def _aes_crypt(data, key, iv, cipher_name, encrypt):
    """AES-CBC / CTR 加解密。data/key/iv 都是 bytes，返回 bytes。"""
    if not data:
        return b""
    lib = _lib()
    _bind_evp(lib)
    cipher = getattr(lib, cipher_name)()
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        raise RuntimeError("EVP_CIPHER_CTX_new 失败")
    try:
        init = lib.EVP_EncryptInit_ex if encrypt else lib.EVP_DecryptInit_ex
        update = lib.EVP_EncryptUpdate if encrypt else lib.EVP_DecryptUpdate
        final = lib.EVP_EncryptFinal_ex if encrypt else lib.EVP_DecryptFinal_ex
        if not init(ctx, cipher, None, key, iv):
            raise RuntimeError("EVP %s Init 失败" % cipher_name)
        out = ctypes.create_string_buffer(len(data) + 32)
        n1 = ctypes.c_int(0)
        if not update(ctx, out, ctypes.byref(n1), data, len(data)):
            raise RuntimeError("EVP %s Update 失败" % cipher_name)
        n2 = ctypes.c_int(0)
        if not final(ctx, ctypes.byref(out, n1.value), ctypes.byref(n2)):
            raise ValueError("EVP %s Final 失败（密码/密钥错误或数据被篡改）" % cipher_name)
        return out.raw[:n1.value + n2.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


# ---------------------------------------------------------------- 密钥派生
def derive_keys(master, salt, domain):
    """从主密钥派生 (enc_key, mac_key)，各 32 字节。

    master : bytes 或 str
    salt   : bytes
    domain : bytes，域分隔标签，避免密码/恢复码两条路径派生出同样的钥匙。
    """
    if isinstance(master, str):
        master = master.encode("utf-8")
    if isinstance(domain, str):
        domain = domain.encode("utf-8")
    # 性能（2026-11-25）：FEK 是 256bit 随机密钥，不是用户口令；数据读写每次
    # 重跑 12 万轮 PBKDF2 会让一万行同步跑十几分钟。同一 FEK 的派生结果做
    # 有上限的内存缓存（进程重启即失效），密码字符串（低熵）永不进缓存。
    cache_key = None
    if isinstance(master, bytes) and len(master) == _FEK_LEN:
        cache_key = (master, salt, domain)
        with _KDF_CACHE_LOCK:
            hit = _KDF_CACHE.get(cache_key)
            if hit:
                _KDF_CACHE.move_to_end(cache_key)
                return hit[0], hit[1]
    dk = hashlib.pbkdf2_hmac("sha256", master + b"\x00" + domain, salt, _KDF_ROUNDS, 64)
    out = (dk[:32], dk[32:])
    if cache_key:
        with _KDF_CACHE_LOCK:
            _KDF_CACHE[cache_key] = out
            while len(_KDF_CACHE) > _KDF_CACHE_MAX:
                _KDF_CACHE.popitem(last=False)
    return out


def new_fek():
    return secrets.token_bytes(_FEK_LEN)


def new_salt():
    return secrets.token_bytes(_SALT_LEN)


# ---------------------------------------------------------------- FEK 包裹
def seal_fek(fek, password, salt=None, domain="pw"):
    """用密码把 FEK 包成可存盘的字符串。返回 (blob, salt)。

    blob 格式：WBE1.<b64salt>.<b64iv>.<b64mac>.<b64ct>
    salt 单独返回是为了存进账号记录；blob 里也带一份便于自校验。
    """
    salt = salt or new_salt()
    enc_key, mac_key = derive_keys(password, salt, "fek:" + domain)
    iv = secrets.token_bytes(_IV_LEN)
    ct = _aes_crypt(fek, enc_key, iv, "EVP_aes_256_cbc", True)
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    blob = ".".join([_MAGIC, b64(salt), b64(iv), b64(mac), b64(ct)])
    return blob, salt


def unseal_fek(blob, password, domain="pw"):
    """从包裹串还原 FEK；密码错 / 数据被篡改返回 None。"""
    try:
        parts = (blob or "").split(".")
        if len(parts) != 5 or parts[0] != _MAGIC:
            return None
        salt, iv, mac, ct = [base64.b64decode(x) for x in parts[1:]]
        enc_key, mac_key = derive_keys(password, salt, "fek:" + domain)
        want = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(want, mac):
            return None
        fek = _aes_crypt(ct, enc_key, iv, "EVP_aes_256_cbc", False)
        return fek if len(fek) == _FEK_LEN else None
    except Exception:
        return None


# ---------------------------------------------------------------- 恢复码
def new_recovery_code():
    """生成高熵恢复码，形如 ABCD-EFGH-IJKL-MNOP（Base32，去掉易混字符由展示层处理）。"""
    raw = secrets.token_bytes(15)                 # 24 个 Base32 字符
    s = base64.b32encode(raw).decode("ascii").rstrip("=")
    return "-".join(s[i:i + 6] for i in range(0, len(s), 6))


def norm_recovery_code(code):
    """归一化：去空白/连字符、转大写，便于用户输入容错。"""
    return "".join(ch for ch in str(code or "").upper() if ch.isalnum())


def recovery_hash(code):
    """恢复码只存这个哈希，用于校验；不存明文。"""
    n = norm_recovery_code(code)
    if len(n) < 16:
        raise ValueError("恢复码太短")
    return hashlib.sha256(("rec:" + n).encode("utf-8")).hexdigest()


def verify_recovery_code(code, stored_hash):
    try:
        return hmac.compare_digest(recovery_hash(code), str(stored_hash or ""))
    except Exception:
        return False


# ---------------------------------------------------------------- 文件加解密（P1 预留）
def encrypt_blob(data, fek, domain="file"):
    """用 FEK 加密任意字节（AES-256-CBC + HMAC）。返回带 magic 的 bytes。

    用于小 JSON / 小附件。大文件走流式 CTR（P1）。
    domain 区分用途：file=文件内容 / recovery=恢复码密文，避免跨用途重放。
    """
    enc_key, mac_key = derive_keys(fek, b"file-blob", "blob:" + domain)
    iv = secrets.token_bytes(_IV_LEN)
    ct = _aes_crypt(data, enc_key, iv, "EVP_aes_256_cbc", True)
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
    return _MAGIC.encode("ascii") + iv + mac + ct


def decrypt_blob(blob, fek, domain="file"):
    """解密 encrypt_blob 的产物；校验 HMAC，失败返回 None。"""
    try:
        if not blob or not blob.startswith(_MAGIC.encode("ascii")):
            return None                            # 明文文件（迁移期）
        body = blob[len(_MAGIC):]
        iv, mac, ct = body[:16], body[16:48], body[48:]
        enc_key, mac_key = derive_keys(fek, b"file-blob", "blob:" + domain)
        if not hmac.compare_digest(hmac.new(mac_key, iv + ct, hashlib.sha256).digest(), mac):
            return None
        return _aes_crypt(ct, enc_key, iv, "EVP_aes_256_cbc", False)
    except Exception:
        return None


def is_encrypted(blob):
    """blob 是否以密文 magic 开头。同时接受 bytes / str：

    调用方有从 SQLite TEXT 读出来的 str（rows.data），也有文件流读出的 bytes。
    早期只认 bytes，传 str 会抛 TypeError，导致 store._dec_data 把**所有明文行**
    误判成解析失败、返回 {}（班级名/学生名大面积丢失）。这里统一按 bytes 比较。
    """
    if not blob:
        return False
    if isinstance(blob, str):
        blob = blob.encode("utf-8", "replace")
    return bool(blob.startswith(_MAGIC.encode("ascii")))


# ---------------------------------------------------------------- 文件/数据加密（P1+）
# 文件流格式：WBF1(4) + IV(16) + HMAC(32) + CTR密文
#   · 用 CTR 是为了支持音视频 Range 拖动进度条（CBC 做不到随机访问）
#   · IV 当 128-bit 大端计数器；Range 时按字节偏移定位到块，解出后丢块内前缀
_FILE_MAGIC = b"WBF1"
_HMAC_LEN = 32
_FILE_HEADER = 4 + _IV_LEN + _HMAC_LEN       # 52


def file_keys(fek):
    """从 FEK 派生文件内容密钥 (enc_key, mac_key)，与密钥包裹域隔离。"""
    return derive_keys(fek, b"file-ctr", "file-content")


def ctr_iv_at(iv, offset):
    """把 IV 视为 128-bit 大端计数器，返回明文 offset 所在块的起始 IV。"""
    block = int.from_bytes(iv, "big") + (offset // _IV_LEN)
    return (block & ((1 << 128) - 1)).to_bytes(_IV_LEN, "big")


def encrypt_file_bytes(plain, fek):
    """整块加密文件内容（≤几十 MB，活动附件上限 40MB，内存可接受）。
    返回 WBF1 格式 bytes：magic + iv + hmac(iv+ct) + ct。"""
    enc_key, mac_key = file_keys(fek)
    iv = secrets.token_bytes(_IV_LEN)
    ct = _aes_crypt(plain, enc_key, iv, "EVP_aes_256_ctr", True)
    mac = hmac.new(mac_key, iv + ct, hashlib.sha256).digest()
    return _FILE_MAGIC + iv + mac + ct


def decrypt_file_bytes(wbf, fek):
    """整块解密 WBF1 文件；校验 HMAC，失败/明文返回 None。"""
    try:
        if not wbf or not wbf.startswith(_FILE_MAGIC):
            return None
        iv = wbf[4:20]
        mac = wbf[20:52]
        ct = wbf[52:]
        enc_key, mac_key = file_keys(fek)
        if not hmac.compare_digest(hmac.new(mac_key, iv + ct, hashlib.sha256).digest(), mac):
            return None
        return _aes_crypt(ct, enc_key, iv, "EVP_aes_256_ctr", False)
    except Exception:
        return None


def is_file_encrypted(head):
    return bool(head and head[:4] == _FILE_MAGIC)


def decrypt_range(wbf, fek, start, length):
    """Range 随机访问：只解密 [start, start+length) 明文字节，不读整个文件到内存重复解密。
    wbf 是整个 WBF1 文件的 bytes（调用方已从磁盘读入；大文件另有流式版本）。"""
    try:
        if not wbf.startswith(_FILE_MAGIC):
            # 明文文件：直接切片
            return wbf[start:start + length]
        iv = wbf[4:20]
        ct = wbf[52:]
        enc_key, _ = file_keys(fek)
        aligned = (start // _IV_LEN) * _IV_LEN
        rem = start - aligned
        need = rem + length
        chunk = ct[aligned:aligned + need]
        if not chunk:
            return b""
        dec = _aes_crypt(chunk, enc_key, ctr_iv_at(iv, aligned), "EVP_aes_256_ctr", False)
        return dec[rem:rem + length]
    except Exception:
        return None


def decrypt_file_stream(fh, fek, start=0, length=None):
    """流式解密磁盘 WBF1 文件，yield 明文字节块（不整块载入内存）。

    fh: 以 'rb' 打开的文件对象；start/length 是【明文】坐标 Range。
    存量明文文件（迁移期）兼容：按明文坐标直接分块 yield。
    """
    head = fh.read(_FILE_HEADER)
    if not head.startswith(_FILE_MAGIC):
        fh.seek(start)
        remain = length
        while remain is None or remain > 0:
            chunk = fh.read(65536 if remain is None else min(65536, remain))
            if not chunk:
                break
            yield chunk
            if remain is not None:
                remain -= len(chunk)
        return
    iv = head[4:20]
    fh.seek(0, os.SEEK_END)
    ct_size = fh.tell() - _FILE_HEADER
    if length is None:
        length = ct_size - start
    if start >= ct_size or length <= 0:
        return
    enc_key, _ = file_keys(fek)
    aligned = (start // _IV_LEN) * _IV_LEN
    rem = start - aligned
    fh.seek(_FILE_HEADER + aligned)
    pos, left = aligned, rem + length
    while left > 0:
        chunk = fh.read(min(65536, left))
        if not chunk:
            break
        dec = _aes_crypt(chunk, enc_key, ctr_iv_at(iv, pos), "EVP_aes_256_ctr", False)
        if rem:
            dec = dec[rem:]
            rem = 0
        if dec:
            yield dec
        pos += len(chunk)
        left -= len(chunk)


def encrypted_size(plain_size):
    """明文长度 → WBF1 文件长度（CTR 无 padding，只加头部）。"""
    return _FILE_HEADER + plain_size


def encrypt_file_stream(src, dst, fek):
    """流式加密写入：从 src.read(chunk) 读明文，写 WBF1 到 dst（'wb' 文件对象）。

    先写 magic+iv+HMAC占位，边读边 CTR 加密（保持计数器连续），累计 HMAC，
    最后 seek 回填 HMAC。全程常数内存，适合几百 MB 课件。返回写入明文字节数。
    """
    enc_key, mac_key = file_keys(fek)
    iv = secrets.token_bytes(_IV_LEN)
    dst.write(_FILE_MAGIC + iv + (b"\x00" * _HMAC_LEN))
    h = hmac.new(mac_key, iv, hashlib.sha256)
    # CTR 计数器跨块连续：用单一 EVP ctx 逐块 update
    lib = _lib()
    _bind_evp(lib)
    cipher = getattr(lib, "EVP_aes_256_ctr")()
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx or not lib.EVP_EncryptInit_ex(ctx, cipher, None, enc_key, iv):
        raise RuntimeError("CTR 流式加密初始化失败")
    n_written = 0
    try:
        while True:
            chunk = src.read(262144)
            if not chunk:
                break
            out = ctypes.create_string_buffer(len(chunk) + 32)
            n1 = ctypes.c_int(0)
            if not lib.EVP_EncryptUpdate(ctx, out, ctypes.byref(n1), chunk, len(chunk)):
                raise RuntimeError("CTR 流式加密失败")
            ct = out.raw[:n1.value]
            dst.write(ct)
            h.update(ct)
            n_written += len(chunk)
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)
    mac = h.digest()
    pos = dst.tell()
    dst.seek(4 + _IV_LEN)
    dst.write(mac)
    dst.seek(pos)
    return n_written


# ---------------------------------------------------------------- 自检
def self_test():
    """被 server.py 启动自检调用；返回 (ok, 说明)。"""
    try:
        lib = _lib()
        fek = new_fek()
        pw, salt = "TestPass123!", new_salt()
        blob, _ = seal_fek(fek, pw, salt)
        assert unseal_fek(blob, pw) == fek, "密码解包失败"
        assert unseal_fek(blob, "wrong") is None, "错密码不应解出"
        rc = new_recovery_code()
        rh = recovery_hash(rc)
        assert verify_recovery_code(rc, rh), "恢复码校验失败"
        assert verify_recovery_code(norm_recovery_code(rc), rh), "归一化后应通过"
        assert not verify_recovery_code("XXXX-XXXX-XXXX-XXXX", rh), "错恢复码不应通过"
        data = b"hello \xe4\xb8\xad\xe6\x96\x87" * 100
        enc = encrypt_blob(data, fek)
        assert is_encrypted(enc), "加密头缺失"
        assert decrypt_blob(enc, fek) == data, "文件加解密 round-trip 失败"
        assert decrypt_blob(enc, new_fek()) is None, "错 FEK 不应解出"
        # 文件 CTR 格式 + Range 随机访问
        big = bytes(range(256)) * 40          # 10240 字节，跨多个 16 字节块
        wbf = encrypt_file_bytes(big, fek)
        assert is_file_encrypted(wbf[:4]), "文件 magic 缺失"
        assert decrypt_file_bytes(wbf, fek) == big, "文件 round-trip 失败"
        assert decrypt_file_bytes(wbf, new_fek()) is None, "错 FEK 不应解出文件"
        assert decrypt_range(wbf, fek, 100, 500) == big[100:600], "Range 解密失败"
        assert decrypt_range(wbf, fek, 0, 33) == big[0:33], "Range 起点对齐失败"
        assert decrypt_range(wbf, fek, 10200, 40) == big[10200:10240], "Range 末尾失败"
        return True, "OpenSSL EVP 可用，密钥/恢复码/文件/Range 加解密 round-trip 通过"
    except Exception as e:
        return False, "加密自检失败：%s" % e


if __name__ == "__main__":
    ok, msg = self_test()
    print(("OK  " if ok else "FAIL ") + msg)
    sys.exit(0 if ok else 1)
