#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · Office 素材读取（.docx / .xlsx，纯标准库）

为什么自己写而不是加 python-docx / openpyxl：
  这套工作台是「解压即用 + Docker 无外网」的，运行环境只保证 Python 标准库。
  .docx / .xlsx 本质都是 ZIP + XML，标准库 zipfile + ElementTree 足够读出
  段落、表格、内嵌图片和单元格，不必引入第三方依赖。

能力边界（界面上要如实告诉老师）：
  · .docx —— 读段落、表格、表格单元格里的内嵌图片（转 dataURL）；
  · .xlsx —— 读第一个工作表，共享字符串 / 行内字符串 / 数字 / 布尔；
             单元格内嵌图片（drawing）不取，图片请写在单元格里当链接；
  · .doc / .xls —— 老版二进制（OLE / BIFF），纯标准库无法可靠还原表格，
             只做「纯文本兜底提取」，并在结果里标记 degraded=True，
             由前端提示「建议另存为 .docx / .xlsx 后重新导入」。
"""

import base64
import io
import re
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

_MIME = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
         "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
         "svg": "image/svg+xml", "emf": "image/emf", "wmf": "image/wmf"}


def _mime_of(path):
    ext = str(path or "").rsplit(".", 1)[-1].lower()
    return _MIME.get(ext, "image/png")


def _rels(z, name):
    """读 <x>_rels/<y>.rels → {rId: target}。缺文件就当没有关系。"""
    try:
        root = ET.fromstring(z.read(name))
    except Exception:
        return {}
    out = {}
    for rel in root:
        rid, tgt = rel.get("Id"), rel.get("Target")
        if rid and tgt:
            out[rid] = tgt
    return out


def _docx_image(z, target):
    """内嵌图片 → dataURL；找不到就返回空串（不抛异常）。"""
    path = str(target or "").lstrip("/")
    if not path:
        return ""
    for cand in (path, "word/" + path, "word/media/" + path.rsplit("/", 1)[-1]):
        try:
            blob = z.read(cand)
        except Exception:
            continue
        if not blob:
            continue
        return "data:%s;base64,%s" % (_mime_of(cand),
                                      base64.b64encode(blob).decode("ascii"))
    return ""


def _para_text(p, z, rels):
    """段落 → (纯文本, [图片 dataURL])。图片按出现顺序单独收集。"""
    parts, imgs = [], []
    for node in p.iter():
        if node.tag == W + "t":
            parts.append(node.text or "")
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag == W + "br":
            parts.append("\n")
        elif node.tag == A + "blip":
            rid = node.get(R + "embed")
            url = _docx_image(z, rels.get(rid) or "") if rid else ""
            if url:
                imgs.append(url)
    return "".join(parts), imgs


def read_docx(blob):
    """解析 .docx → {ok, paragraphs, tables, text, degraded, images}"""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except Exception as e:
        return {"ok": False, "msg": "不是有效的 .docx 文件（%s）" % e}
    try:
        xml = z.read("word/document.xml")
    except Exception:
        return {"ok": False, "msg": "这个 .docx 里没有正文（word/document.xml）"}
    rels = _rels(z, "word/_rels/document.xml.rels")
    try:
        root = ET.fromstring(xml)
    except Exception as e:
        return {"ok": False, "msg": "Word 正文解析失败（%s）" % e}
    body = root.find(W + "body")
    if body is None:
        return {"ok": False, "msg": "Word 正文为空"}

    paragraphs, tables = [], []

    def cell_text(tc):
        chunks = []
        for p in tc.findall(W + "p"):
            t, imgs = _para_text(p, z, rels)
            s = t.strip()
            for u in imgs:
                s = (s + "\n" if s else "") + "![图](%s)" % u
            if s:
                chunks.append(s)
        # 单元格里可能还有嵌套表格：按行拼成文本
        for sub in tc.findall(W + "tbl"):
            for tr in sub.findall(W + "tr"):
                cells = [cell_text(tc2) for tc2 in tr.findall(W + "tc")]
                if any(c.strip() for c in cells):
                    chunks.append(" | ".join(cells))
        return "\n".join(chunks)

    def walk(container):
        """按文档顺序收段落与表格；Word 常用 sdt（内容控件）包一层，也要进去。"""
        for child in list(container):
            if child.tag == W + "p":
                t, imgs = _para_text(child, z, rels)
                s = t.strip()
                for u in imgs:
                    s = (s + "\n" if s else "") + "![图](%s)" % u
                paragraphs.append(s)
            elif child.tag == W + "tbl":
                rows = []
                for tr in child.findall(W + "tr"):
                    rows.append([cell_text(tc) for tc in tr.findall(W + "tc")])
                if rows:
                    tables.append(rows)
            elif child.tag == W + "sdt":
                sc = child.find(W + "sdtContent")
                if sc is not None:
                    walk(sc)

    walk(body)

    text = "\n".join(p for p in paragraphs if p)
    return {"ok": True, "paragraphs": paragraphs, "tables": tables,
            "text": text, "degraded": False}


def _col_index(ref):
    """A1 → 0；AA12 → 26。"""
    m = re.match(r"^([A-Z]+)(\d+)$", str(ref or "").upper())
    if not m:
        return None, None
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col - 1


def read_xlsx(blob):
    """解析 .xlsx 第一个工作表 → {ok, rows, degraded}"""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except Exception as e:
        return {"ok": False, "msg": "不是有效的 .xlsx 文件（%s）" % e}
    names = set(z.namelist())
    if "xl/workbook.xml" not in names:
        return {"ok": False, "msg": "这个 .xlsx 里没有工作簿（xl/workbook.xml）"}

    shared = []
    if "xl/sharedStrings.xml" in names:
        try:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(S + "si"):
                shared.append("".join(t.text or "" for t in si.iter(S + "t")))
        except Exception:
            shared = []

    # 第一个工作表
    try:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = wb.find(S + "sheets")
        first = sheets.findall(S + "sheet")[0] if sheets is not None else None
        rid = first.get(R + "id") if first is not None else ""
        sheet_name = (first.get("name") if first is not None else "") or "Sheet1"
    except Exception as e:
        return {"ok": False, "msg": "工作簿结构解析失败（%s）" % e}
    rels = _rels(z, "xl/_rels/workbook.xml.rels")
    target = rels.get(rid or "") or "worksheets/sheet1.xml"
    path = target.lstrip("/")
    if not path.startswith("xl/"):
        path = "xl/" + path
    try:
        sheet = ET.fromstring(z.read(path))
    except Exception:
        # 兜底：直接拿第一个 worksheet-*.xml
        cands = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))
        if not cands:
            return {"ok": False, "msg": "找不到工作表数据"}
        try:
            sheet = ET.fromstring(z.read(cands[0]))
        except Exception as e:
            return {"ok": False, "msg": "工作表解析失败（%s）" % e}

    grid = {}
    max_r = max_c = -1
    for c in sheet.iter(S + "c"):
        r, ci = _col_index(c.get("r"))
        if r is None:
            continue
        t = c.get("t")
        val = ""
        v = c.find(S + "v")
        if t == "s" and v is not None:
            try:
                val = shared[int(v.text)]
            except Exception:
                val = ""
        elif t == "inlineStr":
            val = "".join(x.text or "" for x in c.iter(S + "t"))
        elif v is not None:
            val = v.text or ""
        elif c.find(S + "is") is not None:
            val = "".join(x.text or "" for x in c.iter(S + "t"))
        grid[(r, ci)] = val
        max_r, max_c = max(max_r, r), max(max_c, ci)

    # 合并单元格：把左上角的值铺到整个区域（老师常把"题干"跨列合并）
    for mc in sheet.iter(S + "mergeCell"):
        ref = mc.get("ref") or ""
        if ":" not in ref:
            continue
        a, b = ref.split(":", 1)
        r1, c1 = _col_index(a)
        r2, c2 = _col_index(b)
        if None in (r1, c1, r2, c2):
            continue
        val = grid.get((r1, c1), "")
        for rr in range(r1, r2 + 1):
            for cc in range(c1, c2 + 1):
                grid.setdefault((rr, cc), val)

    rows = []
    for r in range(1, max_r + 1):
        row = [grid.get((r, c), "") for c in range(0, max_c + 1)]
        if any(str(x).strip() for x in row):
            rows.append([str(x).strip() for x in row])
    if not rows:
        return {"ok": False, "msg": "第一个工作表是空的"}
    return {"ok": True, "rows": rows, "sheet": sheet_name,
            "degraded": False, "header_hint": rows[0]}


def extract_doc_text(blob):
    """.doc（OLE 二进制）纯文本兜底：扫 UTF-16LE 里的可读段。

    不保证顺序与排版，只把明显的中英文正文捞出来；解析结果会标记 degraded，
    前端据此提示「老格式建议另存为 .docx」。
    """
    try:
        txt = blob.decode("utf-16-le", errors="ignore")
    except Exception:
        return {"ok": False, "msg": ".doc 读取失败"}
    chunks = re.findall(r"[\u4e00-\u9fa5\u3000-\u303f\uff00-\uffef"
                        r"A-Za-z0-9，。、；：？！（）【】《》%．.,:;!?()\[\]/\\+\-=\s]{6,}", txt)
    # 去掉 Word 的样式名 / 域代码残渣
    bad = re.compile(r"(Normal|Heading|Default|Times New Roman|Arial|Calibri|"
                     r"Microsoft|YH|SimSun|MS Gothic|Table|Char|AutoFormat)", re.I)
    keep = []
    for c in chunks:
        s = " ".join(c.split())
        if len(s) < 6 or bad.search(s):
            continue
        keep.append(s)
    text = "\n".join(keep)
    return {"ok": bool(text), "text": text, "degraded": True,
            "msg": "" if text else ".doc 里没提取到可读文本，请另存为 .docx 后重试"}


def read_office(filename, blob):
    """按扩展名分发。返回统一结构：

    {ok, kind: docx|xlsx|doc|xls, text?, rows?, tables?, paragraphs?,
     degraded, msg?}
    """
    name = str(filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext == "docx":
        r = read_docx(blob)
        r["kind"] = "docx"
        return r
    if ext == "xlsx":
        r = read_xlsx(blob)
        r["kind"] = "xlsx"
        return r
    if ext == "doc":
        r = extract_doc_text(blob)
        r["kind"] = "doc"
        return r
    if ext == "xls":
        # BIFF8：自己按 OLE2 + 记录流读（core/xls_import.py），纯标准库
        from xls_import import parse_xls
        r = parse_xls(blob)
        r["kind"] = "xls"
        return r
    return {"ok": False, "kind": ext, "msg": "不支持的格式：.%s" % ext}
