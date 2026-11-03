#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QuickForm → 班级工作台 活动数据迁移（方案 B 期二）

搬三样东西：
  1. 活动（task）：task_id / 标题 / 说明 / 创建时间；
  2. 提交（submission）：原始 JSON（学号、姓名、班级、_ip 一并抽成列）；
  3. 附件：提交 JSON 里 attachment 指向的文件，从 QuickForm 的
     static/uploads/<活动号>/ 复制到工作台 <数据目录>/activity/<活动号>/。

幂等：同一个活动第二次跑不会重复导入（除非 --replace）。
校验：导完逐活动比对条数，不一致就明确报出来（默认 dry-run 之外的唯一「失败」）。
"""
import json
import os
import shutil
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))


def _load_data(raw):
    """QuickForm 的 submission.data 可能是 JSON，也可能是 Python 字面量。"""
    if isinstance(raw, dict):
        return raw
    s = str(raw or "")
    for parser in (json.loads,):
        try:
            v = parser(s)
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    try:
        import ast
        v = ast.literal_eval(s)
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    return {"raw_data": s[:5000]}


def _att_urls(data):
    a = (data or {}).get("attachment")
    if isinstance(a, str) and a:
        return [a]
    if isinstance(a, list):
        return [x for x in a if isinstance(x, str) and x]
    return []


def migrate(qf_db, src_uploads, store, dry_run=True, replace=False, log=print):
    """执行迁移。返回统计 dict。"""
    stat = {"tasks": 0, "skipped": 0, "submissions": 0, "files": 0,
            "missing_files": [], "errors": []}
    if not os.path.isfile(qf_db):
        raise IOError("找不到 QuickForm 数据库：%s" % qf_db)
    src = sqlite3.connect(qf_db)
    src.row_factory = sqlite3.Row
    try:
        tasks = src.execute(
            "SELECT id, task_id, title, description, created_at FROM task ORDER BY id").fetchall()
        log("发现 %d 个活动" % len(tasks))
        for t in tasks:
            key = str(t["task_id"] or "").strip()
            if not key:
                stat["skipped"] += 1
                continue
            exists = store.get_task(key)
            if exists and not replace:
                n = store.count(key)
                log("  跳过 %s（%s，已有 %d 条提交）" % (key, t["title"], n))
                stat["skipped"] += 1
                continue
            if exists and replace:
                store.clear(key)
            if not dry_run:
                store.import_task(key, t["title"], t["description"] or "",
                                  created_at=t["created_at"])
            stat["tasks"] += 1

            subs = src.execute(
                "SELECT id, data, submitted_at FROM submission WHERE task_id=? ORDER BY id",
                (t["id"],)).fetchall()
            for s in subs:
                data = _load_data(s["data"])
                sid = str(data.get("学号") or data.get("sid") or "")
                name = str(data.get("姓名") or data.get("name") or "")
                cls = str(data.get("班级") or data.get("cls") or "")
                ip = str(data.get("_ip") or data.get("ip") or "")
                # 附件：搬到工作台目录，并把 JSON 里的 URL 改成本地同构路径
                urls = _att_urls(data)
                new_urls = []
                for u in urls:
                    name_only = os.path.basename(u)
                    src_file = os.path.join(src_uploads, key, name_only)
                    if not os.path.isfile(src_file):
                        stat["missing_files"].append("%s/%s" % (key, name_only))
                        new_urls.append(u)
                        continue
                    if not dry_run:
                        dst_dir = os.path.join(store.files_dir, key)
                        os.makedirs(dst_dir, exist_ok=True)
                        shutil.copy2(src_file, os.path.join(dst_dir, name_only))
                    stat["files"] += 1
                    new_urls.append("/static/uploads/%s/%s" % (key, name_only))
                if new_urls:
                    data["attachment"] = (new_urls[0] if len(new_urls) == 1 else new_urls)
                if not dry_run:
                    store.import_submission(key, data, submitted_at=s["submitted_at"],
                                            sid=sid, name=name, cls=cls, ip=ip)
                stat["submissions"] += 1
            log("  %s %s：%d 条提交" % ("将导入" if dry_run else "已导入", key, len(subs)))
    finally:
        src.close()

    # 校验：条数必须一致
    if not dry_run:
        bad = []
        for t in tasks:
            key = str(t["task_id"] or "").strip()
            if not key:
                continue
            n_src = 0
            src = sqlite3.connect(qf_db)
            try:
                n_src = src.execute("SELECT COUNT(*) FROM submission WHERE task_id=?",
                                    (t["id"],)).fetchone()[0]
            finally:
                src.close()
            n_dst = store.count(key)
            if n_src != n_dst and not (stat["skipped"] and n_dst == 0):
                bad.append("%s 源 %d / 目标 %d" % (key, n_src, n_dst))
        if bad:
            stat["errors"].append("条数不一致：" + "；".join(bad))
    return stat


def default_paths():
    """默认路径：源码目录里的 QF 库与上传目录。"""
    qf_db = os.path.join(ROOT, "quickform", "app", "data", "quickform.db")
    uploads = os.path.join(ROOT, "quickform", "app", "static", "uploads")
    return qf_db, uploads


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="QuickForm 活动数据迁移到班级工作台")
    ap.add_argument("--qf-db", default=None, help="QuickForm 的 quickform.db 路径")
    ap.add_argument("--src-uploads", default=None, help="QuickForm 的 static/uploads 目录")
    ap.add_argument("--data-dir", default=None, help="工作台数据目录（默认 app/data）")
    ap.add_argument("--replace", action="store_true", help="活动已存在时清空后重导")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只预演）")
    args = ap.parse_args(argv)

    req = os.environ.get("WB_DATA_DIR") or os.path.join(ROOT, "app", "data")
    data_dir = args.data_dir or req
    qf_db, uploads = default_paths()
    qf_db = args.qf_db or qf_db
    uploads = args.src_uploads or uploads

    from activity import ActivityStore
    store = ActivityStore(os.path.join(data_dir, "workbench.db"),
                          os.path.join(data_dir, "activity"))
    dry = not args.apply
    print("数据源：%s" % qf_db)
    print("附件源：%s" % uploads)
    print("目标库：%s（%s）" % (store.path, "预演，不写入" if dry else "写入"))
    stat = migrate(qf_db, uploads, store, dry_run=dry, replace=args.replace)
    print("\n活动 %d 个，提交 %d 条，附件 %d 个，跳过 %d 个活动"
          % (stat["tasks"], stat["submissions"], stat["files"], stat["skipped"]))
    if stat["missing_files"]:
        print("缺文件 %d 个（记录保留，文件需手工补）：" % len(stat["missing_files"]))
        for x in stat["missing_files"][:20]:
            print("  - " + x)
    if stat["errors"]:
        print("\n!! 校验失败：\n  " + "\n  ".join(stat["errors"]))
        return 2
    print("\n完成。" + ("（预演模式，加 --apply 才会写库）" if dry else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
