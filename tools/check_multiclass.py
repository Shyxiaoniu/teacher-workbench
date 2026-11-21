#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多班投影自检（2.0.71）
验证：兼属班学生的投影里含主班+兼属班的班级维度行，且不含无关班。
"""
import sys
import os
ROOT = r"E:/Documents/班级工作台2.0"
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "app", "core"))
import core.projection as PJ

# 学生：主班 1，兼属班 2
row = {"id": 10, "class_id": 1, "class_ids": [2]}
ctx = PJ.student_context(row)
assert ctx["class_id"] == 1, ctx
assert set(ctx["class_ids"]) == {1, 2}, ctx

tables = {
    "classes": [{"id": 1, "name": "一班"}, {"id": 2, "name": "二班"}, {"id": 3, "name": "三班"}],
    "students": [row, {"id": 11, "class_id": 3}],
    "lessons": [{"id": 1, "class_id": 1}, {"id": 2, "class_id": 2}, {"id": 3, "class_id": 3}],
    "homeworks": [{"id": 1, "class_id": 1}, {"id": 2, "class_id": 2}, {"id": 3, "class_id": 3}],
    "attendance": [{"id": 1, "student_id": 10}, {"id": 2, "student_id": 11}],
}
out = PJ.project("student", ctx, tables, cfg={})
cls_ids = sorted(x["id"] for x in out.get("classes", []))
les_ids = sorted(x["id"] for x in out.get("lessons", []))
hw_ids = sorted(x["id"] for x in out.get("homeworks", []))
att_ids = sorted(x["id"] for x in out.get("attendance", []))

ok = True
def check(name, got, want):
    global ok
    if sorted(got) != sorted(want):
        ok = False
        print("[FAIL] %s: got %s, want %s" % (name, got, want))
    else:
        print("[OK] %s = %s" % (name, got))

check("可见班级", cls_ids, [1, 2])
check("可见课程", les_ids, [1, 2])
check("可见作业", hw_ids, [1, 2])
check("本人考勤", att_ids, [1])
check("可见学生(仅本人)", [x["id"] for x in out.get("students", [])], [10])

# 单班老数据兼容
ctx2 = PJ.student_context({"id": 20, "class_id": 5})
assert set(ctx2["class_ids"]) == {5}, ctx2
print("[OK] 单班兼容 class_ids=%s" % ctx2["class_ids"])

print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
