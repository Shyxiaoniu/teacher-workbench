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
DATA = (os.environ.get("WB_SUBMISSION_DATA_DIR")
        or (os.path.join(os.environ["WB_DATA_DIR"], "submissions")
            if os.environ.get("WB_DATA_DIR") else "")
        or os.path.join(ROOT, "data"))
CFG_PATH = os.path.join(DATA, "courseware.json")
# 课件库根目录：
#   ① WB_COURSEWARE_DIR（容器部署，指到数据卷，重建镜像不丢）
#   ② WB_DATA_DIR/courseware（只设了数据目录时跟着走）
#   ③ 桌面版老行为：工作台根目录下的 courseware/
DEFAULT_ROOT = (os.environ.get("WB_COURSEWARE_DIR")
                or (os.path.join(os.environ["WB_DATA_DIR"], "courseware")
                    if os.environ.get("WB_DATA_DIR") else "")
                or os.path.join(WB_ROOT, "courseware"))
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
    # 容器部署：以环境变量为准。旧配置里可能留着 Windows/macOS 的绝对路径
    # （导入的备份、从桌面版迁移过来的数据），照搬会让课件页整片 404。
    env_root = os.environ.get("WB_COURSEWARE_DIR")
    if env_root:
        root = os.path.normpath(env_root)
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
    elif os.path.normpath(str(cfg.get("root") or "")) != os.path.normpath(root):
        # 环境变量纠正了根目录（容器里挂载点变了 / 旧机器路径）：落盘一次，
        # 后续所有读写都用新路径，课件与推送记录不会对不上。
        cfg["root"] = root
        cfg["auto"] = os.path.normpath(root) == os.path.normpath(DEFAULT_ROOT)
        cfg["updated_at"] = _now()
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
        body = ("<p>这是旧版 Office 格式（%s），浏览器和服务器都无法安全解析。</p>"
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


def save_stream(course, name, stream, length):
    """流式直传落盘（2026-11-04）：请求体就是文件原始字节，边收边写。

    走 base64 JSON 的 save_upload 要在浏览器里把整个文件读成字符串再翻倍
    序列化，两三百 MB 的课件就能把浏览器内存顶爆（「此页存在问题 Out of
    Memory」）。这里从请求流按 256KB 分块写盘，浏览器用 fetch body=File
    流式发送，前后端内存占用都是常数级。
    """
    folder = abs_of(safe_course(course))
    if not folder:
        return {"ok": False, "msg": "课程名不合法"}
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception as e:
        return {"ok": False, "msg": "无法创建课程目录：%s" % e}
    nm = safe_name(name)
    try:
        length = int(length or 0)
    except Exception:
        length = 0
    if length <= 0:
        return {"ok": False, "msg": "没有收到文件内容"}
    if length > MAX_UPLOAD:
        return {"ok": False, "msg": "超过 %s 上限" % human_size(MAX_UPLOAD)}
    target = unique_path(folder, nm)
    n = 0
    try:
        with open(target, "wb") as fh:
            left = length
            while left > 0:
                chunk = stream.read(min(262144, left))
                if not chunk:
                    break
                fh.write(chunk)
                n += len(chunk)
                left -= len(chunk)
    except Exception as e:
        try:
            os.remove(target)
        except Exception:
            pass
        return {"ok": False, "msg": "写入失败：%s" % e}
    if n <= 0:
        try:
            os.remove(target)
        except Exception:
            pass
        return {"ok": False, "msg": "文件是空的"}
    return {"ok": True, "saved": [{"name": os.path.basename(target),
                                   "id": rel_of(target),
                                   "size_text": human_size(n)}],
            "failed": [], "course": safe_course(course),
            "msg": "已上传 1 个文件"}


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
        # ① 服务器本地文件系统直读（活动附件就在 <数据目录>/activity/ 下，最快最稳）
        #    必须做 commonpath 校验：url 是前端传来的，`/../../etc/passwd`、
        #    `/../../data/config.json` 这种能顺着拼出程序目录外的任意文件，
        #    读出来再落到课件库里就能被下载走（任意文件读取）。
        if url.startswith("/"):
            rel = url.lstrip("/").replace("\\", "/")
            if ".." not in rel.split("/"):
                # 兼容旧前缀：历史备份里的附件地址可能带 /qf/static/uploads/，
                # 统一剥成 static/uploads/<活动>/<文件> 再到活动附件目录找。
                for pre in ("qf/static/uploads/", "static/uploads/"):
                    if rel.startswith(pre):
                        rel = "activity/" + rel[len(pre):]
                        break
                for base_dir in (os.path.join((os.environ.get("WB_DATA_DIR")
                                               or os.path.join(WB_ROOT, "data")),
                                              "activity"),
                                 WB_ROOT):
                    c = os.path.normpath(os.path.join(base_dir, rel))
                    try:
                        if os.path.commonpath([os.path.normpath(base_dir), c]) != os.path.normpath(base_dir):
                            continue
                    except ValueError:
                        continue
                    if os.path.isfile(c):
                        try:
                            with open(c, "rb") as fh:
                                blob = fh.read()
                            break
                        except Exception:
                            blob = None
        # ② 退回 HTTP 拉取（附件不在本机数据目录时兜底）
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
    """把「活动」里学生提交的文件取回来（/static/uploads/... 或绝对地址）。

    优先直接读服务器本地文件（附件就在 <数据目录>/activity/ 下，省一次网络往返），
    读不到再走 HTTP。安全：相对地址只接受以 / 开头的站内路径；
    绝对地址的 base 必须是站点内部地址，避免被人拿来当跳板访问外网（SSRF）。
    允许的范围：回环 / 私网 IP。
    """
    url = str(url or "").strip()
    if not url.startswith("/"):
        raise ValueError("只接受以 / 开头的站内地址")
    base = str(base or "").rstrip("/")
    # 前端交来的是同源相对地址（/api/act）：服务端自己走回环取，端口跟本进程一致。
    if not base.startswith("http"):
        base = "http://127.0.0.1:%s" % (os.environ.get("PORT") or "20020")
    rel = url.lstrip("/")
    if ".." not in rel.split("/"):
        for pre in ("qf/static/uploads/", "static/uploads/"):
            if rel.startswith(pre):
                rel = "activity/" + rel[len(pre):]
                break
        root = os.path.join((os.environ.get("WB_DATA_DIR")
                             or os.path.join(WB_ROOT, "data")), "activity")
        local = os.path.normpath(os.path.join(root, rel))
        try:
            if os.path.commonpath([os.path.normpath(root), local]) == os.path.normpath(root) \
                    and os.path.isfile(local):
                with open(local, "rb") as f:
                    return f.read()
        except ValueError:
            pass
    parsed = urllib.parse.urlparse(base)
    host = (parsed.hostname or "").lower()
    private = host in ("localhost", "127.0.0.1", "::1") or \
        bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", host))
    if not private:
        raise ValueError("活动附件地址必须是站内地址（回环 / 私网）")
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
        # 若删掉的正是这门课的学习单，标记一并清掉（防悬挂引用）
        try:
            cfg = load_cfg()
            sheets = cfg.get("sheets") if isinstance(cfg.get("sheets"), dict) else {}
            changed = False
            for c, v in list(sheets.items()):
                if str(v) == str(rel):
                    sheets.pop(c, None)
                    changed = True
            if changed:
                cfg["sheets"] = sheets
                save_cfg(cfg)
        except Exception:
            pass
        return {"ok": True, "msg": "已删除", "id": rel}
    except Exception as e:
        return {"ok": False, "msg": "删除失败：%s" % e}


def rename_or_move(rel, course=None, new_name=None):
    """改文件名（new_name）/ 换课程目录（course），或两者同时。"""
    full = abs_of(rel, must_exist=True)
    if not full or not os.path.isfile(full):
        return {"ok": False, "msg": "文件不存在"}
    cur_dir = os.path.dirname(full)
    dst_dir = abs_of(safe_course(course)) if str(course or "").strip() else cur_dir
    if not dst_dir:
        return {"ok": False, "msg": "课程名不合法"}
    nm = safe_name(new_name) if str(new_name or "").strip() else os.path.basename(full)
    try:
        os.makedirs(dst_dir, exist_ok=True)
        target = unique_path(dst_dir, nm)
        shutil.move(full, target)
        moved = os.path.normpath(dst_dir) != os.path.normpath(cur_dir)
        renamed = nm != os.path.basename(full)
        if moved and renamed:
            msg = "已改名为「%s」并移动到「%s」" % (nm, safe_course(course))
        elif renamed:
            msg = "已改名为「%s」" % nm
        elif moved:
            msg = "已移动到「%s」" % safe_course(course)
        else:
            msg = "没有变化"
        return {"ok": True, "msg": msg, "id": rel_of(target), "name": nm}
    except Exception as e:
        return {"ok": False, "msg": "操作失败：%s" % e}


def sheet_of(course):
    """某课程被「设为学习单」的网页文件 id（相对路径）；没设过返回 ""。

    存在配置文件 cfg["sheets"] 里（课程名 → 文件相对路径），重启不丢。
    大屏页「学习单」下拉据此自动预选，下发即部署这份网页。"""
    cfg = load_cfg()
    sheets = cfg.get("sheets") if isinstance(cfg.get("sheets"), dict) else {}
    return str(sheets.get(safe_course(course)) or "")


def set_sheet(course, rel):
    """把课程资料里的一个网页文件设为该课程的学习单（rel 为空 = 取消）。

    校验文件真实存在且在根目录内；文件被删 / 移动后标记自动失效
    （读取时以文件是否还在为准）。同一课程同时只有一个学习单，
    重复设置 = 换成新的；在「大屏」下发时按此标记自动预选并部署。"""
    course = str(course or "").strip()
    if not course or course == "未分课程":
        return {"ok": False, "msg": "请先选择一个课程文件夹（未分课程不能设学习单）"}
    cfg = load_cfg()
    sheets = cfg.get("sheets") if isinstance(cfg.get("sheets"), dict) else {}
    rel = str(rel or "").strip()
    if not rel:
        sheets.pop(safe_course(course), None)
        cfg["sheets"] = sheets
        cfg["updated_at"] = _now()
        save_cfg(cfg)
        return {"ok": True, "msg": "已取消「%s」的学习单标记" % safe_course(course), "sheet": ""}
    full = abs_of(rel, must_exist=True)
    if not full or not os.path.isfile(full):
        return {"ok": False, "msg": "文件不存在或已被移动"}
    if ext_of(full) not in ("html", "htm"):
        return {"ok": False, "msg": "只能把网页文件（.html / .htm）设为学习单"}
    sheets[safe_course(course)] = rel_of(full)
    cfg["sheets"] = sheets
    cfg["updated_at"] = _now()
    save_cfg(cfg)
    return {"ok": True, "msg": "已把「%s」设为课程「%s」的学习单" % (os.path.basename(full), safe_course(course)),
            "sheet": rel_of(full)}


def delete_course(name):
    """删除整个课程文件夹（2026-11-04 资源管理器改版）。
    递归删除，不可恢复；「未分课程」是散文件的伪归属，不能删。"""
    name = str(name or "").strip()
    if not name or name == "未分课程":
        return {"ok": False, "msg": "请选择要删除的课程文件夹"}
    folder = abs_of(safe_course(name), must_exist=True)
    if not folder or not os.path.isdir(folder):
        return {"ok": False, "msg": "课程文件夹「%s」不存在" % safe_course(name)}
    if os.path.normpath(folder) == os.path.normpath(root_dir()):
        return {"ok": False, "msg": "不能删除课件根目录"}
    try:
        shutil.rmtree(folder)
        # 课程没了，学习单标记一并清掉
        try:
            cfg = load_cfg()
            sheets = cfg.get("sheets") if isinstance(cfg.get("sheets"), dict) else {}
            if sheets.pop(safe_course(name), None) is not None:
                cfg["sheets"] = sheets
                save_cfg(cfg)
        except Exception:
            pass
        return {"ok": True, "msg": "已删除课程文件夹「%s」" % name}
    except Exception as e:
        return {"ok": False, "msg": "删除失败：%s" % e}


def rename_course(old, new):
    """课节（课程）改名：重命名根目录下的课程文件夹。
    子目录（_学习单 / _活动作品 等）整个跟着走，推送记录与课程匹配
    由前端拿到返回后自行同步（materials.lesson / courseware_links.course）。"""
    old = str(old or "").strip()
    new = str(new or "").strip()
    if not old or not new:
        return {"ok": False, "msg": "请填写新的课节名"}
    if old == "未分课程":
        return {"ok": False, "msg": "「未分课程」是散文件的默认归属，不能改名"}
    src = abs_of(safe_course(old), must_exist=True)
    if not src or not os.path.isdir(src):
        return {"ok": False, "msg": "课节「%s」不存在" % safe_course(old)}
    dst_name = safe_course(new)
    if dst_name == safe_course(old):
        return {"ok": False, "msg": "新名字和现在一样"}
    dst = abs_of(dst_name)
    if not dst:
        return {"ok": False, "msg": "课节名不合法"}
    if os.path.exists(dst):
        return {"ok": False, "msg": "已经有叫「%s」的课节了，换一个名字" % dst_name}
    try:
        os.rename(src, dst)
        # 学习单标记跟着课程改名走（2026-11-04）：键换新课程名，值的路径前缀也要换
        try:
            cfg = load_cfg()
            sheets = cfg.get("sheets") if isinstance(cfg.get("sheets"), dict) else {}
            old_key = safe_course(old)
            if old_key in sheets:
                v = str(sheets.pop(old_key) or "")
                rest = v.split("/", 1)[1] if "/" in v else v
                sheets[dst_name] = dst_name + "/" + rest if rest else dst_name
                cfg["sheets"] = sheets
                save_cfg(cfg)
        except Exception:
            pass
        return {"ok": True, "msg": "已改名为「%s」" % dst_name,
                "old": safe_course(old), "new": dst_name}
    except Exception as e:
        return {"ok": False, "msg": "改名失败：%s" % e}


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
    # 保留同一份配置文件里的其它设置（mounts / allow_lan / version…）。
    # 以前这里直接 `cfg = {"version":1,"root":p,…}` 整份覆盖：
    # 老师只要改一次课件根目录，挂载的资料夹列表和「允许局域网访问」开关就全没了，
    # 而且没有任何提示——下次打开发现资料夹"自己消失了"。
    cfg = load_cfg()
    cfg["version"] = cfg.get("version") or 1
    cfg["root"] = p
    cfg["auto"] = os.path.normpath(p) == os.path.normpath(DEFAULT_ROOT)
    cfg["updated_at"] = _now()
    save_cfg(cfg)
    return True, cfg, ("已切换到 %s" % p) + ("，并迁移了 %d 项" % moved if moved else "") + ("（目录是新建的）" if created else "")


# ===========================================================================
# 本地资料夹（mounts）已于 2026-11-01 整体下线
# ---------------------------------------------------------------------------
# 按需求「所有资料统一上传到服务器存储」，不再支持把老师电脑上的本地文件夹
# 挂进来只读展示。原先的 mounts / pick_dir 代码已整体移除；
# 旧配置文件里残留的 cfg["mounts"] 字段会被忽略，不影响课件库读写。
# ===========================================================================
