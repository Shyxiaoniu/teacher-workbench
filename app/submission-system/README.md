# 学生提交系统（学号去重 · 提交时间排行）

一个**零依赖**（仅用 Python 标准库）的学生作业/作品提交系统，满足以下数据规则：

- **学号（sid）为唯一主键去重**：同一学生多次提交，仅保留「最新版本」。
- **全部记录按提交时间生成排行榜**：提交顺序 = 服务端接收时间倒序。
- **三类提交方式统一进入上述流程**：文件提交 / 测试题提交（自动算分）/ 自定义提交。
- **自动适配既有学生名册**：绑定「班级 / 学号 / 姓名」后，提交记录自动关联对应学生。

## 启动

```bash
# macOS / Linux
./run.command            # 或 python3 server.py

# Windows
run.bat                  # 或 python server.py

# 指定端口
python3 server.py --port 5077
```

启动后访问：

- 教师控制台： http://127.0.0.1:5077/console.html
- 文件提交页： http://127.0.0.1:5077/s/file
- 测试题提交页： http://127.0.0.1:5077/s/quiz
- 自定义提交页： http://127.0.0.1:5077/s/custom

## 教师控制台（console.html）

1. **基础设置**：活动标题、文件保存目录（留空用默认 `data/uploads/`，或填绝对路径）、截止时间、是否允许重复提交。
2. **学生名册**：粘贴 CSV（`学号,姓名,班级`）导入；可下载模板 / 恢复示例名册。
3. **三类提交方式**：
   - 文件提交：一键生成 `/s/file` 网页，学生填学号 + 上传作品。
   - 测试题提交：在控制台配置「导学单 + 题目（选项/正确答案/分值）」，生成 `/s/quiz`，学生提交后**自动算分**。
   - 自定义提交：在控制台编辑自定义 HTML（导学单 / 网页），保存后由 `/s/custom` 托管；页面内 JS 只要 `POST /api/submit`（type=custom）即可对接。
4. **实时排行榜 / 数据大屏**：已交 / 未交 / 提交率 / 总人数 KPI，提交顺序名次、未提交名单、最近提交明细，每 5 秒自动刷新。

## 学生端对接 API（自定义提交 / 二次开发）

所有提交统一入口：

```
POST /api/submit
Content-Type: application/json
{
  "sid": "20250301",            // 必填，学号主键
  "name": "张明轩",             // 选填，缺省按名册匹配
  "type": "file|quiz|custom",   // 提交类型
  "title": "作品标题",          // 选填
  "intro": "简介",              // 选填
  "fields": { "Q1": "A", ... },// 任意键值对（测试题答案 / 自定义字段）
  "score": 95,                  // quiz 类型自动算分后带上
  "files": [ { "name":"a.png", "mime":"image/png", "data":"<base64>" } ], // 文件以 base64 内联
  "submitted_at": "2026-10-28 14:16:09"  // 选填，缺省用服务端时间
}
```

返回：`{ "ok": true, "msg": "...", "record": {...}, "rank": 3 }`
（同一 `sid` 重复提交会覆盖为最新；`rank` 为当前在排行榜中的名次）

读取排行榜：`GET /api/board` → `{ board:[{rank,sid,name,cls,type,score,received_at,...}], not_submitted:[...], stats:{total,submitted,not_submitted,rate} }`

## 数据存储

全部为本地 JSON / 文件，便于备份与迁移：

- `data/config.json`      —— 设置、测试题配置、自定义 HTML
- `data/roster.json`      —— 学生名册
- `data/submissions.json` —— 提交记录（已按学号去重，每份为最新版本）
- 上传文件保存在「文件保存目录」（默认 `data/uploads/`）

> 文件以 base64 内联进 JSON 提交，适合作业类小文件；如需大文件可改用 multipart 直传（后续可扩展）。
