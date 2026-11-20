# -*- coding: utf-8 -*-
"""校验「现在第几节」三处口径是否一致（2.0.49）。

背景：2026-11-20 牛哥反馈「教师端座位表上面的时间和课表设置的时间不一致，
学生端签到也跟着出问题」。根因是服务端遍历全表 periods（七天各一套，
且周一与其它天作息不同），按 seq 排序后先命中的是周一那行 —— 判出来的
节次跟页面上（前端 periodList 只看今天）显示的不是同一节。

本脚本做两件事：
1. 用 ast 抽取 server.py 里真实的 _periods_today()，不复制逻辑；
2. 扫描 7 天 × 1440 分钟，对比
     · 旧服务端（全表遍历）   ← 2.0.49 之前的写法
     · 新服务端（_periods_today）
     · 前端（periodList + curPeriodSeq，逐行照抄 app/index.html）
   三者判出的节次，把不一致的分钟区间打印出来。

用法：python tools/check_periods.py
退出码 0 = 新服务端与前端完全一致。
"""
import ast
import json
import os
import sqlite3
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV = os.path.join(ROOT, "app", "server.py")
DB = os.path.join(ROOT, "app", "data", "workbench.db")


# ---------- 1. 从 server.py 抽取真实的 _periods_today ----------
_CODE = None
_HOLDER = {"rows": [], "lt": None}
_cache = {}


def _extract():
    """只 ast.parse 一次，之后复用同一个 code 对象。"""
    global _CODE
    if _CODE is not None:
        return _CODE
    src = open(SRV, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = None
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "_periods_today":
            fn = n
            break
    if fn is None:
        raise SystemExit("server.py 里找不到 _periods_today()")
    mod = ast.Module(body=[fn], type_ignores=[])
    _CODE = compile(mod, "<server._periods_today>", "exec")
    return _CODE


def _fn():
    """造一个会把 STORE / time 换成假的 _periods_today。"""
    fake_time = types.SimpleNamespace(localtime=lambda *a: _HOLDER["lt"][0])
    fake_store = types.SimpleNamespace(
        table_rows=lambda name: _HOLDER["rows"][0] if name == "periods" else [])
    ns = {"STORE": fake_store, "time": fake_time}
    exec(_extract(), ns)
    return ns["_periods_today"]


def set_rows(rows):
    _HOLDER["rows"] = [rows]
    _cache.clear()


def load_periods_today(rows, dow):
    if _HOLDER["rows"][:1] != [rows]:
        set_rows(rows)
    if dow not in _cache:
        _HOLDER["lt"] = [time_localtime(dow, 0)]
        _cache[dow] = _fn()()
    return _cache[dow]


def time_localtime(dow, minutes):
    lt = types.SimpleNamespace()
    lt.tm_wday = dow
    lt.tm_hour = minutes // 60
    lt.tm_min = minutes % 60
    lt.tm_year, lt.tm_mon, lt.tm_mday = 2026, 11, 20
    return lt


# ---------- 2. 三种「现在第几节」 ----------
def mins(v):
    try:
        a = str(v or "").split(":")
        return int(a[0]) * 60 + int(a[1] or 0)
    except Exception:
        return -1


def seq_of(r):
    try:
        return float(r.get("seq") if r.get("seq") not in (None, "") else r.get("id"))
    except Exception:
        return 999.0


def pick(rows, cur):
    """_current_period / curPeriodSeq 共用的选取规则。"""
    if not rows:
        return None
    rows = sorted(rows, key=seq_of)
    for r in rows:
        s, e = mins(r.get("start_time")), mins(r.get("end_time"))
        if s >= 0 and e > s and s <= cur <= e:
            return r
    past = [r for r in rows if mins(r.get("start_time")) >= 0
            and mins(r.get("start_time")) <= cur]
    return past[-1] if past else rows[0]


def seqnum(r):
    if r is None:
        return None
    return int(float(r.get("seq") if r.get("seq") not in (None, "") else r.get("id")))


def old_server(rows, dow, cur):
    """2.0.49 之前：遍历全表，不分星期。"""
    return seqnum(pick([r for r in rows if not r.get("deleted")], cur))


def new_server(rows, dow, cur):
    return seqnum(pick(load_periods_today(rows, dow), cur))


def fe_period_list(rows, d0):
    """逐行照抄 app/index.html 的 periodList()。"""
    allr = [r for r in rows if not r.get("deleted")]
    base = [p for p in allr if int(p.get("day_of_week") or 0) == 0]
    rs = [p for p in allr if int(p.get("day_of_week") or 0) == d0]
    if not rs:
        rs = base
    seen, out = set(), []
    for p in sorted(rs, key=lambda x: (float(x.get("seq") or 0),
                                       float(x.get("id") or 0))):
        s = float(p.get("seq") or p.get("id") or 0)
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(p)
    if d0 != 0 and base:
        for p in sorted(base, key=lambda x: float(x.get("seq") or 0)):
            s = float(p.get("seq") or p.get("id") or 0)
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(dict(p, day_of_week=d0))
        out.sort(key=lambda x: float(x.get("seq") or 0))
    return out


def fe_client(rows, dow, cur):
    return seqnum(pick(fe_period_list(rows, dow), cur))


# ---------- 3. 扫描 ----------
def main():
    if not os.path.exists(DB):
        raise SystemExit("找不到数据库：%s" % DB)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    # 与 STORE.table_rows(tbl) 完全一致：只取 deleted=0，且 id 缺失时用 rid 兜底
    rows = []
    for r in con.execute(
            "select rid, data from rows where tbl='periods' and deleted=0"):
        try:
            d = json.loads(r["data"])
        except Exception:
            d = {}
        if not isinstance(d, dict):
            d = {}
        d = dict(d)
        d.setdefault("id", r["rid"])
        rows.append(d)
    con.close()

    live = [r for r in rows if not r.get("deleted")]
    print("periods 表：共 %d 行，有效 %d 行" % (len(rows), len(live)))
    from collections import Counter
    print("各天行数：", dict(sorted(Counter(
        int(r.get("day_of_week") or 0) for r in live).items())))
    print()

    names = "一二三四五六日"
    bad_srv = bad_fe = 0
    for dow in range(7):
        today = load_periods_today(rows, dow)
        print("周%s：今天这套 %d 节（%s ~ %s）" % (
            names[dow], len(today),
            today[0].get("start_time") if today else "-",
            today[-1].get("end_time") if today else "-"))
        runs = []
        prev = None
        for cur in range(0, 24 * 60):
            o, n, f = old_server(rows, dow, cur), new_server(rows, dow, cur), \
                fe_client(rows, dow, cur)
            tag = (o, n, f)
            if prev is None or tag[1:] != prev[1:]:
                runs.append([cur, cur, tag])
                prev = tag
            else:
                runs[-1][1] = cur
        for a, b, (o, n, f) in runs:
            mark = ""
            if n != f:
                mark = "  <== 新服务端与前端不一致"
                bad_fe += 1
            elif o != n:
                mark = "  (旧服务端判错)"
                bad_srv += 1
            print("   %02d:%02d-%02d:%02d  旧=%-3s 新=%-3s 前端=%-3s%s" % (
                a // 60, a % 60, b // 60, b % 60, o, n, f, mark))
        print()

    print("=" * 60)
    print("新服务端 vs 前端不一致的区间：%d 段" % bad_fe)
    print("旧服务端判错的区间（新代码已修）：%d 段" % bad_srv)
    return 0 if bad_fe == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
