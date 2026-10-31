#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
角色投影：同一份数据，按角色裁剪后下发。

为什么放在服务端
----------------
把“学生不该看手机号”写在前端，等于把钥匙放在门上——改一行 JS 就能拿到。
这里在出网前就裁掉，学生端拿到的响应里根本没有这些字段。

四条规则
--------
1. 表级：教师私有的表（待办、模板、备注、快捷链接）压根不下发；
2. 模块级：教师关掉“成绩”，成绩相关表连字段都不出现，接口直接 403；
3. 行级：学生只能拿到本班 + 本人的行；
4. 字段级：家长信息、教师备注、德育扣分原因等按开关剔除。
"""

# 学生端永远拿不到的表
STUDENT_HIDDEN_TABLES = {
    "todos", "seat_tpl", "student_notes", "quick_links", "progress_cols",
}

# 字段级剔除（即使整行可见，这些字段也不给学生）
STUDENT_DROP_FIELDS = {
    "students": {"parent_name", "phone1", "phone2", "wechat", "parent_committee_role",
                 "parent_committee_note", "contacted", "note", "email", "address"},
    "classes": {"note"},
    "homeworks": {"note"},
    "scores": {"note"},
    "morality_records": {"reason", "source", "batch_id", "operator", "teacher"},
    "attendance": {"reason"},
    "courseware_links": {"local_path", "abs_path"},
}

# 模块开关 → 该模块依赖的表（关掉模块，连表都不下发）
MODULE_TABLES = {
    "homework": {"homeworks", "homework_records"},
    "scores": {"scores", "exams", "exam_scores"},
    # 学习资料跟课件库走同一个开关（老师关掉课件，学生就都看不到）
    "courseware": {"courseware_links", "materials"},
    "morality": {"morality_items", "morality_records"},
    "duty": {"dutys", "duty_areas", "duty_positions"},
}

# 各模块对应的写接口（关掉模块 → 写也会被拒）
MODULE_SCOPES = ("homework", "scores", "courseware", "morality", "duty", "leave")


def _i(v, d=0):
    try:
        return int(float(v))
    except Exception:
        return d


def module_flags(cfg):
    m = (cfg or {}).get("modules") or {}
    return {k: bool(m.get(k, True)) for k in MODULE_SCOPES}


def _drop_set(tbl, cfg):
    drop = set(STUDENT_DROP_FIELDS.get(tbl, ()))
    vis = (cfg or {}).get("visibility") or {}
    if tbl == "students" and vis.get("contact"):
        drop -= {"parent_name", "phone1", "phone2", "wechat"}
    if tbl == "morality_records" and vis.get("morality_reason"):
        drop.discard("reason")
    if tbl == "attendance" and vis.get("leave_reason"):
        drop.discard("reason")
    return drop


def _strip(tbl, row, cfg):
    drop = _drop_set(tbl, cfg)
    return {k: v for k, v in row.items() if k not in drop}


def _as_int_list(v):
    if isinstance(v, list):
        out = []
        for x in v:
            try:
                out.append(int(float(x)))
            except Exception:
                pass
        return out
    if isinstance(v, str):
        s = v.strip().strip("[]")
        if not s:
            return []
        out = []
        for p in s.replace("，", ",").split(","):
            p = p.strip().strip('"').strip("'")
            try:
                out.append(int(float(p)))
            except Exception:
                pass
        return out
    return []


def _keep(tbl, row, cls, sid, my_seat_ids):
    """这一行是否属于该学生"""
    if tbl == "classes":
        return _i(row.get("id")) == cls
    if tbl == "students":
        return _i(row.get("id")) == sid
    if tbl == "seats":
        return _i(row.get("id")) in my_seat_ids
    if tbl in ("lessons", "homeworks", "course_progress", "dutys",
               "duty_areas", "materials"):
        return _i(row.get("class_id")) == cls
    if tbl == "courseware_links":
        # 老数据/公共课件没有 class_id：放行；写了班级就严格按班过滤
        cid = row.get("class_id")
        return cid in (None, "") or _i(cid) == cls
    if tbl in ("attendance", "homework_records", "morality_records",
               "scores", "exam_scores", "leave_requests"):
        return _i(row.get("student_id")) == sid
    if tbl == "groups":
        ids = _as_int_list(row.get("student_ids"))
        return _i(row.get("class_id")) == cls and (sid in ids)
    if tbl == "exams":
        return bool(row.get("published"))
    # periods / courses / morality_items / duty_positions：全班共用，直接放行
    return True


def _score_key(r):
    """一场考试的归组键：班级 + 日期 + 标题 + 满分。

    class_id 必须在键里——三个班同一天考同一张卷子时，
    混在一起算出来的“班均”对任何一个班都不成立。
    """
    return (str(r.get("class_id") or ""), str(r.get("date") or ""),
            str(r.get("title") or ""), str(r.get("full_score") or ""))


def _score_ident(r):
    """一行成绩的稳定标识。

    不能用 id(row)：统计源可能是全量快照里的另一个对象，
    Python 的 id() 是内存地址，跨请求/跨副本必然对不上。
    """
    rid = r.get("id")
    if rid is not None and rid != "":
        return str(rid)
    return "s%s-c%s" % (r.get("student_id"), r.get("course_id"))


def _score_stats(tables, rows=None):
    """按 (班级, 日期, 标题, 满分) 归组，算出均分、人数、位次。

    必须在这里算：学生只拿到自己那一行，客户端没有别人的分数，
    不然就得把全班成绩下发给每个人——那是最典型的隐私事故。

    rows 传入时用它当统计源（教师端/服务端手里的全量成绩），
    这样增量同步只带 1 行时，班均和位次仍然是全班口径。
    """
    src = rows if rows is not None else tables.get("scores", [])
    groups = {}
    for r in src:
        if not isinstance(r, dict) or r.get("deleted"):
            continue
        groups.setdefault(_score_key(r), []).append(r)
    stats = {}
    for key, rs in groups.items():
        vals = []
        for r in rs:
            try:
                vals.append((float(r.get("score")), _score_ident(r)))
            except Exception:
                pass
        if len(vals) < 2:
            stats[key] = {"avg": None, "n": len(vals), "rank_of": {}}
            continue
        total = sum(v for v, _ in vals)
        ordered = sorted(vals, key=lambda x: -x[0])
        rank_of = {}
        for i, (_, ident) in enumerate(ordered):
            rank_of.setdefault(ident, i + 1)      # 同分并列
        stats[key] = {"avg": round(total / len(vals), 1), "n": len(vals),
                      "rank_of": rank_of}
    return stats


def project(scope, ctx, tables, cfg=None, stats_scores=None):
    """按角色裁剪。ctx: {role, class_id, student_id}"""
    if scope != "student":
        return tables

    flags = module_flags(cfg)
    blocked = set()
    for mod, tls in MODULE_TABLES.items():
        if not flags.get(mod, True):
            blocked |= tls

    cls = _i(ctx.get("class_id"))
    sid = _i(ctx.get("student_id"))

    # 先定位“我的座位”：座位表只下发本人那一行，不下发整张座位图
    my_seat_ids = set()
    for r in tables.get("seats", []):
        if sid and _i(r.get("student_id")) == sid:
            my_seat_ids.add(_i(r.get("id")))

    score_stats = (_score_stats(tables, stats_scores)
                   if ("scores" in tables and not blocked.intersection(
                       MODULE_TABLES.get("scores", set()))) else {})

    out = {}
    for tbl, rows in tables.items():
        if tbl in STUDENT_HIDDEN_TABLES or tbl in blocked:
            continue
        picked = []
        for row in rows:
            if not _keep(tbl, row, cls, sid, my_seat_ids):
                continue
            if row.get("deleted"):
                # 墓碑只带 id，避免把别的班的数据带出去
                picked.append({"id": row.get("id"), "deleted": 1, "_rev": row.get("_rev")})
            else:
                item = _strip(tbl, row, cfg)
                if tbl == "scores":
                    st = score_stats.get(_score_key(row)) or {}
                    item["_avg"] = st.get("avg")
                    item["_n"] = st.get("n")
                    rk = (st.get("rank_of") or {}).get(_score_ident(row))
                    if rk:
                        item["_rank"] = rk
                picked.append(item)
        if picked:
            out[tbl] = picked
    return out


def student_context(student_row):
    if not student_row:
        return {"role": "student", "class_id": 0, "student_id": 0}
    return {
        "role": "student",
        "class_id": _i(student_row.get("class_id")),
        "student_id": _i(student_row.get("id")),
    }
