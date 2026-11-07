#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 整份试卷导入解析（2.0.38+）

目标：老师把一份现成试卷（.txt / .md / .json，或直接粘贴）一次导入，
自动解析出全部题目，不用逐题手工添加。

支持的「标准试卷格式」（界面里可一键填入示例，问号气泡里有完整说明）
-------------------------------------------------------------------
    # 试卷名称：七年级信息科技第一单元检测
    # 课程：信息科技

    一、单选题（每题 5 分）
    1. 下列设备中属于输入设备的是（  ）
    A. 键盘
    B. 显示器
    C. 音箱
    D. 打印机
    答案：A

    2. [多选] 下列属于输出设备的是（5分）
    A. 显示器  ![选项图](data:image/png;base64,...)
    B. 音箱
    答案：A,B

    二、填空题（每题 5 分）
    3. 我国的首都是____。
    答案：北京|北平

    三、判断题（每题 5 分）
    4. 二进制只有 0 和 1。
    答案：对

    四、连线题（每题 5 分）
    5. 把国家与首都连起来
    中国=北京
    日本=东京

    五、简答题（每题 10 分）
    6. 说说算法在生活中的一个例子。
    答案：言之有理即可

    六、图片上传题（每题 5 分）
    7. 上传你的作品截图。

规则：
  · 题号 1. / 1、/ 1) / 第1题 都认；
  · 题型可写在题目里 [单选]，也可由「一、单选题（每题 N 分）」分节统一指定；
  · 分值写（5分）/ [5分] / 5分，分节里带「每题 N 分」则作为默认；
  · 选项 A. / A、/ A） / A: 都认；答案多选用 A,C 或 AC；
  · 填空允许多个等价答案用 | 分隔；判断题对/错/√/× 都认；
  · 连线题用「左=右」每行一对，答案自动由配对生成；
  · 题干与选项都能带图片：Markdown ![说明](URL 或 data:image/...;base64,...)；
    写在题干行或紧随的独立行 → 题干图片；写在选项行 → 该选项图片。

也支持直接导入 JSON：
  · 本工作台导出的试卷 {title, course, questions:[{id,type,title,score,options,answer,...}]}
  · 旧随堂测格式 {quiz:{intro, questions:[{q, options, answer, score}]}}
"""

import json
import re

TYPE_ALIAS = {
    "单选": "single", "单选题": "single", "单项选择": "single", "single": "single",
    "多选": "multiple", "多选题": "multiple", "多项选择": "multiple", "multiple": "multiple",
    "填空": "blank", "填空题": "blank", "blank": "blank",
    "判断": "judge", "判断题": "judge", "judge": "judge",
    "连线": "match", "连线题": "match", "match": "match",
    "简答": "essay", "简答题": "essay", "essay": "essay",
    "图片": "image", "图片题": "image", "图片上传": "image",
    "图片上传题": "image", "image": "image",
}
TYPE_CN = {"single": "单选题", "multiple": "多选题", "blank": "填空题",
           "judge": "判断题", "match": "连线题", "essay": "简答题", "image": "图片上传"}

# 分节标题：一、单选题（每题 5 分） / 【多选题】 / 二、填空题
_SECTION = re.compile(
    r"^\s*(?:[一二三四五六七八九十]+[、.．]\s*|[（(]?[一二三四五六七八九十]+[)）]\s*)?"
    r"[【\[]?\s*(单选|单选题|单项选择|多选|多选题|多项选择|填空|填空题|判断|判断题|"
    r"连线|连线题|简答|简答题|图片|图片题|图片上传|图片上传题|single|multiple|blank|judge|match|essay|image)"
    r"\s*[】\]]?\s*(?:题)?\s*(?:[（(]\s*每题\s*(\d+(?:\.\d+)?)\s*分\s*[)）])?\s*$")
# 题目起始行：1. / 1、/ 1) / 第1题 / (1)
_QSTART = re.compile(r"^\s*(?:第\s*(\d+)\s*题|[（(]?(\d+)[)）]?[.、．:：])\s*(.*)$")
# 题型内联标记：[单选] / 【多选】
_INLINE_TYPE = re.compile(r"^\s*[【\[]\s*([^\]】]{1,8})\s*[】\]]\s*")
# 分值：（5分）/ [5分] / 5分
_SCORE = re.compile(r"[（(\[]\s*(\d+(?:\.\d+)?)\s*分\s*[)）\]]|(\d+(?:\.\d+)?)\s*分")
# 选项：A. / A、/ A）/ A:
_OPT = re.compile(r"^\s*([A-Za-z])\s*[.、．)）：:]\s*(.*)$")
# 答案行
_ANS = re.compile(r"^\s*(?:参考)?答案\s*[:：]\s*(.*)$|^\s*[【\[]\s*答案\s*[】\]]\s*(.*)$")
# 解析行（2.0.38+）：答案解析，只有老师开放时才随答案一起给学生看
_ANALYSIS = re.compile(r"^\s*(?:答案)?解析\s*[:：]\s*(.*)$")
# 图片：![...](...)
_IMG = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# 元信息
_META = re.compile(r"^\s*#\s*(试卷名称|课程|说明|标题)\s*[:：]\s*(.*)$")


def _mk_id(n):
    return "q%d_%s" % (n, "imp")


def _clean_title(s, score_text):
    s = (s or "").strip()
    if score_text:
        s = s.replace(score_text, " ")
    s = _IMG.sub(" ", s)
    return " ".join(s.split()).strip()


def _score_of(line, default):
    m = _SCORE.search(line or "")
    if not m:
        return default
    try:
        return float(m.group(1) or m.group(2))
    except Exception:
        return default


def parse_paper_text(text, default_title="导入试卷"):
    """解析整份试卷文本。返回 {ok, paper, warnings, errors}。"""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return {"ok": False, "errors": ["试卷内容是空的"], "warnings": [], "paper": None}

    # ---- JSON 直通 ----
    if text.lstrip().startswith("{"):
        try:
            d = json.loads(text)
        except Exception as e:
            return {"ok": False, "errors": ["JSON 解析失败：%s" % e], "warnings": [], "paper": None}
        return _from_json(d, default_title)

    meta = {"title": default_title, "course": "", "desc": ""}
    lines, warns = [], []
    for ln in text.split("\n"):
        m = _META.match(ln)
        if m:
            k, v = m.group(1), m.group(2).strip()
            if k in ("试卷名称", "标题"):
                meta["title"] = v or default_title
            elif k == "课程":
                meta["course"] = v
            elif k == "说明":
                meta["desc"] = v
            continue
        lines.append(ln)

    questions, cur, sec_type, sec_score = [], None, "", 5.0

    def flush():
        if cur is None:
            return
        q = cur
        if not q["title"] and not q.get("title_image"):
            warns.append("第 %d 题没有题干，已跳过" % q["_n"])
            return
        t = q["type"]
        if t in ("single", "multiple"):
            if not q["options"]:
                warns.append("第 %d 题是%s但没有选项，已改为填空题" % (q["_n"], TYPE_CN[t]))
                q["type"] = "blank"
            elif not q.get("answer"):
                warns.append("第 %d 题缺答案，自动判分时会算 0 分" % q["_n"])
        elif t in ("blank", "judge", "essay") and not q.get("answer"):
            warns.append("第 %d 题缺答案" % q["_n"])
        elif t == "match" and q.get("pairs"):
            q["answer"] = [[p["l"], p["r"]] for p in q["pairs"]]
        q.pop("_n", None)
        questions.append(q)

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue

        # 分节：切换题型 / 默认分值
        sm = _SECTION.match(line)
        if sm and not _QSTART.match(line):
            key = sm.group(1).strip()
            sec_type = TYPE_ALIAS.get(key, sec_type)
            if sm.group(2):
                try:
                    sec_score = float(sm.group(2))
                except Exception:
                    pass
            continue

        # 解析行（要放在答案行之前判断：`答案解析：` 不会被答案正则吃掉）
        an = _ANALYSIS.match(line)
        if an and cur is not None:
            cur["analysis"] = an.group(1).strip()
            continue

        # 答案行
        am = _ANS.match(line)
        if am and cur is not None:
            val = (am.group(1) or am.group(2) or "").strip()
            cur["answer"] = val
            if cur["type"] == "blank" and "|" in val:
                parts = [x.strip() for x in val.split("|") if x.strip()]
                cur["answer"] = parts[0] if parts else ""
                cur["accept"] = parts[1:]
            if cur["type"] == "multiple":
                cur["answer"] = [x.strip() for x in re.split(r"[,，、\s]+", val) if x.strip()]
            continue

        # 选项行
        om = _OPT.match(_IMG.sub("", line))
        if om and cur is not None and cur["type"] in ("single", "multiple", "judge"):
            # 判断题的选项固定是「对 / 错」，学生端也是这么渲染的；
            # 试卷里就算写了 A/B 选项也不追加，否则编辑器里会出现四个选项。
            if cur["type"] == "judge":
                continue
            key = om.group(1).upper()
            txt = _clean_title(om.group(2), "")
            im = _IMG.search(line)
            opt = {"key": key, "text": txt}
            if im:
                opt["image"] = im.group(1).strip()
            cur["options"].append(opt)
            continue

        # 连线配对行：左=右
        if cur is not None and cur["type"] == "match" and re.match(r"^\s*[^=]+=[^=]+\s*$", line):
            l, r = line.split("=", 1)
            if l.strip() and r.strip():
                cur.setdefault("pairs", []).append({"l": l.strip(), "r": r.strip()})
            continue

        # 纯图片行（题干图）
        if cur is not None and _IMG.match(line.strip()) and not _QSTART.match(line):
            cur["title_image"] = _IMG.search(line).group(1).strip()
            continue

        # 新题目
        qm = _QSTART.match(line)
        if qm:
            flush()
            n = int(qm.group(1) or qm.group(2) or (len(questions) + 1))
            rest = qm.group(3) or ""
            t, sc = sec_type, sec_score
            im = _INLINE_TYPE.match(rest)
            if im:
                alias = im.group(1).strip()
                if alias in TYPE_ALIAS:
                    t = TYPE_ALIAS[alias]
                    rest = rest[im.end():]
            title_score_text = _SCORE.search(rest)
            sc = _score_of(rest, sc)
            title = _clean_title(rest, title_score_text.group(0) if title_score_text else "")
            cur = {"id": _mk_id(n), "_n": n, "type": t or "single",
                   "title": title, "score": sc, "options": [], "pairs": [],
                   "answer": "", "accept": [], "hint": "", "analysis": ""}
            if not cur["type"]:
                cur["type"] = "single"
            if t == "judge":
                cur["options"] = [{"key": "A", "text": "对"}, {"key": "B", "text": "错"}]
                cur["judge"] = True
            title_img = _IMG.search(line)
            if title_img:
                cur["title_image"] = title_img.group(1).strip()
            continue

        # 题干续行 / 选项内图片补充
        if cur is not None:
            if not cur["title"] and not line.startswith(("A", "B", "C", "D")):
                cur["title"] = _clean_title(line, "")
            elif cur["options"] and _IMG.search(line):
                im = _IMG.search(line)
                cur["options"][-1]["image"] = im.group(1).strip()

    flush()
    if not questions:
        return {"ok": False, "errors": ["没解析出任何题目，请检查题号格式（如 1. 题干）"],
                "warnings": warns, "paper": None}

    # 判断题的答案统一成「对 / 错」
    for q in questions:
        if q.get("judge"):
            a = str(q.get("answer") or "").strip()
            low = a.lower()
            if low in ("a", "√", "t", "true", "yes", "1"):
                a = "对" if a not in ("A", "a") else "A"
            elif low in ("b", "×", "x", "f", "false", "no", "0"):
                a = "错" if a not in ("B", "b") else "B"
            q["answer"] = a
            q.pop("judge", None)
        # 图片上传 / 简答不给分值外答案
        if q["type"] == "image":
            q["answer"] = ""

    paper = {"title": meta["title"], "course": meta["course"], "desc": meta["desc"],
             "questions": questions}
    return {"ok": True, "paper": paper, "warnings": warns, "errors": []}


def _from_json(d, default_title):
    """兼容工作台导出的试卷 JSON 与旧随堂测配置。"""
    warns = []
    if not isinstance(d, dict):
        return {"ok": False, "errors": ["JSON 根节点不是对象"], "warnings": [], "paper": None}
    src = d.get("quiz") if isinstance(d.get("quiz"), dict) else d
    qs = src.get("questions") or []
    if not isinstance(qs, list) or not qs:
        return {"ok": False, "errors": ["JSON 里没有 questions 题目数组"], "warnings": [], "paper": None}
    out, n = [], 0
    for raw in qs:
        if not isinstance(raw, dict):
            continue
        n += 1
        q = dict(raw)
        q.setdefault("id", _mk_id(n))
        q.setdefault("type", "single")
        if q["type"] not in TYPE_CN:
            q["type"] = "single"
        if "title" not in q and "q" in q:
            q["title"] = q.get("q") or ""
        q.setdefault("title", "")
        q.setdefault("score", 5)
        opts = []
        for o in (q.get("options") or []):
            if isinstance(o, dict):
                opts.append({"key": str(o.get("key") or ""), "text": str(o.get("text") or ""),
                             **({"image": o["image"]} if o.get("image") else {})})
            else:
                opts.append({"key": chr(64 + len(opts) + 1), "text": str(o)})
        q["options"] = opts
        q.setdefault("pairs", [])
        q.setdefault("answer", "")
        q.setdefault("accept", [])
        q.setdefault("hint", "")
        out.append(q)
    if not out:
        return {"ok": False, "errors": ["JSON 里没有有效题目"], "warnings": [], "paper": None}
    paper = {"title": str(d.get("title") or src.get("title") or default_title),
             "course": str(d.get("course") or ""),
             "desc": str(d.get("desc") or src.get("intro") or ""),
             "questions": out}
    return {"ok": True, "paper": paper, "warnings": warns, "errors": []}


# ---------------------------------------------------------------------------
# 表格型试卷：Word 表格 / Excel 工作表统一按这套表头解析
# ---------------------------------------------------------------------------
TABLE_ALIAS = {
    "题号": "no", "序号": "no", "no": "no", "number": "no", "num": "no",
    "题型": "type", "类型": "type", "type": "type",
    "题干": "title", "题目": "title", "试题": "title", "title": "title",
    "question": "title", "题干内容": "title",
    "答案": "answer", "正确答案": "answer", "参考答案": "answer", "answer": "answer",
    "分值": "score", "分数": "score", "score": "score", "points": "score",
    "解析": "analysis", "答案解析": "analysis", "试题解析": "analysis",
    "analysis": "analysis",
    "选项": "options", "全部选项": "options", "options": "options",
    "图片": "image", "题干图片": "image", "image": "image",
}
_OPT_HEAD = re.compile(r"^选项\s*([a-z])$")
_OPT_INLINE = re.compile(r"(?:^|[\s;；])([A-Za-z])[.、．)）:：]\s*")
_JUDGE_TRUE = ("对", "正确", "√", "✓", "t", "true", "y", "yes", "1", "a")


def _norm_head(s):
    s = str(s or "").strip().lower()
    for a, b in (("（", "("), ("）", ")"), ("：", ":"), ("　", ""), (" ", ""),
                 ("\t", ""), ("\n", "")):
        s = s.replace(a, b)
    return s


def _maybe_img(v):
    """单元格值 → (文字, 图片URL)。支持 Markdown 图片 / dataURL / 图片链接。"""
    s = str(v or "").strip()
    if not s:
        return "", ""
    m = _IMG.search(s)
    if m:
        return _IMG.sub(" ", s).strip(), m.group(1).strip()
    if re.match(r"^data:image/", s, re.I):
        return "", s
    if re.match(r"^https?://\S+\.(png|jpe?g|gif|webp|bmp|svg)(\?\S*)?$", s, re.I):
        return "", s
    return s, ""


def _judge_norm(s):
    v = str(s or "").strip()
    low = v.lower()
    if low in _JUDGE_TRUE:
        return "对"
    return "错"


def _split_options(text):
    """把一格里的全部选项拆开。支持 `A. xx B. yy` 一行写、也支持每行一个。"""
    t = str(text or "").strip()
    if not t:
        return []
    marks = list(_OPT_INLINE.finditer(t))
    if not marks:
        lines = [x.strip() for x in re.split(r"[\r\n]+", t) if x.strip()]
        return [{"key": chr(65 + i), "text": x} for i, x in enumerate(lines)]
    out = []
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(t)
        val = t[start:end].strip().strip(";；")
        if val:
            out.append({"key": m.group(1).upper(), "text": val})
    return out


def _pick_question_table(tables):
    """在 Word 的多个表里挑最像「题目表」的那个（表头命中列数最多）。"""
    best, best_n = None, 0
    for t in (tables or []):
        if not t:
            continue
        head = [_norm_head(x) for x in (t[0] or [])]
        n = sum(1 for h in head if h in TABLE_ALIAS or _OPT_HEAD.match(h))
        if n > best_n:
            best, best_n = t, n
    return best if best_n >= 2 else None


def parse_table_rows(rows, default_title="导入试卷"):
    """表格型试卷（Excel 一行一题 / Word 表格）→ 与文本格式同结构的 paper。

    标准表头（中英文都认，顺序随意，缺列自动跳过）：
      题号 | 题型 | 题干 | 选项A..F | 选项 | 答案 | 分值 | 解析 | 题干图片
    · 没有「选项A/B」列时，可在「选项」一格里用 A. xx B. yy 全部写完；
    · 连线题在「选项」或「答案」里每行写 `左=右`；
    · 判断题答案 对/错/√/×/A/B 都认。
    """
    raw_rows = [[str(c if c is not None else "").strip() for c in r]
                for r in (rows or [])]
    rows2 = [r for r in raw_rows if any(r)]
    if not rows2:
        return {"ok": False, "errors": ["表格是空的"], "warnings": [], "paper": None}

    hi, head = -1, None
    for i, r in enumerate(rows2[:8]):
        cells = [_norm_head(x) for x in r]
        hits = sum(1 for h in cells if h in TABLE_ALIAS or _OPT_HEAD.match(h))
        if hits >= 2 and any(h in ("题干", "题目", "试题", "题型") for h in cells):
            hi, head = i, r
            break
    if hi < 0:
        return {"ok": False, "warnings": [],
                "errors": ["没找到表头行：请让第一行包含「题型」「题干」「答案」等列名"
                           "（详见导入弹窗里的模板说明）"],
                "paper": None}

    colmap, optcols = {}, {}
    for ci, h in enumerate(head):
        n = _norm_head(h)
        if not n:
            continue
        if n in TABLE_ALIAS:
            colmap.setdefault(TABLE_ALIAS[n], ci)
            continue
        m = _OPT_HEAD.match(n)
        if m:
            optcols[m.group(1)] = ci
        elif len(n) == 1 and n.isalpha():
            optcols[n] = ci

    questions, warns = [], []
    for ri in range(hi + 1, len(rows2)):
        r = rows2[ri]
        cell = lambda k: (r[colmap[k]] if k in colmap and colmap[k] < len(r) else "")
        title_raw = cell("title")
        type_raw = cell("type")
        if not title_raw and not type_raw:
            continue

        t = TYPE_ALIAS.get(_norm_head(type_raw), "")
        if not t:
            t = TYPE_ALIAS.get(str(type_raw).strip(), "")
        opts = []
        for k in sorted(optcols):
            txt, img = _maybe_img(r[optcols[k]] if optcols[k] < len(r) else "")
            if txt or img:
                one = {"key": k.upper(), "text": txt}
                if img:
                    one["image"] = img
                opts.append(one)
        all_opts_raw = cell("options")
        if not opts and all_opts_raw:
            if "=" in all_opts_raw and not re.search(r"[A-Za-z][.、．)）:：]", all_opts_raw):
                t = t or "match"
            else:
                for o in _split_options(all_opts_raw):
                    txt, img = _maybe_img(o["text"])
                    one = {"key": o["key"], "text": txt}
                    if img:
                        one["image"] = img
                    opts.append(one)
        if not t:
            t = "single" if opts else "essay"

        title, title_img = _maybe_img(title_raw)
        if not title and not title_img:
            continue
        extra_img = cell("image").strip()
        if extra_img and not title_img:
            _, title_img = _maybe_img(extra_img)

        ans_raw = cell("answer").strip()
        answer, accept, pairs = "", [], []
        if t == "multiple":
            answer = [x.strip() for x in re.split(r"[,，、/\s]+", ans_raw) if x.strip()]
        elif t == "blank":
            parts = [x.strip() for x in ans_raw.split("|") if x.strip()]
            answer = parts[0] if parts else ""
            accept = parts[1:]
        elif t == "judge":
            answer = _judge_norm(ans_raw)
        elif t == "match":
            # 配对可能写在「选项」单列、答案列，或「选项A/选项B」各自的格里
            src = all_opts_raw or ans_raw or "\n".join(str(o.get("text") or "") for o in opts)
            for ln in re.split(r"[\r\n;；]+", src):
                if "=" in ln:
                    l, rr = ln.split("=", 1)
                    if l.strip() and rr.strip():
                        pairs.append({"l": l.strip(), "r": rr.strip()})
            answer = [[p["l"], p["r"]] for p in pairs]
            opts = []            # 连线题不用普通选项，学生端按左项 + 右项候选渲染
        elif t == "image":
            answer = ""
        else:
            answer = ans_raw

        try:
            sc = float(re.sub(r"[^\d.]", "", cell("score")) or 5)
        except Exception:
            sc = 5.0
        q = {"id": _mk_id(len(questions) + 1), "type": t, "title": title,
             "score": max(0.0, sc), "options": opts, "pairs": pairs,
             "answer": answer, "accept": accept, "hint": "",
             "analysis": cell("analysis").strip()}
        if title_img:
            q["title_image"] = title_img
        if t in ("single", "multiple") and not opts:
            warns.append("第 %d 行是%s但没有选项，已改为填空题" % (ri - hi, TYPE_CN.get(t, t)))
            q["type"] = t = "blank"
        if t in ("single", "multiple", "blank", "judge") and not answer:
            warns.append("第 %d 行缺答案，自动判分时会算 0 分" % (ri - hi))
        questions.append(q)

    if not questions:
        return {"ok": False, "errors": ["表格里没读到题目行"], "warnings": warns, "paper": None}
    title = default_title
    for r in rows2[:hi]:
        for c in r:
            if c and len(c) > 2:
                title = c
                break
        if title != default_title:
            break
    return {"ok": True, "warnings": warns,
            "paper": {"title": title, "course": "", "desc": "", "questions": questions}}


def parse_office_blob(filename, blob, default_title="导入试卷"):
    """Office 文件 → 与 parse_paper_text 同结构的结果（含 degraded 标记）。

    .docx 优先按表格解析（一行一题最稳），没有题目表才退回段落文本；
    .xlsx 读第一个工作表；.doc 走纯文本兜底；.xls 直接给出另存指引。
    """
    from office_import import read_office
    r = read_office(filename, blob)
    if not r.get("ok"):
        return {"ok": False, "errors": [r.get("msg") or "文件读取失败"],
                "warnings": [], "paper": None, "kind": r.get("kind"),
                "degraded": bool(r.get("degraded"))}
    kind = r.get("kind")
    res = None
    if kind in ("xlsx", "xls"):
        res = parse_table_rows(r.get("rows") or [], default_title)
    elif kind == "docx":
        tbl = _pick_question_table(r.get("tables") or [])
        if tbl:
            res = parse_table_rows(tbl, default_title)
        if not res or not res.get("ok"):
            res = parse_paper_text(r.get("text") or "", default_title)
    elif kind == "doc":
        res = parse_paper_text(r.get("text") or "", default_title)
    if res is None:
        res = {"ok": False, "errors": ["这个文件里没有可解析的试卷内容"],
               "warnings": [], "paper": None}
    res["kind"] = kind
    res["degraded"] = bool(r.get("degraded"))
    if r.get("degraded") and res.get("ok"):
        res.setdefault("warnings", []).append(
            "老版格式已按纯文本提取，复杂排版与图片可能丢失；建议另存为 .docx / .xlsx")
    return res


SAMPLE = """# 试卷名称：七年级信息科技第一单元检测
# 课程：信息科技

一、单选题（每题 5 分）
1. 下列设备中，属于输入设备的是（  ）
A. 键盘
B. 显示器
C. 音箱
D. 打印机
答案：A

2. 计算机中负责运算与控制的核心部件是（  ）
A. 内存
B. CPU
C. 硬盘
D. 显示器
答案：B

二、多选题（每题 5 分）
3. 下列属于输出设备的是（  ）
A. 显示器
B. 键盘
C. 打印机
D. 鼠标
答案：A,C

三、填空题（每题 5 分）
4. 计算机内部采用____进制表示数据。
答案：二|二进制

四、判断题（每题 5 分）
5. 二进制只有 0 和 1 两个数字。
答案：对

五、连线题（每题 5 分）
6. 请把设备与用途连起来
键盘=输入文字
显示器=显示画面

六、简答题（每题 10 分）
7. 举一个生活中用到算法的例子。
答案：言之有理即可

七、图片上传题（每题 5 分）
8. 上传你本课作品的截图。
"""
