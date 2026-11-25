#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 老版 Excel（.xls / BIFF8）读取，纯标准库

为什么不引 xlrd / pandas：
  工作台是「解压即用 + Docker 离线」的，运行环境只保证 Python 标准库。
  加一个第三方库，Docker 构建、离线部署、免安装包都要跟着改，代价太大。
  而 .xls 的结构是固定的两段：OLE2 复合文档（装文件的容器） +
  BIFF8 记录流（表格数据），标准库的 struct 足够读。

读取范围（覆盖老师做试卷表格的常用写法）：
  · OLE2：FAT / DIFAT / 目录 / miniFAT（小流）都读，取 Workbook 或 Book 流；
  · BIFF8：BOUNDSHEET（取第一个工作表）、SST + CONTINUE（共享字符串）、
    LABELSST / LABEL / RSTRING / NUMBER / RK / MULRK / BOOLERR / FORMULA 缓存值；
  · BIFF5（Excel 95）只做尽力读取，遇到不认识的记录跳过；
  · 单元格格式（颜色/字体/合并）不还原 —— 导入只关心「哪一行哪一列是什么文字」。

出错一律返回 {ok:False, msg}，不抛异常到上层（导入失败要能说人话）。
"""

import struct
from html.parser import HTMLParser

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
FATSECT = 0xFFFFFFFD
DIFSECT = 0xFFFFFFFC


def _u16(b, off=0):
    return struct.unpack_from("<H", b, off)[0]


def _u32(b, off=0):
    return struct.unpack_from("<I", b, off)[0]


class _Ole(object):
    """最小 OLE2 读取器：只做「按名字取流」这一件事。"""

    def __init__(self, blob):
        self.blob = blob
        self.ssz = 1 << _u16(blob, 0x1E)
        self.mssz = 1 << _u16(blob, 0x20)
        self.cutoff = _u32(blob, 0x38) or 4096
        self.fat = self._load_fat()
        self.entries = self._load_dir()
        root = next((e for e in self.entries if e["type"] == 5), None)
        self.mini_stream = self._read_chain(root["start"], root["size"], mini=False) if root else b""
        self.minifat = self._load_minifat()

    # ---------------------------------------------------------------- 基础
    def _sector(self, i):
        off = 512 + i * self.ssz
        return self.blob[off:off + self.ssz]

    def _load_fat(self):
        difat = [_u32(self.blob, 0x4C + i * 4) for i in range(109)]
        # 扩展 DIFAT：一条链上挂着更多 FAT 扇区号
        nxt = _u32(self.blob, 0x44)
        guard = 0
        while nxt not in (ENDOFCHAIN, FREESECT) and guard < 1000:
            s = self._sector(nxt)
            cnt = (self.ssz // 4) - 1
            for i in range(cnt):
                difat.append(_u32(s, i * 4))
            nxt = _u32(s, cnt * 4)
            guard += 1
        fat = []
        for sec in difat:
            if sec in (ENDOFCHAIN, FREESECT):
                continue
            data = self._sector(sec)
            fat.extend(struct.unpack_from("<%dI" % (len(data) // 4), data))
        return fat

    def _load_minifat(self):
        start = _u32(self.blob, 0x3C)
        if start in (ENDOFCHAIN, FREESECT):
            return []
        raw = self._read_chain(start, None, mini=False)
        if not raw:
            return []
        return list(struct.unpack_from("<%dI" % (len(raw) // 4), raw))

    def _chain(self, start, table):
        out, s, guard = [], start, 0
        while s not in (ENDOFCHAIN, FREESECT) and guard < 200000:
            out.append(s)
            s = table[s] if 0 <= s < len(table) else ENDOFCHAIN
            guard += 1
        return out

    def _read_chain(self, start, size, mini):
        if mini:
            table = self.minifat
            secs = self._chain(start, table)
            data = b"".join(self.mini_stream[s * self.mssz:(s + 1) * self.mssz] for s in secs)
        else:
            secs = self._chain(start, self.fat)
            data = b"".join(self._sector(s) for s in secs)
        return data[:size] if size else data

    def _load_dir(self):
        start = _u32(self.blob, 0x30)
        raw = self._read_chain(start, None, mini=False)
        out = []
        for off in range(0, len(raw) - 127, 128):
            d = raw[off:off + 128]
            nlen = _u16(d, 0x40)
            name = d[:max(0, nlen - 2)].decode("utf-16-le", "ignore") if nlen >= 2 else ""
            out.append({"name": name, "type": d[0x42] if len(d) > 0x42 else 0,
                        "start": _u32(d, 0x74), "size": _u32(d, 0x78)})
        return out

    def stream(self, *names):
        want = {n.lower() for n in names}
        for e in self.entries:
            if e["type"] == 2 and e["name"].lower() in want:
                mini = e["size"] < self.cutoff
                return self._read_chain(e["start"], e["size"], mini=mini)
        return b""


# ------------------------------------------------------------------ BIFF8
def _records(stream):
    i, n = 0, len(stream)
    while i + 4 <= n:
        typ = _u16(stream, i)
        ln = _u16(stream, i + 2)
        yield i, typ, stream[i + 4:i + 4 + ln]
        i += 4 + ln


class _Blocks(object):
    """SST 由多条记录拼成：CONTINUE 的开头还有一个编码标志字节。"""

    def __init__(self, blocks):
        self.blocks = blocks or [b""]
        self.i = 0
        self.p = 0

    def _next(self):
        """切到下一个 CONTINUE 块，返回该块首字节（字符编码标志：1=UTF-16）。"""
        self.i += 1
        if self.i >= len(self.blocks):
            raise IndexError
        bit = self.blocks[self.i][0] & 0x01
        self.p = 1                      # 跳过 CONTINUE 的 grbit
        return bit

    def take(self, n):
        out = b""
        while n > 0:
            cur = self.blocks[self.i]
            if self.p >= len(cur):
                self._next()
                cur = self.blocks[self.i]
            k = min(len(cur) - self.p, n)
            out += cur[self.p:self.p + k]
            self.p += k
            n -= k
        return out

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return int.from_bytes(self.take(2), "little")

    def u32(self):
        return int.from_bytes(self.take(4), "little")

    def chars(self, cch, comp):
        """comp=True 表示 8 位压缩编码（按 latin-1 读），否则 UTF-16LE。"""
        out = []
        while cch > 0:
            cur = self.blocks[self.i]
            if self.p >= len(cur):
                comp = not self._next()      # 新块标志位 1=UTF-16 → comp=False
                cur = self.blocks[self.i]
            unit = 1 if comp else 2
            avail = (len(cur) - self.p) // unit
            if avail <= 0:
                comp = not self._next()
                continue
            k = min(avail, cch)
            raw = cur[self.p:self.p + k * unit]
            out.append(raw.decode("latin-1") if comp else raw.decode("utf-16-le", "ignore"))
            self.p += k * unit
            cch -= k
        return "".join(out)


def _read_sst(blocks):
    """SST → 字符串列表。跨 CONTINUE 也能读（长题干不会被截断）。"""
    br = _Blocks(blocks)
    try:
        br.u32()                        # 总数
        unique = br.u32()
    except (IndexError, struct.error):
        return []
    out = []
    for _ in range(min(unique, 200000)):
        try:
            cch = br.u16()
            grbit = br.u8()
            comp = not (grbit & 0x01)
            rich = grbit & 0x08
            ext = grbit & 0x04
            nrun = br.u16() if rich else 0
            extlen = br.u32() if ext else 0
            s = br.chars(cch, comp)
            if rich:
                br.take(4 * nrun)
            if ext:
                br.take(extlen)
            out.append(s)
        except (IndexError, struct.error):
            break
    return out


def _read_label(data, off=6):
    """字符串体：cch(2) + grbit(1) + 字符。

    LABEL / RSTRING 前面有 row/col/xf（cch 在 offset 6）；
    FORMULA 后面的 STRING 记录没有坐标，cch 在 offset 0。
    """
    if len(data) < off + 3:
        return ""
    cch = _u16(data, off)
    grbit = data[off + 2]
    start = off + 3
    if grbit & 0x01:
        return data[start:start + cch * 2].decode("utf-16-le", "ignore")
    return data[start:start + cch].decode("latin-1")


def _rk(v):
    if v & 0x02:
        n = v >> 2
        if n & 0x20000000:
            n -= 0x40000000
        num = float(n)
    else:
        num = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return num / 100.0 if (v & 0x01) else num


def _num_text(v):
    """数字单元格 → 文本。整数不带 .0，避免「5 分」变成「5.0 分」。"""
    try:
        f = float(v)
    except Exception:
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return ("%.6f" % f).rstrip("0").rstrip(".")


def _read_sheet(stream, pos):
    """从工作表 BOF 开始读到 EOF，返回 {(row, col): text}。"""
    cells = {}
    pending_formula = None
    for off, typ, data in _records(stream):
        if off < pos:
            continue
        if typ == 0x0809 and off > pos:      # 下一个 BOF（另一张表）
            break
        if typ == 0x000A:                    # EOF
            break
        try:
            if typ == 0x00FD:                # LABELSST
                r, c = _u16(data, 0), _u16(data, 2)
                cells[(r, c)] = ("__sst__", _u32(data, 6))
            elif typ in (0x0204, 0x00D6):    # LABEL / RSTRING
                r, c = _u16(data, 0), _u16(data, 2)
                cells[(r, c)] = ("text", _read_label(data))
            elif typ == 0x0203:              # NUMBER
                r, c = _u16(data, 0), _u16(data, 2)
                cells[(r, c)] = ("text", _num_text(struct.unpack_from("<d", data, 6)[0]))
            elif typ == 0x027E:              # RK
                r, c = _u16(data, 0), _u16(data, 2)
                cells[(r, c)] = ("text", _num_text(_rk(_u32(data, 6))))
            elif typ == 0x00BD:              # MULRK
                r, c0 = _u16(data, 0), _u16(data, 2)
                n = (len(data) - 6) // 6
                for i in range(n):
                    cells[(r, c0 + i)] = ("text", _num_text(_rk(_u32(data, 4 + i * 6 + 2))))
            elif typ == 0x0205:              # BOOLERR
                r, c = _u16(data, 0), _u16(data, 2)
                val, is_err = data[6], data[7]
                cells[(r, c)] = ("text", ("TRUE" if val else "FALSE") if not is_err else "")
            elif typ == 0x0006:              # FORMULA
                r, c = _u16(data, 0), _u16(data, 2)
                res = data[6:14]
                if len(res) == 8 and res[6:8] == b"\xff\xff":
                    pending_formula = (r, c)   # 字符串结果在下一条 STRING 里
                else:
                    try:
                        cells[(r, c)] = ("text", _num_text(struct.unpack("<d", res)[0]))
                    except Exception:
                        pass
            elif typ == 0x0207:              # STRING（FORMULA 的字符串结果）
                if pending_formula:
                    cells[pending_formula] = ("text", _read_label(data, 0))
                    pending_formula = None
        except Exception:
            continue
    return cells


class _HtmlTable(HTMLParser):
    """很多系统「导出 Excel」其实是一张 HTML 表，只是扩展名叫 .xls。"""

    def __init__(self):
        HTMLParser.__init__(self)
        self.rows, self.row, self.cell = [], None, None
        self.in_table, self.done = False, False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table" and not self.in_table:
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(str(x).strip() for x in self.row):
                self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.in_table:
            self.in_table = False
            self.done = bool(self.rows)

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def _html_rows(blob):
    for enc in ("utf-8", "gbk", "big5", "latin-1"):
        try:
            text = blob.decode(enc)
        except Exception:
            continue
        p = _HtmlTable()
        try:
            p.feed(text)
        except Exception:
            continue
        if p.rows:
            width = max(len(r) for r in p.rows)
            return [r + [""] * (width - len(r)) for r in p.rows]
    return []


def parse_xls(blob):
    """读 .xls 第一个工作表 → {ok, rows, sheet, degraded}"""
    if not blob:
        return {"ok": False, "msg": "文件是空的"}
    head = blob[:400].lstrip().lower()
    # ① 「导出 Excel」常见的 HTML 表
    if (head.startswith(b"<html") or head.startswith(b"<!doctype")
            or b"<table" in head):
        rows = _html_rows(blob)
        if rows:
            return {"ok": True, "rows": rows, "sheet": "", "degraded": False,
                    "header_hint": rows[0], "kind_hint": "html"}
        return {"ok": False, "msg": "这个 .xls 是网页表格，但没读出表格内容"}
    # ② 其实是 xlsx 改了扩展名
    if blob[:2] == b"PK":
        return {"ok": False, "msg": "这个文件其实是 .xlsx（被改名成 .xls），改用 .xlsx 导入即可"}
    # ③ Excel 2.0~4.0 的超老格式：裸 BIFF 流，没有 OLE 容器
    if blob[:2] in (b"\x09\x00", b"\x09\x02", b"\x09\x04"):
        return {"ok": False, "msg": "这是 Excel 2.0~4.0 的超老 .xls（裸 BIFF 流），"
                                    "请用 Excel 打开后另存为 .xlsx 再导入"}
    if blob[:8] != OLE_MAGIC:
        return {"ok": False, "msg": "不是有效的 .xls 文件（文件头不对）"}
    try:
        ole = _Ole(blob)
    except Exception as e:
        return {"ok": False, "msg": ".xls 容器解析失败（%s）" % e}
    stream = ole.stream("Workbook", "Book")
    if not stream:
        return {"ok": False, "msg": "这个 .xls 里没有 Workbook 数据（可能不是 Excel 文件）"}

    sst_blocks, sst, sheet_pos, sheet_name = [], [], None, ""
    recs = list(_records(stream))
    for idx, (off, typ, data) in enumerate(recs):
        if typ == 0x00FC:                    # SST
            sst_blocks = [data]
            for j in range(idx + 1, len(recs)):
                if recs[j][1] == 0x003C:     # CONTINUE
                    sst_blocks.append(recs[j][2])
                else:
                    break
            sst = _read_sst(sst_blocks)
            break
    for off, typ, data in recs:
        if typ == 0x0085:                    # BOUNDSHEET
            try:
                sheet_pos = _u32(data, 0)
                cch = data[6]
                grbit = data[7]
                raw = data[8:8 + cch * (2 if grbit & 0x01 else 1)]
                sheet_name = raw.decode("utf-16-le", "ignore") if grbit & 0x01 \
                    else raw.decode("latin-1")
            except Exception:
                sheet_pos = None
            if sheet_pos:
                break
    if not sheet_pos:
        return {"ok": False, "msg": "没找到工作表"}

    cells = _read_sheet(stream, sheet_pos)
    if not cells:
        return {"ok": False, "msg": "第一个工作表是空的"}
    max_r = max(r for r, _ in cells)
    max_c = max(c for _, c in cells)
    rows = []
    for r in range(max_r + 1):
        row = []
        for c in range(max_c + 1):
            v = cells.get((r, c))
            if not v:
                row.append("")
            elif v[0] == "__sst__":
                idx = v[1]
                row.append(sst[idx] if 0 <= idx < len(sst) else "")
            else:
                row.append(v[1])
        if any(str(x).strip() for x in row):
            rows.append(row)
    if not rows:
        return {"ok": False, "msg": "第一个工作表没有可读内容"}
    return {"ok": True, "rows": rows, "sheet": sheet_name,
            "degraded": False, "header_hint": rows[0]}
