"""备案信息：按「访问域名」决定要不要展示，避免离线安装包带上站主备案号。

设计口径（2026-11-28 牛哥定）：
- 同一个发行包同时给公网部署和本地/内网部署用，包里不能写死任何备案号；
- 公网部署把备案号写进数据卷的 beian.json（或 compose 环境变量），更新时数据卷不丢，
  所以在线更新后备案号自动保留；
- 只有请求 Host 命中 hosts 白名单（或用 WB_PUBLIC_URL 自动带入域名）才展示；
  localhost / 127.0.0.1 / 局域网 IP 访问一律不展示；
- 公安备案图标用官方 20×20 原图，按原始尺寸显示，不再放大糊掉。
"""
import html
import json
import os
import re
import time
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("WB_DATA_DIR") or os.path.join(ROOT, "data")
CONF_PATH = os.path.join(DATA_DIR, "beian.json")

_CACHE = {"mtime": None, "data": {}}


def _load_conf():
    """读数据卷里的 beian.json；文件不存在就返回空配置。按 mtime 缓存。"""
    try:
        mtime = os.path.getmtime(CONF_PATH)
    except OSError:
        mtime = None
        data = {}
    else:
        if _CACHE["mtime"] == mtime:
            return _CACHE["data"]
        try:
            with open(CONF_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
    _CACHE["mtime"] = mtime
    _CACHE["data"] = data
    return data


def _host_of(url):
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def _patterns(conf):
    """域名白名单：配置文件 + WB_BEIAN_HOSTS + WB_PUBLIC_URL 自动带入。"""
    out = []

    def add(value):
        if isinstance(value, str):
            out.extend(p.strip().lower().rstrip(".") for p in value.split(",") if p.strip())
        elif isinstance(value, (list, tuple)):
            for p in value:
                if isinstance(p, str) and p.strip():
                    out.append(p.strip().lower().rstrip("."))

    add(conf.get("hosts"))
    add(os.environ.get("WB_BEIAN_HOSTS", ""))
    add(_host_of(os.environ.get("WB_PUBLIC_URL", "")))
    # 去重且保序
    seen = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def host_matches(host, patterns):
    """支持精确域名与 *.example.com 通配；自动去掉端口和末尾点。"""
    h = (host or "").split("@")[-1].split(":")[0].strip().lower().rstrip(".")
    if not h:
        return False
    for p in patterns:
        if p.startswith("*."):
            suffix = p[2:]
            if h == suffix or h.endswith("." + suffix):
                return True
        elif p == h:
            return True
    return False


def info(host=""):
    """返回当前请求该不该展示备案号，以及展示内容。"""
    conf = _load_conf()
    icp = (os.environ.get("WB_ICP_NO") or conf.get("icp") or "").strip()
    gong = (os.environ.get("WB_GONG_NO") or conf.get("gong") or "").strip()
    patterns = _patterns(conf)
    forced = (os.environ.get("WB_BEIAN_SHOW") == "1" or conf.get("show") is True)

    visible = bool(forced or (patterns and host_matches(host, patterns)))
    if not visible or (not icp and not gong):
        return {"show": False, "html": "", "icp": "", "gong": "",
                "icp_url": "", "gong_url": "", "icon_url": ""}

    icp_url = (os.environ.get("WB_ICP_URL") or conf.get("icp_url")
               or "https://beian.miit.gov.cn/")
    code = conf.get("gong_code") or ""
    if not code:
        m = re.search(r"(\d{10,})", gong)
        code = m.group(1) if m else ""
    gong_url = (os.environ.get("WB_GONG_URL") or conf.get("gong_url")
                or ("https://beian.mps.gov.cn/#/query/webSearch?code=" + code if code
                    else "https://beian.mps.gov.cn/"))

    parts = []
    if icp:
        parts.append('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
                     % (html.escape(icp_url, quote=True), html.escape(icp)))
    if gong:
        parts.append('<span class="beian-gong">'
                     '<img src="/beian-icon.png" alt="公安备案图标" width="20" height="20">'
                     '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a></span>'
                     % (html.escape(gong_url, quote=True), html.escape(gong)))
    return {"show": True, "html": " ".join(parts), "icp": icp, "gong": gong,
            "icp_url": icp_url, "gong_url": gong_url,
            "icon_url": "/beian-icon.png" if gong else ""}


def foot(host=""):
    """服务端渲染页用的完整页脚片段。"""
    data = info(host)
    if not data["html"]:
        return ""
    return ('<div style="max-width:460px;margin:26px auto 18px;text-align:center;'
            'font-size:11.5px;line-height:1.9;color:#849690">'
            '<span class="beian-foot">%s</span></div>' % data["html"])
