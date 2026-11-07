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
    "<student_id>": {"html": "...", "css": "...", "title": "...", "updated": 1730...}
  },
  "updated": 1730...
}
"""
import json
import os
import threading
import time

# 单个学生草稿的上限：正常网页几十 KB，超过 200KB 基本是误粘贴了一整张图片
MAX_HTML = 200 * 1024
MAX_CSS = 100 * 1024

DEFAULT_DRAFT = {"html": "", "css": "", "title": "", "updated": 0}


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

    def save_draft(self, uid, html, css, title=""):
        uid = str(uid or "")
        if not uid:
            return {"ok": False, "msg": "缺少学生身份"}
        html = str(html or "")
        css = str(css or "")
        if len(html) > MAX_HTML or len(css) > MAX_CSS:
            return {"ok": False, "msg": "设计稿太大了（HTML 200KB / CSS 100KB 以内）"}
        with self.lock:
            self.data["drafts"][uid] = {
                "html": html, "css": css,
                "title": str(title or "")[:80],
                "updated": int(time.time()),
            }
            self.data["updated"] = int(time.time())
            try:
                self._save()
            except Exception as e:
                return {"ok": False, "msg": "草稿保存失败：%s" % e}
            return {"ok": True, "updated": self.data["drafts"][uid]["updated"]}

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
                    "updated": int(d.get("updated") or 0),
                }
            try:
                self._save()
            except Exception:
                pass
            return len(self.data["drafts"])
