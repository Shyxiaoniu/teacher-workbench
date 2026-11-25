#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 像素画草稿（pixel_store.py）

设计要点
--------
  · 与 design_store.py 同一套路：独立 JSON（DATA_DIR/pixel.json），不进 29 表
    同步体系。像素画是过程文件，改一格就存一次，独立通道最省事。
  · 原子落盘（临时文件 + os.replace），一把锁。
  · 只存草稿，不存提交记录：提交走 ActivityStore（大屏 / 作品墙 / 归档都读
    那一份），这里再存一遍就出现两套口径。
  · 草稿按 student_id 存，学生换设备登录同一个人就能接着画。

数据形状
--------
{
  "drafts": {
    "<student_id>": {
      "w": 32, "h": 32,
      "px": "....0a..",      # 长度 w*h，一格一个字符；'.'=空，其余是调色板下标
      "pal": ["#ffffff", "#b45b33", ...],
      "title": "我的像素画",
      "grid": 1,
      "updated": 1730...
    }
  },
  "updated": 1730...
}

为什么用单字符而不是 [[r,g,b],...]：32×32 只有 1024 个字符（约 1KB），
64×64 也才 4096；逐格存对象会膨胀几十倍，来回传输还慢。
索引取 base36（0-9a-v）刚好够 32 色。
"""
import json
import os
import re
import threading
import time

# 画布档位：16 适合入门、32 是主力、64/128/256/512 给细节控（2.1.7 扩到 512）。
# 不允许任意尺寸 —— 前端按档位给按钮，服务端也按档位校验，两边一套口径。
SIZES = (16, 24, 32, 48, 64, 128, 256, 512)

# 调色板最多 32 色：索引字符正好用完 base36 的 0-9a-v
MAX_PAL = 32
PAL_CHARS = "0123456789abcdefghijklmnopqrstuv"
EMPTY = "."

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_PAL = ["#2f2a26", "#ffffff", "#b45b33", "#e0a458", "#7d9a6a",
               "#5f8fa8", "#8d6e9e", "#d46a6a"]


def _safe_size(v):
    try:
        n = int(v or 0)
    except Exception:
        return 0
    return n if n in SIZES else 0


def _norm_pal(pal):
    out = []
    for c in (pal or []):
        c = str(c or "").strip().lower()
        if _HEX.match(c) and c not in out:
            out.append(c)
        if len(out) >= MAX_PAL:
            break
    return out


def _norm_px(px, w, h):
    """把像素串规整到正好 w*h 个合法字符。

    不合法字符（超范围下标、乱码）一律按空处理 —— 宁可少画几格，
    也不能让一个脏字符把整张画读崩。
    """
    n = w * h
    s = str(px or "")
    if len(s) > n:
        s = s[:n]
    elif len(s) < n:
        s += EMPTY * (n - len(s))
    return "".join(c if (c == EMPTY or c in PAL_CHARS) else EMPTY for c in s)


DEFAULT_DRAFT = {"w": 0, "h": 0, "px": "", "pal": [], "title": "", "grid": 1, "updated": 0}


class PixelStore(object):
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {"drafts": {}, "updated": 0}
        self._load()

    # ---------------- 底层 ----------------
    def _load(self):
        self._locked = False
        try:
            import enc_ctx as ENC
            d = ENC.read_json_file(self.path, domain="json")
            if isinstance(d, dict) and isinstance(d.get("drafts"), dict):
                self.data = d
        except Exception:
            try:
                self._locked = bool(ENC.is_encrypted_path(self.path))
            except Exception:
                self._locked = False

    def _save(self):
        import enc_ctx as ENC
        if getattr(self, "_locked", False):
            if not ENC.get_fek():
                raise ENC.NoFekError("数据密钥不可用（请重新登录后再保存）")
            self._load()
            self._locked = False
        ENC.write_json_file(self.path, self.data, domain="json")

    # ---------------- 草稿 ----------------
    def draft(self, uid):
        """返回某学生的草稿（没有就给空模板，绝不返回 None）。

        空模板 w/h 为 0，前端看到 0 就用默认 32×32 开一张新画布。
        """
        with self.lock:
            d = self.data["drafts"].get(str(uid))
            if not isinstance(d, dict):
                return dict(DEFAULT_DRAFT)
            out = dict(DEFAULT_DRAFT)
            out.update({k: d.get(k) for k in DEFAULT_DRAFT if k in d})
            out["pal"] = list(out.get("pal") or [])
            return out

    def save_draft(self, uid, w, h, px, pal=None, title="", grid=1):
        uid = str(uid or "")
        if not uid:
            return {"ok": False, "msg": "缺少身份"}
        w = _safe_size(w) or 32
        h = _safe_size(h) or w
        p = _norm_pal(pal) or list(DEFAULT_PAL)
        s = _norm_px(px, w, h)
        with self.lock:
            self.data["drafts"][uid] = {
                "w": w, "h": h, "px": s, "pal": p,
                "title": str(title or "")[:60],
                "grid": 1 if grid else 0,
                "updated": int(time.time()),
            }
            self.data["updated"] = int(time.time())
            try:
                self._save()
            except Exception as e:
                return {"ok": False, "msg": "草稿保存失败：%s" % e}
            d = self.data["drafts"][uid]
            return {"ok": True, "updated": d["updated"], "w": w, "h": h}

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
                        if isinstance(d, dict) and d.get("px")])

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
                w = _safe_size(d.get("w")) or 32
                h = _safe_size(d.get("h")) or w
                self.data["drafts"][str(uid)] = {
                    "w": w, "h": h,
                    "px": _norm_px(d.get("px"), w, h),
                    "pal": _norm_pal(d.get("pal")) or list(DEFAULT_PAL),
                    "title": str(d.get("title") or "")[:60],
                    "grid": 1 if d.get("grid") else 0,
                    "updated": int(d.get("updated") or 0),
                }
            try:
                self._save()
            except Exception:
                pass
            return len(self.data["drafts"])
