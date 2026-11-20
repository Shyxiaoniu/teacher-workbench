# -*- coding: utf-8 -*-
"""签到闸门口径自检（2.0.52）。

从 server.py 里 ast 抽取真实的 _class_lesson_at / _class_has_lesson_today，
按库里的真实数据算一遍「每个班 × 每天 × 每节」能不能签到，打印矩阵。

规则（2.0.52 牛哥反馈后定版）：
  · 工作日（周一~周五）：老师开着签到就允许签 —— 课表没排到这一节不算数；
  · 周末：本班当天有排课才允许签（防在家登录刷出假出勤）；
  · 本班课表完全没用过（no_table）：不拦，保持旧行为。

跑法：python tools/check_checkin.py [--db app/data/workbench.db]
退出码 0 = 与规则一致。
"""
import argparse
import ast
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV = os.path.join(ROOT, "app", "server.py")
DAY = "一二三四五六日"


def load_fns(periods, lessons):
    src = open(SRV, encoding="utf-8").read()
    tree = ast.parse(src)
    want = {"_class_lesson_at", "_class_has_lesson_today"}
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name in want:
            found.append(n)
    missing = want - {f.name for f in found}
    if missing:
        raise SystemExit("server.py 里找不到：%s" % "、".join(sorted(missing)))

    store = type("S", (), {
        "table_rows": staticmethod(
            lambda name: periods if name == "periods" else
            (lessons if name == "lessons" else []))
    })()
    ns = {"STORE": store, "time": __import__("time")}
    mod = ast.Module(body=found, type_ignores=[])
    exec(compile(mod, "<server checkin>", "exec"), ns)
    # 抽出来的是裸函数（原先是类方法），得绑一个假 self 才能当方法调
    import types
    self0 = type("FakeSelf", (), {})()
    return (types.MethodType(ns["_class_lesson_at"], self0),
            types.MethodType(ns["_class_has_lesson_today"], self0))


def allow(lesson_at, has_today, cid, dow, period):
    """照抄 _write_checkin 里的闸门判断（2.0.52）"""
    lesson = lesson_at(cid, dow, period)
    if lesson is False and dow >= 5 and not has_today(cid, dow):
        return False, lesson
    return True, lesson


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "app", "data", "workbench.db"))
    args = ap.parse_args()
    if not os.path.exists(args.db):
        raise SystemExit("找不到数据库：%s" % args.db)

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    def rows(tbl):
        out = []
        for r in con.execute("select rid,data from rows where tbl=? and deleted=0", (tbl,)):
            d = json.loads(r["data"])
            d.setdefault("id", r["rid"])
            out.append(d)
        return out

    periods, lessons, classes = rows("periods"), rows("lessons"), rows("classes")
    con.close()

    if not periods:
        raise SystemExit("periods 表是空的，先建作息")
    if not classes:
        raise SystemExit("classes 表是空的")

    seqs = sorted({int(float(p.get("seq") or p.get("id") or 0)) for p in periods
                   if float(p.get("seq") or p.get("id") or 0) > 0})
    lesson_at, has_today = load_fns(periods, lessons)

    bad = 0
    print("班 / 天   " + "".join("%5s" % ("第%d节" % s) for s in seqs))
    for c in sorted(classes, key=lambda x: int(float(x.get("id") or 0))):
        cid = c.get("id")
        print("\n== %s（id=%s）" % (c.get("name"), cid))
        for dow in range(7):
            row = []
            for s in seqs:
                ok, lesson = allow(lesson_at, has_today, cid, dow, s)
                mark = "签" if ok else "拦"
                if dow >= 5:
                    want_ok = bool(has_today(cid, dow))
                    if ok != want_ok:
                        bad += 1
                        mark += "!"
                row.append("%5s" % mark)
            tag = "" if dow < 5 else "  ← 周末"
            print("  周%s     %s%s" % (DAY[dow], "".join(row), tag))

    # 规则自检：工作日一律"签"，周末只在本班当天有课时"签"
    for c in classes:
        cid = c.get("id")
        for dow in range(7):
            for s in seqs:
                ok, _ = allow(lesson_at, has_today, cid, dow, s)
                if dow < 5 and not ok:
                    bad += 1
                    print("!! 工作日被拦：班%s 周%s 第%s节" % (cid, DAY[dow], s))
                if dow >= 5:
                    if ok and not has_today(cid, dow):
                        bad += 1
                        print("!! 周末该拦却放行：班%s 周%s 第%s节" % (cid, DAY[dow], s))

    print("\n" + ("ok：全部符合 2.0.52 规则" if not bad else "!! %d 处不符合" % bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
