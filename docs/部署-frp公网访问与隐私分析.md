# 班级工作台 2.0 · frp 公网访问部署方案与数据隐私分析

> 场景：家里 NAS 用 Docker 跑工作台（gateway 8080 对内），租一台云服务器跑 frps，
> 公网通过 `https://你的域名` 访问。本文先分析隐私风险，再给可照抄的配置。

---

## 一、数据资产盘点：库里到底有什么

| 数据类别 | 存放位置 | 敏感级别 | 说明 |
|---|---|---|---|
| 学生名单 | SQLite `students` 表 | **高** | 姓名 + 班级 + 学号 = 可识别到具体未成年人 |
| **家长联系方式** | `students` 表的 `parent_name / phone1 / phone2 / wechat / email / address` | **最高** | PIPL 把不满 14 周岁未成年人信息按**敏感个人信息**对待 |
| 教师备注 | `students.note / contacted` | 中 | 家校沟通记录 |
| 成绩 | `exams / exam_scores` | **高** | 学业表现，与身份一一对应 |
| 考勤记录 | 签到 / 迟到 / 缺勤 / 请假 | 中 | 行为轨迹 |
| 德育加减分 + 原因 | 德育表 | 中 | 原因字段学生端默认隐藏 |
| 作业 / 学习单文件 | 数据卷 `<课程>/<班级>/<学号-姓名>/` | 中 | **文件名和目录含实名**，拿到目录即可对号入座 |
| 测验试卷 | `<账号>/quiz.json`（按教师隔离，不进 29 表） | **高** | 含答案与分值 |
| 打字成绩 / 网页作品 | 各自存储 | 低—中 | 星级、作品文件 |
| 账号凭据 | `accounts.json` | — | PBKDF2-SHA256 / 12 万轮 / 每账号独立盐，**拖库不等于拿到明文** |

**结论：这是"一个班未成年人的完整画像"，不是普通论坛数据。泄露的直接受害人是一个个具体的孩子。**

## 二、已有防线（代码里核实过的）

| 机制 | 效果 |
|---|---|
| 学生端字段级裁剪（`projection.py`） | 家长电话等**服务端出网前剔除**，前端改 JS 也拿不到；`家长联系方式` 开关默认关 |
| 登录限速 | 同 IP 10 次/分钟 + **按学号维度限速**（换 IP 绕不过）；不信任 X-Forwarded-For，防伪造 |
| 首次设置 / 免登录导入 | 仅内网 IP + 启动 24h 窗口；公网陌生人不能抢注管理员 |
| 教师密码强度 | 弱密码黑名单 + 至少 8 位 |
| 测验答案不下发 | 服务端裁剪，学生拿到的数据里根本没有答案字段 |
| 备份解密在浏览器端 | 备份文件离开浏览器前是密文（PBKDF2 15 万轮） |

## 三、frp 穿透后的暴露面分析（按"谁能碰到"分层）

### A. 公网陌生人（最主要的威胁）

| 泄露路径 | 前提 | 后果 |
|---|---|---|
| 明文嗅探 | 不做 HTTPS 直接穿透 | 登录凭据、成绩、家长电话全明文——**必须上 HTTPS** |
| 撞学生密码 | 知道班级名 + 学号段，学生用初始密码没改 | 冒充学生看作业/成绩、代交作业 |
| 撞教师密码 | 教师用弱密码 | **全班数据 + 家长电话 + 备份导出接口**，最重的一条 |
| 接口漏洞 | 系统或 frp 本身有 0day | 视漏洞而定 |
| 云服务器被入侵 | frps/SSH 被破 | 看不到明文（TLS 在 Caddy 终结，隧道本身加密），但可断你的服务 |

### B. 云服务器（你自己租的）

- Caddy 终结 TLS 后，**云服务器上能看到解密流量**。自建自用可接受；但绝不要用第三方的 frp 转发服务跑这套系统——等于把全班数据交给别人看。
- 云厂商本身不碰你磁盘内容，但备案主体、域名信息是公开的。

### C. 家庭内网（NAS 所在网络）

- gateway → workbench 之间是 HTTP 明文，但都在 NAS 本机回环/内网，风险低。
- NAS 被入侵（弱 SSH 密码、其他服务被打穿）= **SQLite 明文全量泄露** + 数据卷里的归档文件。这是"拿到机器"级别的风险，与是否穿透无关。

### D. 学生本人（合法用户）

- 正常登录只能看到自己的数据和老师下发的资料，家长字段拿不到——这条在设计上是干净的。

## 四、泄露路径优先级与对策

| 优先级 | 风险 | 对策 |
|---|---|---|
| 1 | HTTP 明文 | 云端 Caddy 强制 HTTPS + HSTS（配置见下） |
| 2 | 教师弱密码 | 上线前全部改强密码；管理员账号不用 `t12345678` |
| 3 | 学生初始密码 | 开"首次登录必改"策略；学期初统一重置 |
| 4 | 数据最小化 | 名单导入**不要带家长电话列**——库里没有就永远漏不了 |
| 5 | NAS 本机安全 | SSH 改密钥登录、关闭密码登录；Docker 数据卷只给工作台容器用 |
| 6 | 端口收敛 | 云防火墙只开 443 + 7000（frp 隧道）；frps 面板不开或绑 127.0.0.1 |
| 7 | 备份 | 备份 zip 含全库+文件，加密存放，别放网盘明文 |

## 五、落地配置（可照抄）

### 5.1 云服务器：frps（`/etc/frp/frps.toml`）

```toml
bindAddr = "0.0.0.0"
bindPort = 7000
auth.method = "token"
auth.token = "换成一段足够长的随机字符串"
# 强制隧道本身加密
transport.tls.force = true
# vhost 只给本机 Caddy 用，防火墙挡掉外网直连
vhostHTTPPort = 8080
# 限制可映射端口，防滥用
allowPorts = [ { start = 8080, end = 8080 } ]
# 不开 dashboard；要开就只绑 127.0.0.1
webServer.addr = "127.0.0.1"
webServer.port = 7500
webServer.user = "admin"
webServer.password = "再换一个强密码"
```

### 5.2 云服务器：Caddy（`/etc/caddy/Caddyfile`）

```
wb.example.com {
    reverse_proxy 127.0.0.1:8080
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        Referrer-Policy "strict-origin-when-cross-origin"
    }
}
```

Caddy 自动签 Let's Encrypt 证书。防火墙（安全组）放行：**443、7000**；8080、7500 一律不对公网。

### 5.3 NAS：frpc（`/volume1/docker/frp/frpc.toml`）

```toml
serverAddr = "云服务器公网 IP"
serverPort = 7000
auth.method = "token"
auth.token = "与 frps 相同的随机字符串"
transport.tls.enable = true
transport.heartbeatInterval = 30
transport.heartbeatTimeout = 90

[[proxies]]
name = "workbench-web"
type = "http"
localIP = "127.0.0.1"
localPort = 8080        # 穿透 gateway 的统一入口，不要穿透 20020
customDomains = ["wb.example.com"]
```

用 Docker 跑 frpc 的话 `localIP` 写 NAS 宿主 IP 或加 `network_mode: host`。

### 5.4 NAS：工作台 compose 的 `.env` 改动

```ini
WB_PUBLIC_URL=https://wb.example.com
```

其余不动：`WB_TRUST_PROXY=1` 已默认打开，真实来源 IP 会一路传到工作台，
限速和日志才能分清是哪个学生（部署后看一眼日志确认不是全是 127.0.0.1）。

## 六、上线前检查清单

- [ ] `WB_ADMIN_PASSWORD` 已换成强密码（或首次设置后立即改）
- [ ] 学生端密码策略：首次登录必改 + 学期初一键重置
- [ ] 名单里没有家长电话列（或确认能接受该风险并已告知学校）
- [ ] 云安全组只开 443 / 7000，frps 面板未对公网
- [ ] `https://域名/teacher/` 打开正常，证书是小锁
- [ ] 工作台日志里能看到访客真实 IP（限速按人生效）
- [ ] NAS SSH 已改密钥登录
- [ ] 备份文件加密存放
- [ ] DNS 未启用通配符解析，只有这一个子域指向云服务器

## 七、两个现实提醒

1. **ICP 备案**：国内云服务器用域名 + 443 提供网页服务，运营商/云厂商会拦，**必须先 ICP 备案**。不想备案：要么用云服务器 IP + 非常规端口（体验差、证书也难签），要么改用 Tailscale/WireGuard 组网（不暴露公网，方案更简单，个人最推荐）。
2. **合规**：公办学校的学生数据放在个人云资源上，建议至少跟学校打个招呼留痕；如果以后想给别的老师/学校用，那就是另一个量级的审计话题了。
