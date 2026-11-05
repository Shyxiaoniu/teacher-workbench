#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学习单提交存档（2026-11-05）

学生提交学习单时，页面会把「填好答案的整页快照」一起发上来（字段 __archive）。
这里负责把它变成一份**独立的 .html 文件**：双击能开、内容与结构跟学生当时
看到的一致，但不含任何脚本与登录浮层。

为什么在浏览器端抓快照
----------------------
学习单是老师自己写的 HTML，控件千奇百怪（input / textarea / select /
contenteditable 各种组合）。服务端只拿得到「name → 值」的扁平字段，
要还原成原版式等于重写一个渲染器，稍有出入就不准。页面自己克隆 DOM，
把用户输入回写到属性上再序列化，天然 1:1。

为什么还要在服务端洗一遍
------------------------
快照来自学生浏览器，属于不可信输入。老师会打开这份存档，
所以必须当成「别人发来的 HTML」处理：脚本、内联事件、javascript: URL、
iframe/object、meta refresh、form 一律拿掉。宁可少一点动效，
也不能让存档页在教师端同源执行脚本。
"""
import html as _html
import re

MAX_BYTES = 3 * 1024 * 1024          # 单份存档上限，超过就放弃存档（不挡提交）

_SCRIPT_PAIR = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
_SCRIPT_ONE = re.compile(r"<script\b[^>]*?/\s*>", re.I)
_SCRIPT_OPEN = re.compile(r"<script\b[^>]*>", re.I)      # 没闭合的残片
_IFRAME_PAIR = re.compile(r"<(iframe|object)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_TAG_KILL = re.compile(r"</?(iframe|object|embed|frame|frameset|applet|base)\b[^>]*>", re.I)
_META_REFRESH = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*(?:\"refresh\"|'refresh'|refresh)[^>]*>", re.I)
_ON_ATTR = re.compile(r"""\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_JS_URL = re.compile(r"""(\s(?:href|src|action)\s*=\s*)(?:"\s*javascript:[^"]*"|'\s*javascript:[^']*')""", re.I)
_FORM_OPEN = re.compile(r"<form\b", re.I)
_FORM_CLOSE = re.compile(r"</form\s*>", re.I)
_CHARSET = re.compile(r"<meta\b[^>]*charset\s*=", re.I)
_TITLE = re.compile(r"<title\b[^>]*>.*?</title\s*>", re.I | re.S)
_BODY_OPEN = re.compile(r"<body\b[^>]*>", re.I)
_HEAD_OPEN = re.compile(r"<head\b[^>]*>", re.I)


def sanitize(raw):
    """去掉一切可能执行的东西，保留版式与文字。"""
    h = str(raw or "")
    if not h.strip():
        return ""
    try:
        if len(h.encode("utf-8", "ignore")) > MAX_BYTES:
            return ""
    except Exception:
        return ""
    h = _SCRIPT_PAIR.sub("", h)
    h = _SCRIPT_ONE.sub("", h)
    h = _SCRIPT_OPEN.sub("", h)
    h = _IFRAME_PAIR.sub("", h)
    h = _TAG_KILL.sub("", h)
    h = _META_REFRESH.sub("", h)
    h = _JS_URL.sub(r'\1"#"', h)
    h = _ON_ATTR.sub("", h)
    # 表单改成普通块：没有脚本时它本来也提交不了，但浏览器仍会在回车时
    # 尝试 GET/POST 当前地址，存档页不该有这种互动。
    h = _FORM_OPEN.sub("<div", h)
    h = _FORM_CLOSE.sub("</div>", h)
    return h


def _esc(v):
    return _html.escape(str(v if v is not None else ""), quote=True)


_STYLE = """<style>
.wb-arc{font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  margin:0 0 18px;padding:12px 16px;border-radius:12px;background:#F2F7F4;
  border:1px solid #D7E7DE;color:#22312A;line-height:1.7;font-size:14px}
.wb-arc-t{font-size:17px;font-weight:800;color:#0F6E56;margin-bottom:2px}
.wb-arc-m{font-size:13px;color:#5F6E68}
.wb-arc-m b{color:#22312A;font-weight:700}
.wb-arc-n{font-size:12px;color:#8A9992;margin-top:4px}
/* 存档是只读的：禁用的控件不要变灰，否则学生填的字看不清 */
input[disabled],textarea[disabled],select[disabled],fieldset[disabled]{
  opacity:1!important;color:inherit!important;-webkit-text-fill-color:currentColor!important;
  background:transparent!important}
@media print{.wb-arc{border:0;background:transparent;padding:0}}
</style>"""


def build(raw_html, meta=None):
    """把页面快照变成可独立打开的存档 HTML。返回空串表示这份不能用。"""
    h = sanitize(raw_html)
    if not h:
        return ""
    meta = meta or {}
    who = " · ".join([x for x in (str(meta.get("cls") or "").strip(),
                                  str(meta.get("name") or "").strip()) if x])
    sid = str(meta.get("sid") or "").strip()
    if sid:
        who = (who + "（" + sid + "）") if who else ("学号 " + sid)
    title = str(meta.get("title") or meta.get("task") or "学习单").strip()
    ts = str(meta.get("time") or "").strip()
    banner = (
        '<div class="wb-arc">'
        '<div class="wb-arc-t">' + _esc(title) + '</div>'
        '<div class="wb-arc-m">' + ("<b>" + _esc(who) + "</b>" if who else "")
        + (" · 提交于 " + _esc(ts) if ts else "") + '</div>'
        '<div class="wb-arc-n">这是学生提交时的页面存档（副本）：答案与版式按提交那一刻保存，'
        '脚本和登录框已移除。</div>'
        '</div>'
    )
    # 统一 UTF-8：存档可能被拷到别的机器上用浏览器打开
    if not _CHARSET.search(h[:4096]):
        if _HEAD_OPEN.search(h):
            h = _HEAD_OPEN.sub(lambda m: m.group(0) + '<meta charset="utf-8">', h, count=1)
        else:
            h = '<meta charset="utf-8">' + h
    if not _TITLE.search(h):
        if _HEAD_OPEN.search(h):
            h = _HEAD_OPEN.sub(lambda m: m.group(0) + "<title>" + _esc(title) + "</title>", h, count=1)
    if _BODY_OPEN.search(h):
        h = _BODY_OPEN.sub(lambda m: m.group(0) + _STYLE + banner, h, count=1)
    else:
        h = _STYLE + banner + h
    if not re.search(r"<html\b", h, re.I):
        h = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
             '<title>' + _esc(title) + '</title></head><body>' + h + "</body></html>")
    elif not re.match(r"\s*<!doctype", h, re.I):
        h = "<!doctype html>" + h
    return h
