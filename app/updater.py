#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""班级工作台 2.0 · 在线更新（仅标准库）

职责：
  · 检查更新：拉取更新源 version.json（manifest），与本地 VERSION 比较
  · 下载安装：下载 zip → 校验 sha256 → 暂存解压 → 结构校验 → 备份 → 换装
  · 回滚：把最近一次备份换回去（换装失败时自动回滚，也可手动触发）
  · 重启：只写 pending 标记，真正重启由 server.py 负责（execv 原地重启）

设计要点：
  · 数据目录（app/data）绝不参与换装——数据库/上传/配置都在里面，
    换装时原地保留，只有代码文件被替换。
  · 所有网络操作带超时与中文错误分类（老师看到的是"怎么办"不是堆栈）。
  · 换装是"先备份、后替换、失败自动还原"三段式，任何一步炸了都能回到原样。
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import urllib.request
import urllib.error

CHECK_TIMEOUT = 10       # manifest 检查超时（秒）
DL_TIMEOUT = 30          # 下载读块超时（秒）
CHUNK = 256 * 1024

# 唯一更新源：官方 Gitee 仓库根目录的 version.json（manifest）。
# 2026-11-05 起不再提供自定义源入口 —— 填错地址是老师最常见的更新失败原因，
# 仓库已公开、也不需要访问令牌，固定这一处最省心。
DEFAULT_MANIFEST_URL = "https://gitee.com/shyxiaoniu/teacher-workbench/raw/main/version.json"

# 备用源（2026-11-20 加）：Gitee 的 raw 已改成 302 跳到 raw.giteeusercontent.com
# 的带签名 CDN 链接，对非浏览器客户端会**间歇性**返回 403（防盗链/限流），
# 表现为「检查更新失败：更新源拒绝访问（HTTP 403）」，过几分钟自己又好。
# 单个源被拦就等于整个更新功能不可用，所以默认源按顺序回退：
#   Gitee（国内直连快） → GitHub raw（内容同步，镜像兜底）
# 两个仓库的内容由同一次 push 更新，manifest 与安装包都一致，可放心互为备份。
MIRROR_MANIFEST_URLS = [
    "https://raw.githubusercontent.com/Shyxiaoniu/teacher-workbench/main/version.json",
]

# 下载安装包时，这些 HTTP 状态码说明「这个源这次不给」，换下一个源再试；
# 其余错误（超时/断网/证书）也换源，只有内容类错误才直接失败。
_RETRY_STATUS = (403, 429, 404)

# 最近一次连接的特别说明（如「跳过证书校验」）：check() 会把它附在返回里，
# 让老师知道刚才那一下其实是降级完成的，不算错误。
LAST_TLS_NOTE = ""


def _valid_manifest_url(u):
    """配置里填的自定义更新源：必须是完整 http(s) 地址，否则视为没配。"""
    u = str(u or "").strip()
    return u if re.match(r"^https?://\S+$", u, re.I) else ""


def _source_name(u):
    """更新源的简称，只用于提示文案（老师看得懂「Gitee 挂了」）。"""
    try:
        h = (urllib.parse.urlparse(str(u)).hostname or "").lower()
    except Exception:
        h = ""
    if "gitee" in h:
        return "Gitee"
    if "github" in h or "githubusercontent" in h:
        return "GitHub"
    return h or "更新源"


def _norm_url(u):
    return str(u or "").strip().rstrip("/").lower()


def _is_default_source(u):
    """这个地址是不是「官方默认源本身」（含镜像）。

    2026-11-20 踩到的坑：老版本前端把默认地址预填进「自定义源」输入框，
    老师一保存就落进了配置 —— 于是 `url` 非空，程序把它当成「老师指定
    单源」，多源回退形同虚设，Gitee 一 403 就彻底检查不了更新。
    所以配置里存的是默认源等价地址时，一律按「没配」处理。
    """
    n = _norm_url(u)
    if not n:
        return False
    if n == _norm_url(DEFAULT_MANIFEST_URL):
        return True
    return n in [_norm_url(x) for x in MIRROR_MANIFEST_URLS]


class UpdateError(Exception):
    """带「老师能看懂」文案的更新失败。"""


# ---------------------------------------------------------------------------
# 版本号比较：'2.0' vs '2.1.3'，逐段按数字比；解析不了的按 0 处理
# ---------------------------------------------------------------------------
def vtuple(v):
    try:
        return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)
    except Exception:
        return (0,)


def is_newer(remote, local):
    return vtuple(remote) > vtuple(local)


# ---------------------------------------------------------------------------
# 网络错误 → 中文提示（老师视角：告诉他怎么办）
# ---------------------------------------------------------------------------
def _net_msg(e, what):
    if isinstance(e, urllib.error.HTTPError):
        code = e.code
        if code in (401, 403):
            # Gitee 对公开仓库 >10MB 的 raw 文件强制登录，匿名必 403；
            # 这是平台规则不是配置错误，更新器会自动换 GitHub 源重试。
            if "gitee" in str(getattr(e, "url", "") or ""):
                return "%s失败：Gitee 大文件需要登录（HTTP 403），正在自动切换备用源。" % what
            return "%s失败：更新源拒绝访问（HTTP %d），检查更新源地址是否正确。" % (what, code)
        if code == 404:
            return ("%s失败：更新源上没找到文件（HTTP 404）。若是在下载安装包，"
                    "请确认 version.json 的 zip 对应的发行包已推送到仓库 dist/ 目录；"
                    "若是在检查更新，再核对更新源地址。" % what)
        if code == 429:
            return "%s失败：更新源限流（HTTP 429），稍等几分钟再试。" % what
        return "%s失败：更新源返回 HTTP %d。" % (what, code)
    if isinstance(e, urllib.error.URLError):
        reason = str(getattr(e, "reason", e))
        if "timed out" in reason or "timeout" in reason.lower():
            return "%s失败：连接超时，检查网络后再试。" % what
        if "Name or service" in reason or "getaddrinfo" in reason or "nodename" in reason:
            return "%s失败：域名解析不了，检查网络/DNS。" % what
        return "%s失败：连不上更新源（%s）。" % (what, reason[:120])
    if isinstance(e, TimeoutError):
        return "%s失败：连接超时，检查网络后再试。" % what
    return "%s失败：%s" % (what, e)


def _fetch(url, timeout=CHECK_TIMEOUT, what="请求", allow_insecure=None):
    """带超时的 GET。仓库已公开（2026-11-04），不再需要访问令牌。
    返回未关闭的响应流（调用方负责读完并 close）——不能 with 后再 return，
    那样返回的已经是关闭的响应，读出来全是空字节（2026-11-04 踩过）。

    2026-11-17 兼容受限网络（含 http 环境下最常见的两类失败）：
      ① 证书校验失败（学校网络 / 加速器做了 TLS 中间人、系统根证书不全）
         → 自动降级为「跳过证书校验」重试一次。更新内容随后会用 sha256
         核对，装包前还有版本一致性校验，跳过证书并不影响包完整性。
      ② 强制 https 的更新源在内网 443 出不去 → 支持 http:// 更新源
         （管理员可配），或用 WB_UPDATE_URL 环境变量整体改源。
    """
    global LAST_TLS_NOTE
    if allow_insecure is None:
        allow_insecure = (os.environ.get("WB_INSECURE_TLS") == "1")
    req = urllib.request.Request(url, headers={"User-Agent": "workbench-updater"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", e))
        tls_fail = ("CERTIFICATE_VERIFY_FAILED" in reason
                    or "SSL" in reason.upper() and "CERTIFICATE" in reason.upper())
        if tls_fail and not allow_insecure:
            # 学校网络常带一层 TLS 中间代理：证书签不出来。跳过校验重试，
            # 靠 sha256 + 包内版本号保证没装错东西。
            try:
                import ssl as _ssl
                ctx = _ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = _ssl.CERT_NONE
                r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
                LAST_TLS_NOTE = ("已跳过 HTTPS 证书校验完成本次连接"
                                 "（网络里有代理/根证书不全，不影响安装包完整性校验）。")
                return r
            except urllib.error.HTTPError:
                raise
            except Exception:
                raise UpdateError(_net_msg(e, what))
        raise UpdateError(_net_msg(e, what))
    except Exception as e:
        raise UpdateError(_net_msg(e, what))


class Updater:
    # server.py 里 UPD 是实例，暴露模块级默认源判定给它
    _is_default_source = staticmethod(_is_default_source)
    def __init__(self, data_dir, current_version, code_root, default_url=""):
        self.data_dir = data_dir
        self.version = str(current_version)
        self.code_root = code_root            # app 目录
        # 安装根：app 的上一级（zip 里第一层是 <名>/app/…，换装只动 app/）
        self.install_root = (os.path.dirname(code_root)
                             if os.path.basename(code_root).lower() == "app" else code_root)
        self.dir = os.path.join(data_dir, "update")
        os.makedirs(self.dir, exist_ok=True)
        self.lock = threading.Lock()
        # 取消标志（2026-11-25）：老师点「取消更新」后置位，下载循环/校验阶段
        # 定期检查；进入换装后不再检查（此时中止会留下半个版本，只能靠自动回滚）。
        self._cancel_evt = threading.Event()
        self.state = {                        # 内存态 + 落盘（重启不丢"有新版本"）
            "phase": "idle",                  # idle/checking/available/downloading/installing/restart_pending/failed
            "msg": "",
            "last_check": 0,
            "last_error": "",
            "manifest": None,                 # 最近一次拉到的 manifest
            "progress": {"phase": "", "pct": 0, "msg": ""},
            "backup": None,                   # {dir, from_version, ts}
            "installed": None,                # {from_version, to_version, ts}
        }
        self._load_state()
        self._bootstrap()

    # ---------------- 状态持久化 ----------------
    def _state_path(self):
        return os.path.join(self.dir, "state.json")

    def _load_state(self):
        try:
            with open(self._state_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                keep = ("last_check", "last_error", "manifest", "backup", "installed")
                for k in keep:
                    if k in d:
                        self.state[k] = d[k]
                if self.state.get("installed"):
                    self.state["phase"] = "idle"
                elif self.state.get("manifest") and is_newer(
                        (self.state.get("manifest") or {}).get("version", ""),
                        self.version):
                    self.state["phase"] = "available"
        except Exception:
            pass

    def _save_state(self):
        try:
            tmp = self._state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({k: self.state.get(k) for k in
                           ("last_check", "last_error", "manifest", "backup", "installed")},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._state_path())
        except Exception:
            pass

    def _bootstrap(self):
        """启动时收尾：上次装完重启，清理 pending 标记与暂存目录。"""
        pending = os.path.join(self.dir, "pending.json")
        if os.path.isfile(pending):
            try:
                with open(pending, "r", encoding="utf-8") as f:
                    p = json.load(f)
                self.state["installed"] = {"from_version": p.get("from", ""),
                                           "to_version": p.get("to", ""),
                                           "ts": p.get("ts", 0)}
            except Exception:
                pass
            try:
                os.remove(pending)
            except Exception:
                pass
            # 回执要落盘：只在内存里留着的话，下次重启就只剩一个 null，
            # 老师回头查「上次更新装了什么版本」会一片空白（2026-11-20）。
            self._save_state()
        shutil.rmtree(os.path.join(self.dir, "stage"), ignore_errors=True)

    def _manifest_urls(self, uc):
        """要依次尝试的 manifest 地址列表（2026-11-20 多源回退）。

        优先级：
          ① 环境变量 WB_UPDATE_URL（容器/内网部署固定镜像源用）—— 只试它；
          ② 配置里有效的 http(s) 自定义源 —— 只试它（老师自己指定的，
             乱加备用源反而会装到不一致的版本）；
          ③ 默认：官方 Gitee 源，失败再试 GitHub 镜像。
        单个源返回 403 时不再直接判定失败，换下一个源；全部失败才报错。
        """
        env = _valid_manifest_url(os.environ.get("WB_UPDATE_URL"))
        if env:
            return [env]
        cfg_url = _valid_manifest_url((uc or {}).get("url"))
        # 配置里存的若是默认源本身（老前端预填留下的），当成没配，走多源回退。
        if cfg_url and not _is_default_source(cfg_url):
            return [cfg_url]
        out = [DEFAULT_MANIFEST_URL]
        for u in MIRROR_MANIFEST_URLS:
            if _valid_manifest_url(u) and u not in out:
                out.append(u)
        return out

    def _manifest_url(self, uc):
        """保留旧调用方的单值语义：列表里的第一个（主源）。"""
        urls = self._manifest_urls(uc)
        return urls[0] if urls else DEFAULT_MANIFEST_URL

    # ---------------- 检查更新 ----------------
    def check(self, uc):
        """拉 manifest 并比较版本。uc = 更新配置 dict（url/token）。"""
        global LAST_TLS_NOTE
        with self.lock:
            try:
                LAST_TLS_NOTE = ""
                self.state["phase"] = "checking"
                self.state["last_error"] = ""
                urls = self._manifest_urls(uc)
                raw = None
                used_url = urls[0]
                fails = []
                for cand in urls:
                    try:
                        r = _fetch(cand, CHECK_TIMEOUT, "检查更新")
                        try:
                            raw = r.read().decode("utf-8", "replace")
                        finally:
                            r.close()
                        used_url = cand
                        break
                    except UpdateError as e:
                        fails.append((cand, e, "升级源不可用"))
                    except urllib.error.HTTPError as e:
                        fails.append((cand, e, "HTTP %d" % e.code))
                    except Exception as e:
                        fails.append((cand, e, "连不上"))
                if raw is None:
                    # 所有源都失败：主源单独失败时报原本的中文提示（老文案不变），
                    # 多源全挂时才列出每个源的简称与原因，免得老师只看到一句
                    # 「更新源拒绝访问」却不知道 Gitee 抽风还是网络断了。
                    if len(fails) == 1:
                        raise UpdateError(_net_msg(fails[0][1], "检查更新"))
                    brief = "；".join("%s %s" % (_source_name(u), tag)
                                      for u, _e, tag in fails)
                    raise UpdateError(
                        "检查更新失败：%d 个更新源都没有响应（%s）。"
                        "稍后重试；若长期如此，说明服务器外网访问被限制，"
                        "可在「自定义源」里填学校内网镜像的 version.json 地址。"
                        % (len(fails), brief))
                try:
                    m = json.loads(raw)
                    if not isinstance(m, dict) or not m.get("version"):
                        raise ValueError("no version")
                except Exception:
                    raise UpdateError("更新源返回的内容不是有效的 version.json（缺 version 字段）。")
                self.state["last_check"] = time.time()
                self.state["manifest"] = m
                # 记下「这次是从哪个源拿到的」：安装时下载安装包要拼同一个源的
                # 目录（Gitee 与 GitHub 的下载地址完全不同），不能重新按主源算。
                self.state["manifest_url"] = used_url
                # 附加信息：把「检测到的版本」与「安装时要用哪个包」记在一起，
                # 便于排查 404。只记录，不参与 has_update / phase 的判定 ——
                # 检测行为保持原样。
                try:
                    self.state["pkg"] = {
                        "zip": str(m.get("zip") or ""),
                        "urls": self._all_zip_urls(m, used_url, uc),
                        "sha256": str(m.get("sha256") or ""),
                    }
                except UpdateError as pe:
                    self.state["pkg"] = {"zip": "", "urls": [],
                                         "sha256": "", "error": str(pe)}
                rv, lv = vtuple(m.get("version")), vtuple(self.version)
                if rv > lv:
                    urls = ((self.state.get("pkg") or {}).get("urls") or [])
                    if urls and not self._package_reachable(urls):
                        # 有新版本但包下不到：不弹「下载并安装」，只在更新页
                        # 留一条明确错误，避免老师点了才看到 404。
                        self.state["manifest"] = {}
                        self.state["phase"] = "idle"
                        self.state["last_error"] = (
                            "发现 v%s，但安装包下载地址不可达（404/403）。"
                            "请等待发行版附件上传完成，或用离线包更新。" % m.get("version"))
                        try:
                            self._save_state()
                        except Exception:
                            pass
                        return {"ok": True, "has_update": False,
                                "manifest": {}, "local_version": self.version,
                                "relation": "package_unreachable"}
                if rv > lv:
                    self.state["phase"] = "available"
                    self.state["msg"] = "发现新版本 v%s" % m.get("version")
                    r = {"ok": True, "has_update": True, "manifest": m,
                         "local_version": self.version, "relation": "remote_newer"}
                elif rv == lv:
                    self.state["phase"] = "idle"
                    self.state["msg"] = "已是最新版本"
                    r = {"ok": True, "has_update": False, "manifest": m,
                         "local_version": self.version, "relation": "same"}
                else:
                    # 本机比更新源新（常见于本地修了补丁、更新源还没发版）：
                    # 不能说成「已是最新版本 v旧号」，那会让人以为更新倒退了。
                    self.state["phase"] = "idle"
                    self.state["msg"] = ("本机 v%s 高于更新源 v%s（更新源未同步）"
                                         % (self.version, m.get("version")))
                    r = {"ok": True, "has_update": False, "manifest": m,
                         "local_version": self.version, "relation": "local_newer"}
                if LAST_TLS_NOTE:
                    self.state["note"] = LAST_TLS_NOTE
                    r["note"] = LAST_TLS_NOTE
                return r
            except UpdateError as e:
                self.state["phase"] = "failed"
                self.state["last_error"] = str(e)
                return {"ok": False, "msg": str(e)}
            except Exception as e:
                self.state["phase"] = "failed"
                self.state["last_error"] = "检查更新失败：%s" % e
                return {"ok": False, "msg": self.state["last_error"]}
            finally:
                self._save_state()

    # ---------------- 下载 + 安装 ----------------
    def _set_prog(self, phase, pct, msg):
        self.state["progress"] = {"phase": phase, "pct": pct, "msg": msg}
        self.state["progress_ts"] = time.time()

    class Cancelled(Exception):
        """用户取消更新：只用在「还没动代码」的阶段，安全中断。"""

    def cancel(self):
        """取消正在进行的更新（仅下载 / 校验 / 解压阶段有效）。

        返回 ok=False 的两种情况：
          · 没有正在跑的任务；
          · 已进入换装（备份/写入代码）—— 这时硬停会把 app/ 留在半新半旧
            的状态，只能让它跑完或等失败自动回滚，不能给「取消」按钮。
        """
        with self.lock:
            phase = self.state.get("phase")
            if phase not in ("downloading", "installing"):
                return {"ok": False, "msg": "当前没有正在进行的更新任务。"}
            # 换装从第 60% 开始移文件（见 _install_zip 的「备份当前版本」）
            if phase == "installing" and (self.state.get("progress") or {}).get("pct", 0) >= 60:
                return {"ok": False, "msg": "已经开始写入新版本，无法取消；请等待完成（失败会自动回滚）。"}
            self._cancel_evt.set()
        return {"ok": True, "msg": "已取消，正在清理临时文件"}

    def _check_cancel(self):
        """在安全中断点调用；被取消时抛 Cancelled 由上层统一收尾。"""
        if self._cancel_evt.is_set():
            raise self.Cancelled()

    def _finish_cancel(self, dl=""):
        """取消后的统一收尾：清临时文件、状态回 idle，不影响现有版本。"""
        self._cancel_evt.clear()
        for p in (dl, os.path.join(self.dir, "download.zip"),
                  os.path.join(self.dir, "stage")):
            if not p:
                continue
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                elif os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
        with self.lock:
            self.state["phase"] = "idle"
            self.state["msg"] = "更新已取消，当前版本未改动"
            self.state["last_error"] = ""
            self._save_state()
        self._set_prog("cancelled", 0, "已取消，当前版本未改动")

    def _zip_urls(self, manifest, manifest_url):
        """把 manifest 的 zip 字段解析成「可以尝试的下载地址列表」。

        2026-11-05 修复「检测得到更新、点下载却报 404」：
          · 发行包按仓库约定放在 dist/ 下，但 make_release 早先只把
            纯文件名写进 version.json；_zip_url 又直接拼到 manifest
            所在目录（仓库根），于是去下载
            <仓库根>/班级工作台2.0.x.zip —— 这个文件根本不存在。
            check() 只拉 version.json、从不碰安装包，所以检测永远成功、
            安装永远 404，两边结论不一致。
          · 现在兼容三种写法并给出候选：完整 URL / 仓库相对路径（含子目录）
            / 老格式纯文件名（先试 dist/，再试仓库根）。
        """
        z = str(manifest.get("zip") or "").strip().replace("\\", "/")
        if not z:
            raise UpdateError("version.json 里没有 zip 字段，不知道去哪下载安装包。")
        if re.match(r"^https?://", z, re.I):
            return [z]
        base = manifest_url.rsplit("/", 1)[0]
        quoted = urllib.parse.quote(z.lstrip("/"), safe="/")
        urls = []
        if "/" in z.lstrip("/"):
            # 新格式：相对仓库根的路径，例如 dist/班级工作台2.0.13-261105.zip
            urls.append(base + "/" + quoted)
        else:
            # 老格式纯文件名：包在 dist/ 下；也保留仓库根兜底
            urls.append(base + "/dist/" + quoted)
            urls.append(base + "/" + quoted)
        # 去重，保持顺序
        out = []
        for u in urls:
            if u not in out:
                out.append(u)
        return out

    def _package_reachable(self, urls):
        """manifest 说有新版本还不够：包本身必须能下载，否则只显示更新提示、
           点下载却 404——这正是 2.1.20 安装包未挂到发行版时踩的坑。 """
        try:
            import urllib.request
        except Exception:
            return True
        for u in (urls or []):
            for method in ("HEAD", "GET"):
                try:
                    h = {"User-Agent": "class-workbench-updater"}
                    if method == "GET":
                        h["Range"] = "bytes=0-0"   # 只探 1 字节，避免拉整包
                    req = urllib.request.Request(u, method=method, headers=h)
                    with urllib.request.urlopen(req, timeout=10) as r:
                        if getattr(r, "status", 200) < 400:
                            return True
                except Exception:
                    continue
        return False

    def _zip_url(self, manifest, manifest_url):
        """保留旧调用方的单值语义（pending.json / 状态里只用主候选）。"""
        return self._zip_urls(manifest, manifest_url)[0]

    def _all_zip_urls(self, manifest, manifest_url, uc):
        """安装包的全部候选地址，按「下载成功率」排序（2026-11-22）。

        2026-11-22 实测：Gitee 对公开仓库 >10MB 的 raw 文件强制登录，
        匿名请求一律 403「large file require login for access」；27MB 的
        离线包每次都撞 403 再落 GitHub，白等 0.6 秒还吓人一跳。所以把
        GitHub raw（匿名可下，实测 27MB 正常）提到前面，Gitee 留作兜底
        （万一某网络只通 Gitee 不通 GitHub，仍可回退；小文件 manifest
        继续走 Gitee，国内直连快）。

        只对「没有自定义源」的情况重排 —— 老师指了内网镜像就只认它。
        """
        urls = self._zip_urls(manifest, manifest_url)
        for alt in self._manifest_urls(uc):
            if alt == manifest_url:
                continue
            try:
                for u2 in self._zip_urls(manifest, alt):
                    if u2 not in urls:
                        urls.append(u2)
            except UpdateError:
                pass
        # 下载优先 GitHub raw；Gitee raw 大文件必 403，垫底留作兜底
        if not _valid_manifest_url((uc or {}).get("url")):
            urls.sort(key=lambda u: 0 if ("githubusercontent" in u or "github.com" in u) else 1)
        return urls

    def apply(self, uc):
        """下载并安装（后台线程跑，进度走 self.state['progress']）。"""
        self._cancel_evt.clear()
        if self.state["phase"] in ("downloading", "installing"):
            # 15 分钟内没有任何进度推进 = 上一次下载卡死了（网络断开后线程
            # 没能正常收尾）。放行重新安装，别让老师对着一句
            # 「正在下载安装中，请稍候」等到天荒地老（2026-11-17）。
            ts = self.state.get("progress_ts") or 0
            if time.time() - ts < 900:
                return {"ok": False, "msg": "正在下载安装中，请稍候"}
        t = threading.Thread(target=self._apply, args=(uc,), daemon=True)
        t.start()
        return {"ok": True, "msg": "开始更新"}

    def _apply(self, uc):
        try:
            # 每次安装都重新拉 manifest：缓存的可能是旧版本/旧 sha（源是会变的）
            r = self.check(uc)
            if not r.get("ok"):
                raise UpdateError(r.get("msg") or "检查更新失败")
            m = self.state.get("manifest") or {}
            # 用 check() 实际成功过的那个源来拼下载地址：Gitee 与 GitHub 的
            # 下载路径完全不同，按主源重算会把 GitHub 拿到的 manifest 拼成
            # 一个不存在的 Gitee 地址。
            manifest_url = self.state.get("manifest_url") or self._manifest_url(uc)
            if r.get("relation") == "local_newer":
                raise UpdateError("本机 v%s 高于更新源 v%s，更新源未同步，无需（也不会）降级。"
                                  % (self.version, m.get("version")))
            if not is_newer(m.get("version"), self.version):
                raise UpdateError("当前已是最新版本，无需更新。")

            # ---- 1. 下载 ----
            with self.lock:
                self.state["phase"] = "downloading"
            zip_urls = self._all_zip_urls(m, manifest_url, uc)
            zip_url = zip_urls[0]
            dl = os.path.join(self.dir, "download.zip")
            self._set_prog("download", 0, "连接更新源…")
            # 老 manifest 只写纯文件名时，先试 dist/ 再试仓库根；
            # 全 404 要给出「包和 manifest 对不上」的明确结论，而不是
            # 笼统一句「检查更新源地址」（地址其实是对的，缺的是包）。
            r = None
            tried = []
            for cand in zip_urls:
                try:
                    r = _fetch(cand, DL_TIMEOUT, "下载安装包")
                    zip_url = cand
                    break
                except urllib.error.HTTPError as e:
                    tried.append(cand)
                    if e.code in _RETRY_STATUS and cand != zip_urls[-1]:
                        try:
                            e.close()
                        except Exception:
                            pass
                        continue
                    raise UpdateError(_net_msg(e, "下载安装包"))
                except UpdateError:
                    raise
                except Exception as e:
                    raise UpdateError(_net_msg(e, "下载安装包"))
            if r is None:
                raise UpdateError(
                    "安装包不在更新源上（试过 %d 个地址）。请确认 version.json 的 "
                    "version、zip、sha256 与仓库 dist/ 下实际发行的包一致：%s"
                    % (len(tried), "、".join(tried[-2:])))
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            try:
                try:
                    with open(dl, "wb") as f:
                        while True:
                            self._check_cancel()      # 下载中每块检查一次取消
                            chunk = r.read(CHUNK)
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            pct = min(99, int(done * 100 / total)) if total else 0
                            self._set_prog("download", pct,
                                           "下载中 %.1f MB%s" % (done / 1048576.0,
                                                                (" / %.1f MB" % (total / 1048576.0)) if total else ""))
                finally:
                    r.close()
            except UpdateError:
                raise
            except Exception as e:
                raise UpdateError(_net_msg(e, "下载安装包"))
            if total and done < total:
                raise UpdateError("下载不完整（%d/%d 字节），请重试。" % (done, total))

            # ---- 2~4. 解压 → 校验 → 备份 → 换装 → 写重启标记 ----
            # 统一走 _install_zip：离线安装（install_local）走同一段，
            # 两条路的校验强度、备份与自动回滚行为完全一致。
            self._install_zip(dl, m.get("version"), m.get("sha256"), zip_url)
            try:
                os.remove(dl)
            except Exception:
                pass
        except self.Cancelled:
            self._finish_cancel(os.path.join(self.dir, "download.zip"))
        except UpdateError as e:
            with self.lock:
                self.state["phase"] = "failed"
                self.state["last_error"] = str(e)
                self._save_state()
            self._set_prog("error", 0, str(e))
        except Exception as e:
            with self.lock:
                self.state["phase"] = "failed"
                self.state["last_error"] = "更新失败：%s" % e
                self._save_state()
            self._set_prog("error", 0, "更新失败：%s" % e)

    # ---------------- 换装（在线 / 离线共用）----------------
    def _install_zip(self, dl, want_version="", want_sha="", src=""):
        """把已落地的安装包换装：校验 → 解压 → 结构/版本校验 → 备份 → 替换 → 写重启标记。

        在线更新（_apply）与离线安装（install_local）共用这一段，保证两条路的
        校验强度、备份与自动回滚行为完全一致 —— 离线包不比在线包「更可信」，
        只是来源不同（U 盘拷进来 vs 网上下的），该查的一样要查。

        want_sha 为空 = 离线包（没有 manifest 可比，改用 zip 内部 CRC 兜底）。
        返回包里的版本号，供调用方记审计。
        """
        # ---- 1. 完整性校验 ----
        want = str(want_sha or "").strip().lower()
        self._check_cancel()          # 还没动代码，可以取消
        if want:
            h = hashlib.sha256()
            try:
                with open(dl, "rb") as f:
                    while True:
                        self._check_cancel()
                        chunk = f.read(CHUNK)
                        if not chunk:
                            break
                        h.update(chunk)
            except Exception as e:
                raise UpdateError("读安装包失败：%s" % e)
            if h.hexdigest() != want:
                raise UpdateError("安装包校验失败（sha256 不符），文件可能损坏，请重试。")
        else:
            # 离线包没有 manifest 里的 sha256 可比：退一步用 zip 自带的 CRC32
            # 逐个文件校验。能查出 U 盘/网盘拷贝造成的静默损坏，比什么都不查强。
            try:
                with _zipfile.ZipFile(dl) as z:
                    bad = z.testzip()
            except Exception as e:
                raise UpdateError("这不是一个有效的 zip 安装包：%s" % e)
            if bad:
                raise UpdateError("安装包已损坏（%s 的文件校验和对不上），请重新拷贝一份再试。"
                                  % bad)

        # ---- 2. 暂存解压 + 结构校验 ----
        self._check_cancel()          # 解压前仍可取消（只动 stage 临时目录）
        with self.lock:
            self.state["phase"] = "installing"
        self._set_prog("install", 0, "解压安装包…")
        stage = os.path.join(self.dir, "stage")
        shutil.rmtree(stage, ignore_errors=True)
        os.makedirs(stage, exist_ok=True)
        try:
            with zipfile_guard(dl, stage):
                pass
        except Exception as e:
            raise UpdateError("安装包解压失败：%s" % e)
        # zip 第一层可能有包名目录（<名>/app/…），自动定位包里的 app/
        stage_app = _find_app_dir(stage)
        if not stage_app:
            raise UpdateError("安装包结构不对（里面没有 app/server.py），不能安装。")
        v_in_pkg = ""
        try:
            # 必须读全文：VERSION 在 server.py 里位置很靠后（前面是模块文档与
            # 一大段 import），只读前 8KB 会搜不到 —— 2026-11-20 离线安装实测
            # 就栽在这里，报「读不到 VERSION」。全文 300KB 左右，一次读完不心疼。
            with open(os.path.join(stage_app, "server.py"), "r",
                      encoding="utf-8", errors="replace") as f:
                py_src = f.read()
            mv = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', py_src, re.M)
            v_in_pkg = mv.group(1) if mv else ""
        except Exception:
            v_in_pkg = ""
        if not v_in_pkg:
            raise UpdateError("安装包里的 app/server.py 读不到 VERSION，无法确认版本，拒装。")
        if want_version and v_in_pkg != str(want_version):
            raise UpdateError("安装包版本（%s）与更新源版本（%s）不一致，拒装以防装错包。"
                              % (v_in_pkg, want_version))

        # 最后一个安全取消点：下一步就要把旧代码移走，之后不可取消
        self._check_cancel()

        # ---- 3. 备份当前代码（app/data 保留原地，绝不搬动）----
        self._set_prog("install", 60, "备份当前版本…")
        bdir = os.path.join(self.dir, "backup-v%s-%d" % (self.version, int(time.time())))
        os.makedirs(bdir, exist_ok=True)
        keep = {"data", "__pycache__"}
        moved = []
        try:
            for name in os.listdir(self.code_root):
                if name in keep:
                    continue
                shutil.move(os.path.join(self.code_root, name),
                            os.path.join(bdir, name))
                moved.append(name)
            # 装新代码
            self._set_prog("install", 80, "写入新版本…")
            for name in os.listdir(stage_app):
                if name == "data":       # 包里的 data 只是占位说明，不覆盖真实数据
                    continue
                shutil.move(os.path.join(stage_app, name),
                            os.path.join(self.code_root, name))
        except Exception as e:
            # ---- 失败自动回滚 ----
            self._set_prog("install", 0, "安装失败，正在回滚…")
            try:
                for name in os.listdir(self.code_root):
                    if name in keep:
                        continue
                    p = os.path.join(self.code_root, name)
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                for name in moved:
                    shutil.move(os.path.join(bdir, name),
                                os.path.join(self.code_root, name))
                self._set_prog("install", 0, "已回滚到原版本")
            except Exception:
                self._set_prog("install", 0, "自动回滚也失败了，请用手动回滚按钮或重新解压原安装包")
            raise UpdateError("安装失败已回滚：%s" % e)

        # ---- 4. 写重启标记，等 server 重启加载新代码 ----
        with self.lock:
            self.state["phase"] = "restart_pending"
            self.state["backup"] = {"dir": bdir, "from_version": self.version,
                                    "ts": int(time.time())}
            self.state["msg"] = "已安装 v%s，重启服务后生效" % v_in_pkg
            self._save_state()
        pending = {"from": self.version, "to": v_in_pkg,
                   "ts": int(time.time()), "zip": src or os.path.basename(dl)}
        with open(os.path.join(self.dir, "pending.json"), "w", encoding="utf-8") as f:
            json.dump(pending, f, ensure_ascii=False, indent=1)
        self._set_prog("done", 100, "安装完成，重启服务后生效")
        return v_in_pkg

    # ---------------- 离线安装包（服务端没有外网时的正路）----------------
    def install_local(self, zip_path, label=""):
        """直接换装一个本地 zip：老师在能上网的电脑下载发行包，拷进内网再上传。

        与在线更新走同一套换装逻辑（_install_zip），进度同样写
        state['progress']，前端照旧轮询 /update/progress。
        """
        self._cancel_evt.clear()
        if self.state["phase"] in ("downloading", "installing"):
            ts = self.state.get("progress_ts") or 0
            if time.time() - ts < 900:
                return {"ok": False, "msg": "正在下载安装中，请稍候"}
        t = threading.Thread(target=self._install_local, args=(zip_path, label),
                             daemon=True)
        t.start()
        return {"ok": True, "msg": "开始安装离线包"}

    def _install_local(self, zip_path, label):
        try:
            with self.lock:
                self.state["last_error"] = ""
            self._set_prog("install", 2, "校验离线安装包…")
            if not os.path.isfile(zip_path):
                raise UpdateError("没收到安装包文件，请重新上传。")
            v = self._install_zip(zip_path, "", "",
                                  "offline:%s" % (label or os.path.basename(zip_path)))
            # 离线包装完补一条最小 manifest：状态页要显示「现在是多少版本」，
            # 否则前端还挂着上一次在线检查留下的旧「有新版本」。
            with self.lock:
                self.state["manifest"] = {"version": v, "date": "", "notes": [],
                                          "zip": "", "sha256": "", "offline": True}
                self.state["manifest_url"] = ""
                self.state["pkg"] = {"zip": label or os.path.basename(zip_path),
                                     "urls": [], "sha256": "", "offline": True}
                self._save_state()
        except self.Cancelled:
            self._finish_cancel(zip_path)
        except UpdateError as e:
            with self.lock:
                self.state["phase"] = "failed"
                self.state["last_error"] = str(e)
                self._save_state()
            self._set_prog("error", 0, str(e))
        except Exception as e:
            with self.lock:
                self.state["phase"] = "failed"
                self.state["last_error"] = "安装离线包失败：%s" % e
                self._save_state()
            self._set_prog("error", 0, "安装离线包失败：%s" % e)

    # ---------------- 回滚 ----------------
    def rollback(self):
        with self.lock:
            b = self.state.get("backup")
            if not b or not os.path.isdir(b.get("dir", "")):
                return {"ok": False, "msg": "没有可用的备份，无法回滚。"}
            keep = {"data", "__pycache__"}
            try:
                # 当前（新版）代码退位
                for name in os.listdir(self.code_root):
                    if name in keep:
                        continue
                    p = os.path.join(self.code_root, name)
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        try:
                            os.remove(p)
                        except Exception:
                            pass
                # 备份换回来
                for name in os.listdir(b["dir"]):
                    shutil.move(os.path.join(b["dir"], name),
                                os.path.join(self.code_root, name))
                os.rmdir(b["dir"])
            except Exception as e:
                return {"ok": False, "msg": "回滚失败：%s（备份还在 %s）" % (e, b["dir"])}
            # 重启标记一并撤掉：否则下次重启会被误记成"已装新版"
            try:
                p = os.path.join(self.dir, "pending.json")
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
            self.state["backup"] = None
            self.state["phase"] = "idle"
            self.state["msg"] = "已回滚到 v%s，重启服务后生效" % b.get("from_version")
            self._save_state()
            return {"ok": True, "msg": self.state["msg"], "version": b.get("from_version")}

    # ---------------- 状态给前端 ----------------
    def info(self, uc):
        with self.lock:
            m = self.state.get("manifest") or {}
            # 2.1.20：离线升级源曾留下 2.1.20.1 的旧 manifest，
            # 但本机实际版本仍是 2.1.20。已记录为 installed/backup 的
            # 版本就是本机装过的版本，不能再当成「可更新」反复提示。
            inst = self.state.get("installed") or {}
            if m and (str(m.get("version") or "") in (
                    str(inst.get("from_version") or ""),
                    str(inst.get("to_version") or ""))):
                self.state["manifest"] = {}
                try:
                    self._save_state()
                except Exception:
                    pass
                m = {}
            b = self.state.get("backup") or None
            return {
                "ok": True,
                "version": self.version,
                "phase": self.state["phase"],
                "msg": self.state.get("msg", ""),
                "last_check": self.state.get("last_check", 0),
                "last_error": self.state.get("last_error", ""),
                "latest": m.get("version") or "",
                "date": m.get("date") or "",
                "notes": m.get("notes") or [],
                "pkg": dict(self.state.get("pkg") or {}),
                "has_update": bool(m.get("version") and is_newer(m.get("version"), self.version)
                                   and self.state["phase"] in ("idle", "available", "failed")),
                "progress": dict(self.state.get("progress") or {}),
                "backup": ({"from_version": b.get("from_version"), "ts": b.get("ts")}
                           if b else None),
                "installed": self.state.get("installed"),
                "note": str(self.state.get("note") or ""),
                "config": {
                    "url": str(uc.get("url") or ""),
                    # 实际生效的源：优先显示上一次检查真正成功的那一个，
                    # 便于老师确认「现在走的是 Gitee 还是 GitHub 镜像」。
                    "effective_url": (self.state.get("manifest_url")
                                      or self._manifest_url(uc)),
                    "mirrors": list(MIRROR_MANIFEST_URLS),
                    "auto": bool(uc.get("auto", True)),
                    "interval_hours": int(uc.get("interval_hours") or 24),
                    "default_url": DEFAULT_MANIFEST_URL,
                },
            }


# ---------------------------------------------------------------------------
# zip 解压（zip-slip 防护）：with zipfile_guard(zip_path, dest)
# ---------------------------------------------------------------------------
import zipfile as _zipfile     # noqa: E402
from contextlib import contextmanager as _cm


@_cm
def zipfile_guard(zip_path, dest):
    with _zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name.startswith("__MACOSX") or "/." in name:
                continue
            parts = [p for p in name.split("/") if p not in ("", ".", "..")]
            if not parts or name.startswith("/"):
                continue
            target = os.path.join(dest, *parts)
            if not os.path.abspath(target).startswith(os.path.abspath(dest) + os.sep):
                continue          # 目录穿越，丢弃
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # 权限位必须自己补回来（2026-11-05 牛哥反馈：Mac 上更新完重启不了）。
            # open(target, "wb") 建出来的是 0666 & ~umask（通常 0644），zip 里存的
            # 755 被彻底丢掉；换装后 app/启动教师端.command、docker/*.sh 全部
            # 不可执行 —— 老师点「重启」或下次双击启动就是「没有权限」。
            # 注意 mode 为 0 说明打包时没写权限（老版本 make_release），保持默认即可。
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                try:
                    os.chmod(target, mode)
                except Exception:
                    pass
    yield


def _find_app_dir(stage):
    """在解压结果里找包内的 app/ 目录（第一层可能是包名目录，深度 ≤2）。"""
    root = os.path.join(stage, "app")
    if os.path.isfile(os.path.join(root, "server.py")):
        return root
    try:
        for name in os.listdir(stage):
            d = os.path.join(stage, name, "app")
            if os.path.isfile(os.path.join(d, "server.py")):
                return d
            for name2 in os.listdir(os.path.join(stage, name)):
                d2 = os.path.join(stage, name, name2, "app")
                if os.path.isfile(os.path.join(d2, "server.py")):
                    return d2
    except Exception:
        pass
    return ""
