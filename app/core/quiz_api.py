#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 测验/检测接口层（quiz_api.py，2.0.38）

抽出来单独放，是为了不让 270KB 的 server.py 里再塞几百行分支：
server.py 只在 GET / POST 分发开头各插一行，其余全在这里。

安全边界（重点）
--------------
· 教师接口一律 _is_teacher()；
· 学生接口一律按会话取 student_row，班级由**服务端**查出来，不信客户端传的 class_id；
· 下发的试卷是 quiz_store.paper_for_student() 裁剪过的，没有 answer / accept / 分值；
· 提交由服务端 grade_paper() 判分，answers 里客户端塞 score 也没用；
· 提交后是否给答案 / 给分数，按该次发布的 show_answer / show_score 在**出网前**裁掉，
  不是前端隐藏——前端隐藏等于把答案发到学生机器上了；
· 图片题落 <工作区>/quiz_files/，取图时教师可看全部、学生只能看自己的。
"""

import os
import re
import json
import base64
import time
import urllib.parse

# SSE 事件版本：每次提交 / 发布变更 +1，server.py 的 _sse_loop 推 event: quiz。
# 数据不进 STORE.rev（试卷不参与同步），所以必须有独立的一条实时通道。
EPOCH = [0]
LAST_EVENT = [{}]


def bump(payload=None):
    EPOCH[0] += 1
    LAST_EVENT[0] = payload or {}
    return EPOCH[0]


def last_event():
    return {"epoch": EPOCH[0], "data": LAST_EVENT[0]}


_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
MAX_IMAGE = 2 * 1024 * 1024          # 单张 2MB（base64 解码后）
MAX_IMAGES = 6                        # 单次提交最多 6 张


class QuizAPI(object):
    def __init__(self, handler, quiz, store, act, files_dir,
                 student_row_fn, class_name_fn, notify_fn):
        self.h = handler
        self.q = quiz
        self.store = store
        self.act = act
        self.files_dir = files_dir
        self.student_row = student_row_fn
        self.class_name = class_name_fn
        self.notify = notify_fn

    # ------------------------------------------------------------------ 工具
    def ok(self, **kw):
        # 返回值是给 server.py 分发器的「我已处理」标记。
        # Handler._json() 本身返回 None，直接 return 它会被当成「未知接口」
        # 而在同一条 keep-alive 连接上再写一次 404 响应（HTTP 流被污染）。
        self.h._json(dict({"ok": True}, **kw))
        return True

    def err(self, msg, code=400):
        self.h._json({"ok": False, "msg": msg}, code)
        return True

    def teacher(self):
        return self.h._is_teacher()

    def me(self):
        s = self.h._session()
        if not s or s.get("role") != "student":
            return None, None
        row = self.student_row(s.get("student_id"))
        if not row:
            return s, None
        return s, row

    def roster(self):
        """{class_id: 人数} 与班级名，全部从 STORE 实时取（改名/转班自动跟着变）。"""
        counts, names = {}, {}
        try:
            for c in self.store.table_rows("classes"):
                names[str(c.get("id"))] = str(c.get("name") or "")
            for s in self.store.table_rows("students"):
                k = str(s.get("class_id") or "")
                counts[k] = counts.get(k, 0) + 1
        except Exception:
            pass
        return counts, names

    def classes(self):
        out = []
        try:
            counts, _ = self.roster()
            for c in self.store.table_rows("classes"):
                out.append({"id": c.get("id"), "name": str(c.get("name") or ""),
                            "n": int(counts.get(str(c.get("id")), 0))})
        except Exception:
            pass
        return out

    def now(self):
        return {"date": time.strftime("%Y-%m-%d"), "time": time.strftime("%H:%M:%S"),
                "stamp": time.strftime("%Y-%m-%d %H:%M"), "ts": time.time()}

    # ------------------------------------------------------------------ 图片
    def _save_images(self, sub_id, paper, answers):
        """把 dataURL 落到磁盘，answers 里只留只读 URL（quiz.json 不被撑爆）。"""
        qmap = {str(q.get("id")): q for q in (paper.get("questions") or [])}
        for qid, val in list((answers or {}).items()):
            q = qmap.get(str(qid)) or {}
            if str(q.get("type")) != "image" or not isinstance(val, dict):
                continue
            data = str(val.get("data") or "")
            m = re.match(r"^data:image/([a-zA-Z0-9+.-]+);base64,([A-Za-z0-9+/=\s]+)$", data)
            if not m:
                answers[qid] = {"name": str(val.get("name") or ""), "url": "", "size": 0}
                continue
            ext = "." + m.group(1).lower().replace("jpeg", "jpg")
            if ext not in _MIME:
                ext = ".png"
            try:
                raw = base64.b64decode(m.group(2), validate=True)
            except Exception:
                raw = b""
            if not raw or len(raw) > MAX_IMAGE:
                answers[qid] = {"name": str(val.get("name") or ""), "url": "", "size": 0}
                continue
            try:
                os.makedirs(self.files_dir, exist_ok=True)
                fn = "%s_%s%s" % (re.sub(r"[^A-Za-z0-9_-]", "", str(sub_id)),
                                  re.sub(r"[^A-Za-z0-9_-]", "", str(qid)), ext)
                with open(os.path.join(self.files_dir, fn), "wb") as f:
                    f.write(raw)
                answers[qid] = {"name": str(val.get("name") or "图片"), "size": len(raw),
                                "url": "/api/v2/quiz/file?sub=%s&q=%s" % (
                                    urllib.parse.quote(str(sub_id)), urllib.parse.quote(str(qid)))}
            except Exception:
                answers[qid] = {"name": str(val.get("name") or ""), "url": "", "size": 0}
        return answers

    def _file(self, qs):
        s = self.h._session()
        if not s:
            return self.err("未登录", 401)
        sub = (qs.get("sub") or [""])[0]
        qid = (qs.get("q") or [""])[0]
        rows = [x for x in self.q.submissions() if str(x.get("id")) == sub]
        if not rows:
            return self.err("图片不存在", 404)
        rec = rows[0]
        if s.get("role") != "teacher" and str(s.get("student_id")) != str(rec.get("student_id")):
            return self.err("无权查看", 403)
        val = (rec.get("answers") or {}).get(qid) or {}
        url = str(val.get("url") or "")
        if not url.startswith("/api/v2/quiz/file"):
            return self.err("图片不存在", 404)
        fn = "%s_%s" % (re.sub(r"[^A-Za-z0-9_-]", "", sub),
                        re.sub(r"[^A-Za-z0-9_-]", "", qid))
        found = None
        for ext, mime in _MIME.items():
            p = os.path.normpath(os.path.join(self.files_dir, fn + ext))
            if os.path.commonpath([os.path.abspath(self.files_dir), p]) != os.path.abspath(self.files_dir):
                continue
            if os.path.isfile(p):
                found = (p, mime)
                break
        if not found:
            return self.err("图片不存在", 404)
        with open(found[0], "rb") as f:
            body = f.read()
        self.h.send_response(200)
        self.h.send_header("Content-Type", found[1])
        self.h.send_header("Content-Length", str(len(body)))
        self.h.send_header("Cache-Control", "no-store")
        self.h.end_headers()
        self.h.wfile.write(body)
        return True

    # ------------------------------------------------------------------ GET
    def get(self, path, qs):
        if path == "/api/v2/quiz/file":
            return self._file(qs)

        if path == "/api/v2/quiz/board":
            # 大屏实时看板只给教师：名单里有别班学号，学生端不该拿到
            if not self.teacher():
                return self.err("未登录", 401)
            return self._board(qs)

        if path == "/api/v2/quiz/teacher/overview":
            if not self.teacher():
                return self.err("未登录", 401)
            pubs = self.q.publishes()
            counts, names = self.roster()
            for p in pubs:
                ok, st = self.q.state(p, time.strftime("%Y-%m-%d %H:%M"))
                p["state"] = st
                p["open"] = ok
                p["paper_title"] = (self.q.paper(p.get("paper_id")) or {}).get("title", "（试卷已删除）")
                p["class_names"] = [names.get(str(c), str(c)) for c in (p.get("class_ids") or [])]
                p["submitted"] = len(self.q.latest_per_person(self.q.submissions(p.get("id"))))
                p["roster"] = sum(int(counts.get(str(c), 0)) for c in (p.get("class_ids") or []))
            pubs.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            papers = self.q.papers()
            papers.sort(key=lambda x: str(x.get("updated_at") or ""), reverse=True)
            return self.ok(papers=papers, publishes=pubs, classes=self.classes(),
                           now=self.now(), board=self._board_data(),
                           policy=self._policy_text())

        if path == "/api/v2/quiz/teacher/paper":
            if not self.teacher():
                return self.err("未登录", 401)
            p = self.q.paper((qs.get("id") or [""])[0])
            return self.ok(paper=p) if p else self.err("试卷不存在", 404)

        if path == "/api/v2/quiz/teacher/stats":
            if not self.teacher():
                return self.err("未登录", 401)
            pid = (qs.get("pub") or [""])[0]
            if not self.q.publish(pid):
                return self.err("测验不存在", 404)
            cid = (qs.get("class") or [""])[0] or None
            counts, names = self.roster()
            st = self.q.stats(pid, cid, counts)
            for b in st["classes"]:
                b["class_name"] = names.get(str(b.get("class_id")), "") or b.get("class_name") or "未分班"
            return self.ok(stats=st, classes=self.classes(), now=self.now())

        if path == "/api/v2/quiz/teacher/score-rows":
            # 成绩页「从测验导入」的数据源：只给行，真正的写库由前端走既有同步链路
            if not self.teacher():
                return self.err("未登录", 401)
            pid = (qs.get("pub") or [""])[0]
            cid = (qs.get("class") or [""])[0]
            pub = self.q.publish(pid)
            if not pub:
                return self.err("测验不存在", 404)
            rows = self.q.score_rows(pid, cid or None)
            paper = self.q.paper(pub.get("paper_id")) or {}
            _c, names = self.roster()
            return self.ok(rows=rows, publish=pub,
                           paper={"id": paper.get("id"), "title": paper.get("title"),
                                  "total": paper.get("total", 0)},
                           class_name=names.get(cid, ""), class_id=cid)

        if path == "/api/v2/quiz/student/list":
            s, row = self.me()
            if not s:
                return self.err("未登录", 401)
            if not row:
                return self.err("学生档案不存在", 404)
            cid = row.get("class_id")
            out = []
            for p in self.q.visible_publishes(cid, time.strftime("%Y-%m-%d %H:%M")):
                d = dict(p)
                # 显示策略由服务端在「提交回执」里执行，配置本身不下发给学生端
                d.pop("show_answer", None)
                d.pop("show_score", None)
                d["paper_title"] = (self.q.paper(p.get("paper_id")) or {}).get("title", "")
                d["attempts"] = self.q.attempt_count(p.get("id"), s.get("student_id"))
                d["last"] = self._receipt(p, s.get("student_id"), False)
                out.append(d)
            return self.ok(publishes=out, now=self.now(),
                           class_name=self.class_name(cid), class_id=cid)

        if path == "/api/v2/quiz/student/paper":
            s, row = self.me()
            if not s:
                return self.err("未登录", 401)
            if not row:
                return self.err("学生档案不存在", 404)
            pid = (qs.get("pub") or [""])[0]
            pub = self.q.publish(pid)
            if not pub:
                return self.err("测验不存在", 404)
            if int(row.get("class_id") or 0) not in [int(x) for x in (pub.get("class_ids") or [])]:
                return self.err("这份测验没有下发给你的班级", 403)
            ok, st = self.q.state(pub, time.strftime("%Y-%m-%d %H:%M"))
            if st == "draft":
                return self.err("测验尚未发布", 403)
            paper = self.q.paper_for_student(pub.get("paper_id"))
            if not paper:
                return self.err("试卷不存在", 404)
            safe = {k: pub.get(k) for k in ("id", "title", "course", "max_attempts",
                                            "allow_multiple", "open_at", "close_at")}
            safe["state"] = st
            safe["open"] = ok
            safe["attempts"] = self.q.attempt_count(pid, s.get("student_id"))
            return self.ok(paper=paper, publish=safe, now=self.now(),
                           class_name=self.class_name(row.get("class_id")),
                           student={"name": row.get("name"), "no": row.get("student_no")})

        if path == "/api/v2/quiz/student/result":
            s, row = self.me()
            if not s:
                return self.err("未登录", 401)
            pid = (qs.get("pub") or [""])[0]
            pub = self.q.publish(pid)
            if not pub:
                return self.err("测验不存在", 404)
            return self.ok(receipt=self._receipt(pub, s.get("student_id"), True))

        return None      # 不是测验接口

    # ------------------------------------------------------------------ 回执
    def _receipt(self, pub, sid, with_detail):
        """按 show_answer / show_score 在出网前裁剪，绝不把答案发给不该看的人。"""
        rows = [x for x in self.q.submissions(pub.get("id"))
                if str(x.get("student_id")) == str(sid)]
        if not rows:
            return None
        rec = sorted(rows, key=lambda x: str(x.get("submitted_at") or ""))[-1]
        out = {"id": rec.get("id"), "attempt": rec.get("attempt"),
               "submitted_at": rec.get("submitted_at"), "needs_grade": bool(rec.get("needs_grade")),
               "show_answer": bool(pub.get("show_answer")),
               "show_score": bool(pub.get("show_score"))}
        if pub.get("show_score"):
            out["score"] = rec.get("score")
            out["auto_full"] = rec.get("auto_full")
            out["full"] = rec.get("full")
        if pub.get("show_answer"):
            paper = self.q.paper(rec.get("paper_id")) or {}
            ans = {}
            for q in (paper.get("questions") or []):
                qid = str(q.get("id") or "")
                ans[qid] = {"answer": q.get("answer"), "accept": q.get("accept") or [],
                            "pairs": q.get("pairs") or []}
            out["answers"] = ans
            if with_detail:
                out["detail"] = rec.get("detail") or {}
                out["mine"] = rec.get("answers") or {}
        return out

    def _board_data(self):
        """同源合并：测验提交 + 学习单按钮提交 → 按窗口分组。"""
        act_subs = []
        try:
            for t in (self.act.tasks() or []):
                tid = t.get("task_id") or t.get("id")
                if not tid:
                    continue
                # ActivityStore.submissions() 返回的是已摊平的 data 字典本身
                # （按钮名在 data["按钮"]，时间/学号/姓名也都在这一层）。
                for d in (self.act.submissions(tid) or []):
                    if not isinstance(d, dict):
                        continue
                    if str(d.get("按钮") or "").strip() == "测试提交":
                        continue
                    act_subs.append({"按钮": d.get("按钮") or d.get("button") or "",
                                     "data": d,
                                     "submitted_at": d.get("submitted_at") or ""})
        except Exception:
            pass
        return self.q.board(act_subs)

    def _board(self, qs):
        task = (qs.get("task") or [""])[0]
        b = self._board_data()
        b["now"] = self.now()
        b["task"] = task
        return self.ok(board=b)

    @staticmethod
    def _policy_text():
        return {
            "screen": "大屏（上课页）：实时收件看板，只统计「谁交了 / 交到哪个窗口」，不显示答案与分数。",
            "quiz": "测验/检测页：唯一的逐题、分数、按班提交与成绩分析入口。",
            "scores": "成绩页：把某次测验某班成绩导入成一场考试，之后走既有成绩分析。",
            "rule": "三处读同一份提交；同一次发布同一名学生只算最后一次提交，不重复计数。",
        }

    # ------------------------------------------------------------------ POST
    def post(self, path, body):
        if not path.startswith("/api/v2/quiz/"):
            return None
        if not self.teacher():
            # 学生提交单独走下面的学生分支
            if path != "/api/v2/quiz/student/submit":
                return self.err("未登录", 401)
        body = body if isinstance(body, dict) else {}

        if path == "/api/v2/quiz/teacher/paper/save":
            p = self.q.save_paper(body.get("paper") or body)
            if not p:
                return self.err("试卷内容不对")
            return self.ok(paper=p)

        if path == "/api/v2/quiz/teacher/paper/delete":
            ok = self.q.delete_paper(body.get("id"))
            return self.ok(deleted=ok) if ok else self.err("试卷不存在", 404)

        if path == "/api/v2/quiz/teacher/publish/save":
            raw = body.get("publish") or body
            pid = str(raw.get("paper_id") or "")
            if not self.q.paper(pid):
                return self.err("请先选择一份试卷")
            if not (raw.get("class_ids") or []):
                return self.err("请至少选择一个班级")
            p = self.q.save_publish(raw)
            bump({"kind": "publish", "pub": p.get("id"), "window": p.get("window")})
            self.notify()
            return self.ok(publish=p)

        if path == "/api/v2/quiz/teacher/publish/delete":
            ok = self.q.delete_publish(body.get("id"))
            if not ok:
                return self.err("测验不存在", 404)
            bump({"kind": "publish-delete"})
            self.notify()
            return self.ok(deleted=True)

        if path == "/api/v2/quiz/teacher/grade":
            # 人工批阅简答 / 图片上传题（按题目给分，服务端复核上限）
            sid = str(body.get("sub") or "")
            marks = body.get("marks") if isinstance(body.get("marks"), dict) else {}
            rec = next((x for x in self.q.submissions() if str(x.get("id")) == sid), None)
            if not rec:
                return self.err("提交不存在", 404)
            n = self.q.grade_submission(sid, marks) if hasattr(self.q, "grade_submission") else 0
            return self.ok(graded=n)

        if path == "/api/v2/quiz/student/submit":
            return self._submit(body)

        return None

    # ------------------------------------------------------------------ 提交
    def _submit(self, body):
        s, row = self.me()
        if not s:
            return self.err("未登录", 401)
        if not row:
            return self.err("学生档案不存在", 404)
        pid = str(body.get("pub") or "")
        pub = self.q.publish(pid)
        if not pub:
            return self.err("测验不存在", 404)
        cid = row.get("class_id")
        if int(cid or 0) not in [int(x) for x in (pub.get("class_ids") or [])]:
            return self.err("这份测验没有下发给你的班级", 403)
        paper = self.q.paper(pub.get("paper_id"))
        if not paper:
            return self.err("试卷不存在", 404)
        answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
        # 图片先落盘（判分前做，答卷里只留 URL）
        answers = self._save_images("tmp%s" % int(time.time() * 1000), paper, answers)
        who = {
            "student_id": s.get("student_id"),
            "student_no": row.get("student_no") or "",
            "name": row.get("name") or "",
            "class_id": cid,
            "class_name": self.class_name(cid),
        }
        rec, err = self.q.add_submission(pub, paper, who, answers,
                                         now=time.strftime("%Y-%m-%d %H:%M"))
        if err:
            return self.err(err, 403)
        # 图片文件名带的是临时前缀，落库后按 sub_id 重命名一次
        self._rename_images(rec, paper)
        bump({"kind": "submit", "pub": pid, "window": pub.get("window"),
              "class_id": cid, "student_no": who["student_no"]})
        self.notify()
        return self.ok(receipt=self._receipt(pub, s.get("student_id"), True),
                       submitted_at=rec.get("submitted_at"))

    def _rename_images(self, rec, paper):
        """把临时名换成 <sub_id>_<qid>，同时把答卷里的 url 指到正式记录。"""
        sub = str(rec.get("id") or "")
        qids = {str(q.get("id")) for q in (paper.get("questions") or [])
                if str(q.get("type")) == "image"}
        changed = False
        with self.q.lock:
            for x in self.q.data["submissions"]:
                if str(x.get("id")) != sub:
                    continue
                for qid in qids:
                    val = (x.get("answers") or {}).get(qid)
                    if not isinstance(val, dict) or not val.get("url"):
                        continue
                    for ext in _MIME:
                        tmp = val["url"].split("sub=", 1)[-1].split("&", 1)[0]
                        old = os.path.join(self.files_dir,
                                           "%s_%s%s" % (re.sub(r"[^A-Za-z0-9_-]", "", tmp),
                                                        re.sub(r"[^A-Za-z0-9_-]", "", qid), ext))
                        if not os.path.isfile(old):
                            continue
                        new = os.path.join(self.files_dir, "%s_%s%s" % (sub, qid, ext))
                        try:
                            os.replace(old, new)
                        except Exception:
                            pass
                        val["url"] = "/api/v2/quiz/file?sub=%s&q=%s" % (
                            urllib.parse.quote(sub), urllib.parse.quote(qid))
                        changed = True
                        break
                break
            if changed:
                self.q._save()
