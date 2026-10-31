#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课件库 · 本地文件服务（仅 Python 标准库）
=========================================
职责
----
1. 管理一个「课件根目录」：默认自动生成，老师可改成任意位置（含其他磁盘）；
2. 上传课程资料（图片 / 文档 / 音视频…），按「课程」分文件夹存放；
3. 在线查看：图片、音视频、PDF 直接给原始文件；docx / xlsx / pptx / 文本
   由服务端解析成 HTML 再喂给前端 iframe（不依赖 Office、不联网、不装库）；
4. 活动作品归档：把「活动」里学生提交的文件复制进 `<课程>/_活动作品/<活动名>/`。

设计原则
--------
· **目录即数据**：不维护索引文件，目录结构就是事实。老师用资源管理器手动
  往里拷课件，工作台刷新就能看到；把整个目录拷走就是完整备份。
· **路径必须锁在根目录内**：所有 id 都是「相对根目录的路径」，读写前统一做
  commonpath 校验，避免 ../ 穿越。
· **失败要说人话**：所有错误返回中文原因，界面直接展示，不让老师猜。
"""
import os
import io
import re
import json
import html
import shutil
import zipfile
import datetime
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))          # submission-system/
WB_ROOT = os.path.dirname(ROOT)                            # 工作台根
# 同 submission-server：容器部署时由 WB_SUBMISSION_DATA_DIR 指到数据卷
DATA = os.environ.get("WB_SUBMISSION_DATA_DIR") or os.path.join(ROOT, "data")
CFG_PATH = os.path.join(DATA, "courseware.json")
DEFAULT_ROOT = os.path.join(WB_ROOT, "courseware")         # 默认自动生成的位置
MAX_UPLOAD = 300 * 1024 * 1024                             # 单文件上限 300MB

# 目录里不当作课件的东西（工作台自己在每个课程下写的说明文件除外）
_IGNORE = (".ds_store", "thumbs.db", ".write_probe")
# 危险根目录：指向这些位置会把系统/源码搞乱，直接拒绝
_BAD_ROOTS = ("/", "/System", "/Library", "/usr", "/bin", "/etc", "/private",
              "/Applications", "C:\\", "C:\\Windows", "C:\\Program Files")
# 前缀级黑名单（/System/xxx 这类也要拦；只做整词匹配，避免误伤 /Users/xxx/System）
_BAD_PREFIX = ("/system", "/library", "/usr", "/bin", "/sbin", "/etc", "/private",
               "/applications", "/cores", "/volumes",
               "c:/windows", "c:/program files", "c:/programdata")


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_cfg():
    try:
        with open(CFG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    root = str(cfg.get("root") or "").strip()
    if not root:
        root = DEFAULT_ROOT
        # 注意：只补字段，不能整个重建 cfg —— 否则 mounts（本地资料夹）、allow_lan
        # 这些同文件里的设置会在「根目录字段丢失」时被一起抹掉。
        cfg["version"] = cfg.get("version") or 1
        cfg["root"] = root
        cfg["auto"] = True
        cfg["updated_at"] = cfg.get("updated_at") or _now()
        save_cfg(cfg)
        ensure_root(root)
    cfg.setdefault("version", 1)
    cfg.setdefault("auto", os.path.normpath(root) == os.path.normpath(DEFAULT_ROOT))
    return cfg


def save_cfg(cfg):
    os.makedirs(DATA, exist_ok=True)
    tmp = CFG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, CFG_PATH)


def root_dir():
    return os.path.normpath(load_cfg().get("root") or DEFAULT_ROOT)


def ensure_root(path=None):
    p = path or root_dir()
    try:
        os.makedirs(p, exist_ok=True)
        return True, p
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# 路径安全
# ---------------------------------------------------------------------------
def safe_name(name):
    """文件名清洗：去掉路径分隔符与控制字符，保留中文"""
    s = str(name or "").replace("\\", "/").split("/")[-1]
    s = re.sub(r'[\x00-\x1f<>:"|?*]', "", s).strip().strip(".")
    return s or "未命名"


def safe_course(name):
    """课程名 → 文件夹名（去掉非法字符，防止把目录撑成多级）"""
    s = safe_name(name)
    s = s.replace("/", "-")
    return s[:60] or "未分课程"


def abs_of(rel, must_exist=False):
    """相对路径 → 绝对路径，并保证仍在根目录内；非法返回 None"""
    base = root_dir()
    rel = str(rel or "").replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        return None
    full = os.path.normpath(os.path.join(base, rel.replace("/", os.sep)))
    try:
        if os.path.commonpath([os.path.normpath(base), full]) != os.path.normpath(base):
            return None
    except ValueError:
        return None
    if must_exist and not os.path.exists(full):
        return None
    return full


def rel_of(full):
    base = root_dir()
    try:
        r = os.path.relpath(full, base)
    except ValueError:
        return ""
    return r.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# 文件类型
# ---------------------------------------------------------------------------
EXT_IMAGE = ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif")
EXT_VIDEO = ("mp4", "webm", "mov", "m4v", "ogv")
EXT_AUDIO = ("mp3", "wav", "m4a", "aac", "ogg", "flac")
EXT_PDF = ("pdf",)
EXT_OFFICE = ("docx", "xlsx", "pptx")
EXT_LEGACY = ("doc", "xls", "ppt")
EXT_TEXT = ("txt", "md", "csv", "tsv", "json", "xml", "html", "htm",
            "log", "py", "js", "css", "ini", "yaml", "yml", "srt")


def ext_of(name):
    return str(name or "").rsplit(".", 1)[-1].lower() if "." in str(name or "") else ""


def kind_of(name):
    e = ext_of(name)
    if e in EXT_IMAGE:
        return "image"
    if e in EXT_VIDEO:
        return "video"
    if e in EXT_AUDIO:
        return "audio"
    if e in EXT_PDF:
        return "pdf"
    if e in EXT_OFFICE:
        return "office"
    if e in EXT_LEGACY:
        return "legacy"
    if e in EXT_TEXT:
        return "text"
    return "other"


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f GB" % n


# ---------------------------------------------------------------------------
# 目录扫描
# ---------------------------------------------------------------------------
def list_courses():
    """一级文件夹 = 课程；根目录下的散文件归到「未分课程」"""
    base = root_dir()
    ok, _ = ensure_root(base)
    out = []
    try:
        names = os.listdir(base)
    except Exception as e:
        return [], "无法读取课件目录：%s" % e
    loose = 0
    for name in sorted(names, key=lambda x: x.lower()):
        full = os.path.join(base, name)
        if name.startswith(".") or name.lower() in _IGNORE:
            continue
        if os.path.isdir(full):
            cnt = 0
            size = 0
            for dirpath, dirnames, filenames in os.walk(full):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    if fn.startswith(".") or fn.lower() in _IGNORE:
                        continue
                    cnt += 1
                    try:
                        size += os.path.getsize(os.path.join(dirpath, fn))
                    except Exception:
                        pass
            out.append({"name": name, "count": cnt, "size": size,
                        "size_text": human_size(size)})
        elif os.path.isfile(full):
            loose += 1
    if loose:
        out.append({"name": "未分课程", "count": loose, "size": 0,
                    "size_text": "", "loose": True})
    return out, None


def list_files(course):
    """某课程下的文件（含子目录，_活动作品 单独标记）"""
    base = root_dir()
    if course == "未分课程":
        folder = base
        walk_root = base
        only_top = True
    else:
        folder = abs_of(safe_course(course))
        walk_root = folder
        only_top = False
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if only_top and os.path.normpath(dirpath) != os.path.normpath(walk_root):
            continue
        rel_dir = os.path.relpath(dirpath, walk_root).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        for fn in filenames:
            if fn.startswith(".") or fn.lower() in _IGNORE:
                continue
            if only_top and os.path.isdir(os.path.join(dirpath, fn)):
                continue
            full = os.path.join(dirpath, fn)
            if not os.path.isfile(full):
                continue
            try:
                st = os.stat(full)
                size, mtime = st.st_size, st.st_mtime
            except Exception:
                size, mtime = 0, 0
            rel = rel_of(full)
            top = rel_dir.split("/")[0]
            archived = top in ("_活动作品", "_活动数据")     # 活动归档：作品文件 + 提交数据表
            act_name = ""
            if archived:
                parts = rel_dir.split("/")
                act_name = parts[1] if len(parts) > 1 else re.sub(r"-提交数据$", "", os.path.splitext(fn)[0])
            out.append({
                "id": rel,
                "name": fn,
                "rel_dir": rel_dir,
                "course": course,
                "kind": kind_of(fn),
                "ext": ext_of(fn),
                "size": size,
                "size_text": human_size(size),
                "mtime": datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime else "",
                "archived": archived,
                "activity": act_name,
                "previewable": kind_of(fn) in ("image", "video", "audio", "pdf", "office", "text"),
            })
    out.sort(key=lambda x: (x["archived"], x["rel_dir"], x["name"].lower()))
    return out


def all_files():
    courses, err = list_courses()
    if err:
        return [], err
    out = []
    for c in courses:
        out.extend(list_files(c["name"]))
    return out, None


# ---------------------------------------------------------------------------
# 预览：把 Office / 文本转成可读 HTML（纯标准库）
# ---------------------------------------------------------------------------
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _docx_html(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    tree = ET.fromstring(xml)
    body = tree.find(_W + "body")
    if body is None:
        return "<p>（文档是空的）</p>"
    ps = []

    def para_text(p):
        buf = []
        for node in p.iter():
            tag = node.tag
            if tag == _W + "t" and node.text:
                buf.append(html.escape(node.text))
            elif tag == _W + "br":
                buf.append("<br>")
            elif tag == _W + "tab":
                buf.append("&nbsp;&nbsp;&nbsp;&nbsp;")
        return "".join(buf)

    def style_level(p):
        pr = p.find(_W + "pPr")
        if pr is None:
            return 0
        st = pr.find(_W + "pStyle")
        if st is None:
            return 0
        v = str(st.get(_W + "val") or "")
        m = re.search(r"(\d)", v)
        if v.lower().startswith(("heading", "title")) or "标题" in v:
            return int(m.group(1)) if m else 1
        return 0

    for child in list(body):
        if child.tag == _W + "p":
            t = para_text(child)
            lv = style_level(child)
            if not t.strip():
                ps.append('<div class="p-gap"></div>')
            elif lv:
                lv = min(lv + 1, 5)
                ps.append('<h%d>%s</h%d>' % (lv, t, lv))
            else:
                ps.append("<p>%s</p>" % t)
        elif child.tag == _W + "tbl":
            rows = []
            for tr in child.findall(_W + "tr"):
                cells = []
                for tc in tr.findall(_W + "tc"):
                    txt = " ".join(para_text(p) for p in tc.findall(_W + "p")).strip()
                    cells.append("<td>%s</td>" % (txt or "&nbsp;"))
                rows.append("<tr>%s</tr>" % "".join(cells))
            ps.append("<table>%s</table>" % "".join(rows))
    return "\n".join(ps) or "<p>（文档没有可显示的内容）</p>"


def _col_letter(idx):
    s = ""
    idx = int(idx)
    while idx > 0:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _col_index(ref):
    m = re.match(r"([A-Z]+)", str(ref or ""))
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n


def _xlsx_html(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            st = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in st.findall(_S + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_S + "t")))
        # 工作簿里的工作表顺序与名称
        sheets = []
        try:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rels = {}
            if "xl/_rels/workbook.xml.rels" in names:
                rt = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
                for rel in rt:
                    rels[rel.get("Id")] = rel.get("Target")
            for sh in wb.iter(_S + "sheet"):
                tgt = rels.get(sh.get(_R + "id"), "") or ""
                tgt = tgt.lstrip("/")
                if not tgt.startswith("xl/"):
                    tgt = "xl/" + tgt
                sheets.append((sh.get("name") or "Sheet", tgt))
        except Exception:
            pass
        if not sheets:
            sheets = [("Sheet1", n) for n in sorted(names)
                      if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
        out = []
        for sname, target in sheets:
            if target not in names:
                continue
            root = ET.fromstring(z.read(target))
            rows = []
            for row in root.iter(_S + "row"):
                cells = {}
                for c in row.findall(_S + "c"):
                    t = c.get("t")
                    v = c.find(_S + "v")
                    val = ""
                    if t == "s" and v is not None:
                        try:
                            val = shared[int(v.text)]
                        except Exception:
                            val = ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(_S + "t"))
                    elif v is not None:
                        val = v.text or ""
                    if val != "":
                        cells[_col_index(c.get("r"))] = val
                if cells:
                    mx = max(cells)
                    rows.append([cells.get(i, "") for i in range(1, mx + 1)])
            if not rows:
                continue
            head = rows[0]
            body = rows[1:]
            out.append('<div class="sheet"><h3>%s</h3><div class="tblwrap"><table><tr>%s</tr>%s</table></div></div>'
                       % (html.escape(sname),
                          "".join("<th>%s</th>" % html.escape(x) for x in head),
                          "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % html.escape(x) for x in r) for r in body)))
        return "".join(out) or "<p>（表格是空的）</p>"


def _pptx_html(path):
    with zipfile.ZipFile(path) as z:
        slides = [n for n in z.namelist()
                  if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        slides.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[-1]).group(1)))
        out = []
        for i, n in enumerate(slides, 1):
            root = ET.fromstring(z.read(n))
            paras = []
            for p in root.iter(_A + "p"):
                t = "".join(x.text or "" for x in p.iter(_A + "t")).strip()
                if t:
                    paras.append(t)
            if not paras:
                continue
            title = paras[0]
            rest = paras[1:]
            out.append('<div class="slide"><div class="slide-no">第 %d 页</div><h3>%s</h3>%s</div>'
                       % (i, html.escape(title),
                          "".join("<p>%s</p>" % html.escape(x) for x in rest)))
        return "".join(out) or "<p>（演示文稿里没有可提取的文字）</p>"


def _text_html(path, name):
    e = ext_of(name)
    raw = b""
    with open(path, "rb") as f:
        raw = f.read(2 * 1024 * 1024)          # 最多读 2MB，够看
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            text = None
    if text is None:
        text = raw.decode("utf-8", "replace")
    if e == "md":
        return _md_html(text)
    if e in ("csv", "tsv"):
        sep = "\t" if e == "tsv" else ","
        lines = [l for l in text.splitlines() if l.strip()][:500]
        if not lines:
            return "<p>（空文件）</p>"
        rows = [l.split(sep) for l in lines]
        head, body = rows[0], rows[1:]
        return ('<div class="tblwrap"><table><tr>%s</tr>%s</table></div>'
                % ("".join("<th>%s</th>" % html.escape(x) for x in head),
                   "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % html.escape(x) for x in r) for r in body)))
    return "<pre>%s</pre>" % html.escape(text)


def _md_html(text):
    out = []
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
            out.append("<pre><code>" if in_code else "</code></pre>")
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            lv = min(len(m.group(1)) + 1, 6)
            out.append("<h%d>%s</h%d>" % (lv, html.escape(m.group(2)), lv))
            continue
        if re.match(r"^\s*[-*+]\s+", line):
            out.append("<li>%s</li>" % html.escape(re.sub(r"^\s*[-*+]\s+", "", line)))
            continue
        if not line.strip():
            out.append('<div class="p-gap"></div>')
            continue
        t = html.escape(line)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        out.append("<p>%s</p>" % t)
    return "\n".join(out)


PREVIEW_CSS = """
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{margin:0;padding:26px 30px 60px;font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
  background:#fff;color:#1A2E28;max-width:980px;margin:0 auto}
h1,h2,h3,h4,h5{color:#0F6E56;line-height:1.35;margin:1.3em 0 .5em}
h1{font-size:24px}h2{font-size:21px}h3{font-size:18px}h4,h5{font-size:16px}
p{margin:.55em 0}
.p-gap{height:.6em}
table{border-collapse:collapse;width:100%;margin:.8em 0;font-size:14px}
th,td{border:1px solid #dfe7e4;padding:6px 10px;text-align:left;vertical-align:top}
th{background:#E1F5EE;color:#0F6E56;font-weight:600}
.tblwrap{overflow-x:auto;max-width:100%}
pre{background:#F4F7F6;border:1px solid #e2eae7;border-radius:10px;padding:12px 14px;
  overflow:auto;font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-word}
code{background:#F4F7F6;border-radius:5px;padding:1px 5px;font-size:13px}
li{margin:.2em 0 .2em 1.2em}
.sheet{margin-bottom:26px}
.slide{border:1px solid #e2eae7;border-radius:12px;padding:14px 18px;margin-bottom:14px;background:#FBFDFC}
.slide-no{font-size:12px;color:#5F6E68;margin-bottom:4px}
.note{background:#FFF6E9;border:1px solid #F0C36D;color:#8A5A10;border-radius:10px;
  padding:10px 14px;font-size:13px;margin-bottom:18px}
html.dark body{background:#0B1210;color:#E6EFEA}
html.dark h1,html.dark h2,html.dark h3,html.dark h4,html.dark h5{color:#7FD9B8}
html.dark th{background:#123027;color:#9FE6C9}
html.dark th,html.dark td{border-color:#1E3A31}
html.dark pre,html.dark code{background:#101C18;border-color:#1E3A31}
html.dark .slide{background:#101C18;border-color:#1E3A31}
html.dark .slide-no{color:#9BB0A8}
html.dark .note{background:#2A2114;border-color:#6B5220;color:#E8C98A}
@media (prefers-color-scheme:dark){
  body{background:#0B1210;color:#E6EFEA}
  h1,h2,h3,h4,h5{color:#7FD9B8}
  th{background:#123027;color:#9FE6C9}
  th,td{border-color:#1E3A31}
  pre,code{background:#101C18;border-color:#1E3A31}
  .slide{background:#101C18;border-color:#1E3A31}
  .slide-no{color:#9BB0A8}
  .note{background:#2A2114;border-color:#6B5220;color:#E8C98A}
}
"""


def preview_html(full, name, dark=False):
    """把文件内容包成一张完整的 HTML 页（iframe 里显示，样式与主题隔离）"""
    e = ext_of(name)
    kind = kind_of(name)
    body = ""
    note = ""
    if kind == "office":
        try:
            if e == "docx":
                body = _docx_html(full)
            elif e == "xlsx":
                body = _xlsx_html(full)
            else:
                body = _pptx_html(full)
            note = "这是<b>内容预览</b>：文字、表格、幻灯片内容都能看，复杂排版（分栏、艺术字、动画）会简化。要看原样请点右上角「下载原件」。"
        except Exception as ex:
            body = "<p>解析失败：%s</p>" % html.escape(str(ex))
            note = "这个文件可能已损坏，或不是标准的 Office 文件。"
    elif kind == "text":
        body = _text_html(full, name)
    elif kind == "legacy":
        body = ("<p>这是旧版 Office 格式（%s），浏览器和本机都无法安全解析。</p>"
                "<p>建议用 Office / WPS 另存为 <b>.docx / .xlsx / .pptx</b> 后再上传，就能在线查看了。</p>" % e.upper())
        note = "旧格式只能下载后打开。"
    else:
        body = "<p>这个文件类型不支持内容预览。</p>"
    return ("<!doctype html><html lang=\"zh-CN\"%s><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<style>%s</style></head><body>%s%s</body></html>"
            % (' class="dark"' if dark else "", PREVIEW_CSS,
               ('<div class="note">%s</div>' % note) if note else "", body))


# ---------------------------------------------------------------------------
# 上传 / 删除 / 归档
# ---------------------------------------------------------------------------
def unique_path(folder, name):
    target = os.path.join(folder, name)
    if not os.path.exists(target):
        return target
    stem, ext = os.path.splitext(name)
    i = 2
    while True:
        cand = os.path.join(folder, "%s（%d）%s" % (stem, i, ext))
        if not os.path.exists(cand):
            return cand
        i += 1


def save_upload(course, files):
    import base64
    folder = abs_of(safe_course(course))
    if not folder:
        return {"ok": False, "msg": "课程名不合法"}
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": "无法创建课程目录：%s" % e}
    saved, failed = [], []
    for f in (files or []):
        nm = safe_name(f.get("name"))
        raw = f.get("data") or ""
        if "base64," in raw:
            raw = raw.split("base64,", 1)[1]
        try:
            blob = base64.b64decode(raw, validate=False)
        except Exception:
            failed.append({"name": nm, "msg": "内容不是有效的文件数据"})
            continue
        if not blob:
            failed.append({"name": nm, "msg": "文件是空的"})
            continue
        if len(blob) > MAX_UPLOAD:
            failed.append({"name": nm, "msg": "超过 %s 上限" % human_size(MAX_UPLOAD)})
            continue
        target = unique_path(folder, nm)
        try:
            with open(target, "wb") as fh:
                fh.write(blob)
            saved.append({"name": os.path.basename(target), "id": rel_of(target),
                          "size_text": human_size(len(blob))})
        except Exception as e:
            failed.append({"name": nm, "msg": "写入失败：%s" % e})
    return {"ok": bool(saved) or not failed, "saved": saved, "failed": failed,
            "course": safe_course(course), "dir": folder,
            "msg": ("已上传 %d 个文件" % len(saved)) if saved else "没有文件被保存"}


def archive_items(course, activity, base, items):
    """把活动提交的文件复制进 <课程>/_活动作品/<活动名>/"""
    course_dir = abs_of(safe_course(course))
    if not course_dir:
        return {"ok": False, "msg": "课程名不合法"}
    act = safe_name(activity) or "未命名活动"
    target_dir = os.path.join(course_dir, "_活动作品", act)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": "无法创建归档目录：%s" % e}
    base = str(base or "").rstrip("/")
    saved, skipped = [], []
    for it in (items or []):
        url = str(it.get("url") or "")
        nm = safe_name(it.get("name") or url.rsplit("/", 1)[-1] or "file")
        stu = safe_name(it.get("student") or "")
        if stu:
            nm = "%s_%s" % (stu, nm)
        if not url:
            skipped.append({"name": nm, "msg": "没有文件地址"})
            continue
        blob = None
        # ① 本机文件系统直读（QuickForm 的 /static/... 就在隔壁，最快最稳）
        if url.startswith("/"):
            cands = [os.path.join(WB_ROOT, "quickform-server", url.lstrip("/")),
                     os.path.join(WB_ROOT, url.lstrip("/"))]
            for c in cands:
                if os.path.isfile(c):
                    try:
                        with open(c, "rb") as fh:
                            blob = fh.read()
                        break
                    except Exception:
                        blob = None
        # ② 退回 HTTP 拉取（QuickForm 目录不在默认位置、或换了端口）
        if blob is None and base:
            try:
                req = urllib.request.Request(base + (url if url.startswith("/") else "/" + url),
                                             headers={"User-Agent": "class-workbench"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    blob = r.read()
            except Exception as e:
                skipped.append({"name": nm, "msg": "下载失败：%s" % e})
                continue
        if not blob:
            skipped.append({"name": nm, "msg": "读取到的内容是空的"})
            continue
        target = os.path.join(target_dir, nm)
        try:
            if os.path.isfile(target):
                try:
                    if os.path.getsize(target) == len(blob):
                        skipped.append({"name": nm, "msg": "已是最新"})
                        continue
                except Exception:
                    pass
            with open(target, "wb") as fh:
                fh.write(blob)
            saved.append({"name": nm, "size_text": human_size(len(blob))})
        except Exception as e:
            skipped.append({"name": nm, "msg": "写入失败：%s" % e})
    return {"ok": True, "saved": saved, "skipped": skipped,
            "dir": target_dir, "rel": rel_of(target_dir),
            "msg": "已归档 %d 个文件到 %s" % (len(saved), act)}


def fetch_remote(base, url, timeout=20):
    """把「活动」里学生提交的文件取回来（QuickForm 的 /static/...）。

    优先直接读本机文件（QuickForm 就在隔壁，省一次网络往返），读不到再走 HTTP。
    安全：只接受以 / 开头的相对地址，且 base 必须是本机 / 局域网地址，
    避免被人拿来当跳板访问外网（SSRF）。
    """
    url = str(url or "").strip()
    if not url.startswith("/"):
        raise ValueError("只接受以 / 开头的站内地址")
    base = str(base or "").rstrip("/")
    local = os.path.join(WB_ROOT, "quickform-server", url.lstrip("/"))
    if os.path.isfile(local):
        with open(local, "rb") as f:
            return f.read()
    if not base:
        raise ValueError("缺少活动服务地址，无法取回文件")
    parsed = urllib.parse.urlparse(base)
    host = (parsed.hostname or "").lower()
    if not (host in ("localhost", "127.0.0.1", "::1") or re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host)):
        raise ValueError("活动服务地址必须是本机或局域网地址")
    req = urllib.request.Request(base + url, headers={"User-Agent": "class-workbench"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def remote_preview_html(base, url, dark=False):
    """远程文件的在线预览：Office / 文本转 HTML，其它类型交给前端直连原地址"""
    blob = fetch_remote(base, url)
    name = os.path.basename(urllib.parse.urlparse(url).path) or "file"
    kind = kind_of(name)
    if kind not in ("office", "text", "legacy"):
        return None, name, kind
    cache = os.path.join(DATA, "_rwcache")
    os.makedirs(cache, exist_ok=True)
    ext = ext_of(name) or "bin"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)[-40:]
    tmp = os.path.join(cache, "%s_%s" % (str(abs(hash(url)))[-10:], safe))
    if not os.path.isfile(tmp) or os.path.getsize(tmp) != len(blob):
        with open(tmp, "wb") as f:
            f.write(blob)
    return preview_html(tmp, name, dark), name, kind


def save_text(course, subdir, name, text):
    """把一段文本写成文件（活动提交数据 CSV / JSON 归档用），同名直接覆盖为最新。"""
    course_dir = abs_of(safe_course(course))
    if not course_dir:
        return {"ok": False, "msg": "课程名不合法"}
    folder = os.path.join(course_dir, safe_name(subdir or "_活动数据"))
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": "无法创建目录：%s" % e}
    target = os.path.join(folder, safe_name(name))
    try:
        data = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=2)
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(data)
        return {"ok": True, "id": rel_of(target), "msg": "已保存 " + rel_of(target)}
    except Exception as e:
        return {"ok": False, "msg": "写入失败：%s" % e}


def delete_file(rel):
    full = abs_of(rel, must_exist=True)
    if not full or not os.path.isfile(full):
        return {"ok": False, "msg": "文件不存在"}
    try:
        os.remove(full)
        return {"ok": True, "msg": "已删除", "id": rel}
    except Exception as e:
        return {"ok": False, "msg": "删除失败：%s" % e}


def rename_or_move(rel, course):
    full = abs_of(rel, must_exist=True)
    dst_dir = abs_of(safe_course(course))
    if not full or not dst_dir:
        return {"ok": False, "msg": "路径不合法"}
    try:
        os.makedirs(dst_dir, exist_ok=True)
        target = unique_path(dst_dir, os.path.basename(full))
        shutil.move(full, target)
        return {"ok": True, "msg": "已移动到「%s」" % safe_course(course), "id": rel_of(target)}
    except Exception as e:
        return {"ok": False, "msg": "移动失败：%s" % e}


def set_root(new_root, move):
    """切换课件根目录。返回 (ok, cfg, msg)"""
    raw = str(new_root or "").strip().strip('"').strip("'")
    if not raw:
        return False, None, "请填写目录路径"
    p = os.path.expanduser(raw).replace("\\", "/")
    p = os.path.normpath(p)
    low = p.lower()
    if p in _BAD_ROOTS or len(p.rstrip("/\\")) <= 1:
        return False, None, "这个位置太危险（系统盘根目录或系统文件夹），请换一个专门放课件的文件夹"
    for bad in _BAD_PREFIX:
        if low == bad or low.startswith(bad + "/"):
            return False, None, "不能放在系统目录（%s）里，请换一个专门放课件的文件夹，例如「文档/班级课件」" % p
    old = root_dir()
    # 目录不存在就自动生成（面向新手：允许直接写一个还没建的路径）
    created = False
    if not os.path.isdir(p):
        try:
            os.makedirs(p, exist_ok=True)
            created = True
        except Exception as e:
            return False, None, "目录不存在且无法创建：%s（检查盘符是否存在、有没有写权限）" % e
    probe = os.path.join(p, ".write_probe")
    try:
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
    except Exception as e:
        return False, None, "目录没有写入权限：%s（换个位置，或用管理员身份运行）" % e
    moved = 0
    if move and os.path.normpath(old) != p and os.path.isdir(old):
        try:
            for name in os.listdir(old):
                if name.startswith("."):
                    continue
                src = os.path.join(old, name)
                dst = os.path.join(p, name)
                if os.path.exists(dst):
                    continue
                shutil.move(src, dst)
                moved += 1
        except Exception as e:
            return False, None, "迁移旧文件时出错：%s（配置未改动，可先手动拷贝再切换）" % e
    cfg = {"version": 1, "root": p,
           "auto": os.path.normpath(p) == os.path.normpath(DEFAULT_ROOT),
           "updated_at": _now()}
    save_cfg(cfg)
    return True, cfg, ("已切换到 %s" % p) + ("，并迁移了 %d 项" % moved if moved else "") + ("（目录是新建的）" if created else "")


# ===========================================================================
# 本地资料夹（mounts）—— 只加载 / 只展示
# ---------------------------------------------------------------------------
# 与上面的「课件库」是两套东西，请勿混用：
#   · 课件库：工作台自己管理的目录，可上传 / 删除 / 归档，目录即数据；
#   · 资料夹：老师电脑上**原本就有**的任意文件夹（如「D:\教学\八年级」），
#     工作台只是把它「挂」进来读一读、展示出来，**绝不写入、绝不移动、绝不删除**。
#
# 关键约束（界面上的说明与这里是同一套规则）：
#   1. 只读：本模块不含任何写磁盘的函数，全部是 listdir / stat / open(rb)；
#   2. 不同步：资料夹列表只存 config/data/courseware.json，不进浏览器数据库，
#      所以「导出备份 / 从备份恢复」都不会带走它 —— 换电脑要重新添加一次；
#   3. 路径锁死：所有相对路径都在 mount 根目录内做 commonpath 校验，防 ../ 穿越；
#   4. 有上限：单次扫描最多 SCAN_LIMIT 个文件、SCAN_DEPTH 层目录，避免误挂 C:\ 卡死。
# ===========================================================================
MAX_MOUNTS = 20
SCAN_LIMIT = 6000
SCAN_DEPTH = 6


def _clean_path(raw):
    """用户输入的路径 → 规范化绝对路径（支持 ~ 和 %VAR%，去掉复制粘贴带上的引号）"""
    p = str(raw or "").strip().strip('"').strip("'")
    if not p:
        return ""
    p = os.path.expanduser(os.path.expandvars(p))
    try:
        p = os.path.normpath(p)
    except Exception:
        return ""
    return p


def _bad_path(p):
    """危险目录（系统盘根 / 系统目录）返回 True"""
    low = p.lower().replace("\\", "/")
    if len(p.rstrip("/\\")) <= 1:
        return True
    if low.rstrip("/") in [x.lower().rstrip("/") for x in _BAD_ROOTS]:
        return True
    for bad in _BAD_PREFIX:
        b = bad.replace("\\", "/")
        if low == b or low.startswith(b + "/"):
            return True
    return False


def mounts_raw(cfg=None):
    cfg = cfg if isinstance(cfg, dict) else load_cfg()
    arr = cfg.get("mounts")
    return arr if isinstance(arr, list) else []


def mount_find(mid, cfg=None):
    for m in mounts_raw(cfg):
        if str(m.get("id")) == str(mid):
            return m
    return None


def mount_root(m):
    """挂载点根目录（不存在就返回空串，交给上层报错，不自动创建）"""
    p = _clean_path((m or {}).get("path"))
    return p if p and os.path.isdir(p) else ""


def mount_public(m):
    """给前端的挂载点信息（不含磁盘上的任何文件内容）"""
    p = _clean_path(m.get("path"))
    ok = bool(p) and os.path.isdir(p)
    err = ""
    if not ok:
        err = "文件夹不存在或已断开（U 盘拔了 / 移动了位置？）"
    else:
        try:
            os.listdir(p)
        except Exception as e:
            ok = False
            err = "无法读取：%s" % e
    st = m.get("last_scan") or {}
    return {
        "id": m.get("id"), "name": m.get("name") or os.path.basename(p) or "资料夹",
        "path": p, "ok": ok, "error": err,
        "hidden_count": len(m.get("hidden") or []),
        "alias_count": len(m.get("aliases") or {}),
        "count": int(st.get("count") or 0),
        "size": int(st.get("size") or 0),
        "size_text": human_size(st.get("size") or 0) if st.get("count") else "",
        "scanned_at": st.get("at") or "",
        "truncated": bool(st.get("truncated")),
        "added_at": m.get("added_at") or "",
    }


def mounts_list():
    return [mount_public(m) for m in mounts_raw()]


def mount_add(path, name=""):
    p = _clean_path(path)
    if not p:
        return {"ok": False, "msg": "请填写文件夹路径"}
    if _bad_path(p):
        return {"ok": False, "msg": "这个位置太危险（系统目录 / 磁盘根目录），请选一个真正放资料的文件夹"}
    if not os.path.exists(p):
        return {"ok": False, "msg": "文件夹不存在：%s" % p}
    if not os.path.isdir(p):
        return {"ok": False, "msg": "这是一个文件，不是文件夹：%s" % p}
    try:
        os.listdir(p)
    except Exception as e:
        return {"ok": False, "msg": "没有读取权限：%s" % e}
    cfg = load_cfg()
    arr = mounts_raw(cfg)
    if len(arr) >= MAX_MOUNTS:
        return {"ok": False, "msg": "最多同时添加 %d 个资料夹，请先移除不用的" % MAX_MOUNTS}
    rp = os.path.realpath(p)
    for m in arr:
        if os.path.realpath(_clean_path(m.get("path"))) == rp:
            return {"ok": False, "msg": "这个文件夹已经添加过了，直接点它的卡片即可"}
    nm = safe_name(name).strip() if str(name or "").strip() else os.path.basename(p)
    nm = (nm or os.path.basename(p) or "资料夹")[:40]
    rec = {
        "id": "m%d" % int(datetime.datetime.now().timestamp() * 1000),
        "name": nm, "path": p,
        "hidden": [], "aliases": {},
        "added_at": _now(), "last_scan": {},
    }
    arr.append(rec)
    cfg["mounts"] = arr
    cfg["updated_at"] = _now()
    save_cfg(cfg)
    return {"ok": True, "msg": "已添加资料夹「%s」" % nm, "mount": mount_public(rec)}


def mount_update(mid, name=None, path=None):
    cfg = load_cfg()
    arr = mounts_raw(cfg)
    m = None
    for x in arr:
        if str(x.get("id")) == str(mid):
            m = x
            break
    if not m:
        return {"ok": False, "msg": "资料夹不存在（可能已被移除）"}
    if name is not None:
        nm = safe_name(name).strip()[:40]
        if not nm:
            return {"ok": False, "msg": "名称不能为空"}
        m["name"] = nm
    if path is not None and str(path).strip():
        p = _clean_path(path)
        if _bad_path(p):
            return {"ok": False, "msg": "这个位置太危险（系统目录 / 磁盘根目录），请换一个"}
        if not os.path.isdir(p):
            return {"ok": False, "msg": "文件夹不存在：%s" % p}
        for x in arr:
            if str(x.get("id")) != str(mid) and os.path.realpath(_clean_path(x.get("path"))) == os.path.realpath(p):
                return {"ok": False, "msg": "这个文件夹已经被别的资料夹占用了"}
        m["path"] = p
        m["last_scan"] = {}
    cfg["mounts"] = arr
    cfg["updated_at"] = _now()
    save_cfg(cfg)
    return {"ok": True, "msg": "已保存", "mount": mount_public(m)}


def mount_remove(mid):
    """只移除「挂载记录」，磁盘上的文件一个都不动"""
    cfg = load_cfg()
    arr = mounts_raw(cfg)
    left = [m for m in arr if str(m.get("id")) != str(mid)]
    if len(left) == len(arr):
        return {"ok": False, "msg": "资料夹不存在"}
    cfg["mounts"] = left
    cfg["updated_at"] = _now()
    save_cfg(cfg)
    return {"ok": True, "msg": "已从列表移除（磁盘上的文件没有动）"}


def mount_abs(mid, rel, must_exist=True):
    """资料夹内的相对路径 → 绝对路径；越界 / 不存在返回 None"""
    m = mount_find(mid)
    base = mount_root(m)
    if not base:
        return None
    r = str(rel or "").replace("\\", "/").strip("/")
    if not r or ".." in r.split("/"):
        return None
    full = os.path.normpath(os.path.join(base, r.replace("/", os.sep)))
    try:
        if os.path.commonpath([os.path.normpath(base), full]) != os.path.normpath(base):
            return None
    except ValueError:
        return None
    if must_exist and not os.path.isfile(full):
        return None
    return full


def mount_scan(mid, limit=SCAN_LIMIT, depth=SCAN_DEPTH):
    """扫描资料夹（只读）。返回文件清单 + 目录清单 + 是否被截断。"""
    m = mount_find(mid)
    if not m:
        return {"ok": False, "msg": "资料夹不存在（可能已被移除）", "files": [], "dirs": []}
    base = mount_root(m)
    if not base:
        return {"ok": False, "msg": "文件夹打不开：不存在或已断开连接",
                "files": [], "dirs": [], "mount": mount_public(m)}
    hidden = set(m.get("hidden") or [])
    aliases = m.get("aliases") or {}
    files, dirs = [], []
    total_size = 0
    truncated = False
    base_depth = base.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted([d for d in dirnames if not d.startswith(".") and d.lower() not in _IGNORE],
                             key=lambda x: x.lower())
        cur_depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if cur_depth >= depth:
            dirnames[:] = []
        rel_dir = os.path.relpath(dirpath, base).replace(os.sep, "/")
        if rel_dir == ".":
            rel_dir = ""
        if rel_dir:
            dirs.append(rel_dir)
        for fn in sorted(filenames, key=lambda x: x.lower()):
            if fn.startswith(".") or fn.lower() in _IGNORE:
                continue
            full = os.path.join(dirpath, fn)
            try:
                if not os.path.isfile(full):
                    continue
                st = os.stat(full)
            except Exception:
                continue
            rel = (rel_dir + "/" + fn) if rel_dir else fn
            size = st.st_size
            total_size += size
            k = kind_of(fn)
            al = str(aliases.get(rel) or "").strip()
            files.append({
                "rel": rel, "name": fn, "dir": rel_dir, "depth": cur_depth,
                "ext": ext_of(fn), "kind": k,
                "size": size, "size_text": human_size(size),
                "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "mtime_ts": int(st.st_mtime),
                "alias": al, "display": al or fn,
                "hidden": rel in hidden,
                "previewable": k in ("image", "video", "audio", "pdf", "office", "text"),
            })
            if len(files) >= limit:
                truncated = True
                break
        if truncated:
            break
    # 顺手把「上次加载」的统计写回配置（只写 cfg，不碰文件）
    try:
        cfg = load_cfg()
        for x in mounts_raw(cfg):
            if str(x.get("id")) == str(mid):
                x["last_scan"] = {"at": _now(), "count": len(files),
                                  "size": total_size, "truncated": truncated}
        cfg["mounts"] = mounts_raw(cfg)
        cfg["updated_at"] = _now()
        save_cfg(cfg)
        m = mount_find(mid) or m
    except Exception:
        pass
    return {"ok": True, "files": files, "dirs": sorted(dirs, key=lambda x: x.lower()),
            "total": len(files), "truncated": truncated, "limit": limit, "depth": depth,
            "size": total_size, "size_text": human_size(total_size),
            "mount": mount_public(m), "scanned_at": _now()}


def mount_set_alias(mid, rel, alias):
    cfg = load_cfg()
    arr = mounts_raw(cfg)
    m = None
    for x in arr:
        if str(x.get("id")) == str(mid):
            m = x
            break
    if not m:
        return {"ok": False, "msg": "资料夹不存在"}
    if not mount_abs(mid, rel):
        return {"ok": False, "msg": "文件不存在或不在这个资料夹里"}
    al = safe_name(alias).strip()[:80] if str(alias or "").strip() else ""
    mp = m.get("aliases") or {}
    if al:
        mp[rel] = al
    else:
        mp.pop(rel, None)
    m["aliases"] = mp
    cfg["mounts"] = arr
    cfg["updated_at"] = _now()
    save_cfg(cfg)
    return {"ok": True, "msg": "已改为显示名「%s」" % al if al else "已恢复原名", "alias": al}


def mount_set_hidden(mid, rels, hidden=True):
    """隐藏 / 恢复（只影响展示，磁盘文件不动）"""
    cfg = load_cfg()
    arr = mounts_raw(cfg)
    m = None
    for x in arr:
        if str(x.get("id")) == str(mid):
            m = x
            break
    if not m:
        return {"ok": False, "msg": "资料夹不存在"}
    cur = set(m.get("hidden") or [])
    rs = [str(r).replace("\\", "/").strip("/") for r in (rels or []) if str(r).strip()]
    if not rs:
        return {"ok": False, "msg": "没有选中文件"}
    if hidden:
        cur.update(rs)
    else:
        cur.difference_update(rs)
    m["hidden"] = sorted(cur)
    cfg["mounts"] = arr
    cfg["updated_at"] = _now()
    save_cfg(cfg)
    return {"ok": True, "msg": ("已隐藏 %d 个文件" if hidden else "已恢复 %d 个文件") % len(rs),
            "hidden_count": len(cur)}


def pick_dir(prompt_text="选择要加载的资料文件夹"):
    """调系统原生「选择文件夹」对话框。返回 (ok, 路径或提示语)。

    只在老师的本机对话框里选，不猜路径；取消 / 不支持都返回可读原因，
    前端会退回「手动填路径」的输入框，功能不会因此不可用。
    """
    import platform
    import subprocess
    s = platform.system().lower()
    try:
        if s == "darwin":
            script = ('try\nPOSIX path of (choose folder with prompt "%s")\n'
                      'on error number -128\nreturn ""\nend try' % prompt_text.replace('"', "'"))
            r = subprocess.run(["osascript", "-e", script], capture_output=True,
                               text=True, timeout=600)
            out = (r.stdout or "").strip()
            if out:
                return True, out.rstrip("/")
            err = (r.stderr or "").strip()
            if err:
                return False, "系统对话框打不开：%s（直接粘贴路径即可）" % err.splitlines()[-1]
            return False, "没有选中文件夹（如果刚才没看到弹窗，可能是系统拦了对话框——直接把路径粘进输入框也行）"
        if s == "windows":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
                "$d.Description='%s';$d.ShowNewFolderButton=$false;"
                "if($d.ShowDialog() -eq 'OK'){Write-Output $d.SelectedPath}" % prompt_text.replace("'", "''")
            )
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                               capture_output=True, text=True, timeout=600)
            out = (r.stdout or "").strip()
            if out:
                return True, out
            err = (r.stderr or "").strip()
            if err:
                return False, "系统对话框打不开：%s（直接粘贴路径即可）" % err.splitlines()[-1]
            return False, "没有选中文件夹（如果刚才没看到弹窗，直接把路径粘进输入框也行）"
        for cmd in (["zenity", "--file-selection", "--directory", "--title", prompt_text],
                    ["kdialog", "--getexistingdirectory", "."]):
            from shutil import which
            if which(cmd[0]):
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                out = (r.stdout or "").strip()
                if out:
                    return True, out
                return False, "已取消选择"
        return False, "当前系统没有可用的图形选择框，请手动填写文件夹路径"
    except Exception as e:
        return False, "打开系统对话框失败：%s（可手动填写路径）" % e


def mount_copy_to_library(mid, rels, course="", move=False):
    """把资料夹里选中的文件「复制」进课件库（默认复制，源文件不动）。

    为什么要复制而不是引用：资料夹是只读挂载、不进备份，换电脑或 U 盘拔掉就没了；
    进了课件库才是工作台自己的资料，可以随目录整体搬走。
    """
    m = mount_find(mid)
    if not m:
        return {"ok": False, "msg": "资料夹不存在（可能已被移除）", "copied": [], "failed": []}
    if not mount_root(m):
        return {"ok": False, "msg": "资料夹打不开：文件夹不存在或已断开", "copied": [], "failed": []}
    rs = [str(r).replace("\\", "/").strip("/") for r in (rels or []) if str(r).strip()]
    if not rs:
        return {"ok": False, "msg": "没有选中文件", "copied": [], "failed": []}
    cname = safe_course(course) if str(course or "").strip() else "未分课程"
    dst_dir = abs_of(cname)
    if not dst_dir:
        return {"ok": False, "msg": "课程名不合法", "copied": [], "failed": []}
    try:
        os.makedirs(dst_dir, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": "无法创建课程目录：%s" % e, "copied": [], "failed": []}
    copied, failed = [], []
    for rel in rs:
        src = mount_abs(mid, rel)
        if not src:
            failed.append({"rel": rel, "msg": "文件不存在或不在这个资料夹里"})
            continue
        try:
            target = unique_path(dst_dir, os.path.basename(src))
            if move:
                shutil.move(src, target)
            else:
                shutil.copy2(src, target)
            copied.append({"name": os.path.basename(target), "id": rel_of(target),
                           "from": rel})
        except Exception as e:
            failed.append({"rel": rel, "msg": str(e)})
    ok = bool(copied)
    if ok and not failed:
        msg = "已加入课件库：%d 个文件 → 「%s」" % (len(copied), cname)
    elif ok:
        msg = "已加入 %d 个，%d 个失败（见下方明细）" % (len(copied), len(failed))
    else:
        msg = "没有一个文件加入成功，请检查文件是否还在原文件夹里"
    return {"ok": ok, "msg": msg, "course": cname, "copied": copied, "failed": failed}
