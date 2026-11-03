# 第三方组件与许可声明（Third-Party Notices）

班级工作台 2.0 自身以 **MIT 许可证**发布（见 [LICENSE](LICENSE)，Copyright (c) 2026 Shyxiaoniu）。
本文件列出**随本项目一起分发、但版权与许可归第三方**的组件，以及它们各自的授权条件。
分发本项目（尤其是 `python tools/make_release.py` 产出的发行包、以及自行构建的 Docker 镜像）时，
请连同本文件一并保留。

> 一句话记法：`quickform/app/` 整个目录是**别人的 MIT 代码**（© 2026 谢作如），
> 其余目录是本项目的 MIT 代码（© 2026 Shyxiaoniu）。两套 MIT，两个版权人，都得留名。

---

## 一、核心第三方模块：QuickForm

| 项目 | 内容 |
|---|---|
| 名称 | QuickForm 教师版（智能表单管理服务 / 表单收集与数据大屏） |
| 版本 | 2.5（纯代码版） |
| 许可证 | **MIT License** |
| 版权人 | **Copyright (c) 2026 xiezuoru（版权所有 (c) 2026 谢作如）** |
| 许可全文 | [quickform/app/LICENSE](quickform/app/LICENSE)（中英双语，随上游原样保留） |
| 上游仓库 | <https://gitee.com/wstlab/quickform> · <https://github.com/wstlab/quickform> |
| 在线演示 | <https://quickform.cn> |
| 文档 | <https://quickform.readthedocs.io/zh-cn/latest/> |
| 本项目中的位置 | `quickform/app/`（**官方源码，零改动**）；另有 `quickform/serve.py`、`quickform/init_admin.py`、`quickform/Dockerfile`、`quickform/docker-entrypoint.sh` 为本项目原创的适配层，属本项目 MIT |
| 接入方式 | 作为 `/qf/` 前缀下的独立容器运行，经 nginx 网关与工作台统一入口 |

### MIT 许可下的义务（已履行）

MIT 是全宽松许可，**允许商用、允许修改、允许再分发、允许闭源分发**，唯一实质义务是：

> 上述版权声明和本许可声明应包含在本软件的所有副本或重要部分中。

本项目履行方式：

1. `quickform/app/LICENSE` 保留上游 MIT 全文与 `Copyright (c) 2026 xiezuoru`；
2. 本文件说明其来源、版本、位置与许可；
3. 发行包与 Docker 镜像均包含上述两处文件；
4. QuickForm 页面内的页脚「免责声明」「访问 QF 在线版」「联系我们」链接保持原样，未做删改。

### MIT 许可下的权利

- 可以免费用于教学、商用或任何目的；
- 可以修改源码后自行分发（修改后建议注明改动，便于他人比对；本项目选择零改动以简化升级）；
- 可以再许可（sublicense）给第三方。

### 需要注意的边界

- MIT **不提供任何担保**，出问题由使用者自行承担（见下文「免责声明」）；
- MIT **不授予商标权**：不得使用「QuickForm」及其作者名义为本项目的衍生版本背书或做推广；
- 上游若未来更换许可证，**以你所取得的那份源码自带的 LICENSE 为准**；本项目升级 `quickform/app/` 时须同步复核新许可。

---

## 二、QuickForm 前端第三方库

以下库位于 `quickform/app/static/vendor/`（另在 `static/js`、`static/css` 有同名副本），
随 QuickForm 源码一起分发。

| 组件 | 版本 | 许可证 | 版权声明 | 位置 |
|---|---|---|---|---|
| Bootstrap | 5.1.3 | **MIT** | Copyright 2011-2021 The Bootstrap Authors / Twitter, Inc. | `static/vendor/bootstrap/`、`static/css/bootstrap.min.css`、`static/js/bootstrap.bundle.min.js` |
| Bootstrap Icons | 1.10.5 | **MIT** | Copyright 2019-2023 The Bootstrap Authors | `static/vendor/bootstrap-icons/` |
| highlight.js | 11.7.0 | **BSD-3-Clause** | (c) 2006-2022 见上游 LICENSE | `static/vendor/highlight.js/` |
| marked | 15.0.12 | **MIT** | Copyright (c) 2011-2025, Christopher Jeffrey | `static/vendor/marked/` |
| qrcodejs | —（无版本横幅） | **MIT** | Copyright (c) 2012 davidshimjs | `static/vendor/qrcodejs/` |

**BSD-3-Clause 比 MIT 多一条**：不得使用贡献者或组织名称（此处为 highlight.js 作者与组织）
为衍生产品背书。除此之外同样允许商用、修改与再分发，义务同样是保留版权声明与许可全文。

上述库均可在浏览器端直接查看其文件头部的版权与许可注释；如需完整许可全文，
见各库上游仓库的 `LICENSE` 文件（Bootstrap：<https://github.com/twbs/bootstrap/blob/main/LICENSE>，
Bootstrap Icons：<https://github.com/twbs/icons/blob/main/LICENSE>，
highlight.js：<https://github.com/highlightjs/highlight.js/blob/main/LICENSE>，
marked：<https://github.com/markedjs/marked/blob/master/LICENSE.md>，
qrcodejs：<https://github.com/davidshimjs/qrcodejs/blob/master/LICENSE>）。

---

## 三、QuickForm 的 Python 依赖

由 `quickform/app/requirements.txt` 声明，在 **Docker 镜像构建时**用 pip 安装（不随源码包直接分发）。
直接运行模式下由使用者自己 `pip install`。许可证如下（仅供参考，以各项目发布物为准）：

| 包 | 版本 | 许可证 |
|---|---|---|
| Flask | 3.0.3 | BSD-3-Clause |
| Flask-Login | 0.6.3 | MIT |
| Flask-SQLAlchemy | 3.1.1 | BSD-3-Clause |
| Flask-Bcrypt | 1.0.1 | BSD-3-Clause |
| Flask-Babel | 4.0.0 | BSD-3-Clause |
| SQLAlchemy | 2.0.45 | MIT |
| bcrypt | 5.0.0 | Apache-2.0 |
| python-dotenv | 1.0.1 | BSD-3-Clause |
| pandas | 2.0.3 | BSD-3-Clause |
| matplotlib | 3.7.5 | PSF-based（matplotlib license） |
| requests | 2.32.4 | Apache-2.0 |
| qrcode[pil] | 8.0 | BSD-3-Clause |
| Pillow（qrcode 依赖） | — | MIT-CMU（HPND） |

**Apache-2.0 的两条额外要求**（`bcrypt`、`requests`）：保留 `NOTICE` 文件（如有）；
若修改其源码须注明。本项目未修改这些库，仅按原样安装使用。

---

## 四、Docker 基础镜像

| 镜像 | 用途 | 说明 |
|---|---|---|
| `python:3.11-slim` | QuickForm 容器 | 基于 Debian，含 GPL/LGPL 组件（glibc、bash 等）；仅在容器内使用，**不与本项目源码混合分发**，通过镜像层单独分发 |
| `python:3.12-slim` 或 `node`/`nginx` 等 | 工作台 / 网关 | 同上 |

容器镜像属于「聚合（aggregation）」而非「衍生作品」，本项目源码的 MIT 许可不会传导到镜像内的
GPL 组件，反之亦然。自行分发镜像时请遵守各基础镜像的许可（Debian 组件的完整版权声明位于
镜像内 `/usr/share/doc/*/copyright`）。

---

## 五、本项目原创部分的许可

除上述第三方内容外，`app/`、`docker/`、`tools/`、`docs/`、根目录各文档，以及 `quickform/`
下除 `app/` 之外的适配层文件，均为本项目原创，以 **MIT 许可证**发布：

```
MIT License · Copyright (c) 2026 Shyxiaoniu
```

---

## 六、免责声明

1. 本项目及所集成的第三方组件均按**「原样」**提供，**不提供任何明示或暗示的担保**，
   包括但不限于对适销性、特定用途适用性和非侵权性的保证。
2. 在教学场景中使用本系统所产生的一切后果（包括但不限于考勤判定争议、成绩记录误差、
   数据丢失、设备故障、因操作不当导致的信息泄露），由**使用者自行承担**，
   作者与贡献者不承担任何责任。
3. 签到、考勤、成绩、德育等记录属于**日常教学记录**，不构成法律意义上的证明文件；
   涉及学生权益的重要结论，请以学校正式流程与纸质/盖章材料为准，并另行备份。
4. **请勿在系统中存储身份证号、家庭住址、健康状况等敏感个人信息**。学生姓名、学号等
   必要信息请在取得学校许可、并告知学生与家长的前提下使用。QuickForm 侧的官方提示
   （「请勿在 QuickForm 中存储姓名、身份证号等敏感数据」）保持原样，未做删改，
   详见其[免责声明](https://my.feishu.cn/docx/KYlJdyq3wojmwox8KJTcnKORnob)。
5. 部署到公网或校园网前，请自行完成安全加固（修改默认口令与 `QF_SECRET_KEY`、
   限制访问范围、配置 HTTPS、定期备份），因未加固导致的风险由部署者承担。
6. 本项目与 QuickForm 上游作者**无隶属或背书关系**；「QuickForm」「Bootstrap」
   「highlight.js」等名称与商标归各自权利人所有。

---

## 七、如何核对这些信息

```bash
# 1. QuickForm 的许可与版权人
head -4 quickform/app/LICENSE

# 2. 前端库的版本与许可（文件头注释）
head -6 quickform/app/static/vendor/bootstrap/css/bootstrap.min.css
head -6 quickform/app/static/vendor/highlight.js/highlight.min.js
head -6 quickform/app/static/vendor/marked/marked.min.js
head -4 quickform/app/static/vendor/bootstrap-icons/bootstrap-icons.min.css

# 3. 确认 quickform/app 与上游一致（零改动）
#    与 https://gitee.com/wstlab/quickform 对应版本比对 app.py 即可

# 4. 发行包里是否带上了许可文件
unzip -l dist/班级工作台2.0-*.zip | grep -Ei 'LICENSE|THIRD-PARTY'
```

---

最后更新：2026-11-03 · 对应本项目版本 2.0 / QuickForm 2.5
