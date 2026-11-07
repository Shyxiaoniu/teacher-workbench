#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · H5 网页设计草稿（design_store.py）

设计要点
--------
  · 独立 JSON（DATA_DIR/design.json），不进 29 表同步体系 —— 学生设计稿
    是「过程文件」，几十 KB 一段，写频率高，走独立通道最省事；
  · 原子落盘（临时文件 + os.replace），一把锁；
  · 只存草稿，不存提交记录：提交走 ActivityStore（大屏 / 作品墙 / 归档
    都读那一份），这里再存一遍只会出现两套口径；
  · 草稿按 student_id 存，学生换设备登录同一个人就能接着改。

数据形状
--------
{
  "drafts": {
    "<student_id>": {"html": "...", "css": "...", "title": "...",
                     "mode": "hl|plain", "width": 900, "updated": 1730...}
  },
  "updated": 1730...
}

教师练习草稿也放这里，key 用 "t:<账号>" 前缀与学生 id 区分开：
学生 id 全是数字，永远不会和带冒号的教师键撞上。
"""
import json
import os
import threading
import time

# 单个学生草稿的上限：正常网页几十 KB，超过 200KB 基本是误粘贴了一整张图片
MAX_HTML = 200 * 1024
MAX_CSS = 100 * 1024

# mode：代码区显示方式（2.0.40）
#   hl    = 关键词高亮（标签 / 属性 / 选择器分色）
#   plain = 普通文本（纯 textarea，复制粘贴最省心）
MODES = ("hl", "plain")
WIDTHS = (900, 768, 390)

DEFAULT_DRAFT = {"html": "", "css": "", "title": "", "mode": "", "width": 0, "updated": 0}


def _safe_width(v):
    try:
        n = int(v or 0)
    except Exception:
        return 0
    return n if n in WIDTHS else 0


class DesignStore(object):
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"drafts": {}, "updated": 0}
        self._load()

    # ---------------- 底层 ----------------
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("drafts"), dict):
                self.data = d
        except Exception:
            pass

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ---------------- 草稿 ----------------
    def draft(self, uid):
        """返回某学生的草稿（没有就给空模板，绝不返回 None）。"""
        with self.lock:
            d = self.data["drafts"].get(str(uid))
            if not isinstance(d, dict):
                return dict(DEFAULT_DRAFT)
            out = dict(DEFAULT_DRAFT)
            out.update({k: d.get(k) for k in DEFAULT_DRAFT if k in d})
            return out

    def save_draft(self, uid, html, css, title="", mode="", width=0):
        uid = str(uid or "")
        if not uid:
            return {"ok": False, "msg": "缺少身份"}
        html = str(html or "")
        css = str(css or "")
        if len(html) > MAX_HTML or len(css) > MAX_CSS:
            return {"ok": False, "msg": "设计稿太大了（HTML 200KB / CSS 100KB 以内）"}
        mode = str(mode or "")
        if mode not in MODES:
            mode = ""
        try:
            width = int(width or 0)
        except Exception:
            width = 0
        if width not in WIDTHS:
            width = 0
        with self.lock:
            old = self.data["drafts"].get(uid) or {}
            self.data["drafts"][uid] = {
                "html": html, "css": css,
                "title": str(title or "")[:80],
                # 显示设置没传就沿用上一次（老页面 / 只存文字时不丢设置）
                "mode": mode or str(old.get("mode") or ""),
                "width": width or int(old.get("width") or 0),
                "updated": int(time.time()),
            }
            self.data["updated"] = int(time.time())
            try:
                self._save()
            except Exception as e:
                return {"ok": False, "msg": "草稿保存失败：%s" % e}
            d = self.data["drafts"][uid]
            return {"ok": True, "updated": d["updated"],
                    "mode": d["mode"], "width": d["width"]}

    def clear_draft(self, uid):
        with self.lock:
            self.data["drafts"].pop(str(uid), None)
            self.data["updated"] = int(time.time())
            try:
                self._save()
            except Exception:
                pass
        return {"ok": True}

    def count(self):
        with self.lock:
            return len([1 for d in self.data["drafts"].values()
                        if isinstance(d, dict) and (d.get("html") or d.get("css"))])

    # ---------------- 备份 ----------------
    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data, ensure_ascii=False))

    def restore(self, data):
        if not isinstance(data, dict) or not isinstance(data.get("drafts"), dict):
            return 0
        with self.lock:
            self.data = {"drafts": {}, "updated": int(time.time())}
            for uid, d in (data.get("drafts") or {}).items():
                if not isinstance(d, dict):
                    continue
                self.data["drafts"][str(uid)] = {
                    "html": str(d.get("html") or "")[:MAX_HTML],
                    "css": str(d.get("css") or "")[:MAX_CSS],
                    "title": str(d.get("title") or "")[:80],
                    "mode": d.get("mode") if d.get("mode") in MODES else "",
                    "width": _safe_width(d.get("width")),
                    "updated": int(d.get("updated") or 0),
                }
            try:
                self._save()
            except Exception:
                pass
            return len(self.data["drafts"])
