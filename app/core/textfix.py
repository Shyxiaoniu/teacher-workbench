# -*- coding: utf-8 -*-
"""文件名 / 文本乱码还原（纯标准库）。

为什么需要这个模块（2026-11-05 牛哥反馈「归档出来的文件名是乱码」）：

  浏览器 multipart 上传时，中文文件名是**直接以 UTF-8 字节**塞在
  `Content-Disposition: …; filename="…"` 头里的。HTTP 头本身按 latin-1 /
  ascii 读，历史上换过三种解析口径，任何一环错了，名字就会变成：

      ä½œä¸š.txt         UTF-8 字节被当成 latin-1（cgi.FieldStorage 时代）
      \\udce4\\udcbd…     ascii + surrogateescape 的残渣（email 解析器）
      浣滀笟.txt         UTF-8 字节被当成 GBK（个别旧客户端/工具）

  错码会一路存进 act_file.orig，再被自动归档原样复制到「资料」，
  于是老师看到的就是一堆认不出是谁交的乱码文件名。

修复原则（宁可不改，也不能改坏）：
  · 只处理**确定是错码**的两种形态：surrogateescape 残渣、latin-1 错码；
  · 只有当“还原结果像中文、原文不像”时才采纳；
  · 正常的中文 / 英文文件名原样返回，绝不做多余转换。
"""
import re
import unicodedata

# latin-1 错码的典型前缀字符：UTF-8 中文丢进 latin-1 一定落在这个区间
_MOJI_CHARS = set("ÃÂÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞß"
                  "âãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ")

# 纯十六进制/随机名（服务端落盘用的 uuid 名），不是学生起的名字
_UUID_LIKE = re.compile(r"^[0-9a-fA-F]{8,32}(\.[A-Za-z0-9]{1,8})?$")


def _cjk_count(s):
    n = 0
    for ch in s:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            n += 1
    return n


def _bad_count(s):
    n = 0
    for ch in s:
        o = ord(ch)
        if 0xD800 <= o <= 0xDFFF or ch == "\ufffd" or ch in _MOJI_CHARS:
            n += 1
    return n


def _score(s):
    """像中文的程度：汉字加分，错码字符/替换符扣分。"""
    n = max(1, len(s))
    return (_cjk_count(s) * 2.0 - _bad_count(s)) / n


def has_surrogate(s):
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in str(s or ""))


def fix_text(text, max_len=0):
    """把错码文件名还原成正常中文；正常文本原样返回。"""
    s = str(text or "")
    if not s:
        return s

    # ① surrogateescape 残渣：原始字节还在，按 UTF-8 还原即可
    if has_surrogate(s):
        try:
            s = s.encode("utf-8", "surrogateescape").decode("utf-8")
        except Exception:
            try:
                s = s.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
            except Exception:
                pass

    # ② latin-1 错码：原文几乎没有汉字、却有一堆 ÃÂåä 这类字符，才尝试还原
    if _bad_count(s) and _cjk_count(s) == 0:
        try:
            cand = s.encode("latin-1").decode("utf-8")
        except Exception:
            cand = ""
        if cand and _score(cand) > _score(s) + 0.2:
            s = cand

    s = unicodedata.normalize("NFC", s)
    if max_len:
        s = s[:max_len]
    return s


def fix_filename(name, max_len=0):
    """文件名专用：先还原错码，再把 %XX 百分号编码解回来。

    前端有些链路（历史归档、下载地址）会把文件名转成 %E4%BD%9C…，
    直接当文件名用就是「乱码」。这里统一解回中文。
    """
    s = fix_text(name)
    if "%" in s:
        try:
            from urllib.parse import unquote
            u = unquote(s, encoding="utf-8", errors="strict")
            if u and u != s and _cjk_count(u) >= _cjk_count(s):
                s = u
        except Exception:
            pass
    return fix_text(s, max_len=max_len)


def looks_like_uuid_name(name):
    """像服务端落盘名（12 位 hex + 扩展名），不是学生上传时的原名。"""
    return bool(_UUID_LIKE.match(str(name or "").strip()))
