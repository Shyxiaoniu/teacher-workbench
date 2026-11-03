# quickform —— QuickForm 服务模块（可独立替换）

班级工作台把 QuickForm 教师版做成**一个独立容器 + 一段外部适配层**，
好处是：官方仓库更新时，只要替换 `app/` 里的源码，适配层和工作台的代码都不用动。

```
quickform/
├── app/                  ← QuickForm 官方源码（gitee.com/wstlab/quickform），可整目录替换
│   ├── app.py            主程序（Flask，约 3000 行）
│   ├── config.py         DATABASE_URL / UPLOAD_FOLDER / API_UPLOAD_FOLDER
│   ├── requirements.txt
│   ├── templates/  static/  translations/  scripts/
│   └── data/             SQLite（运行时软链到数据卷，源码目录里不存数据）
├── serve.py              ← 适配层：加 /qf 前缀，官方源码零改动（本文件不用跟着升级改）
├── init_admin.py         ← 首次启动创建管理员（表为空才插入）
├── docker-entrypoint.sh  ← 把源码复制到可写目录 → 软链数据 → 建账号 → 起服务
├── Dockerfile            ← python:3.11-slim + 中文字体 + 时区，uid 10002
└── README.md             ← 本文件
```

## 一、它怎么接进统一入口

```
浏览器 ──▶ gateway(nginx:8080) ──┬─ /      ──▶ workbench:20020   工作台
                                 ├─ /qf/   ──▶ quickform:5055    QuickForm（剥掉 /qf）
                                 └─ /static/ ─▶ quickform:5055   官方模板里的绝对路径
```

`serve.py` 做三件事，全部在 Flask 外面完成，不碰官方代码：

1. `ProxyFix(x_prefix=1)` —— 让 `url_for()` / `redirect()` 自动带 `/qf`；
2. `after_request` —— 修补模板里写死的 `href|src|action="/..."` 与
   `window.location.href='/task/...'`；
3. 往 `</head>` 前注入一段十几行的 shim —— 运行时 `fetch('/api/...')`、
   `XMLHttpRequest`、`window.open`、`<a href>` 点击也补上 `/qf`。

> 只在请求带 `X-Forwarded-Prefix` 时才加前缀。直接 `curl http://容器:5055/`
> 调试时不加，两种访问方式都可用。

## 二、数据在哪

| 内容 | 容器内路径 | 宿主机 |
|---|---|---|
| SQLite 数据库 | `/data/db/quickform.db` | 命名卷 `teacher-workbench-quickform-data` |
| 任务/收集表附件 | `/data/uploads/` | 同上 |
| 提交的作品 | `/data/assets/` | 同上 |
| storage 配置 | `/data/storage.json` | 同上 |

`docker-entrypoint.sh` 会把源码复制到 `/run/quickform`，再把
`data/`、`static/uploads`、`static/assets` 三个目录软链到 `/data` 下：
**重建镜像、换源码都不会动到数据**。

> 备份只需要导工作台里的「全量加密备份」+ 这个卷。整卷打包命令见根 README §1.7。

## 三、三种更新方式

### 方式 A（推荐）：替换源码目录后重建，几十秒

```bash
cd /volume1/docker/teacher-workbench        # 你的 compose 目录
mv quickform/app quickform/app.bak          # 留个退路
# 把官方仓库解压/克隆到 quickform/app（保持 app.py 在 quickform/app/app.py）
docker compose build quickform
docker compose up -d quickform
docker compose logs -f quickform            # 看到 listening 即可
```

如果新版官方源码改了依赖（`requirements.txt` 变了），上面的 `build` 会自动重装，
不需要手工 `pip install`。

### 方式 B：只读挂载，改完重启（不重建镜像，最快的回滚）

把 `docker-compose.yml` 里 quickform 服务这行取消注释：

```yaml
    volumes:
      - quickform-data:/data
      - ./quickform/app:/opt/quickform/app:ro     # ← 取消注释
```

之后更新只要覆盖宿主机上的 `./quickform/app`，然后：

```bash
docker compose restart quickform
```

入口脚本会把只读目录复制到 `/run/quickform` 再运行，所以 `:ro` 不会报权限错。
想回滚就换回旧目录再 restart。

### 方式 C：换镜像（多台 NAS 统一升级，或不想在 NAS 上编译）

```bash
# 构建并推到你自己的仓库
docker compose build quickform
docker tag teacher-workbench-quickform:2.5 <你的仓库>/quickform:<版本>
docker push <你的仓库>/quickform:<版本>

# 其它机器上只改 .env，然后拉取
QF_IMAGE_REPO=<你的仓库>/quickform
QF_IMAGE_TAG=<版本>
docker compose pull quickform && docker compose up -d quickform
```

### 升级前必做

```bash
docker run --rm -v teacher-workbench-quickform-data:/d -v "$PWD":/b alpine \
  tar czf /b/qfdata-$(date +%Y%m%d).tgz -C /d .
```

出问题就 `docker compose down quickform`，把 `quickform/app.bak` 换回来重新 build。

## 四、账号

- 首次启动由 `init_admin.py` 建一个管理员：默认 `wst / quickform`，
  用 `.env` 里的 `QF_ADMIN_USER` / `QF_ADMIN_PASSWORD` 改，**上线前务必改掉**。
- 之后加人、改密码都在 QuickForm 自己的「用户管理」页面里做，脚本不会再覆盖。
- 会话密钥 `QF_SECRET_KEY` 换了会让所有人重新登录；生成方式：`openssl rand -hex 24`。

## 五、自检清单

```bash
# 1. 容器健康
docker compose ps                       # quickform 显示 healthy
# 2. 前缀入口
curl -sI http://127.0.0.1:8080/qf/      # 200 或 302，Location 里带 /qf/
# 3. 登录页静态资源（不能 404）
curl -s http://127.0.0.1:8080/qf/login | grep -o '/qf/static/[^"]*' | head -3
# 4. 直连容器（不加前缀也要能开）
docker compose exec quickform python -c \
  "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:5055/login').status)"
```

## 六、常见问题

**页面能开但样式全丢 / 按钮点击 404**
说明前缀没补齐。先确认 nginx 的 `/qf/` 里带了 `proxy_set_header X-Forwarded-Prefix /qf;`，
再确认 `serve.py` 是被 `python /opt/quickform/serve.py` 启动的（不是直接跑 `app.py`）。

**图表里的中文变成方块**
镜像已装 `fonts-noto-cjk`。如果自己换基础镜像，记得补 `fontconfig` + 中文字体，
否则 matplotlib 生成的图片全是方框。

**上传大文件 413**
nginx 的 `client_max_body_size` 已放到 320m；如果自己又套了一层反代，那一层也要改。

**时间差 8 小时**
容器 `TZ` 已设为 `Asia/Shanghai`；compose 里改 `.env` 的 `TZ` 即可。

## 七、和官方仓库的关系

- `app/` 里的代码来自官方仓库，**不做任何修改**（方便直接比对、直接替换）。
- 所有适配都在 `serve.py` / `init_admin.py` / `docker-entrypoint.sh` 里，
  官方更新时不需要 rebase、不需要解冲突。
- 官方默认端口 5001，这里用 5055（避开开发机上的常用端口），
  由 `QUICKFORM_PORT` 环境变量控制，与官方 `app.run()` 读取的变量同名。

## 八、许可与版权

QuickForm 以 **MIT 许可证**开源，这是**很宽松但仍有义务**的许可，需要说清楚：

| 项目 | 内容 |
|---|---|
| 许可证 | **MIT License** |
| 版权人 | **Copyright (c) 2026 xiezuoru（版权所有 (c) 2026 谢作如）** |
| 许可全文 | [`app/LICENSE`](app/LICENSE)（中英双语，随上游原样保留） |
| 上游仓库 | <https://gitee.com/wstlab/quickform> · <https://github.com/wstlab/quickform> |
| 上游文档 | <https://quickform.readthedocs.io/zh-cn/latest/> |

### 义务与履行方式

MIT 唯一的实质义务是「版权声明和许可声明应包含在本软件的所有副本或重要部分中」。本模块的做法：

1. `app/LICENSE` 保留上游 MIT 全文与 `Copyright (c) 2026 xiezuoru`；
2. 本文件与根目录 [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md) 双重说明来源与归属；
3. 发行包和 Docker 镜像都带上上述文件；
4. QuickForm 页面页脚的「免责声明 / 访问 QF 在线版 / 联系我们」链接**保持原样**，未做删改。

### 可以做什么 / 不可以做什么

- ✅ 免费用于教学、商用；修改源码、改主题、加功能；再分发甚至收费分发
- ❌ 不能删掉或替换 `app/LICENSE` 里的版权人与许可声明
- ❌ 不能用「谢作如」「QuickForm 官方」的名义给你的衍生版本背书
- ❌ 不能拿去存储身份证号等敏感数据（见上游页脚「免责声明」：
  <https://my.feishu.cn/docx/KYlJdyq3wojmwox8KJTcnKORnob>）

### 升级时要注意

替换 `app/` 为新版官方源码时，**必须一起检查新的 LICENSE**——万一上游换了许可证
（例如改成 AGPL），本项目的整体分发策略要跟着复核。切勿直接把新版 `app/` 覆盖进来就发布。

`app/static/vendor/` 下还有一批前端库（Bootstrap 5.1.3 / Bootstrap Icons 1.10.5 均 MIT、
highlight.js 11.7.0 为 **BSD-3-Clause**、marked 15.0.12 MIT、qrcodejs MIT），
清单与许可见根目录 [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md)。
