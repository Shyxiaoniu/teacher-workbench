#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成若干班级的虚拟测试数据（只标准库）

用途：给「班级工作台 2.0」灌一批像真实课堂的数据，方便试用 / 演示 / 回归测试。
覆盖全部功能模块：
  班级 · 学生 · 座位 · 作息 · 课程 · 课表 · 考勤 · 作业(+提交记录) · 德育
  · 成绩 · 考试(+成绩) · 分组 · 值日(包干区/职务) · 课程进度 · 学生备注
  · 待办 · 请假申请

★ 不会覆盖你现有数据
  走的是增量同步接口 /api/v2/sync（按行 upsert），不是「整库导入」。
  演示数据的 id 统一从 90 万起，和你自己录入的班级/学生不会撞号。
  --clean 可以一键把上次灌的演示数据删干净。

用法（服务已在运行）：
  python tools/seed_demo.py                        # 3 个班 × 40 人
  python tools/seed_demo.py --classes 5 --students 45
  python tools/seed_demo.py --base http://127.0.0.1:20020 --user teacher --pass 你的密码
  python tools/seed_demo.py --clean                # 删掉演示数据
  python tools/seed_demo.py --json demo.json       # 只导出 JSON，不写服务端
"""
import os
import sys
import json
import random
import argparse
import urllib.request
import http.cookiejar
from datetime import date, datetime, timedelta

DEFAULT_BASE = "http://127.0.0.1:20020"

# ---- id 命名空间：演示数据全部落在 90 万以后，绝不动真实数据 ----
N_CLASS = 9000
N_STUDENT = 900000
N_SEAT = 910000
N_PERIOD = 900
N_COURSE = 900
N_ATT = 920000
N_HW = 930000
N_MOR = 950000
N_SCORE = 960000
N_EXAM = 970
N_GROUP = 980000
N_DUTY = 990000
N_AREA = 991000
N_POS = 992000
N_PROG = 993000
N_COL = 994000
N_NOTE = 995000
N_TODO = 996000
N_MITEM = 940

XING = ("王李张刘陈杨黄赵吴周徐孙马朱胡林郭何高罗郑梁谢宋唐许韩冯邓曹彭曾"
        "蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康")
MING_M = ("明轩 思远 子豪 浩然 俊杰 一鸣 宇轩 文博 天佑 嘉豪 泽宇 子墨 峻熙 皓宇 "
          "承泽 梓豪 睿轩 宇辰 天翊 奕辰 景行 立诚 家豪 子涵 泽楷 泓宇 振轩 书豪 "
          "博文 亦辰 锦程 泽宇 沐辰 云帆").split()
MING_F = ("雨桐 欣怡 梓萱 雅雯 诗涵 佳怡 雨萱 语彤 一诺 若曦 思彤 芷晴 心悦 梦琪 "
          "静怡 亦菲 晓彤 清越 予安 思琪 楚萱 沐晴 嘉怡 书瑶 星辰 语汐 念安 明月 "
          "欣然 若彤 婉清 忆楠 子瑜 沐瑶").split()

CLASS_NAMES = ["七(1)班", "七(2)班", "七(3)班", "七(4)班", "七(5)班", "七(6)班",
               "八(1)班", "八(2)班", "八(3)班", "八(4)班"]
CLASS_ROOMS = ["教学楼 A-301", "教学楼 A-302", "教学楼 A-303", "教学楼 A-304",
               "教学楼 A-305", "信息机房 1", "信息机房 2", "综合楼 B-201"]
CLASS_COLORS = ["#0F6E56", "#378ADD", "#EF9F27", "#D4537E", "#7F77DD",
                "#0EA5E9", "#E24B4A", "#8B5CF6"]

COURSES = ["信息科技", "数学", "语文", "英语", "体育", "历史", "地理", "生物"]
PERIODS = [("第1节", "08:00", "08:45"), ("第2节", "08:55", "09:40"),
           ("第3节", "10:10", "10:55"), ("第4节", "11:05", "11:50"),
           ("第5节", "14:00", "14:45"), ("第6节", "14:55", "15:40"),
           ("第7节", "15:50", "16:35"), ("第8节", "16:45", "17:30")]

MORAL_ITEMS = [("主动帮助同学", 2, "bonus", "#1D9E75"),
               ("课堂积极发言", 1, "bonus", "#5DCAA5"),
               ("卫生评比优秀", 3, "bonus", "#2F9E44"),
               ("课堂讲话", -1, "penalty", "#EF9F27"),
               ("未交作业", -2, "penalty", "#E24B4A"),
               ("仪容仪表不合格", -1, "penalty", "#E24B4A")]

HW_TITLES = [("practice", "Scratch 初体验：角色与舞台"),
             ("practice", "键盘指法练习（一）"),
             ("written", "第一单元字词默写"),
             ("written", "有理数混合运算 50 题"),
             ("practice", "WPS 文字：我的自我介绍"),
             ("practice", "Scratch 进阶：小猫走迷宫"),
             ("written", "Unit 2 单词抄写"),
             ("practice", "算法初步：流程图绘制")]

DUTY_AREAS = ["包干区1：教室前门地面", "包干区2：黑板与讲台",
              "包干区3：走廊与门窗", "包干区4：垃圾清运"]
DUTY_POSITIONS = ["班长", "学习委员", "纪律委员", "劳动委员",
                  "体育委员", "宣传委员", "生活委员", "信息委员"]


def weekdays_back(n, end=None):
    end = end or date.today()
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return list(reversed(out))


class Gen(object):
    def __init__(self, classes=3, students=40, days=10, seed=20261031,
                 prefix=""):
        self.rnd = random.Random(seed)
        self.n_class = max(1, min(classes, len(CLASS_NAMES)))
        self.n_stu = max(1, min(students, 60))
        self.days = max(1, min(days, 30))
        self.prefix = prefix
        self.T = {k: [] for k in (
            "classes", "students", "seats", "periods", "courses", "lessons",
            "attendance", "homeworks", "homework_records", "morality_items",
            "morality_records", "scores", "exams", "exam_scores", "groups",
            "dutys", "duty_areas", "duty_positions", "course_progress",
            "progress_cols", "student_notes", "todos", "leave_requests",
            "materials")}
        self.by_class = {}

    # ---------------- 名单 ----------------
    def build_people(self):
        used = set()
        names = []
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            stus = []
            for i in range(self.n_stu):
                while True:
                    nm = self.rnd.choice(XING) + self.rnd.choice(
                        MING_M if self.rnd.random() < 0.5 else MING_F)
                    if nm not in used:
                        used.add(nm)
                        break
                names.append(nm)
                male = self.rnd.random() < 0.5
                sid = N_STUDENT + c * 100 + i + 1
                stus.append(dict(
                    id=sid, class_id=cid, name=nm,
                    gender="male" if male else "female", seat_no="",
                    student_no="%d%02d%02d" % (2025, (c % 9) + 1, i + 1),
                    parent_name=self.rnd.choice(XING) + self.rnd.choice(
                        ["建国", "伟", "芳", "磊", "涛", "敏", "强", "丽", "娟"]),
                    phone1="138%08d" % self.rnd.randint(0, 99999999),
                    phone2="" if self.rnd.random() < 0.7 else
                           "139%08d" % self.rnd.randint(0, 99999999),
                    wechat="" if self.rnd.random() < 0.4 else
                           "wx%06d" % self.rnd.randint(0, 999999),
                    parent_committee_role=self.rnd.choice(
                        ["pc_head", "pc_vice", "pc_pr", "pc_logistics",
                         "pc_other", "pc_none", "pc_none", "pc_none"]),
                    parent_committee_note="",
                    contacted=self.rnd.random() < 0.35,
                    note=self.rnd.choice(["", "", "", "数学课代表",
                                          "信息课代表", "需重点关注视力"])))
                self.T["students"].append(stus[-1])
            self.by_class[cid] = stus

    # ---------------- 班级 / 座位 / 课表 ----------------
    def build_classes(self):
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            name = CLASS_NAMES[c]
            if self.prefix:
                name = self.prefix + name
            self.T["classes"].append(dict(
                id=cid, name=name, room=CLASS_ROOMS[c % len(CLASS_ROOMS)],
                teacher="牛老师", student_count=len(self.by_class[cid]),
                color=CLASS_COLORS[c % len(CLASS_COLORS)],
                note="演示数据 · 可随时删除"))

    def build_seats(self):
        sid = N_SEAT
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            stus = self.by_class[cid]
            k = 0
            for r in range(1, 7):
                for col in range(1, 9):
                    st = stus[k] if k < len(stus) else None
                    sn = "%d-%d" % (r, col)
                    if st:
                        st["seat_no"] = sn
                    sid += 1
                    self.T["seats"].append(dict(
                        id=sid, class_id=cid, seat_no=sn, row=r, col=col,
                        student_id=st["id"] if st else None,
                        is_computer=(c % 3 == 2)))
                    k += 1

    def build_timetable(self):
        for i, (label, st, et) in enumerate(PERIODS):
            self.T["periods"].append(dict(id=N_PERIOD + i + 1, day_of_week=0,
                                          seq=i + 1, label=label,
                                          start_time=st, end_time=et,
                                          kind="class"))
        for i, nm in enumerate(COURSES):
            self.T["courses"].append(dict(id=N_COURSE + i + 1, name=nm,
                                          teacher="牛老师" if i == 0 else "任课老师",
                                          location="信息机房2" if i == 0 else "",
                                          color=CLASS_COLORS[i % len(CLASS_COLORS)],
                                          active=True))
        # 信息科技同一个时段不能两个班同时上（同一位老师）
        info_slots = {0: [(0, 1), (2, 3)], 1: [(1, 1), (3, 1)],
                      2: [(0, 2), (1, 2)], 3: [(2, 1), (4, 2)],
                      4: [(1, 3), (3, 2)]}
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            mine = info_slots.get(c, [(c % 5, 1), ((c + 2) % 5, 2)])
            p = 0
            for d in range(5):
                for per in range(1, 8):
                    if (d, per) in mine:
                        crid = N_COURSE + 1
                    elif per == 7 and d in (0, 2, 4):
                        crid = N_COURSE + 5      # 体育
                    else:
                        crid = N_COURSE + 2 + (p % (len(COURSES) - 2))
                        p += 1
                    self.T["lessons"].append(dict(
                        day_of_week=d, period_id=N_PERIOD + per,
                        class_id=cid, course_id=crid, week_pattern="all",
                        room_override="信息机房2" if crid == N_COURSE + 1 else ""))
                    self.T["lessons"][-1]["class_id"] = cid

    # ---------------- 业务流水 ----------------
    def build_attendance(self):
        days = weekdays_back(self.days)
        pool = (["present"] * 88 + ["late"] * 5 + ["sick"] * 3 +
                ["personal"] * 2 + ["absent"] * 1 + ["early_leave"] * 1)
        aid = N_ATT
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            ds = days if c == 0 else days[-max(3, self.days // 2):]
            for d in ds:
                for st in self.by_class[cid]:
                    s = self.rnd.choice(pool)
                    reason = ""
                    if s == "sick":
                        reason = "感冒发烧"
                    elif s == "personal":
                        reason = "家中有事"
                    aid += 1
                    self.T["attendance"].append(dict(
                        id=aid, class_id=cid, date=d, period=1,
                        student_id=st["id"], status=s, reason=reason,
                        seated_flag=True))

    def build_homework(self):
        today = date.today()
        hid = N_HW
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            for i, (typ, title) in enumerate(HW_TITLES):
                # 最后一条留成「进行中」：学生端才有待交作业、老师端才有待收
                if i == len(HW_TITLES) - 1:
                    pub = (today - timedelta(days=2)).isoformat()
                    due = (today + timedelta(days=5)).isoformat()
                else:
                    off = (len(HW_TITLES) - i) * 7
                    pub = (today - timedelta(days=off)).isoformat()
                    due = (today - timedelta(days=max(1, off - 6))).isoformat()
                hid += 1
                self.T["homeworks"].append(dict(
                    id=hid, class_id=cid, course_id=N_COURSE + 1,
                    subject="信息科技", title=title, type=typ,
                    published=pub, due=due, total=len(self.by_class[cid]),
                    submitted=0, checked=0,
                    note="完成后拍照上传" if typ == "written" else "提交作品文件"))
        for h in self.T["homeworks"]:
            stus = self.by_class[h["class_id"]]
            ongoing = h["due"] >= date.today().isoformat()
            sub = chk = 0
            for st in stus:
                x = self.rnd.random()
                if ongoing:
                    status = ("submitted" if x < 0.68 else
                              "late_submit" if x < 0.72 else "missing")
                else:
                    status = ("checked" if x < 0.72 else
                              "submitted" if x < 0.90 else
                              "late_submit" if x < 0.93 else
                              "correcting" if x < 0.95 else
                              "exempt" if x < 0.96 else "missing")
                if status in ("submitted", "late_submit", "checked"):
                    sub += 1
                if status == "checked":
                    chk += 1
                self.T["homework_records"].append(dict(
                    id=h["id"] * 100000 + st["id"], homework_id=h["id"],
                    student_id=st["id"], status=status, type=h["type"],
                    note="", updated_at=None))
            h["submitted"] = sub
            h["checked"] = chk

    def build_morality(self):
        for i, (nm, sc, cat, color) in enumerate(MORAL_ITEMS):
            self.T["morality_items"].append(dict(
                id=N_MITEM + i + 1, name=nm, score=sc, category=cat,
                enabled=True, color=color, sort_order=i + 1))
        mid = N_MOR
        days = weekdays_back(min(self.days, 10))
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            for st in self.by_class[cid]:
                bias = self.rnd.random() < 0.15
                for d in days:
                    if self.rnd.random() > 0.28:
                        continue
                    penalty = (self.rnd.random() < 0.55) if bias else \
                              (self.rnd.random() < 0.2)
                    pool = [x for x in MORAL_ITEMS
                            if x[2] == ("penalty" if penalty else "bonus")]
                    it = self.rnd.choice(pool)
                    mid += 1
                    self.T["morality_records"].append(dict(
                        id=mid, class_id=cid, date=d, student_id=st["id"],
                        item_id=N_MITEM + MORAL_ITEMS.index(it) + 1,
                        score=it[1], reason=it[0], batch_id=None, source="seed"))

    def build_scores(self):
        today = date.today()
        titles = [("开学摸底", (today - timedelta(days=42)).isoformat()),
                  ("第一次月考", (today - timedelta(days=28)).isoformat()),
                  ("单元测评", (today - timedelta(days=14)).isoformat())]
        scid = N_SCORE
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            ability = {s["id"]: self.rnd.uniform(55, 95)
                       for s in self.by_class[cid]}
            for title, d in titles:
                for st in self.by_class[cid]:
                    v = ability[st["id"]] + self.rnd.uniform(-8, 8)
                    scid += 1
                    self.T["scores"].append(dict(
                        id=scid, class_id=cid, date=d, title=title,
                        full_score=100, student_id=st["id"],
                        score=max(35, min(100, round(v))), note=""))

    def build_exams(self):
        today = date.today()
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            for k, (nm, off, pub) in enumerate([
                    ("第一次月考", 28, True),
                    ("期中模拟", 7, False)]):
                eid = N_EXAM + c * 10 + k + 1
                self.T["exams"].append(dict(
                    id=eid, class_id=cid, name=nm,
                    date=(today - timedelta(days=off)).isoformat(),
                    courses=[N_COURSE + 1, N_COURSE + 2, N_COURSE + 3],
                    published=pub))
                for st in self.by_class[cid]:
                    for cr in (N_COURSE + 1, N_COURSE + 2, N_COURSE + 3):
                        self.T["exam_scores"].append(dict(
                            exam_id=eid, student_id=st["id"], course_id=cr,
                            score=max(30, min(100, round(self.rnd.gauss(76, 12))))))

    def build_groups(self):
        gid = N_GROUP
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            seats = [s for s in self.T["seats"] if s["class_id"] == cid]
            n = min(6, max(1, len(seats) // 6))
            size = max(1, len(seats) // n)
            for g in range(n):
                chunk = seats[g * size:(g + 1) * size]
                roles = {}
                for k, s in enumerate(chunk):
                    roles[str(s["id"])] = "leader" if k == 0 else "member%d" % k
                gid += 1
                self.T["groups"].append(dict(
                    id=gid, class_id=cid, name="第%d组" % (g + 1), mode="seat",
                    seat_ids=[s["id"] for s in chunk],
                    student_ids=[s["student_id"] for s in chunk if s["student_id"]],
                    roles=roles, locked=False))

    def build_duty(self):
        for i, nm in enumerate(DUTY_POSITIONS):
            self.T["duty_positions"].append(dict(
                id=N_POS + i + 1, name=nm, sort_order=i + 1,
                enabled=True, builtin=True))
        daid, did = N_AREA, N_DUTY
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            seats = [s for s in self.T["seats"] if s["class_id"] == cid]
            for i, a in enumerate(DUTY_AREAS):
                daid += 1
                self.T["duty_areas"].append(dict(
                    id=daid, class_id=cid, name=a, sort_order=i + 1, enabled=True))
            for day in range(1, 6):
                for i, a in enumerate(DUTY_AREAS):
                    start = (i * 8 + day) % max(1, len(seats) - 3)
                    chunk = seats[start:start + 4]
                    did += 1
                    self.T["dutys"].append(dict(
                        id=did, class_id=cid, day=day, area=a,
                        seat_ids=[s["id"] for s in chunk],
                        done=(day < date.today().weekday() + 1), skip=False))

    def build_progress(self):
        cols = ["第1课 信息与信息技术", "第2课 计算机系统组成",
                "第3课 网络与互联网", "第4课 数据与编码",
                "第5课 算法与程序", "第6课 图形化编程"]
        for i, nm in enumerate(cols):
            self.T["progress_cols"].append(dict(id=N_COL + i + 1, name=nm,
                                                sort_order=i + 1))
        pid = N_PROG
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            done = 6 - min(4, c)
            for i in range(done):
                pid += 1
                self.T["course_progress"].append(dict(
                    id=pid, class_id=cid, course_id=N_COL + i + 1,
                    content=cols[i].split(" ", 1)[1][:14],
                    date=(date.today() - timedelta(days=(done - i) * 7)).isoformat(),
                    note="", sort_order=i + 1))

    def build_notes_todos_leaves(self):
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            stus = self.by_class[cid]
            if not stus:
                continue
            samples = [
                ("score", "信息思维很好，建议推荐参加区编程赛", False),
                ("score", "数学偏弱，已安排同桌帮扶，关注月考变化", True),
                ("moral", "开学两周讲话频繁，已谈话并联系家长", True),
                ("moral", "劳动积极，提名卫生委员候选人", False),
            ]
            for i, (scope, txt, flag) in enumerate(samples):
                if i >= len(stus):
                    break
                self.T["student_notes"].append(dict(
                    id=N_NOTE + c * 10 + i + 1, student_id=stus[i]["id"],
                    scope=scope, text=txt, flag=flag,
                    date=(date.today() - timedelta(days=3 * i + 2)).isoformat()))
        t0 = date.today()
        self.T["todos"] = [
            dict(id=N_TODO + 1, title="批改演示班级的 Scratch 作品", date=t0.isoformat(),
                 priority="high", category="teach", done=False, note="", subtasks=[]),
            dict(id=N_TODO + 2, title="准备期中模拟讲评", date=t0.isoformat(),
                 priority="high", category="exam", done=False, note="重点讲第 4、5 题",
                 subtasks=[dict(t="统计各题得分率", done=True),
                           dict(t="准备错题投影", done=False)]),
            dict(id=N_TODO + 3, title="机房 3 号机报修跟进",
                 date=(t0 - timedelta(days=1)).isoformat(), priority="mid",
                 category="prepare", done=False, note="", subtasks=[]),
            dict(id=N_TODO + 4, title="更新家长通讯录",
                 date=(t0 + timedelta(days=1)).isoformat(), priority="low",
                 category="class_affairs", done=False, note="两个换号", subtasks=[]),
        ]
        # 请假：每班 1 待批 + 1 已批 + 1 已驳
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            stus = self.by_class[cid]
            if len(stus) < 3:
                continue
            for k, (dt, reason, status) in enumerate([
                    (0, "发烧 38.5℃，上午去医院", "pending"),
                    (1, "家中老人住院，需陪同", "pending"),
                    (-2, "牙齿矫正复诊", "approved")]):
                st = stus[k % len(stus)]
                d = (date.today() + timedelta(days=dt)).isoformat()
                self.T["leave_requests"].append(dict(
                    id="lv%s-%s" % (st["id"], d), student_id=st["id"], date=d,
                    reason=reason, status=status,
                    updated_at=(date.today() - timedelta(days=k)).isoformat() + " 08:00:00",
                    **({"decided_at": date.today().isoformat() + " 09:00:00"}
                       if status != "pending" else {})))

    def build_materials(self):
        """教师下发的学习资料（按班），学生端「资料」页签能看到"""
        t0 = date.today()
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            items = [
                ("第 3 课 · 课堂练习单", "link",
                 "https://docs.qq.com/doc/demo-lesson3", "信息科技",
                 "课堂前 10 分钟完成，做完举手"),
                ("Scratch 素材包（角色/背景）", "file",
                 "/files/demo/scratch-assets.zip", "信息科技",
                 "双击解压后用 Scratch 打开"),
                ("本周知识点小结", "text",
                 "1. 流程图三种结构：顺序、分支、循环\n"
                 "2. 循环必须要有结束条件\n"
                 "3. 变量命名要有意义", "信息科技", "早读时读一遍"),
                ("有理数运算易错点", "link",
                 "https://docs.qq.com/doc/demo-math", "数学",
                 "晚自习前看完，明天小测"),
            ]
            for i, (title, kind, url, subj, note) in enumerate(items):
                self.T["materials"].append(dict(
                    id="mat%d%02d" % (cid, i + 1), class_id=cid,
                    title=title, kind=kind, url=url,
                    content=("" if kind != "text" else url),
                    subject=subj, note=note,
                    date=(t0 - timedelta(days=i)).isoformat(),
                    updated_at=datetime.now().isoformat(timespec="seconds")))

    def build_checkin(self):
        """今天这节课的课堂签到：模拟真实课堂（大部分已签到、少数病假/事假/未签到）"""
        t0 = date.today().isoformat()
        # 用当前时间所在节次（和教师端/服务端同一套算法）
        now = datetime.now()
        cur = now.hour * 60 + now.minute
        seq = 1

        def mins(v):
            try:
                a = str(v or "").split(":")
                return int(a[0]) * 60 + int(a[1] or 0)
            except Exception:
                return -1
        ps = sorted(self.T["periods"], key=lambda p: float(p.get("seq") or 999))
        hit = [p for p in ps if mins(p.get("start_time")) >= 0
               and mins(p.get("end_time")) > mins(p.get("start_time"))
               and mins(p.get("start_time")) <= cur <= mins(p.get("end_time"))]
        if hit:
            seq = int(hit[0].get("seq") or hit[0].get("id") or 1)
        else:
            # 课间 / 放学后：取“已经过去的最近一节”，和教师端、服务端同一套算法
            past = [p for p in ps if 0 <= mins(p.get("start_time")) <= cur]
            if past:
                seq = int(past[-1].get("seq") or past[-1].get("id") or 1)
            elif ps:
                seq = int(ps[0].get("seq") or ps[0].get("id") or 1)
        for c in range(self.n_class):
            cid = N_CLASS + c + 1
            stus = self.by_class[cid]
            for i, st in enumerate(stus):
                mod = i % 10
                if mod in (0, 1, 2, 3, 4, 5, 6):      # 约 70% 已签到
                    status, reason = "checked", ""
                elif mod == 7:                         # 病假（教师登记）
                    status, reason = "sick", "感冒发烧，家长已请假"
                elif mod == 8:                         # 事假（教师登记）
                    status, reason = "personal", "外出参加比赛"
                else:                                  # 未签到（结算后变旷课）
                    continue
                self.T["attendance"].append(dict(
                    id="ck%s-%s-%s" % (st["id"], t0, seq),
                    student_id=st["id"], class_id=cid, date=t0, period=seq,
                    status=status, reason=reason,
                    source=("student" if status == "checked" else "teacher"),
                    checked_at=("%02d:%02d" % (now.hour, max(0, now.minute - i % 12))
                                if status == "checked" else ""),
                    updated_at=datetime.now().isoformat(timespec="seconds")))

    def run(self):
        self.build_people()
        self.build_classes()
        self.build_seats()
        self.build_timetable()
        self.build_attendance()
        self.build_homework()
        self.build_morality()
        self.build_scores()
        self.build_exams()
        self.build_groups()
        self.build_duty()
        self.build_progress()
        self.build_notes_todos_leaves()
        self.build_materials()
        self.build_checkin()
        # updated_at 统一给一个本地时间串：服务端按它判冲突，缺了会反复判脏。
        # 用「当前时刻」而不是固定时间：--clean 会留下墓碑，墓碑的时间戳也是刚才，
        # 固定成早上 08:30 会让紧接着的重灌被判成“旧数据”而整批丢弃。
        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        for tbl, rows in self.T.items():
            if tbl in ("lessons", "exam_scores"):
                continue
            for r in rows:
                if r.get("updated_at") is None:
                    r["updated_at"] = now
        return self.T


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Api(object):
    def __init__(self, base, user, pwd):
        self.base = base.rstrip("/")
        self.user = user
        self.pwd = pwd
        cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(cj))

    def _req(self, path, payload=None, method=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method=method or ("POST" if data else "GET"),
            headers={"Content-Type": "application/json"})
        try:
            with self.op.open(req, timeout=30) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode() or "{}")
            except Exception:
                return {"ok": False, "msg": "HTTP %s" % e.code}

    def login(self):
        st = self._req("/api/v2/state")
        if not st.get("has_password"):
            return self._req("/api/v2/auth/setup",
                             {"username": self.user, "password": self.pwd,
                              "question": "我第一只宠物的名字？", "answer": "旺财"})
        return self._req("/api/v2/auth/teacher",
                         {"username": self.user, "password": self.pwd})

    def push(self, tables, chunk=600):
        total, done = sum(len(v) for v in tables.values()), 0
        for tbl, rows in tables.items():
            if tbl in ("lessons", "exam_scores"):
                continue           # 没有 id 的表也照样能推，只是分批按 id 无所谓
            for i in range(0, len(rows), chunk):
                piece = rows[i:i + chunk]
                r = self._req("/api/v2/sync",
                              {"base_rev": 0,
                               "changes": [{"table": tbl, "rows": piece}]})
                done += len(piece)
                if not r.get("ok"):
                    print("  ! %s 写入失败：%s" % (tbl, r.get("msg")))
        # 无 id 的两张表一次性推
        for tbl in ("lessons", "exam_scores"):
            rows = tables.get(tbl) or []
            for i in range(0, len(rows), chunk):
                self._req("/api/v2/sync",
                          {"base_rev": 0,
                           "changes": [{"table": tbl, "rows": rows[i:i + chunk]}]})
                done += len(rows[i:i + chunk])
        return done, total

    def clean(self):
        """删掉演示数据（按 id 命名空间找）"""
        snap = self._req("/api/v2/sync?since=0&scope=teacher")
        tables = snap.get("tables") or {}
        dels = {}
        for tbl, rows in tables.items():
            keep = []
            for r in rows:
                rid = r.get("id")
                try:
                    n = int(float(rid))
                except Exception:
                    n = -1
                demo = (n >= N_CLASS) or str(rid).startswith(("lv9", "mat9", "ck9"))
                if demo:
                    keep.append({"id": rid, "deleted": 1})
            if keep:
                dels[tbl] = keep
        # 学生删了要级联；服务端 upsert(tombstone) 会带走从表
        total = 0
        for tbl, rows in dels.items():
            for i in range(0, len(rows), 300):
                self._req("/api/v2/sync",
                          {"base_rev": 0, "changes": [{"table": tbl, "rows": rows[i:i + 300]}]})
                total += len(rows[i:i + 300])
        return total, {k: len(v) for k, v in dels.items()}


def main():
    ap = argparse.ArgumentParser(description="生成班级工作台演示数据")
    ap.add_argument("--base", default=DEFAULT_BASE, help="服务地址")
    ap.add_argument("--user", default="teacher")
    ap.add_argument("--pass", dest="pwd", default="class2026")
    ap.add_argument("--classes", type=int, default=3, help="班级数（1-10）")
    ap.add_argument("--students", type=int, default=40, help="每班人数（1-60）")
    ap.add_argument("--days", type=int, default=10, help="考勤/德育回看天数")
    ap.add_argument("--prefix", default="", help="班级名前缀，例如「演示-」")
    ap.add_argument("--clean", action="store_true", help="删除已有演示数据")
    ap.add_argument("--json", default="", help="只导出到 JSON 文件，不写服务端")
    args = ap.parse_args()

    T = Gen(args.classes, args.students, args.days, prefix=args.prefix).run()
    counts = {k: len(v) for k, v in T.items() if v}
    total = sum(counts.values())
    print("生成：%d 行" % total)
    print("  " + "  ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"tables": T}, f, ensure_ascii=False)
        print("已写入 %s（可用「设置 → 从备份恢复」导入）" % args.json)
        return

    api = Api(args.base, args.user, args.pwd)
    j = api.login()
    if not j.get("ok"):
        print("登录失败：%s" % (j.get("msg") or j))
        print("  密码不对就加 --pass 你的密码；首次使用会按你给的密码创建账号。")
        sys.exit(1)
    if args.clean:
        n, detail = api.clean()
        print("已删除演示数据 %d 行：%s" % (n, detail))
        return
    done, want = api.push(T)
    print("写入服务端：%d / %d 行" % (done, want))
    print("完成。刷新教师端即可看到 %d 个演示班级。" % args.classes)


if __name__ == "__main__":
    main()
