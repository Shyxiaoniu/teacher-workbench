#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""静态加密 · 存量数据后台迁移（P4，2026-11-25 牛哥定规）

谁登录迁谁：教师 FEK 首次解锁后启动一个后台线程，把该账号工作区里
尚未加密的数据逐项转成密文。迁移期间明文/密文共存，读端一律按 magic 判断；
每项先写临时文件 / 先算出密文，成功后再原子替换，中断可重跑（幂等）。

覆盖范围（只迁「读取端已接解密」的数据，避免把功能改坏）：
  · SQLite rows.data / act_submission.data（结构化业务数据）
  · typing.json / quiz.json / design.json / pixel.json
  · 课件库、学生提交附件、活动附件、测验图片（WBF1，保留 Range）

状态写入工作区 enc_migration.json（仅进度，不含任何密钥）。
"""
import json
import os
import sqlite3
import threading
import time

try:
    import fec as FEC
    import enc_ctx as ENC
except Exception:
    FEC = ENC = None

_running = set()
_lock = threading.Lock()

# 这些目录下的「用户文件」才加密；配置 JSON / 数据库 / 索引一律跳过。
_SKIP_EXT = {".json", ".db", ".db-wal", ".db-shm", ".tmp", ".log"}
_SKIP_NAMES = {"enc_migration.json", "accounts.json", "config.json"}


def _status_path(ws, account):
    return os.path.join(ws.paths_for(account)["dir"], "enc_migration.json")


def status(ws, account):
    try:
        with open(_status_path(ws, account), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"running": False, "phase": "", "done": 0, "total": 0, "error": ""}


def _write_status(ws, account, **kw):
    st = status(ws, account)
    st.update(kw)
    st["at"] = int(time.time())
    tmp = _status_path(ws, account) + ".tmp"
    try:
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, _status_path(ws, account))
    except Exception:
        pass


def start(ws, account, fek):
    """FEK 首次解锁时调用，重复调用无副作用。"""
    if not (FEC and FEC.available() and fek and ws):
        return False
    acc = str(account)
    with _lock:
        if acc in _running:
            return False
        _running.add(acc)
    t = threading.Thread(target=_run, args=(ws, acc, fek), daemon=True,
                         name="wb-enc-migrate-" + acc)
    t.start()
    return True


def _run(ws, acc, fek):
    ENC.set_fek(fek)                      # 后台线程显式注入，不依赖会话请求
    p = ws.paths_for(acc)
    total, done = 0, 0
    err = ""
    try:
        jobs = []
        # 1) 结构化数据 -------------------------------------------------------
        db = p["db"]
        if os.path.isfile(db):
            con = sqlite3.connect(db, timeout=30)
            con.execute("PRAGMA busy_timeout=30000")
            try:
                rows = con.execute(
                    "SELECT tbl,rid,data FROM rows WHERE substr(data,1,5)!='WB64.'").fetchall()
                subs = con.execute(
                    "SELECT id,data FROM act_submission WHERE substr(data,1,5)!='WB64.'").fetchall()
            finally:
                con.close()
            total += len(rows) + len(subs)
            jobs.append(("rows", (db, rows, subs)))
        # 2) 独立 JSON --------------------------------------------------------
        jsons = [p[k] for k in ("typing", "quiz", "design", "pixel")
                 if os.path.isfile(p[k])]
        total += len(jsons)
        jobs.append(("json", jsons))
        # 3) 用户文件 ---------------------------------------------------------
        files = []
        roots = [p["courseware"], p["activity"], p["quiz_files"],
                 os.path.join(p["submissions"], "uploads"),
                 os.path.join(p["submissions"], "enh", "uploads"),
                 os.path.join(p["submissions"], "enh", "subs_files"),
                 os.path.join(p["submissions"], "enh", "materials")]
        for root in roots:
            if not os.path.isdir(root):
                continue
            for base, _dirs, names in os.walk(root):
                for nm in names:
                    fp = os.path.join(base, nm)
                    if os.path.splitext(nm)[1].lower() in _SKIP_EXT or nm in _SKIP_NAMES:
                        continue
                    try:
                        with open(fp, "rb") as f:
                            if f.read(4) == b"WBF1":
                                continue
                        if os.path.getsize(fp) == 0:
                            continue
                    except OSError:
                        continue
                    files.append(fp)
        total += len(files)
        jobs.append(("files", files))

        _write_status(ws, acc, running=True, phase="starting",
                      done=0, total=total, error="")
        for kind, payload in jobs:
            if kind == "rows":
                db_path, rows, subs = payload
                con = sqlite3.connect(db_path, timeout=30)
                con.execute("PRAGMA busy_timeout=30000")
                try:
                    batch = 0
                    for tbl, rid, data in rows:
                        try:
                            obj = json.loads(data)
                            enc = ENC.enc_text(obj, domain="row")
                            con.execute(
                                "UPDATE rows SET data=? WHERE tbl=? AND rid=? AND data=?",
                                (enc, tbl, rid, data))
                        except ENC.NoFekError:
                            raise
                        except Exception:
                            pass
                        done += 1
                        batch += 1
                        if batch >= 100:
                            con.commit()      # 分批放锁，别让登录写 audit 撞 database is locked
                            batch = 0
                    for sid, data in subs:
                        try:
                            enc = ENC.enc_text(json.loads(data), domain="act")
                            con.execute(
                                "UPDATE act_submission SET data=? WHERE id=? AND data=?",
                                (enc, sid, data))
                        except Exception:
                            pass
                        done += 1
                    con.commit()
                finally:
                    con.close()
            elif kind == "json":
                for fp in payload:
                    try:
                        with open(fp, "rb") as f:
                            raw = f.read()
                        if FEC.is_encrypted(raw):
                            done += 1
                            continue
                        obj = json.loads(raw.decode("utf-8", "replace"))
                        enc = ENC.enc_json_file(obj, domain="json")
                        tmp = fp + ".enc.tmp"
                        with open(tmp, "wb") as f:
                            f.write(enc)
                            f.flush()
                            os.fsync(f.fileno())
                        os.replace(tmp, fp)
                    except Exception as e:
                        err = str(e)[:160]
                    done += 1
            else:
                for fp in payload:
                    try:
                        tmp = fp + ".enc.tmp"
                        with open(fp, "rb") as src, open(tmp, "wb") as dst:
                            n = FEC.encrypt_file_stream(src, dst, fek)
                        if n <= 0:
                            os.remove(tmp)
                        else:
                            os.replace(tmp, fp)
                    except Exception as e:
                        err = str(e)[:160]
                        try:
                            if os.path.exists(tmp):
                                os.remove(tmp)
                        except Exception:
                            pass
                    done += 1
            _write_status(ws, acc, running=True, phase=kind,
                          done=done, total=total, error=err)
    except Exception as e:
        err = str(e)[:160]
    finally:
        ENC.set_fek(None)
        with _lock:
            _running.discard(acc)
        _write_status(ws, acc, running=False, phase="done",
                      done=done, total=total, error=err)
