# -*- coding: utf-8 -*-
"""把 Office 的 OOXML 文档（.pptx / .docx / .xlsx）用纯 Python 渲染成 HTML。

为什么不用「WPS 转 PDF」这条路（2026-11-02 实测结论）：
  · 本机 WPS 没有注册 COM 自动化组件（HKCR / HKCU\\Software\\Classes 里
    找不到 KWPP.Application / KWPS.Application / KET.Application），
    只剩文件类型关联，win32com 也建不起来；
  · clitool\\wpscli.exe 只是个启动器（内部指向 kwpsconvert.exe / wpspdf.exe），
    二进制里根本没有 ppt2pdf / word2pdf / excel2pdf 这些子命令，不能当转换器用；
  · pywin32 没装、LibreOffice 没装，服务端没法靠外部程序转 PDF。

纯 Python 解析 OOXML 的好处：不依赖任何外部程序、不需要联网、结果能按
「文件名+大小+修改时间」缓存成 .html，第二次打开是秒开。代价是还原度有限：
PPT 按「形状位置 + 字号」还原版面（动画、母版装饰、SmartArt 不还原）；
Word 还原段落/表格/图片流；Excel 还原单元格表格。

公开接口只有一个：render(path) -> html 字符串。
解析不了（老式 .doc/.ppt/.xls 二进制格式、文件损坏）时抛异常，由调用方回退。
"""
import os
import re
import base64
import posixpath
import zipfile
import html as _h
import xml.etree.ElementTree as ET

_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

# 只有这几种图片格式浏览器能直接显示（emf/wmf/tif 跳过）
_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
    "svg": "image/svg+xml",
}

_BASE_CSS = (
    "*{box-sizing:border-box}"
    "body{margin:0;background:#fff;color:#1f2937;"
    "font-family:-apple-system,'Segoe UI','Microsoft YaHei','PingFang SC',sans-serif;"
    "-webkit-text-size-adjust:100%}"
)


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _a(el, name, default=""):
    """按「本地名」取属性（OOXML 属性常带命名空间前缀）。"""
    if el is None:
        return default
    for k, v in el.attrib.items():
        if _local(k) == name:
            return v
    return default


def _i(v, default=0):
    try:
        return int(float(v))
    except Exception:
        return default


def _esc(s):
    return _h.escape(str(s or ""), quote=True)


def _rels(z, part):
    """读某个部件对应的 .rels，返回 {rId: 目标部件绝对名}。"""
    d, b = posixpath.split(part)
    rp = posixpath.join(d, "_rels", b + ".rels")
    if rp not in z.namelist():
        return {}
    try:
        root = ET.fromstring(z.read(rp))
    except Exception:
        return {}
    out = {}
    for el in root:
        tgt = el.get("Target") or ""
        rid = el.get("Id") or ""
        if not rid or not tgt or el.get("TargetMode") == "External":
            continue
        if tgt.startswith("/"):
            out[rid] = tgt.lstrip("/")
        else:
            out[rid] = posixpath.normpath(posixpath.join(d, tgt))
    return out


def _data_uri(z, part):
    ext = part.rsplit(".", 1)[-1].lower()
    mime = _MIME.get(ext)
    if not mime:
        return ""
    try:
        raw = z.read(part)
    except Exception:
        return ""
    if not raw:
        return ""
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


# ---------------------------------------------------------------------------
# PPTX：按形状的绝对位置还原版面
# ---------------------------------------------------------------------------
def _shape_xfrm(el):
    """取 sp / grpSp 的 a:xfrm：返回 (off, ext, chOff, chExt)。"""
    off = ext = choff = chext = None
    for ch in el:
        if _local(ch.tag) not in ("spPr", "grpSpPr"):
            continue
        for x in ch:
            if _local(x.tag) != "xfrm":
                continue
            for q in x:
                n = _local(q.tag)
                if n == "off":
                    off = (_i(_a(q, "x")), _i(_a(q, "y")))
                elif n == "ext":
                    ext = (_i(_a(q, "cx")), _i(_a(q, "cy")))
                elif n == "chOff":
                    choff = (_i(_a(q, "x")), _i(_a(q, "y")))
                elif n == "chExt":
                    chext = (_i(_a(q, "cx")), _i(_a(q, "cy")))
    return off, ext, choff, chext


def _ppt_paras(el):
    """把一个形状里的 txBody 拆成段落列表：[(字号pt, 颜色, 粗体, 文本)]。"""
    out = []
    body = None
    for ch in el:
        if _local(ch.tag) == "txBody":
            body = ch
            break
    if body is None:
        return out
    for p in body:
        if _local(p.tag) != "p":
            continue
        size = None
        color = ""
        bold = False
        text = ""
        for r in p.iter():
            n = _local(r.tag)
            if n == "rPr":
                if size is None:
                    size = _i(_a(r, "sz"), 0) or None
                if _a(r, "b") in ("1", "true"):
                    bold = True
                if not color:
                    for c in r.iter():
                        if _local(c.tag) == "srgbClr":
                            color = (c.get("val") or "").strip()
                            break
            elif n == "t":
                text += r.text or ""
            elif n == "br":
                text += "\n"
        # 段落级默认字号
        if size is None:
            for q in p.iter():
                if _local(q.tag) == "defRPr":
                    size = _i(_a(q, "sz"), 0) or None
                    if size:
                        break
        if not text.strip():
            continue
        out.append((size or 18, color, bold, text))
    return out


def _ppt_block(sz_pt, color, bold, text, box_h_pt, cq_per_pt):
    """一段文字 → HTML。行距按字号给，避免多行文字溢出框。

    cq_per_pt = 12700(EMU/pt) * 100 / 幻灯片宽度(EMU)，即「1pt 等于多少 cqw」，
    这样 16:9 与 4:3 的幻灯片字号都能按各自宽度正确缩放。"""
    lines = text.split("\n")
    n = max(1, len(lines))
    # 若行数不多，字号用原值；行数过多则按框高压缩，尽量不溢出
    fsz = sz_pt
    if box_h_pt > 0 and n * fsz * 1.32 > box_h_pt:
        fsz = max(6.0, box_h_pt / (n * 1.32))
    style = "font-size:%.4fcqw;line-height:1.32" % (fsz * cq_per_pt)
    if color:
        style += ";color:#" + _esc(color)
    if bold:
        style += ";font-weight:700"
    return "<p style=\"%s\">%s</p>" % (style, "<br>".join(_esc(x) for x in lines))


def _ph_info(el):
    """取形状的占位符信息：返回 (idx, type)；不是占位符返回 (None, None)。"""
    for ch in el:
        if _local(ch.tag) not in ("nvSpPr", "nvPicPr", "nvGraphicFramePr"):
            continue
        for q in ch:
            if _local(q.tag) != "nvPr":
                continue
            for r in q:
                if _local(r.tag) == "ph":
                    return (r.get("idx"), (r.get("type") or "body"))
    return (None, None)


def _rel_target(z, part, kind):
    """在 part 的 .rels 里找 Type 以 /kind 结尾的关系目标（如 slideLayout）。"""
    d, b = posixpath.split(part)
    rp = posixpath.join(d, "_rels", b + ".rels")
    if rp not in z.namelist():
        return None
    try:
        root = ET.fromstring(z.read(rp))
    except Exception:
        return None
    for el in root:
        tgt = el.get("Target") or ""
        typ = el.get("Type") or ""
        if not tgt or not typ.endswith("/" + kind):
            continue
        if tgt.startswith("/"):
            return tgt.lstrip("/")
        return posixpath.normpath(posixpath.join(d, tgt))
    return None


# 占位符完全继承不到位置时的兜底版面（相对幻灯片宽高的比例）
_DEFAULT_PH = {
    "title": (0.075, 0.055, 0.85, 0.18),
    "ctrTitle": (0.10, 0.30, 0.80, 0.24),
    "subTitle": (0.15, 0.57, 0.70, 0.20),
    "body": (0.075, 0.28, 0.85, 0.62),
    "obj": (0.075, 0.28, 0.85, 0.62),
}


def _layout_ph_map(z, slide_part):
    """从本页版式（slideLayout）收集占位符的位置：{(idx|type, 值): (off, ext)}。"""
    lay = _rel_target(z, slide_part, "slideLayout")
    if not lay or lay not in z.namelist():
        return {}
    try:
        root = ET.fromstring(z.read(lay))
    except Exception:
        return {}
    res = {}
    for el in root.iter():
        if _local(el.tag) != "sp":
            continue
        idx, typ = _ph_info(el)
        if typ is None:
            continue
        off, ext, _, _ = _shape_xfrm(el)
        if not off or not ext:
            continue
        if idx:
            res.setdefault(("idx", str(idx)), (off, ext))
        res.setdefault(("type", typ), (off, ext))
    return res


def _ph_geometry(el, ctx):
    """占位符位置：版式继承 → 按类型给默认版面。"""
    idx, typ = _ph_info(el)
    if typ is None:
        return None, None
    phmap = ctx.get("ph") or {}
    for key in ((("idx", str(idx)) if idx else None), ("type", typ)):
        if key and key in phmap:
            return phmap[key]
    box = _DEFAULT_PH.get(typ) or _DEFAULT_PH["body"]
    cx, cy = ctx["cx"], ctx["cy"]
    return ((int(cx * box[0]), int(cy * box[1])),
            (int(cx * box[2]), int(cy * box[3])))


def _ppt_walk(node, psx, psy, ptx, pty, ctx, out):
    """递归遍历 spTree：把 sp / pic 换算成绝对位置后追加到 out。

    形状自身没写 a:xfrm 时（占位符通常从版式继承位置），依次退回
    「版式里同名占位符的位置」→「按占位符类型的默认版面」，避免标题/正文整块丢失。"""
    rels = ctx["rels"]
    slide_cx = ctx["cx"]
    for el in list(node):
        n = _local(el.tag)
        if n == "grpSp":
            off, ext, choff, chext = _shape_xfrm(el)
            if off and ext and choff and chext and choff[0] and choff[1]:
                gsx = float(ext[0]) / choff[0]
                gsy = float(ext[1]) / choff[1]
                _ppt_walk(el, psx * gsx, psy * gsy,
                          ptx + (off[0] - choff[0] * gsx) * psx,
                          pty + (off[1] - choff[1] * gsy) * psy,
                          ctx, out)
            else:
                _ppt_walk(el, psx, psy, ptx, pty, ctx, out)
            continue
        if n not in ("sp", "pic", "graphicFrame"):
            continue
        off, ext, _, _ = _shape_xfrm(el)
        if not off or not ext:
            off, ext = _ph_geometry(el, ctx)
        if not off or not ext or ext[0] <= 0 or ext[1] <= 0:
            continue
        x = ptx + off[0] * psx
        y = pty + off[1] * psy
        w = ext[0] * psx
        h = ext[1] * psy
        pos = "left:%.4f%%;top:%.4f%%;width:%.4f%%;height:%.4f%%" % (
            x * 100.0 / slide_cx, y * 100.0 / slide_cx,
            w * 100.0 / slide_cx, h * 100.0 / slide_cx)

        if n == "pic":
            rid = ""
            for b in el.iter():
                if _local(b.tag) == "blip":
                    rid = b.get(_R_ID) or ""
                    break
            uri = _data_uri(ctx["zip"], rels.get(rid, "")) if rid else ""
            if uri:
                out.append("<img class=\"pp\" style=\"%s\" src=\"%s\" alt=\"\">" % (pos, uri))
            continue

        paras = _ppt_paras(el)
        if not paras:
            continue
        # 框高换算成 pt（1pt = 12700 EMU），用于行数溢出保护
        box_h_pt = h / 12700.0 if h else 0
        blocks = "".join(_ppt_block(s, c, b, t, box_h_pt, ctx["cq"])
                         for s, c, b, t in paras)
        # 垂直对齐：bodyPr/@anchor
        anchor = "t"
        for ch in el:
            if _local(ch.tag) == "txBody":
                for q in ch:
                    if _local(q.tag) == "bodyPr":
                        anchor = _a(q, "anchor", "t") or "t"
                        break
                break
        just = {"ctr": "center", "b": "flex-end"}.get(anchor, "flex-start")
        out.append("<div class=\"pt\" style=\"%s;justify-content:%s\">%s</div>"
                   % (pos, just, blocks))


_PPT_CSS = (
    "body{background:#eef1f4;padding:14px}"
    ".slide{max-width:1100px;margin:0 auto 16px;background:#fff;border-radius:10px;"
    "box-shadow:0 2px 12px rgba(15,40,30,.12);overflow:hidden}"
    ".cv{position:relative;width:100%;container-type:inline-size;background:#fff}"
    ".in{position:absolute;left:0;top:0;right:0;bottom:0}"
    ".pt{position:absolute;display:flex;flex-direction:column;overflow:hidden;"
    "padding:.6cqw 1cqw}"
    ".pt p{margin:0;white-space:pre-wrap;word-break:break-word}"
    ".pp{position:absolute;object-fit:contain}"
    ".pno{max-width:1100px;margin:0 auto 6px;font-size:12px;color:#8a94a0;"
    "font-family:system-ui,'Microsoft YaHei',sans-serif;padding-left:2px}"
)


def _pptx_html(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "ppt/presentation.xml" not in names:
            raise ValueError("不是有效的 PPTX")
        pres = ET.fromstring(z.read("ppt/presentation.xml"))
        cx = cy = 0
        for el in pres.iter():
            if _local(el.tag) == "sldSz":
                cx = _i(_a(el, "cx"))
                cy = _i(_a(el, "cy"))
                break
        if cx <= 0 or cy <= 0:
            cx, cy = 12192000, 6858000
        prels = _rels(z, "ppt/presentation.xml")

        order = []
        for el in pres.iter():
            if _local(el.tag) == "sldId":
                rid = el.get(_R_ID) or ""
                tgt = prels.get(rid)
                if tgt:
                    order.append(tgt)
        # 兜底：presentation.xml 里没列出时，按文件名序号排
        if not order:
            order = sorted([n for n in names
                            if re.match(r"^ppt/slides/slide\d+\.xml$", n)],
                           key=lambda s: int(re.findall(r"\d+", s)[-1]))

        ratio = cy * 100.0 / cx
        cq_per_pt = 12700.0 * 100.0 / cx   # 1pt 对应多少 cqw（随幻灯片宽度变化）
        parts = []
        for idx, part in enumerate(order, 1):
            if part not in names:
                continue
            try:
                root = ET.fromstring(z.read(part))
            except Exception:
                continue
            tree = None
            for el in root.iter():
                if _local(el.tag) == "spTree":
                    tree = el
                    break
            if tree is None:
                continue
            # 本页的上下文：zip、图片关系、画布尺寸、字号换算系数、版式占位符位置
            ctx = {"zip": z, "rels": _rels(z, part), "cx": cx, "cy": cy,
                   "cq": cq_per_pt, "ph": _layout_ph_map(z, part)}
            out = []
            _ppt_walk(tree, 1.0, 1.0, 0, 0, ctx, out)
            if not out:
                out.append("<div class=\"pt\" style=\"left:0;top:0;width:100%;height:100%;"
                           "justify-content:center;align-items:center\">"
                           "<p style=\"font-size:1.6cqw;color:#9aa3ad\">（这一页没有可提取的文字或图片）</p></div>")
            parts.append(
                "<div class=\"pno\">第 %d 页 / 共 %d 页</div>"
                "<div class=\"slide\"><div class=\"cv\" style=\"padding-bottom:%.4f%%\">"
                "<div class=\"in\">%s</div></div></div>"
                % (idx, len(order), ratio, "".join(out)))
        if not parts:
            raise ValueError("幻灯片为空")
        note = ("<div class=\"pno\" style=\"text-align:center;margin:4px 0 14px\">"
                "在线预览由文件内容还原，动画与母版装饰不显示；需要原样效果请下载原文件</div>")
        return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>" + _esc(os.path.basename(path)) + "</title><style>"
                + _BASE_CSS + _PPT_CSS + "</style></head><body>"
                + "".join(parts) + note + "</body></html>")


# ---------------------------------------------------------------------------
# DOCX：段落 + 表格 + 图片，按文档顺序
# ---------------------------------------------------------------------------
_DOC_CSS = (
    "body{padding:22px 26px;max-width:900px;margin:0 auto;line-height:1.75;font-size:15.5px}"
    "p{margin:0 0 .62em}"
    "h1,h2,h3{margin:1.1em 0 .5em;line-height:1.4}"
    "h1{font-size:1.5em}h2{font-size:1.28em}h3{font-size:1.12em}"
    "table{border-collapse:collapse;width:100%;margin:.7em 0;font-size:14px}"
    "td,th{border:1px solid #d5dbe1;padding:6px 9px;vertical-align:top}"
    "th{background:#f3f6f8;font-weight:700}"
    "img{max-width:100%;height:auto;display:block;margin:.6em auto}"
    ".dtab{overflow-x:auto}"
)


def _doc_run_text(el):
    t = ""
    for q in el.iter():
        n = _local(q.tag)
        if n == "t":
            t += q.text or ""
        elif n == "tab":
            # 带 val 的是 w:tabs 里的「制表位定义」，不是正文制表符
            if not q.attrib:
                t += "    "
        elif n in ("br", "cr"):
            t += "\n"
    return t


def _docx_html(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/document.xml" not in names:
            raise ValueError("不是有效的 DOCX")
        rels = _rels(z, "word/document.xml")
        root = ET.fromstring(z.read("word/document.xml"))
        body = None
        for el in root.iter():
            if _local(el.tag) == "body":
                body = el
                break
        if body is None:
            raise ValueError("文档正文为空")

        blocks = []

        def emit_table(tbl):
            rows = []
            for tr in tbl:
                if _local(tr.tag) != "tr":
                    continue
                cells = []
                for tc in tr:
                    if _local(tc.tag) != "tc":
                        continue
                    cells.append("<td>" + "<br>".join(
                        _esc(_doc_run_text(p)) for p in tc.iter() if _local(p.tag) == "p") + "</td>")
                if cells:
                    rows.append("<tr>" + "".join(cells) + "</tr>")
            if rows:
                blocks.append("<div class=\"dtab\"><table>" + "".join(rows) + "</table></div>")

        for el in list(body):
            n = _local(el.tag)
            if n == "tbl":
                emit_table(el)
                continue
            if n != "p":
                continue
            # 段落样式：标题级别
            style = ""
            for q in el.iter():
                if _local(q.tag) == "pStyle":
                    style = (_a(q, "val") or "").lower()
                    break
            html_parts = []
            for r in el.iter():
                if _local(r.tag) == "t":
                    html_parts.append(_esc(r.text or ""))
                elif _local(r.tag) == "tab":
                    # 同 _doc_run_text：只认正文制表符，跳过 w:tabs 里的制表位定义
                    if not r.attrib:
                        html_parts.append("&nbsp;&nbsp;&nbsp;&nbsp;")
                elif _local(r.tag) == "br":
                    html_parts.append("<br>")
                elif _local(r.tag) == "blip":
                    uri = _data_uri(z, rels.get(r.get(_R_ID) or "", ""))
                    if uri:
                        html_parts.append("<img src=\"" + uri + "\" alt=\"\">")
            text = "".join(html_parts).strip()
            if not text:
                continue
            if style.startswith("heading") or style in ("1", "2", "3"):
                lv = style[-1] if style[-1].isdigit() else "2"
                tag = "h1" if lv == "1" else ("h3" if lv == "3" else "h2")
            else:
                tag = "p"
            blocks.append("<%s>%s</%s>" % (tag, text, tag))

        if not blocks:
            raise ValueError("没有可提取的内容")
        return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>" + _esc(os.path.basename(path)) + "</title><style>"
                + _BASE_CSS + _DOC_CSS + "</style></head><body>"
                + "".join(blocks) + "</body></html>")


# ---------------------------------------------------------------------------
# XLSX：单元格表格
# ---------------------------------------------------------------------------
_XLS_CSS = (
    "body{padding:14px}"
    "h4{margin:16px 0 6px;font-size:14px;color:#33504a}"
    ".xtab{overflow:auto;border:1px solid #d5dbe1;border-radius:8px}"
    "table{border-collapse:collapse;font-size:13.5px;background:#fff}"
    "td,th{border:1px solid #e2e8ec;padding:5px 9px;white-space:pre-wrap;max-width:420px}"
    "tr:first-child td{background:#f6f8fa;font-weight:600}"
    ".xtip{color:#8a94a0;font-size:12.5px;margin:6px 0 12px}"
)

_A1 = re.compile(r"^([A-Z]+)(\d+)$")


def _col_idx(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _xlsx_html(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("不是有效的 XLSX")
        shared = []
        if "xl/sharedStrings.xml" in names:
            try:
                sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in sroot:
                    if _local(si.tag) != "si":
                        continue
                    shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
            except Exception:
                shared = []
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels = _rels(z, "xl/workbook.xml")
        sheets = []
        for el in wb.iter():
            if _local(el.tag) != "sheet":
                continue
            nm = el.get("name") or "Sheet"
            rid = el.get(_R_ID) or ""
            tgt = rels.get(rid)
            if tgt and tgt in names:
                sheets.append((nm, tgt))
        if not sheets:
            raise ValueError("工作簿里没有工作表")

        out = []
        for nm, part in sheets:
            try:
                sroot = ET.fromstring(z.read(part))
            except Exception:
                continue
            rows = []
            for row in sroot.iter():
                if _local(row.tag) != "row":
                    continue
                cells = {}
                maxc = -1
                for c in row:
                    if _local(c.tag) != "c":
                        continue
                    ref = c.get("r") or ""
                    m = _A1.match(ref)
                    ci = _col_idx(m.group(1)) if m else (maxc + 1)
                    t = c.get("t") or ""
                    val = ""
                    for q in c.iter():
                        qn = _local(q.tag)
                        if qn == "v":
                            val = q.text or ""
                        elif qn == "t" and t == "inlineStr":
                            val += q.text or ""
                    if t == "s" and val.isdigit():
                        k = int(val)
                        val = shared[k] if 0 <= k < len(shared) else ""
                    elif t == "b":
                        val = "是" if val == "1" else "否"
                    if val:
                        cells[ci] = val
                        maxc = max(maxc, ci)
                if maxc < 0:
                    rows.append("<tr></tr>")
                else:
                    rows.append("<tr>" + "".join(
                        "<td>" + _esc(cells.get(i, "")) + "</td>" for i in range(maxc + 1)) + "</tr>")
                if len(rows) >= 600:
                    break
            if rows:
                out.append("<h4>" + _esc(nm) + "</h4><div class=\"xtab\"><table>"
                           + "".join(rows) + "</table></div>")
        if not out:
            raise ValueError("工作表为空")
        return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>" + _esc(os.path.basename(path)) + "</title><style>"
                + _BASE_CSS + _XLS_CSS + "</style></head><body>"
                + "<div class=\"xtip\">在线预览，表格内容按单元格还原；公式显示为计算结果，日期可能显示为数字序列。</div>"
                + "".join(out) + "</body></html>")


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
OOXML_EXTS = ("pptx", "docx", "xlsx")


def render(path):
    """渲染 OOXML 文档为 HTML；不支持的格式返回 None。"""
    ext = os.path.splitext(str(path or ""))[1].lstrip(".").lower()
    if ext == "pptx":
        return _pptx_html(path)
    if ext == "docx":
        return _docx_html(path)
    if ext == "xlsx":
        return _xlsx_html(path)
    return None


if __name__ == "__main__":
    # 自检：python app/core/ooxml_preview.py <文件> [输出.html]
    # 不写输出路径时只打印渲染结果的体积与首屏片段，方便快速确认能不能解析。
    import sys
    if len(sys.argv) < 2:
        print("用法: python ooxml_preview.py <文件.pptx|docx|xlsx> [输出.html]")
        raise SystemExit(2)
    src = sys.argv[1]
    try:
        page = render(src)
    except Exception as exc:
        print("渲染失败: %s: %s" % (type(exc).__name__, exc))
        raise SystemExit(1)
    if not page:
        print("不支持的格式")
        raise SystemExit(1)
    if len(sys.argv) >= 3:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            f.write(page)
        print("已写出 %s（%d 字节）" % (sys.argv[2], len(page.encode("utf-8"))))
    else:
        print("渲染成功：%d 字符" % len(page))
        print(page[:600])
