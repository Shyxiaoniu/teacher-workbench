#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 测验/检测数据层（quiz_store.py，2.0.38）

为什么独立一份 quiz.json
------------------------
试卷里带**正确答案与分值**，提交里带**分数与逐题判定**。这两类数据绝不能进
STORE 的 29 表同步体系——/api/v2/sync?scope=student 会把表按班级下发给学生端，
学生打开开发者工具就能提前看到答案、改掉自己的分数。所以：

  · 教师端、学生端一律走 /api/v2/quiz/* 鉴权接口；
  · 学生端拿到的试卷是**服务端裁剪过的**（剥掉 answer / accept / score 判定细节）；
  · 提交由服务端判分，客户端上报的 score 一律不认；
  · 全量备份单独带一个 quiz 段（与 typing / activity 同款），换设备不丢。

三处入口、一份数据（统一口径，勿各算各的）
------------------------------------------
  1. 大屏（上课页）= 实时收件看板：只看「谁交了 / 交到哪个窗口」，
     不显示答案、不显示分数、不算正确率（延续 2.0.32 定版）。
  2. 测验/检测页 = 唯一的分析入口：逐题正确率、分数、按班提交与成绩。
  3. 成绩页 = 汇总分析：把某次测验某班成绩导入成一场考试，之后走既有成绩分析。
  三者都读这里的 submissions，**同一名学生同一次发布只算最后一次提交**
  （与学习单「按人去重留最新」一致），不重复计数、不另算一套分。

落盘：临时文件 + os.replace 原子替换，一把 RLock 保证多线程安全。
时间：一律服务端本地时间（server.py 的 now_str 是唯一时间源）。
"""

import json
import os
import time
import threading

# 七种题型。essay / image 需要人工批阅，不参与自动判分。
TYPES = ("single", "multiple", "blank", "judge", "match", "essay", "image")
AUTO_TYPES = ("single", "multiple", "blank", "judge", "match")
TYPE_NAMES = {
    "single": "单选题", "multiple": "多选题", "blank": "填空题",
    "judge": "判断题", "match": "连线题", "essay": "简答题", "image": "图片上传",
}

_JUDGE_TRUE = ("对", "正确", "是", "√", "true", "t", "yes", "y", "a", "1")
_JUDGE_FALSE = ("错", "错误", "否", "×", "x", "false", "f", "no", "n", "b", "0")


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _norm(v):
    """答案归一化：去首尾空格、折叠连续空白、英文转小写。"""
    return " ".join(str(v if v is not None else "").strip().split()).lower()


def _judge(v):
    n = _norm(v)
    if n in _JUDGE_TRUE:
        return True
    if n in _JUDGE_FALSE:
        return False
    return None


def _as_set(v):
    if isinstance(v, (list, tuple)):
        return set(_norm(x) for x in v if _norm(x))
    s = _norm(v)
    if not s:
        return set()
    # 兼容 "A,C" / "A，C" / "A C" 三种常见写法
    for sep in (",", "，", "|", "、", " "):
        if sep in s:
            return set(x for x in (_norm(p) for p in s.split(sep)) if x)
    return {s}


def _match_pairs(q):
    """连线题的正确配对：优先 answer 里的 [[左,右]...]，否则用 pairs 字段。"""
    ans = q.get("answer")
    out = []
    if isinstance(ans, list):
        for p in ans:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                out.append((_norm(p[0]), _norm(p[1])))
    if not out:
        for p in (q.get("pairs") or []):
            if isinstance(p, dict):
                out.append((_norm(p.get("l")), _norm(p.get("r"))))
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                out.append((_norm(p[0]), _norm(p[1])))
    return out


def _student_match(v, lefts):
    """学生连线答案兼容两种格式：{左:右} 或 [[左,右],...]。"""
    out = {}
    if isinstance(v, dict):
        for k, r in v.items():
            if _norm(k):
                out[_norm(k)] = _norm(r)
    elif isinstance(v, list):
        for p in v:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                out[_norm(p[0])] = _norm(p[1])
            elif isinstance(p, dict):
                out[_norm(p.get("l"))] = _norm(p.get("r"))
    return {k: out.get(k, "") for k in lefts}


def grade_question(q, ans):
    """单题判分，返回 (是否满分, 得分, 是否正确/bool|None)。

    None 表示需要人工批阅（简答 / 图片上传），这种题不计入已判分总分。
    """
    score = q.get("score")
    try:
        full = float(score)
    except Exception:
        full = 0.0
    if full < 0:
        full = 0.0
    t = str(q.get("type") or "single")
    if t not in AUTO_TYPES:
        return (False, 0.0, None)

    ok = False
    if t == "single":
        ok = bool(_norm(ans)) and _norm(ans) == _norm(q.get("answer"))
    elif t == "multiple":
        want, got = _as_set(q.get("answer")), _as_set(ans)
        ok = bool(want) and want == got
    elif t == "blank":
        accept = [_norm(q.get("answer"))]
        for a in (q.get("accept") or []):
            accept.append(_norm(a))
        ok = bool(_norm(ans)) and _norm(ans) in set(x for x in accept if x)
    elif t == "judge":
        want, got = _judge(q.get("answer")), _judge(ans)
        ok = want is not None and got is not None and want == got
    elif t == "match":
        pairs = _match_pairs(q)
        lefts = [l for l, _ in pairs]
        got = _student_match(ans, lefts)
        ok = bool(pairs) and all(got.get(l) == r for l, r in pairs)
    return (ok, full if ok else 0.0, bool(ok))


def grade_paper(paper, answers):
    """整卷判分。返回 (score, full_auto, detail, needs_grade)。

    full_auto 只统计可自动判分的题（简答/图片另需人工批），
    所以「提交后立即显示分数」显示的是客观题得分，界面必须写清楚口径。
    """
    answers = answers if isinstance(answers, dict) else {}
    detail, score, full_auto, needs = {}, 0.0, 0.0, False
    for q in (paper.get("questions") or []):
        qid = str(q.get("id") or "")
        if not qid:
            continue
        ok, got, correct = grade_question(q, answers.get(qid))
        detail[qid] = {"correct": correct, "got": got}
        if correct is None:
            needs = True
            continue
        full_auto += float(q.get("score") or 0)
        score += got
    return (round(score, 2), round(full_auto, 2), detail, needs)


def _paper_full(paper):
    return round(sum(float(q.get("score") or 0) for q in (paper.get("questions") or [])), 2)


class QuizStore(object):
    """每个教师账号一个实例（由 WorkspaceProxy 懒加载），路径 <工作区>/quiz.json。"""

    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.data = {"papers": {}, "publishes": {}, "submissions": [], "seq": 0}
        self._load()

    # ---------------------------------------------------------------- 落盘
    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data = {
                    "papers": d.get("papers") if isinstance(d.get("papers"), dict) else {},
                    "publishes": d.get("publishes") if isinstance(d.get("publishes"), dict) else {},
                    "submissions": d.get("submissions") if isinstance(d.get("submissions"), list) else [],
                    "seq": int(d.get("seq") or 0),
                }
        except FileNotFoundError:
            return
        except Exception:
            # 文件坏了不能让整个服务起不来：保留空库，原文件留着人工排查
            return

    def _save(self):
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def _next(self, prefix):
        self.data["seq"] = int(self.data.get("seq") or 0) + 1
        return "%s%d" % (prefix, self.data["seq"])

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data, ensure_ascii=False))

    def restore(self, data):
        """全量备份恢复：只认结构完整的段，坏数据一律不覆盖本机。"""
        if not isinstance(data, dict):
            return 0
        with self.lock:
            papers = data.get("papers") if isinstance(data.get("papers"), dict) else None
            publishes = data.get("publishes") if isinstance(data.get("publishes"), dict) else None
            subs = data.get("submissions") if isinstance(data.get("submissions"), list) else None
            if papers is None or publishes is None or subs is None:
                return 0
            self.data = {"papers": papers, "publishes": publishes,
                         "submissions": subs, "seq": int(data.get("seq") or 0)}
            self._save()
            return len(papers) + len(publishes) + len(subs)

    # ---------------------------------------------------------------- 试卷
    def papers(self):
        with self.lock:
            return [dict(v) for v in self.data["papers"].values()]

    def paper(self, pid):
        with self.lock:
            p = self.data["papers"].get(str(pid))
            return dict(p) if p else None

    def save_paper(self, raw):
        """新建 / 更新试卷。题目字段在服务端洗一遍，不接受客户端塞奇怪结构。"""
        if not isinstance(raw, dict):
            return None
        qs = []
        for i, q in enumerate(raw.get("questions") or []):
            if not isinstance(q, dict):
                continue
            t = str(q.get("type") or "single")
            if t not in TYPES:
                t = "single"
            opts = []
            for o in (q.get("options") or []):
                if isinstance(o, dict):
                    opts.append({"key": str(o.get("key") or ""), "text": str(o.get("text") or "")})
                else:
                    opts.append({"key": str(o), "text": str(o)})
            pairs = []
            for p in (q.get("pairs") or []):
                if isinstance(p, dict):
                    pairs.append({"l": str(p.get("l") or ""), "r": str(p.get("r") or "")})
            try:
                sc = float(q.get("score") or 0)
            except Exception:
                sc = 0.0
            qs.append({
                "id": str(q.get("id") or ("q%d" % (i + 1))),
                "type": t, "title": str(q.get("title") or ""),
                "score": max(0.0, sc), "options": opts, "pairs": pairs,
                "answer": q.get("answer"), "accept": q.get("accept") or [],
                "hint": str(q.get("hint") or ""),
            })
        with self.lock:
            pid = str(raw.get("id") or "").strip()
            old = self.data["papers"].get(pid) if pid else None
            if not pid:
                pid = self._next("paper")
            p = {
                "id": pid,
                "title": str(raw.get("title") or "未命名试卷").strip() or "未命名试卷",
                "course": str(raw.get("course") or "").strip(),
                "desc": str(raw.get("desc") or "").strip(),
                "questions": qs,
                "total": _paper_full({"questions": qs}),
                "created_at": (old or {}).get("created_at") or now_str(),
                "updated_at": now_str(),
            }
            self.data["papers"][pid] = p
            self._save()
            return dict(p)

    def delete_paper(self, pid):
        pid = str(pid)
        with self.lock:
            if pid not in self.data["papers"]:
                return False
            del self.data["papers"][pid]
            # 已发布的记录 / 已交的答卷保留（历史成绩不能因为删了试卷就消失）
            self._save()
            return True

    # ---------------------------------------------------------------- 发布
    def publishes(self):
        with self.lock:
            return [dict(v) for v in self.data["publishes"].values()]

    def publish(self, pid):
        with self.lock:
            p = self.data["publishes"].get(str(pid))
            return dict(p) if p else None

    @staticmethod
    def _clean_publish(raw, old=None):
        old = old or {}
        def _flag(k, default=False):
            v = raw.get(k, old.get(k, default))
            return bool(v) if not isinstance(v, str) else v.strip().lower() in ("1", "true", "yes", "on")
        try:
            mx = int(raw.get("max_attempts", old.get("max_attempts", 1)) or 1)
        except Exception:
            mx = 1
        mx = max(1, min(20, mx))
        classes = []
        for c in (raw.get("class_ids") or old.get("class_ids") or []):
            try:
                classes.append(int(c))
            except Exception:
                pass
        return {
            "paper_id": str(raw.get("paper_id") or old.get("paper_id") or ""),
            "title": str(raw.get("title") or old.get("title") or "未命名测验").strip(),
            "course": str(raw.get("course") or old.get("course") or "").strip(),
            "class_ids": classes,
            "open_at": str(raw.get("open_at") or old.get("open_at") or "").strip(),
            "close_at": str(raw.get("close_at") or old.get("close_at") or "").strip(),
            "max_attempts": mx,
            "allow_multiple": _flag("allow_multiple", False),
            "show_answer": _flag("show_answer", False),
            "show_score": _flag("show_score", False),
            "status": str(raw.get("status") or old.get("status") or "published"),
            # 大屏窗口名：一次发布固定绑一个窗口，SSE 只刷新这一个窗口，避免错配
            "window": str(raw.get("window") or old.get("window") or "").strip()
                      or ("测验：" + str(raw.get("title") or old.get("title") or "未命名测验")),
            "created_at": old.get("created_at") or now_str(),
            "updated_at": now_str(),
        }

    def save_publish(self, raw):
        if not isinstance(raw, dict):
            return None
        with self.lock:
            pid = str(raw.get("id") or "").strip()
            old = self.data["publishes"].get(pid) if pid else None
            if not pid:
                pid = self._next("pub")
            p = self._clean_publish(raw, old)
            p["id"] = pid
            self.data["publishes"][pid] = p
            self._save()
            return dict(p)

    def delete_publish(self, pid):
        pid = str(pid)
        with self.lock:
            if pid not in self.data["publishes"]:
                return False
            del self.data["publishes"][pid]
            self.data["submissions"] = [s for s in self.data["submissions"]
                                        if str(s.get("publish_id")) != pid]
            self._save()
            return True

    # ---------------------------------------------------------------- 状态
    @staticmethod
    def state(pub, now=None):
        """返回 (是否开放, 状态文案)。服务端时间为准，学生端不自己算。"""
        if str(pub.get("status") or "") == "draft":
            return False, "draft"
        if str(pub.get("status") or "") == "closed":
            return False, "closed"
        stamp = now or time.strftime("%Y-%m-%d %H:%M")
        op = str(pub.get("open_at") or "").strip()
        cl = str(pub.get("close_at") or "").strip()
        if op and stamp < op[:16]:
            return False, "before"
        if cl and stamp > cl[:16]:
            return False, "expired"
        return True, "open"

    def visible_publishes(self, class_id, now=None):
        """学生端可见：只给本班、非草稿、有试卷的发布。答案一律不在这里出现。"""
        out = []
        with self.lock:
            for p in self.data["publishes"].values():
                if int(class_id) not in [int(x) for x in (p.get("class_ids") or [])]:
                    continue
                ok, st = self.state(p, now)
                if st == "draft":
                    continue
                if not self.data["papers"].get(str(p.get("paper_id"))):
                    continue
                d = dict(p)
                d["state"] = st
                d["open"] = ok
                out.append(d)
        return out

    def paper_for_student(self, pid):
        """下发给学生的试卷：剥掉 answer / accept / score 判定细节。"""
        p = self.paper(pid)
        if not p:
            return None
        qs = []
        for q in (p.get("questions") or []):
            d = {k: q.get(k) for k in ("id", "type", "title", "options", "hint")}
            # 判断题 / 连线题要给选项，但不给哪个是正确项。
            # 连线题尤其不能直接下发 pairs（那就是答案表）：
            # 只给打乱后的右项候选 + 左项列表，配对关系留在服务端判分。
            if str(q.get("type")) == "match":
                pairs = _match_pairs(q)
                d["lefts"] = [l for l, _ in pairs]
                d["choices"] = sorted(set(r for _, r in pairs))
            qs.append(d)
        return {"id": p["id"], "title": p["title"], "course": p.get("course", ""),
                "desc": p.get("desc", ""), "questions": qs, "total": p.get("total", 0)}

    # ---------------------------------------------------------------- 提交
    def attempt_count(self, pid, sid):
        with self.lock:
            return sum(1 for s in self.data["submissions"]
                       if str(s.get("publish_id")) == str(pid)
                       and str(s.get("student_id")) == str(sid))

    def add_submission(self, pub, paper, who, answers, now=None):
        """服务端判分后落库。返回 (记录, 错误文案)。"""
        pid = str(pub.get("id") or "")
        sid = str(who.get("student_id") or "")
        with self.lock:
            ok, st = self.state(pub, now)
            if not ok:
                return None, {"before": "测验还没到开放时间",
                              "expired": "测验已截止", "closed": "测验已关闭",
                              "draft": "测验尚未发布"}.get(st, "当前不可提交")
            n = self.attempt_count(pid, sid)
            limit = int(pub.get("max_attempts") or 1)
            allow = bool(pub.get("allow_multiple"))
            if not allow and n >= 1:
                return None, "本测验只允许提交一次"
            if n >= limit:
                return None, "已达到提交次数上限（%d 次）" % limit
            score, full_auto, detail, needs = grade_paper(paper, answers)
            full = _paper_full(paper)
            rec = {
                "id": self._next("sub"),
                "publish_id": pid, "paper_id": str(paper.get("id") or ""),
                "window": str(pub.get("window") or ""),
                "student_id": sid,
                "student_no": str(who.get("student_no") or ""),
                "name": str(who.get("name") or ""),
                "class_id": who.get("class_id"),
                "class_name": str(who.get("class_name") or ""),
                "answers": answers if isinstance(answers, dict) else {},
                "detail": detail,
                "score": score, "auto_full": full_auto, "full": full,
                "needs_grade": needs, "graded": not needs,
                "attempt": n + 1,
                "submitted_at": now_str(),
            }
            self.data["submissions"].append(rec)
            self._save()
            return dict(rec), None

    def submissions(self, pid=None, class_id=None):
        with self.lock:
            out = []
            for s in self.data["submissions"]:
                if pid and str(s.get("publish_id")) != str(pid):
                    continue
                if class_id is not None and str(s.get("class_id")) != str(class_id):
                    continue
                out.append(dict(s))
            return out

    def latest_per_person(self, rows):
        """统一口径：同一次发布同一名学生只留最后一次提交。"""
        idx = {}
        for s in rows:
            key = str(s.get("student_no") or s.get("student_id") or s.get("name") or "")
            old = idx.get(key)
            if not old or str(s.get("submitted_at") or "") >= str(old.get("submitted_at") or ""):
                idx[key] = s
        return list(idx.values())

    def board(self, act_submissions=None):
        """大屏实时看板：按窗口分组的「谁交了」。

        act_submissions 是学习单的提交（按钮名 → 窗口），与测验提交**同一份口径**
        （同一窗口 + 同一学号只留最新一条），所以大屏、测验页、成绩页不会各算各的。
        返回 {windows:{窗口:[{name,no,at,from}]}, total, quiz_total}。
        """
        wins, quiz_total = {}, 0
        with self.lock:
            for s in self.data["submissions"]:
                w = str(s.get("window") or "测验")
                wins.setdefault(w, []).append({
                    "name": s.get("name") or "", "no": str(s.get("student_no") or ""),
                    "at": s.get("submitted_at") or "", "from": "quiz",
                })
                quiz_total += 1
        for s in (act_submissions or []):
            if not isinstance(s, dict):
                continue
            w = str(s.get("按钮") or s.get("button") or s.get("task") or "").strip()
            w = " ".join(w.split())
            if not w or w == "整页提交":
                continue
            d = s.get("data") if isinstance(s.get("data"), dict) else s
            wins.setdefault(w, []).append({
                "name": str(d.get("姓名") or d.get("name") or ""),
                "no": str(d.get("学号") or d.get("student_no") or d.get("sid") or ""),
                "at": str(s.get("submitted_at") or d.get("submitted_at") or ""),
                "from": "sheet",
            })
        out = {}
        for w, rows in wins.items():
            idx = {}
            for r in rows:
                k = r.get("no") or r.get("name")
                if not k:
                    continue
                old = idx.get(k)
                if not old or str(r.get("at") or "") >= str(old.get("at") or ""):
                    idx[k] = r
            out[w] = sorted(idx.values(), key=lambda x: str(x.get("at") or ""))
        return {"windows": out, "total": sum(len(v) for v in out.values()),
                "quiz_total": quiz_total}

    def stats(self, pid, class_id=None, roster=None):
        """一次测验的统计（测验页 / 成绩页导入 / 大屏计数共用这一个口径）。

        roster = {class_id: 应考人数}，由 API 层从 STORE 传入。
        分数取每人最后一次提交的客观题得分；含人工批阅题时标 needs_grade。
        """
        roster = roster or {}
        pub = self.publish(pid)
        paper = self.paper((pub or {}).get("paper_id"))
        rows = self.latest_per_person(self.submissions(pid, class_id))
        by_cls = {}
        for s in rows:
            cid = str(s.get("class_id") or "")
            b = by_cls.setdefault(cid, {"class_id": s.get("class_id"),
                                        "class_name": s.get("class_name") or "未分班",
                                        "submitted": 0, "scores": [], "attempts": 0,
                                        "needs_grade": 0})
            b["submitted"] += 1
            if s.get("score") is not None:
                b["scores"].append(float(s.get("score") or 0))
            if s.get("needs_grade"):
                b["needs_grade"] += 1
        # 没交过的班也要出现在表里（0 人 / 0 分），否则按班对比会漏班
        for cid in [str(x) for x in (pub or {}).get("class_ids", [])]:
            if class_id is not None and str(class_id) != cid:
                continue
            by_cls.setdefault(cid, {"class_id": cid, "class_name": "",
                                    "submitted": 0, "scores": [], "attempts": 0,
                                    "needs_grade": 0})
        classes = []
        for cid, b in by_cls.items():
            sc = b["scores"]
            full = float((paper or {}).get("total") or 0)
            b["roster"] = int(roster.get(cid, 0) or 0)
            b["avg"] = round(sum(sc) / len(sc), 2) if sc else 0
            b["max"] = round(max(sc), 2) if sc else 0
            b["min"] = round(min(sc), 2) if sc else 0
            b["pass"] = round(100.0 * sum(1 for x in sc if full > 0 and x >= full * 0.6) / len(sc), 1) if sc else 0
            b["rate"] = round(100.0 * b["submitted"] / b["roster"], 1) if b["roster"] else 0
            b["full"] = full
            classes.append(b)
        classes.sort(key=lambda x: str(x.get("class_name") or x.get("class_id") or ""))
        all_sc = [x for b in classes for x in b["scores"]]
        # 逐题正确率：同一份 detail，不另算
        qstat = {}
        qs = (paper or {}).get("questions") or []
        for q in qs:
            qid = str(q.get("id") or "")
            vals = [s.get("detail", {}).get(qid, {}).get("correct") for s in rows
                    if isinstance(s.get("detail"), dict)]
            vals = [v for v in vals if v is not None]
            qstat[qid] = {"type": q.get("type"), "title": q.get("title"),
                          "right": sum(1 for v in vals if v), "answered": len(vals),
                          "rate": round(100.0 * sum(1 for v in vals if v) / len(vals), 1) if vals else 0}
        return {
            "publish": pub, "paper": {"id": (paper or {}).get("id"),
                                      "title": (paper or {}).get("title"),
                                      "total": (paper or {}).get("total", 0),
                                      "questions": [{"id": str(q.get("id")), "type": q.get("type"),
                                                     "title": q.get("title")} for q in qs]},
            "classes": classes,
            "submitted": len(rows),
            "avg": round(sum(all_sc) / len(all_sc), 2) if all_sc else 0,
            "max": round(max(all_sc), 2) if all_sc else 0,
            "min": round(min(all_sc), 2) if all_sc else 0,
            "full": float((paper or {}).get("total") or 0),
            "needs_grade": sum(1 for s in rows if s.get("needs_grade")),
            "question_stats": qstat,
            "rows": rows,
        }

    def grade_submission(self, sub_id, marks):
        """人工批阅简答 / 图片上传题，marks = {qid: 得分}，服务端按题目分值封顶。"""
        sub_id = str(sub_id)
        marks = marks if isinstance(marks, dict) else {}
        with self.lock:
            rec = next((x for x in self.data["submissions"]
                        if str(x.get("id")) == sub_id), None)
            if not rec:
                return 0
            paper = self.data["papers"].get(str(rec.get("paper_id"))) or {}
            qmap = {str(q.get("id")): q for q in (paper.get("questions") or [])}
            detail = dict(rec.get("detail") or {})
            n = 0
            for qid, val in marks.items():
                q = qmap.get(str(qid))
                if not q or str(q.get("type")) not in ("essay", "image"):
                    continue
                try:
                    got = float(val)
                except Exception:
                    got = 0.0
                cap = float(q.get("score") or 0)
                detail[str(qid)] = {"correct": None, "got": max(0.0, min(got, cap)),
                                    "manual": True}
                n += 1
            if not n:
                return 0
            rec["detail"] = detail
            rec["score"] = round(sum(float(d.get("got") or 0) for d in detail.values()), 2)
            # 还有没批的主观题就继续标 needs_grade
            pending = [q for q in (paper.get("questions") or [])
                       if str(q.get("type")) in ("essay", "image")
                       and not (detail.get(str(q.get("id")), {}) or {}).get("manual")]
            rec["needs_grade"] = bool(pending)
            rec["graded"] = not pending
            self._save()
            return n

    def score_rows(self, pid, class_id):
        """成绩页导入用：返回可直接写进 exam_scores 的 [{student_id, student_no, name, score}]。"""
        out = []
        for s in self.latest_per_person(self.submissions(pid, class_id)):
            out.append({"student_id": s.get("student_id"), "student_no": s.get("student_no"),
                        "name": s.get("name"), "score": float(s.get("score") or 0),
                        "full": float(s.get("full") or 0), "attempt": s.get("attempt"),
                        "submitted_at": s.get("submitted_at")})
        return out
