#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建发行包（免安装，解压即用）。

用法：
    python tools/make_release.py                 # 版本取 app/server.py 的 VERSION，日期取今天
    python tools/make_release.py --date 261103    # 指定日期（yyMMdd）
    python tools/make_release.py --out D:\\dist    # 指定输出目录

产物：<out>/班级工作台<版本>-<yyMMdd>.zip
包内第一层是同名目录，结构与本仓库一致（app / quickform / docker / docs + 说明文档），
只是**不含任何运行期数据**：真实班级、学生、成绩、上传附件、课件库、数据库、日志一律不进包。
"""
import argparse
import os
import re
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 整目录带上的
INCLUDE_DIRS = ["app", "quickform", "docker"]
# docs 只带 md（截图约 5 MB，本地留着就行）
INCLUDE_DOC_GLOB = ".md"
# 根目录散文件
INCLUDE_FILES = ["README.md", "LICENSE", "THIRD-PARTY-NOTICES.md",
                 ".env.example", "docker-compose.yml", "Dockerfile"]

# 任意层级出现即跳过
SKIP_DIR_NAMES = {"__pycache__", ".git", ".workbuddy", ".idea", ".vscode",
                  "_analysis", "_backup", "work_dir", "node_modules"}
SKIP_FILE_SUFFIX = (".pyc", ".pyo", ".log", ".zip", ".db", ".db-wal", ".db-shm",
                    ".sqlite", ".sqlite3", ".tmp", ".bak")
SKIP_FILE_NAMES = {".DS_Store", "Thumbs.db", ".env", "workbench.json"}

# 这些目录里只保留目录说明/占位文件（真实数据绝不进包）
DATA_ONLY_KEEP = {
    "app/data",
    "app/submission-system/data",
    "quickform/app/data",
    "quickform/app/static/uploads",
    "quickform/app/static/assets",
}
KEEP_NAMES = {"README.txt", ".keep", ".gitkeep"}

# 整棵子树都不进包（课件库体积大且属于老师的素材）
SKIP_SUBTREES = {"app/courseware"}

STARTER_BAT = """@echo off
rem 解压后双击这个文件即可启动（等同 app\\启动教师端.bat）
rem 需要 Python 3.9+；如果拿到的是带 runtime 的版本，则无需另装 Python。
cd /d "%~dp0"
call "app\\启动教师端.bat" %*
"""

STARTER_SH = """#!/bin/bash
# 解压后双击运行；首次可能需要 chmod +x 启动工作台.command
cd "$(dirname "$0")"
exec ./app/启动教师端.command "$@"
"""

NOTES = """# {name} · 发行说明

- 版本：**{version}**
- 打包日期：{date_human}
- 包名：`{name}.zip`
- 说明：本包**不含任何真实班级 / 学生 / 成绩数据**，解压后是一套空白工作台，
  首次打开会让你设置自己的管理员账号与密码。

## 一、包里有什么

| 路径 | 内容 |
|---|---|
| `启动工作台.bat` / `.command` | 双击启动（Windows / macOS），参数原样传给 `app/launch.py` |
| `app/` | 工作台主程序：服务端、教师端页面、学生端、打字练习、提交系统、图标、启动脚本 |
| `quickform/` | 表单服务 QuickForm（收集表、活动任务、数据大屏）；`app/` 是官方源码，零改动 |
| `docker/` | Docker 部署用的入口脚本与 nginx 网关配置 |
| `Dockerfile` / `docker-compose.yml` / `.env.example` | 容器化部署三件套 |
| `docs/` | 使用与设计文档，以及文档里引用的功能截图（`docs/shots/` 下 app / student / student-pc / quickform 四个目录） |
| `README.md` | 完整使用说明：功能、部署、建班、一堂课的用法、备份换机、常见问题 |
| `LICENSE` | 本项目的 MIT 许可证（Copyright (c) 2026 Shyxiaoniu） |
| `THIRD-PARTY-NOTICES.md` | 第三方组件与许可清单（QuickForm 的 MIT、前端库、Python 依赖） |
| `quickform/app/LICENSE` | **QuickForm 上游 MIT 全文（Copyright (c) 2026 xiezuoru 谢作如）** |

## 一点五、许可提醒（转发前请看一眼）

本包里**有两套 MIT 许可，两个版权人**：

- 本项目原创部分（`app/`、`docker/`、`tools/`、`docs/`）→ `LICENSE`，© 2026 Shyxiaoniu
- `quickform/app/` 下的 QuickForm 官方源码 → `quickform/app/LICENSE`，© 2026 谢作如

MIT 的义务只有一条：**再分发时保留版权声明与许可全文**。所以把这个包发给同事、传到网盘、
或放进自己的仓库时，**请不要删除上面两个 LICENSE 和 `THIRD-PARTY-NOTICES.md`**；
也不要删掉 QuickForm 页面页脚的「免责声明 / 访问 QF 在线版 / 联系我们」链接。
详见 `THIRD-PARTY-NOTICES.md` 与 `README.md` 第十节。

## 二、怎么启动

### 方式一：直接运行（免安装）

1. 解压到本地目录（路径尽量不含空格与特殊符号）。
2. Windows 双击 `启动工作台.bat`；macOS 双击 `启动工作台.command`。
3. 浏览器打开 `http://127.0.0.1:20020/`，按提示设置管理员账号。
4. 学生用手机访问 `http://<这台电脑的局域网 IP>:20020/s/`。

前置：Python 3.9 或更高（Windows 安装时勾选 **Add Python to PATH**）。
若网络受限，`/qf/` 起不来也不影响考勤、座位、作业、成绩等主功能。

### 方式二：Docker（推荐长期使用）

```bash
cp .env.example .env      # 至少改 QF_SECRET_KEY
docker compose up -d --build
```

浏览器打开 `http://<服务器IP>:8080/`。首次构建需要联网，之后可完全离线运行。

## 三、数据存在哪

- 直接运行：`app/data/`（数据库、上传附件、课件库、每日快照）
- Docker：命名卷 `teacher-workbench-data`、`teacher-workbench-quickform-data`

换机器请用教师端 **设置 → 数据与备份 → 一键导出 / 一键恢复**，不要手工拷数据库。

## 四、怎么重新构建这个包

发行包由仓库里的脚本生成，不手工打包：

```bash
python tools/make_release.py                # 版本取 app/server.py 的 VERSION，日期取当天
python tools/make_release.py --date 261103  # 指定日期（yyMMdd）
python tools/make_release.py --out D:\\dist  # 指定输出目录
```

命名规则：`班级工作台<版本>-<yyMMdd>`，例如 `班级工作台2.0-261103`。
脚本会自动排除运行期数据、数据库、日志、课件库、`.git` 等，产出的包可以直接发给同事。
"""


def read_version():
    path = os.path.join(ROOT, "app", "server.py")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\s*VERSION\s*=\s*"([^"]+)"', line)
            if m:
                return m.group(1)
    raise SystemExit("没找到 app/server.py 里的 VERSION")


def rel_in_data_only(rel):
    for d in DATA_ONLY_KEEP:
        if rel == d:
            return True
        if rel.startswith(d + "/"):
            return True
    return False


def blocked(rel):
    """rel 用 / 分隔的仓库相对路径。返回 True 表示不进包。"""
    parts = rel.split("/")
    for p in parts[:-1]:
        if p in SKIP_DIR_NAMES:
            return True
    if any(rel == s or rel.startswith(s + "/") for s in SKIP_SUBTREES):
        return True
    name = parts[-1]
    if name in SKIP_FILE_NAMES or name.lower().endswith(SKIP_FILE_SUFFIX):
        return True
    if rel_in_data_only(rel) and name not in KEEP_NAMES:
        return True
    return False


def collect():
    out = []
    for top in INCLUDE_DIRS:
        base = os.path.join(ROOT, top)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
                if not blocked(rel):
                    out.append(rel)
    docs = os.path.join(ROOT, "docs")
    if os.path.isdir(docs):
        for fn in sorted(os.listdir(docs)):
            if fn.endswith(INCLUDE_DOC_GLOB):
                out.append("docs/" + fn)
    # 文档里引用的功能截图：一起带上，离线看文档才不会裂图
    for sub in ("app", "student", "student-pc", "quickform"):
        shots = os.path.join(ROOT, "docs", "shots", sub)
        if not os.path.isdir(shots):
            continue
        for fn in sorted(os.listdir(shots)):
            if fn.lower().endswith((".png", ".jpg", ".jpeg")):
                out.append("docs/shots/%s/%s" % (sub, fn))
    for fn in INCLUDE_FILES:
        if os.path.isfile(os.path.join(ROOT, fn)):
            out.append(fn)
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description="构建班级工作台发行包")
    ap.add_argument("--date", default=time.strftime("%y%m%d"), help="日期（yyMMdd）")
    ap.add_argument("--out", default="", help="输出目录（默认 <仓库>/dist）")
    ap.add_argument("--root", default="", help="仓库根目录（默认按脚本位置推断）")
    ap.add_argument("--no-shots", action="store_true", help="兼容旧参数，当前 docs 只带 md")
    args = ap.parse_args()

    global ROOT
    if args.root:
        ROOT = os.path.abspath(args.root)
    if not args.out:
        args.out = os.path.join(ROOT, "dist")

    version = read_version()
    name = "班级工作台%s-%s" % (version, args.date)
    os.makedirs(args.out, exist_ok=True)
    zip_path = os.path.join(args.out, name + ".zip")

    files = collect()
    if not files:
        raise SystemExit("没有可打包的文件")

    date_human = time.strftime("%Y-%m-%d")
    notes = NOTES.format(name=name, version=version, date_human=date_human)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(name + "/发行说明.md", notes.encode("utf-8"))

        zi = zipfile.ZipInfo(name + "/启动工作台.bat")
        zi.external_attr = 0o644 << 16
        z.writestr(zi, STARTER_BAT.replace("\n", "\r\n").encode("gbk", "replace"))

        zi = zipfile.ZipInfo(name + "/启动工作台.command")
        zi.external_attr = 0o755 << 16
        z.writestr(zi, STARTER_SH.encode("utf-8"))

        for rel in files:
            full = os.path.join(ROOT, rel.replace("/", os.sep))
            z.write(full, name + "/" + rel)

    total = sum(os.path.getsize(os.path.join(ROOT, r.replace("/", os.sep))) for r in files)
    print("版本 %s ｜ 日期 %s" % (version, args.date))
    print("包名 %s.zip" % name)
    print("文件 %d 个 ｜ 原始 %.1f MB ｜ 压缩后 %.1f MB"
          % (len(files) + 3, total / 1024 / 1024,
             os.path.getsize(zip_path) / 1024 / 1024))
    print("输出 %s" % zip_path)
    tops = {}
    for r in files:
        tops[r.split("/")[0]] = tops.get(r.split("/")[0], 0) + 1
    for k in sorted(tops):
        print("  %-14s %4d 个文件" % (k, tops[k]))


if __name__ == "__main__":
    sys.exit(main())
