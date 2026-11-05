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


def _fetch(url, timeout=CHECK_TIMEOUT, what="请求"):
    """带超时的 GET。仓库已公开（2026-11-04），不再需要访问令牌。
    返回未关闭的响应流（调用方负责读完并 close）——不能 with 后再 return，
    那样返回的已经是关闭的响应，读出来全是空字节（2026-11-04 踩过）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "workbench-updater"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except Exception as e:
        raise UpdateError(_net_msg(e, what))


class Updater:
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
        shutil.rmtree(os.path.join(self.dir, "stage"), ignore_errors=True)

    def _manifest_url(self, uc):
        # 固定走官方 Gitee 仓库；老配置里若残留自定义 url 也一律忽略
        return DEFAULT_MANIFEST_URL

    # ---------------- 检查更新 ----------------
    def check(self, uc):
        """拉 manifest 并比较版本。uc = 更新配置 dict（url/token）。"""
        with self.lock:
            try:
                self.state["phase"] = "checking"
                self.state["last_error"] = ""
                url = self._manifest_url(uc)
                try:
                    r = _fetch(url, CHECK_TIMEOUT, "检查更新")
                    try:
                        raw = r.read().decode("utf-8", "replace")
                    finally:
                        r.close()
                except UpdateError:
                    raise
                except urllib.error.HTTPError as e:
                    raise UpdateError(_net_msg(e, "检查更新"))
                except Exception as e:
                    raise UpdateError(_net_msg(e, "检查更新"))
                try:
                    m = json.loads(raw)
                    if not isinstance(m, dict) or not m.get("version"):
                        raise ValueError("no version")
                except Exception:
                    raise UpdateError("更新源返回的内容不是有效的 version.json（缺 version 字段）。")
                self.state["last_check"] = time.time()
                self.state["manifest"] = m
                # 附加信息：把「检测到的版本」与「安装时要用哪个包」记在一起，
                # 便于排查 404。只记录，不参与 has_update / phase 的判定 ——
                # 检测行为保持原样。
                try:
                    self.state["pkg"] = {
                        "zip": str(m.get("zip") or ""),
                        "urls": self._zip_urls(m, url),
                        "sha256": str(m.get("sha256") or ""),
                    }
                except UpdateError as pe:
                    self.state["pkg"] = {"zip": "", "urls": [],
                                         "sha256": "", "error": str(pe)}
                if is_newer(m.get("version"), self.version):
                    self.state["phase"] = "available"
                    self.state["msg"] = "发现新版本 v%s" % m.get("version")
                    return {"ok": True, "has_update": True, "manifest": m}
                self.state["phase"] = "idle"
                self.state["msg"] = "已是最新版本"
                return {"ok": True, "has_update": False, "manifest": m}
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

    def _zip_url(self, manifest, manifest_url):
        """保留旧调用方的单值语义（pending.json / 状态里只用主候选）。"""
        return self._zip_urls(manifest, manifest_url)[0]

    def apply(self, uc):
        """下载并安装（后台线程跑，进度走 self.state['progress']）。"""
        if self.state["phase"] in ("downloading", "installing"):
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
            manifest_url = self._manifest_url(uc)
            if not is_newer(m.get("version"), self.version):
                raise UpdateError("当前已是最新版本，无需更新。")

            # ---- 1. 下载 ----
            with self.lock:
                self.state["phase"] = "downloading"
            zip_urls = self._zip_urls(m, manifest_url)
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
                    if e.code == 404 and cand != zip_urls[-1]:
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
            h = hashlib.sha256()
            try:
                try:
                    with open(dl, "wb") as f:
                        while True:
                            chunk = r.read(CHUNK)
                            if not chunk:
                                break
                            f.write(chunk)
                            h.update(chunk)
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
            want = str(m.get("sha256") or "").strip().lower()
            if want and h.hexdigest() != want:
                raise UpdateError("安装包校验失败（sha256 不符），文件可能损坏，请重试。")

            # ---- 2. 暂存解压 + 结构校验 ----
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
            try:
                with open(os.path.join(stage_app, "server.py"), "r", encoding="utf-8") as f:
                    head = f.read(8192)
                mv = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', head, re.M)
                v_in_pkg = mv.group(1) if mv else ""
            except Exception:
                v_in_pkg = ""
            if v_in_pkg != str(m.get("version")):
                raise UpdateError("安装包版本（%s）与更新源版本（%s）不一致，拒装以防装错包。"
                                  % (v_in_pkg or "未知", m.get("version")))

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
                self.state["msg"] = "已安装 v%s，重启服务后生效" % m.get("version")
                self._save_state()
            pending = {"from": self.version, "to": m.get("version"),
                       "ts": int(time.time()), "zip": zip_url}
            with open(os.path.join(self.dir, "pending.json"), "w", encoding="utf-8") as f:
                json.dump(pending, f, ensure_ascii=False, indent=1)
            self._set_prog("done", 100, "安装完成，重启服务后生效")
            try:
                os.remove(dl)
            except Exception:
                pass
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
                "config": {
                    "url": self._manifest_url(uc),
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
