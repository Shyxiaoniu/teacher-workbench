#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号级配置代理（2026-11-25）。

背景：以前 config.json 是「全服务一份」，一个老师改模块开关 / 签到设置 /
打字配置，另一个老师跟着变。账号的业务数据早就按工作区隔离了，只有配置
这一层还串。

做法：把全局 CFG 换成本模块的对象。它按 `accounts.current_account()`（线程
局部，每个请求开头由 `_bind_account()` 设置）把读写路由到对应账号的配置：

  · 全局键（端口 / 绑定 / 更新源 / 备份 / 同步 / 教师口令镜像）→ 原 config.json
  · 账号键（学校名 / 模块开关 / 可见性 / 学生口令 / 签到 / 打字 / 设计 /
    像素 / 作品角 / 座位图 / 活动 / 班号）→ <账号工作区>/config.json
    default 账号沿用原 config.json（老安装零迁移，路径不变）。

业务代码里的 `CFG["x"]` / `CFG.get(...)` / `CFG.setdefault(...)` 全部不用改；
保存统一走 `CFG.save()`（server.py 里的 cfg_save() 已经转过来）。

存量账号首次启动时把当时 default 的账号级配置快照成自己的初值，所以老账号
不会突然丢掉原有设置；之后各改各的。新账号用 ACCOUNT_DEFAULTS 出厂默认，
不继承任何老师的自定义配置。
"""
import os
import copy
import json
import threading

import auth as A
import accounts as ACC

# 部署级设置：跟「机器」走，不跟老师走。故意用白名单，任何没列进来的键
# 一律视为账号级，将来新增功能配置天然就是账号隔离的。
GLOBAL_KEYS = frozenset({
    "port", "port_migrated", "bind",
    "teacher",          # 旧单账号镜像；权威已在 accounts.json
    "update",           # 更新源 / 自动检查
    "backup",           # 自动备份
    "sync",             # 增量同步分页等
    "sse",              # SSE 连接池上限
    "_flags_migrated_v2", "_flags_migrated_v3",
})

# 新账号的出厂默认（不复制任何现有老师的设置）。
ACCOUNT_DEFAULTS = {
    "school_name": "",
    "student": {"session_hours": 8, "password_mode": "uniform",
                "initial_password": "123456", "force_change": False,
                "require_name": True},
    "modules": {"homework": True, "scores": True, "courseware": True,
                "morality": True, "duty": True},
    "visibility": {"rank": False, "morality_reason": False,
                   "contact": False, "leave_reason": False},
    "activity": {"submit_hours": 12},
    "typing": {"enabled": False, "grades": []},
    "checkin": {"enabled": True, "manual_time": ""},
    "design": {"enabled": False, "allow_add": True, "allow_submit": True,
               "course": "网页设计", "window": "网页设计作品", "intro": "",
               "max_submit": 1},
    "pixel": {"enabled": False, "allow_add": True, "allow_submit": True,
              "course": "像素画", "window": "像素画作品", "intro": "",
              "size": 32, "max_submit": 1},
    "studio": {"designers": {}, "free_student": 1, "free_teacher": 2},
    "seatmap": {},
    "class_code": "",
}


def _deep_update(dst, src):
    """把 src 合进 dst（dict 递归，其余覆盖），返回 dst。"""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


class AccountConfig(object):
    """按当前账号路由的配置代理；用法与普通 dict 基本一致。"""

    def __init__(self, config_path, workspace):
        self.config_path = os.path.abspath(config_path)
        self.ws = workspace
        self._lock = threading.RLock()
        # 原文件含全局键 + default 的账号键；按用途拆开。
        base = A.load_config(self.config_path)
        self._global = {k: copy.deepcopy(v) for k, v in base.items()
                        if k in GLOBAL_KEYS}
        seed = {k: copy.deepcopy(v) for k, v in base.items()
                if k not in GLOBAL_KEYS}
        # 兜底：老配置可能缺某些账号键，补齐结构性默认（不覆盖已有值）。
        seed = _deep_update(copy.deepcopy(ACCOUNT_DEFAULTS), seed)
        self._accounts = {ACC.DEFAULT_TEACHER_ID: seed}
        self._loaded = {ACC.DEFAULT_TEACHER_ID}

    # ---- 路径 -----------------------------------------------------------
    def path_of(self, acc):
        acc = str(acc or ACC.DEFAULT_TEACHER_ID)
        # 管理员会话（account="admin"）没有自己的教学配置，沿用 default 视角。
        if acc == "admin":
            acc = ACC.DEFAULT_TEACHER_ID
        if acc == ACC.DEFAULT_TEACHER_ID:
            return self.config_path
        return os.path.join(self.ws.dir_of(acc), "config.json")

    def _read_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                v = json.load(f)
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}

    # ---- 账号配置实例 ----------------------------------------------------
    def _cfg(self, acc):
        acc = str(acc or ACC.DEFAULT_TEACHER_ID)
        if acc == "admin":
            acc = ACC.DEFAULT_TEACHER_ID
        with self._lock:
            if acc not in self._loaded:
                seed = copy.deepcopy(ACCOUNT_DEFAULTS)
                _deep_update(seed, self._read_file(self.path_of(acc)))
                self._accounts[acc] = seed
                self._loaded.add(acc)
            return self._accounts[acc]

    def for_account(self, acc):
        """显式取某账号的账号级配置（后台任务 / 迁移用）。"""
        return self._cfg(acc)

    def migrate_existing(self, teacher_ids):
        """启动时把存量账号的共享配置快照成各自初值；已有文件不动。"""
        for tid in (teacher_ids or []):
            tid = str(tid or "")
            if not tid or tid == ACC.DEFAULT_TEACHER_ID:
                continue
            p = self.path_of(tid)
            if os.path.exists(p):
                continue
            try:
                A.save_config(p, copy.deepcopy(self._cfg(ACC.DEFAULT_TEACHER_ID)))
            except Exception:
                pass

    # ---- dict 协议：按 key 路由 -------------------------------------------
    def _bucket(self, key):
        if key in GLOBAL_KEYS or key in self._global:
            return self._global
        return self._cfg(ACC.current_account())

    def __getitem__(self, key):
        return self._bucket(key)[key]

    def __setitem__(self, key, value):
        self._bucket(key)[key] = value

    def __delitem__(self, key):
        del self._bucket(key)[key]

    def __contains__(self, key):
        return key in self._global or key in self._cfg(ACC.current_account())

    def get(self, key, default=None):
        try:
            return self._bucket(key).get(key, default)
        except (TypeError, KeyError):
            return default

    def setdefault(self, key, default=None):
        b = self._bucket(key)
        if key not in b:
            b[key] = copy.deepcopy(default) if isinstance(default, (dict, list)) else default
        return b[key]

    def pop(self, key, *args):
        return self._bucket(key).pop(key, *args)

    def update(self, other=None, **kw):
        src = dict(other or {})
        src.update(kw)
        for k, v in src.items():
            self[k] = v

    def clear(self):
        raise TypeError("账号级配置不支持整体清空")

    def keys(self):
        return self.snapshot().keys()

    def values(self):
        return self.snapshot().values()

    def items(self):
        return self.snapshot().items()

    def __iter__(self):
        return iter(self.snapshot())

    def __len__(self):
        return len(self.snapshot())

    def snapshot(self):
        """当前账号视角的完整配置副本（导出 / JSON 序列化用）。"""
        out = copy.deepcopy(self._global)
        _deep_update(out, self._cfg(ACC.current_account()))
        return out

    # ---- 落盘 ------------------------------------------------------------
    def save(self, acc=None):
        """保存全局段 + 指定账号（默认当前账号）的账号段。

        每次都顺手写一次全局文件：里面只放全局键和 default 的账号键，
        其他账号的文件在各自工作区，互不覆盖。
        """
        with self._lock:
            acc = str(acc or ACC.current_account())
            if acc == "admin":
                acc = ACC.DEFAULT_TEACHER_ID
            glob = copy.deepcopy(self._global)
            glob.update(copy.deepcopy(self._cfg(ACC.DEFAULT_TEACHER_ID)))
            A.save_config(self.config_path, glob)
            if acc != ACC.DEFAULT_TEACHER_ID:
                A.save_config(self.path_of(acc),
                              copy.deepcopy(self._cfg(acc)))

    def save_all(self):
        with self._lock:
            for acc in list(self._loaded):
                self.save(acc)
