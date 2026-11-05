#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学生端「取不到数据」只读诊断。

为什么需要它
------------
学生端和教师端走的是同一个数据源，但出口完全不同：

    教师端  /api/v2/sync?scope=teacher  → STORE.snapshot() 全量，29 张表（空表也下发）
    学生端  /api/v2/sync?scope=student  → projection.project() 按「本班 + 本人」裁剪，
                                          只下发有内容的表（空表整个 key 不出现）

所以「教师端有、学生端没有」绝大多数不是接口坏了，而是学生这一侧的
**归属/开关**不满足，导致行级过滤把数据全滤掉了。最常见的三种：

  1. 学生档案的 class_id 为空 / 为 0 / 指向已删除的班级 → classes 一行都没有，
     lessons / materials / homeworks / scores / seats 跟着全没；
  2. 学生会话绑到了错误的工作区（多教师账号）→ 找不到学生档案，sync 直接 403；
  3. 老师关掉了模块（作业 / 成绩 / 课件 / 德育 / 值日）→ 整张表不下发。

用法
----
    python tools/diag_student.py
        只查本地数据（默认 <仓库>/app/data，或环境变量 WB_DATA_DIR）。

    python tools/diag_student.py --data D:\\workbench-data
        指定数据目录（NAS / Docker 挂载点）。

    python tools/diag_student.py --base http://192.168.1.9:8080 \\
        --user teacher --pass ****** --student 2026001
        额外走一遍接口：教师视角 vs 学生视角各拿到哪些表，并逐表对比。

只读：数据库用 mode=ro 打开，接口只用 GET，不会改任何数据。
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


# ---------------------------------------------------------------------------
# 数据体检
# ---------------------------------------------------------------------------
def _intish(v):
    try:
        return int(float(str(v).strip()))
    except Exception:
        return None


def _rows(db, tbl):
    out = []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db.replace("\\", "/"), uri=True,
                              timeout=8)
        try:
            for (raw,) in con.execute(
                    "SELECT data FROM rows WHERE tbl=? AND deleted=0", (tbl,)):
                try:
                    d = json.loads(raw)
                    if isinstance(d, dict) and not d.get("deleted"):
                        out.append(d)
                except Exception:
                    pass
        finally:
            con.close()
    except Exception as e:
        print("    [读库失败] %s" % e)
    return out


def audit_db(db, label):
    print("\n=== %s ===" % label)
    print("  库：%s" % db)
    classes = _rows(db, "classes")
    students = _rows(db, "students")
    ids = {_intish(c.get("id")) for c in classes}
    ids.discard(None)
    print("  班级 %d 个：%s" % (
        len(classes),
        "、".join("%s(%s)" % (c.get("id"), c.get("name") or "") for c in classes[:8])
        or "（无）"))
    print("  学生 %d 人" % len(students))

    bad, orphan = [], []
    for s in students:
        n = _intish(s.get("class_id"))
        if n is None or n <= 0:
            bad.append(s)
        elif n not in ids:
            orphan.append(s)

    print("  class_id 正常：%d 人" % (len(students) - len(bad) - len(orphan)))
    if bad:
        print("  ⚠ class_id 缺失 / 为 0 / 非数字：%d 人" % len(bad))
        for s in bad[:10]:
            print("     ! id=%s 姓名=%s class_id=%r 学号=%s"
                  % (s.get("id"), s.get("name"), s.get("class_id"), s.get("student_no")))
        print("     影响：学生端 classes 一行都拿不到，课表/资料/作业/座位跟着全空，")
        print("           座位表接口直接返回「没有对应的班级」。")
    if orphan:
        print("  ⚠ class_id 指向不存在的班级：%d 人" % len(orphan))
        for s in orphan[:10]:
            print("     ! id=%s 姓名=%s class_id=%r 学号=%s"
                  % (s.get("id"), s.get("name"), s.get("class_id"), s.get("student_no")))
        print("     影响：同「班级缺失」，且教师端按班级筛选也看不到这些人。")
    if not bad and not orphan:
        print("  ✓ 全部学生都能对上班级 —— 投影不会因为归属问题把他们过滤掉")
    return len(bad) + len(orphan)


def data_dir(arg):
    if arg:
        return os.path.abspath(arg)
    env = os.environ.get("WB_DATA_DIR")
    if env:
        return os.path.abspath(env)
    return os.path.join(ROOT, "app", "data")


# ---------------------------------------------------------------------------
# 接口对比（可选）
# ---------------------------------------------------------------------------
class Api(object):
    def __init__(self, base):
        import http.cookiejar
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()

    def get(self, path):
        req = urllib.request.Request(self.base + path)
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPCookieProcessor(self.jar))
        try:
            with op.open(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode() or "{}")
            except Exception:
                return {"ok": False, "msg": "HTTP %s" % e.code}
        except Exception as e:
            return {"ok": False, "msg": str(e)}

    def post(self, path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPCookieProcessor(self.jar))
        try:
            with op.open(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode() or "{}")
            except Exception:
                return {"ok": False, "msg": "HTTP %s" % e.code}
        except Exception as e:
            return {"ok": False, "msg": str(e)}


def _cnt(j):
    return {k: len(v) for k, v in sorted((j.get("tables") or {}).items())}


def compare(base, user, pwd, stu_no):
    print("\n=== 接口对比（%s） ===" % base)
    tea = Api(base)
    r = tea.post("/api/v2/auth/teacher",
                 {"username": user, "password": pwd, "role": "teacher"})
    if not r.get("ok"):
        print("  教师登录失败：%s" % (r.get("msg") or r))
        return 2
    t = tea.get("/api/v2/sync?since=0&scope=teacher")
    tc = _cnt(t)
    print("  教师视角：%d 张表" % len(tc))
    print("    " + json.dumps(tc, ensure_ascii=False))

    # 找到这名学生所属的班级 id
    students = (t.get("tables") or {}).get("students") or []
    hit = None
    for s in students:
        if str(s.get("student_no") or "").strip() == str(stu_no).strip():
            hit = s
            break
    if not hit:
        print("  ⚠ 教师视角的 students 里没有学号 %s" % stu_no)
        print("     → 先确认学号，或这个学生是否在别的教师账号工作区")
        return 3
    print("  学生：%s（id=%s class_id=%r）"
          % (hit.get("name"), hit.get("id"), hit.get("class_id")))

    stu = Api(base)
    r = stu.post("/api/v2/auth/student",
                 {"class_id": hit.get("class_id"), "student_id": hit.get("id"),
                  "password": "123456"})
    print("  学生登录：ok=%s %s" % (r.get("ok"), r.get("msg") or ""))
    if not r.get("ok"):
        print("     → 初始密码可能已被改过；用教师端「重置密码」设一个再试")
        return 4

    s = stu.get("/api/v2/sync?since=0&scope=student")
    sc = _cnt(s)
    print("  学生视角：ok=%s，%d 张表" % (s.get("ok"), len(sc)))
    print("    " + json.dumps(sc, ensure_ascii=False))

    lost = [k for k in tc if tc[k] > 0 and sc.get(k, 0) == 0]
    print("\n  教师有、学生没有的表：%s" % (json.dumps(lost, ensure_ascii=False) if lost else "（无）"))
    print("  行数不一致：%s" % json.dumps(
        {k: "%d→%d" % (tc[k], sc.get(k, 0)) for k in tc if tc[k] != sc.get(k, 0)},
        ensure_ascii=False))

    for path in ("/api/v2/student/me", "/api/v2/student/seatmap",
                 "/api/v2/student/now"):
        j = stu.get(path)
        print("  %-28s ok=%s %s" % (path, j.get("ok"),
                                    (j.get("msg") or "")[:60]))
    return 0


def main():
    ap = argparse.ArgumentParser(description="学生端取不到数据的只读诊断")
    ap.add_argument("--data", default="", help="数据目录（默认 WB_DATA_DIR 或 app/data）")
    ap.add_argument("--base", default="", help="服务地址，例如 http://127.0.0.1:20020")
    ap.add_argument("--user", default="teacher", help="教师账号")
    ap.add_argument("--pass", dest="pwd", default="", help="教师密码")
    ap.add_argument("--student", default="", help="要对比的学生学号")
    a = ap.parse_args()

    d = data_dir(a.data)
    print("数据目录：%s" % d)
    dbs = []
    main_db = os.path.join(d, "workbench.db")
    if os.path.isfile(main_db):
        dbs.append((main_db, "default（主工作区）"))
    for p in sorted(glob.glob(os.path.join(d, "accounts", "*", "workbench.db"))):
        dbs.append((p, "账号 %s" % os.path.basename(os.path.dirname(p))))
    if not dbs:
        print("没找到 workbench.db —— 确认 --data 指向的是数据根目录")
        return 1

    total = sum(audit_db(db, label) for db, label in dbs)
    print("\n== 数据体检：%d 名学生归属异常 ==" % total)

    if a.base and a.pwd and a.student:
        return compare(a.base, a.user, a.pwd, a.student) or (1 if total else 0)
    if a.base and not a.pwd:
        print("\n（要对比接口，请再带上 --pass 和 --student <学号>）")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
