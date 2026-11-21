#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账号隔离自检（2.0.71）
1) 工作区路径按账号物理隔离；
2) 测验 SSE 事件按账号分桶：A 账号 bump 不改变 B 账号读到的 epoch/event。
"""
import sys
import os

ROOT = r"E:/Documents/班级工作台2.0"
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "app", "core"))

import core.accounts as ACC           # noqa: E402
import core.quiz_api as QAPI          # noqa: E402

ok = True

# ---- 1) 工作区路径隔离 ----
ws = ACC.Workspace(os.path.join(ROOT, "app", "data"))
p_def = ws.paths_for("default")
p_t2 = ws.paths_for("t2")
assert p_def["db"] != p_t2["db"], "default 与 t2 的 db 路径相同！"
assert os.sep + "accounts" + os.sep in p_t2["db"], "t2 未落在 accounts 子目录"
print("[OK] 工作区路径隔离: default=%s" % p_def["db"])
print("                     t2     =%s" % p_t2["db"])

# ---- 2) SSE 事件按账号分桶 ----
QAPI.set_account_provider(ACC.current_account)
ACC.set_account("default")
e0_def = QAPI.EPOCH[0]
ACC.set_account("t2")
e0_t2 = QAPI.EPOCH[0]

ACC.set_account("default")
QAPI.bump({"kind": "submit", "window": "窗口A", "student_no": "2026010101"})
e1_def = QAPI.EPOCH[0]
ev_def = QAPI.last_event()

ACC.set_account("t2")
e1_t2 = QAPI.EPOCH[0]
ev_t2 = QAPI.last_event()

ACC.clear_account()

if e1_def != e0_def + 1:
    ok = False
    print("[FAIL] default bump 后 epoch 未 +1: %d -> %d" % (e0_def, e1_def))
if e1_t2 != e0_t2:
    ok = False
    print("[FAIL] default 的 bump 影响了 t2 的 epoch: %d -> %d" % (e0_t2, e1_t2))
if "窗口A" in str(ev_t2):
    ok = False
    print("[FAIL] t2 读到了 default 的事件内容: %s" % ev_t2)
if "窗口A" not in str(ev_def):
    ok = False
    print("[FAIL] default 读不到自己的事件: %s" % ev_def)

if ok:
    print("[OK] SSE 事件账号隔离: default epoch %d->%d, t2 epoch %d->%d（未串）"
          % (e0_def, e1_def, e0_t2, e1_t2))
    print("       default event=%s" % ev_def["data"])
    print("       t2      event=%s" % ev_t2["data"])
else:
    print("[RESULT] FAILED")

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
