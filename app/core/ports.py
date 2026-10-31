#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端口选择 · 被占用时的「改端口」引导（仅标准库）

为什么默认不用 8080
--------------------
8080 / 3000 / 5000 / 8000 是开发工具的“公共停车位”，装了 IIS、微信开发者工具、
各种代理/迅雷/网盘客户端的机器上十有八九已经被占着。教师机是教室里的办公电脑，
不该因为别的软件先开机一步就起不来。所以默认改成 20020（不在任何常见服务清单里，
也不在 Windows/macOS 的临时端口段 49152+ 里，不会和系统随机分配的端口撞车）。

被占用了怎么办
--------------
绝不停在“端口被占用”这一行字上。这里提供三层兜底，从好到差自动降级：

  1. tkinter 原生窗口（python.org 安装包自带）—— 直接弹一个小窗，输入新端口；
  2. 本地网页（没有 tkinter 时）—— 在本机随机端口起一个只有一页的“端口设置页”，
     用默认浏览器打开，填完点“保存并继续启动”就回到终端继续；
  3. 控制台问答（无图形界面/无浏览器）—— 终端里输入端口。

无论走哪条路，选好的端口都写回 data/config.json 的 "port"，下次启动直接用它。
"""
import os
import sys
import json
import time
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 默认端口：不常用 + 不在系统动态端口段里
DEFAULT_PORT = 20020
MIN_PORT, MAX_PORT = 1024, 65535

# 换端口时优先试这几个（同一台机器上通常都空着，且相邻好记）
FALLBACK_PORTS = (20020, 20021, 20022, 20023, 20120, 20200)


# ---------------------------------------------------------------------------
# 端口探测
# ---------------------------------------------------------------------------
def is_free(port, host="127.0.0.1", timeout=0.35):
    """这个端口在本机能不能用。

    先 connect 一次（有服务在监听就说明被占），再 bind 一次
    （覆盖“只监听别的网卡 / TIME_WAIT 未释放”的情况）。
    故意不给 bind 加 SO_REUSEADDR：Windows 上那个选项会让人 bind 到别人的端口，
    看起来“成功”，实际流量全被原进程接走。
    """
    try:
        port = int(port)
    except Exception:
        return False
    if not (MIN_PORT <= port <= MAX_PORT):
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as c:
            c.settimeout(timeout)
            if c.connect_ex((host, port)) == 0:
                return False
    except Exception:
        pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(timeout)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def free_ports(start=DEFAULT_PORT, count=3, span=400, avoid=()):
    """从 start 往后找 count 个空闲端口（跳过 avoid 里的）"""
    out = []
    avoid = set(int(x) for x in (avoid or ()) if str(x).isdigit())
    p = int(start)
    stop = min(MAX_PORT, p + span)
    while p <= stop and len(out) < count:
        if p not in avoid and is_free(p):
            out.append(p)
        p += 1
    return out


def suggest_ports(current, n=3):
    """给用户推荐的几个端口：优先“当前端口 ±1/±2”，再退到 FALLBACK_PORTS"""
    cand = []
    for p in (current + 1, current + 2, current - 1, current + 10):
        if MIN_PORT <= p <= MAX_PORT:
            cand.append(p)
    cand.extend(FALLBACK_PORTS)
    seen, out = set(), []
    for p in cand:
        if p in seen:
            continue
        seen.add(p)
        if is_free(p):
            out.append(p)
        if len(out) >= n:
            break
    return out


# ---------------------------------------------------------------------------
# 配置读写（只碰 "port" 这一个键，其余原样保留）
# ---------------------------------------------------------------------------
def read_cfg(cfg_path):
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_port(cfg_path, port):
    d = read_cfg(cfg_path)
    d["port"] = int(port)
    try:
        os.makedirs(os.path.dirname(cfg_path) or ".", exist_ok=True)
        tmp = cfg_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, cfg_path)
        return True
    except Exception:
        return False


def config_port(cfg_path, default=DEFAULT_PORT):
    try:
        p = int(read_cfg(cfg_path).get("port") or default)
    except Exception:
        p = default
    return p if MIN_PORT <= p <= MAX_PORT else default


# ---------------------------------------------------------------------------
# 方案一：tkinter 原生窗口
# ---------------------------------------------------------------------------
def _tk_available():
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def _ask_tk(current, reason, ports, cfg_path):
    import tkinter as tk

    res = {"port": None}
    root = tk.Tk()
    root.title("班级工作台 · 端口设置")
    root.configure(bg="#F7FAF9")
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    w, h = 470, 330
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))
    except Exception:
        root.geometry("%dx%d" % (w, h))
    root.resizable(False, False)

    tk.Label(root, text="端口 %d 被占用" % current, bg="#F7FAF9", fg="#B3261E",
             font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=22, pady=(18, 2))
    tk.Label(root, text=reason or "", bg="#F7FAF9", fg="#55655F",
             font=("Microsoft YaHei UI", 9), justify="left", wraplength=420).pack(anchor="w", padx=22)
    tk.Label(root, text="换一个端口继续启动（会自动记住，下次不用再改）：",
             bg="#F7FAF9", fg="#16231F", font=("Microsoft YaHei UI", 10)).pack(anchor="w", padx=22, pady=(14, 6))

    var = tk.StringVar(value=str(ports[0] if ports else DEFAULT_PORT))
    row = tk.Frame(root, bg="#F7FAF9")
    row.pack(anchor="w", padx=22, fill="x")
    ent = tk.Entry(row, textvariable=var, width=10, font=("Consolas", 13),
                   relief="solid", bd=1)
    ent.pack(side="left", ipady=4)
    tk.Label(row, text="（1024 - 65535）", bg="#F7FAF9", fg="#849690",
             font=("Microsoft YaHei UI", 9)).pack(side="left", padx=8)

    chips = tk.Frame(root, bg="#F7FAF9")
    chips.pack(anchor="w", padx=22, pady=(10, 0))
    tk.Label(chips, text="推荐：", bg="#F7FAF9", fg="#849690",
             font=("Microsoft YaHei UI", 9)).pack(side="left")
    for p in ports:
        tk.Button(chips, text=str(p), font=("Consolas", 9), relief="groove", bd=1,
                  bg="#EAF4F0", fg="#0F6E56", activebackground="#D6EBE2",
                  padx=6, pady=1, cursor="hand2",
                  command=lambda v=p: var.set(str(v))).pack(side="left", padx=(4, 0))

    msg = tk.Label(root, text="", bg="#F7FAF9", fg="#B3261E", font=("Microsoft YaHei UI", 9))
    msg.pack(anchor="w", padx=22, pady=(10, 0))

    def ok(*_):
        try:
            p = int(str(var.get()).strip())
        except Exception:
            msg.config(text="请输入数字端口")
            return
        if not (MIN_PORT <= p <= MAX_PORT):
            msg.config(text="端口范围是 1024 - 65535")
            return
        if not is_free(p):
            msg.config(text="端口 %d 也被占用了，换一个试试" % p)
            return
        save_port(cfg_path, p)
        res["port"] = p
        root.destroy()

    def cancel(*_):
        res["port"] = None
        root.destroy()

    btns = tk.Frame(root, bg="#F7FAF9")
    btns.pack(anchor="w", padx=22, pady=(8, 0))
    tk.Button(btns, text="保存并启动", command=ok, bg="#0F6E56", fg="#fff",
              activebackground="#1D9E75", activeforeground="#fff", relief="flat",
              font=("Microsoft YaHei UI", 10, "bold"), padx=18, pady=5,
              cursor="hand2").pack(side="left")
    tk.Button(btns, text="退出", command=cancel, bg="#EAF4F0", fg="#0F6E56",
              relief="flat", font=("Microsoft YaHei UI", 10), padx=14, pady=5,
              cursor="hand2").pack(side="left", padx=8)

    tk.Label(root, text="也可以直接改 data/config.json 里的 \"port\"。\n查谁占用了：netstat -ano | findstr :%d" % current,
             bg="#F7FAF9", fg="#849690", font=("Microsoft YaHei UI", 8),
             justify="left").pack(anchor="w", padx=22, pady=(12, 0))

    ent.focus_set()
    ent.select_range(0, "end")
    root.bind("<Return>", ok)
    root.bind("<Escape>", cancel)
    root.protocol("WM_DELETE_WINDOW", cancel)
    try:
        root.mainloop()
    except Exception:
        pass
    return res["port"]


# ---------------------------------------------------------------------------
# 方案二：本地网页（没有 tkinter 时的“端口设置页”）
# ---------------------------------------------------------------------------
PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>端口设置 · 班级工作台</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#F7FAF9;color:#16231F;
 font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:520px;margin:7vh auto;padding:0 20px}
.card{background:#fff;border:1px solid rgba(15,110,86,.16);border-radius:16px;
 padding:26px 28px;box-shadow:0 2px 10px rgba(15,110,86,.07)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:16px}
.logo{width:34px;height:34px;border-radius:9px;background:#0F6E56;color:#fff;
 display:grid;place-items:center;font-weight:700}
h1{font-size:20px;margin:0 0 6px}
.bad{color:#B3261E;font-weight:600}
.sub{color:#5F6E68;font-size:13.5px;margin:0 0 4px}
label{display:block;font-size:12.6px;color:#55655F;margin:18px 0 6px;font-weight:600}
input{width:100%;padding:11px 13px;border:1px solid #D6E3DE;border-radius:10px;
 font-size:16px;font-family:ui-monospace,Menlo,Consolas,monospace;background:#FBFDFC;outline:none}
input:focus{border-color:#1D9E75;box-shadow:0 0 0 3px rgba(29,158,117,.12)}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.chip{padding:6px 12px;border-radius:99px;background:#EAF4F0;color:#0F6E56;
 font-size:13px;cursor:pointer;border:1px solid transparent;font-family:inherit}
.chip:hover{border-color:#1D9E75}
button.go{width:100%;margin-top:20px;padding:12px;border:0;border-radius:10px;
 background:#0F6E56;color:#fff;font-size:15px;font-weight:600;cursor:pointer}
button.go:hover{background:#1D9E75}
button.go:disabled{opacity:.6;cursor:default}
.err{color:#B3261E;font-size:13px;min-height:20px;margin-top:10px}
.ok{color:#0B6B3A;font-size:15px;font-weight:600;padding:10px 0}
.tip{color:#849690;font-size:12.4px;line-height:1.9;margin-top:16px}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.4px;
 background:#F0F5F3;padding:1px 5px;border-radius:5px}
</style></head><body><div class="wrap"><div class="card">
<div class="brand"><div class="logo">班</div>
 <div><div style="font-weight:750;font-size:15px">班级工作台</div>
 <div style="font-size:12px;color:#849690">启动器 · 端口设置</div></div></div>
<h1>端口 <span class="bad">__PORT__</span> 被占用</h1>
<p class="sub">__REASON__</p>
<p class="sub">换一个端口继续启动，会自动记住，下次不用再改。</p>
<label>新端口（1024 - 65535）</label>
<input id="p" type="number" min="1024" max="65535" value="__SUGGEST__">
<div class="chips">__CHIPS__</div>
<div class="err" id="e"></div>
<button class="go" id="b">保存并继续启动</button>
<div class="tip">也可以直接编辑 <code>data/config.json</code> 里的 <code>"port"</code>。<br>
查谁占用了这个端口：Windows <code>netstat -ano | findstr :__PORT__</code>；
macOS/Linux <code>lsof -i :__PORT__</code>。</div>
</div></div>
<script>
var p=document.getElementById('p'),e=document.getElementById('e'),b=document.getElementById('b');
Array.prototype.forEach.call(document.querySelectorAll('.chip'),function(c){
  c.onclick=function(){p.value=c.dataset.p;e.textContent='';};
});
b.onclick=function(){
  var v=parseInt(p.value,10);
  if(!v||v<1024||v>65535){e.textContent='端口范围是 1024 - 65535';return;}
  b.disabled=true;b.textContent='保存中…';e.textContent='';
  fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({port:v})}).then(function(r){return r.json();}).then(function(j){
    if(j.ok){document.querySelector('.card').innerHTML=
      '<div class="ok">✓ 端口已改成 '+j.port+'，正在启动服务…</div>'+
      '<div class="tip">这个窗口可以关掉了，回到启动窗口看地址。</div>';return;}
    e.textContent=j.msg||'保存失败';b.disabled=false;b.textContent='保存并继续启动';
  }).catch(function(){e.textContent='连接已断开';b.disabled=false;b.textContent='保存并继续启动';});
};
p.addEventListener('keydown',function(ev){if(ev.key==='Enter')b.click();});
</script></body></html>"""


def _ask_web(current, reason, ports, cfg_path, timeout=300, log=print):
    """在本机随机端口起一个只有一页的端口设置页，用默认浏览器打开。"""
    res = {"port": None}
    done = threading.Event()

    class H(BaseHTTPRequestHandler):
        server_version = "WorkbenchPortSetup"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            raw = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except Exception:
                pass

        def do_GET(self):
            if self.path.split("?")[0] not in ("/", "/index.html"):
                return self._send(404, "not found", "text/plain; charset=utf-8")
            chips = "".join('<div class="chip" data-p="%d">%d</div>' % (p, p) for p in ports)
            html = (PAGE.replace("__PORT__", str(current))
                        .replace("__REASON__", reason or "")
                        .replace("__SUGGEST__", str(ports[0] if ports else DEFAULT_PORT))
                        .replace("__CHIPS__", chips))
            return self._send(200, html)

        def do_POST(self):
            if self.path.split("?")[0] != "/save":
                return self._send(404, json.dumps({"ok": False}), "application/json")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                p = int(body.get("port"))
            except Exception:
                return self._send(400, json.dumps({"ok": False, "msg": "端口格式不对"},
                                                  ensure_ascii=False), "application/json")
            if not (MIN_PORT <= p <= MAX_PORT):
                return self._send(400, json.dumps({"ok": False, "msg": "端口范围是 1024 - 65535"},
                                                  ensure_ascii=False), "application/json")
            if not is_free(p):
                return self._send(400, json.dumps({"ok": False, "msg": "端口 %d 也被占用了" % p},
                                                  ensure_ascii=False), "application/json")
            save_port(cfg_path, p)
            res["port"] = p
            done.set()
            return self._send(200, json.dumps({"ok": True, "port": p}), "application/json")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    httpd.daemon_threads = True
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]

    def serve():
        try:
            httpd.serve_forever(poll_interval=0.3)
        except Exception:
            pass

    threading.Thread(target=serve, daemon=True).start()
    log("  已在浏览器打开「端口设置」页面：%s" % url)
    log("  （如果没自动打开，手动把这个地址粘到浏览器）")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    deadline = time.time() + max(30, int(timeout))
    while time.time() < deadline:
        if done.wait(0.4):
            break
    # 页面响应先落地，再宽限 1 秒关服务
    time.sleep(0.8)
    try:
        httpd.shutdown()
    except Exception:
        pass
    if res["port"]:
        log("  已把端口保存为 %d" % res["port"])
    return res["port"]


# ---------------------------------------------------------------------------
# 方案三：控制台问答
# ---------------------------------------------------------------------------
def _ask_console(current, reason, ports, cfg_path):
    log("")
    log("  端口 %d 被占用（%s）" % (current, reason or ""))
    if ports:
        log("  推荐端口：%s" % "、".join(str(p) for p in ports))
    try:
        if not (sys.stdin and sys.stdin.isatty()):
            return ports[0] if ports else None
    except Exception:
        return ports[0] if ports else None
    while True:
        try:
            s = input("  请输入新端口（直接回车用 %s，输入 q 退出）："
                      % (ports[0] if ports else DEFAULT_PORT)).strip()
        except Exception:
            return ports[0] if ports else None
        if s.lower() in ("q", "quit", "exit"):
            return None
        p = ports[0] if (not s and ports) else s
        try:
            p = int(p)
        except Exception:
            log("  请输入数字端口")
            continue
        if not (MIN_PORT <= p <= MAX_PORT):
            log("  端口范围是 1024 - 65535")
            continue
        if not is_free(p):
            log("  端口 %d 也被占用了" % p)
            continue
        save_port(cfg_path, p)
        return p


# ---------------------------------------------------------------------------
# 对外总入口
# ---------------------------------------------------------------------------
def ask_port(current, reason="", cfg_path=None, avoid=(), timeout=300, log=print):
    """弹出窗口/页面让用户改端口。返回可用端口（并已写回配置），取消返回 None。"""
    ports = suggest_ports(int(current), 3)
    ports = [p for p in ports if p not in set(avoid or ())]
    if not ports:
        ports = free_ports(int(current) + 1, 3, avoid=avoid)

    if _tk_available():
        try:
            p = _ask_tk(int(current), reason, ports, cfg_path)
            if p:
                log("  已把端口保存为 %d" % p)
            return p
        except Exception as e:
            log("  图形窗口打不开（%s），改用浏览器页面" % e)
    try:
        return _ask_web(int(current), reason, ports, cfg_path, timeout=timeout, log=log)
    except Exception as e:
        log("  浏览器页面打不开（%s），改在终端里改" % e)
    return _ask_console(int(current), reason, ports, cfg_path)


def pick_port(want, cfg_path=None, is_mine=None, no_dialog=False, log=print, tries=8):
    """确定最终端口。

    返回 (port, state)：
      state = "free"      端口可用，直接起服务
              "mine"      端口上跑的就是本工作台（复用，不起第二个）
              "busy"      被别的程序占着且用户不想/不能改
              "cancelled" 用户主动取消
    """
    port = int(want)
    used = []
    for _ in range(max(1, tries)):
        if is_free(port):
            return port, "free"
        if is_mine:
            try:
                if is_mine(port):
                    return port, "mine"
            except Exception:
                pass
        used.append(port)
        if no_dialog:
            return port, "busy"
        log("  端口 %d 被占用，正在打开端口设置…" % port)
        newp = ask_port(port, reason="端口 %d 上有别的程序在监听（可能是 IIS、代理或"
                                    "另一个开发工具）。" % port,
                        cfg_path=cfg_path, avoid=used, log=log)
        if not newp:
            return port, "cancelled"
        port = int(newp)
    return port, "busy"
