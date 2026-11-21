# 宠物设计 SOP · 从图稿到工作台宠物

> 本文是把 2026-11-18 ~ 11-21 实际做「学生作品宠物（创意宠物）」的全套流程整理成的操作手册。
> 目标：以后要新增/替换一只宠物，照着做即可，不用重新踩坑。
> 版本位：2.0.75 定版；闭眼图方案 2.0.60 定版（**程序化画闭眼已全否，勿恢复**）。

---

## 0. 先分清两套宠物（最容易搞混的地方）

工作台里其实有**两套完全不同**的宠物，制作方式毫无关系，动手前先确认要做哪套：

| | 系统自带宠物（22 只） | 学生作品宠物 / 创意宠物（14 只） |
|---|---|---|
| 载体 | **纯 SVG**，代码画出来 | **PNG 位图**，AI 图稿抠图 |
| 定义处 | `app/typing/typing-data.js` 的 `TD.PETS` | `app/core/studio_data.py` 的 `STUDIO_WORKS` |
| 图片 | 无（前端按参数渲染） | `app/typing/studio/pets/*.png` |
| 新增方式 | 加一条参数对象（耳型/尾型/配色等），见文件内 11-17 扩十只的写法 | **走本文整套流程** |
| 出现位置 | 牧场系统宠物、普通孵蛋 | 创意宠物、作品蛋（彩蛋）、学生设计金标 |

**本文讲的是第二套（PNG 位图作品宠物）**——就是牛哥手绘图纸、AI 出图那套。第一套 SVG 宠物若要加，直接在 `TD.PETS` 数组里照现有格式加参数即可（编号顺序=图鉴顺序，服务端 `PET_IDS` 要同步），不涉及图稿。

---

## 1. 全流程总览

```
手绘图纸（牛哥提供，剪贴板 jpg）
   │  ① AI 图生图定造型（ImageGen，image-to-image）
   ▼
白底 PNG 图稿（work_dir/typing-pet-v1/），1024×1024
   │  ② 抠白底（PIL 四边 flood-fill）
   ▼
透明底宠物图（studio/pets/<name>.png）
   │  ③ 清白边（tools/clean_studio_edges.py）
   ├──────────────┬─────────────────┐
   ▼              ▼                 ▼
睁眼底图      闭眼帧 blink/      睁眼层 eyes/        （笑眯眼宠物才需要睁眼层）
pets/<name>   blink/<name>.png   eyes/<name>.png
   │              │                 │
   │  ④ AI 重绘闭眼（ImageGen）  ⑤ AI 重画睁眼（ImageGen，仅 2 只）
   ▼              ▼                 ▼
   │  ⑥ 宠物蛋图 eggs/<name>.png（ImageGen 纯文字 prompt）
   ▼
⑦ 接入 studio_data.py（注册 id/动作/局部动作/包围盒/眨眼/蛋）
   ▼
⑧ 前端自动读取（studio-ui.js / public_cfg），浏览器验收
   ▼
⑨ 教师端填「创作者」笔名（2.0.76 起只下发笔名，真实信息不公开）
```

---

## 2. 目录与文件对照表

| 用途 | 路径 |
|---|---|
| 手绘图稿 / AI 原图 / 蛋图草稿 | `work_dir/typing-pet-v1/`（历史稿都在，`pets_src_list.txt` 有清单） |
| 定版宠物原图 | `app/typing/studio/pets/<name>.png` |
| 闭眼帧 | `app/typing/studio/blink/<name>.png` |
| 睁眼层（仅笑眯眼宠物） | `app/typing/studio/eyes/<name>.png` |
| 宠物蛋 | `app/typing/studio/eggs/<name>.png` |
| 界面主题（已下架，保留图） | `app/typing/studio/themes/` |
| **数据注册（核心）** | `app/core/studio_data.py` |
| 作品前端渲染 | `app/typing/studio-ui.js` / `studio-ui.css` |
| 抠白边脚本 | `tools/clean_studio_edges.py` |
| ⚠️ 已作废的程序化眨眼脚本 | `app/tools/make_blink_overlays.py`、`make_open_eyes.py`、`eye_kit.py` |
| 原始图备份 | `E:/wb-shots/studio_edge_backup_20261121/` |
| 抠图预览 | `E:/wb-shots/studio_cleaned/` |

---

## 3. 逐步操作

### ① AI 图生图定造型

- **输入**：牛哥手绘图纸（剪贴板 jpg），裁出单只角色当 `image1`，眼睛特写当 `image2`。
- **工具**：ImageGen 图生图，`input_fidelity=high`，`size=1024x1024`（海鱼特殊 `1024x1536`）。
- **风格 prompt（定版，照抄别改方向）**：
  > polished **2D VECTOR GAME CHARACTER** mascot (Subway Surfers / 天天酷跑风格), smooth bold outlines, flat cel shading, chibi proportions, bright clean colors, **NOT 3D, no sketchy lines, no clay/volume/realistic highlights**, full body visible from head to feet. Keep the exact pose, colors and features of the reference drawing.

- **血泪教训（这些方向全被否过，别再试）**：
  - ❌ 写实 3D 公仔（玻璃眼反光、AI 水印）——否。
  - ❌ 拟物 3D（礼帽梅花、粉耳粉尾）——刚定又否。
  - ❌ 手绘线稿风（深色勾线 + 彩铅）——牛哥改口要游戏角色风。
  - ✅ **最终定版 = 2D 游戏角色风 + 每只完整全身**（能看到腿脚）。
- 生成多张挑一张，草稿留在 `work_dir/typing-pet-v1/`，定版的才进下一步。

### ② 抠白底

AI 出的是白底图，要转成透明底：

- 用 PIL **从四边 flood-fill 近白区域**：阈值 `RGB 每通道 ≥ 232` 且 `通道差 ≤ 22`，连通到画布边缘的才转透明。
- 角色内部的奶白/浅色部分因为有描边包围，不会被连通到边缘，因此**被保护、不会被误抠**。
- 先把原始白底图备份到 `work_dir/typing-pet-v1/studio-orig/` 再处理。

### ③ 清白边

alpha 硬切后轮廓外沿会留 1~2px 浅色残留，深色牧场舞台上放大就是「粗糙抠图痕迹」：

```bash
# 先预览到单独目录，确认无误再原地处理
py -3 tools/clean_studio_edges.py --out-dir E:/wb-shots/studio_cleaned
# 原地处理（建议先备份）
py -3 tools/clean_studio_edges.py --backup-dir E:/wb-shots/studio_edge_backup_20261121
```

- 原理：只处理**最外 2px**（`MinFilter(5)` 求差）里「亮且低饱和」的像素（`min(RGB)>150` 且 `spread<80`），深色描边和彩色主体不动。
- 处理对象只有 `pets/` 和 `blink/` 两个目录；已有抗锯齿的素材（eyes 层）会自动跳过。
- 输出仍是硬边（alpha 只有 0/255），缩放抗锯齿交给浏览器。

### ④ 闭眼帧（ blink/ ）—— ⚠️ 全程最大的坑

**结论先记死：闭眼/表情这类改动，一律用 AI image-to-image 照原图重绘，绝不用程序在原图上 P。**

- **已全否的方案（勿恢复）**：`app/tools/make_blink_overlays.py` / `make_open_eyes.py` / `eye_kit.py`。先后试了 7 版程序算法（补皮肤、透镜眼皮线、压扁行插值、高斯模糊+月牙线），全部有竖条/色块/接缝/塌陷。**根因：在卡通图上凭空抹掉眼睛再补皮肤，不可能天衣无缝。** 牛哥明确否掉。
- **定版做法（2.0.60）**：
  1. 每只宠物逐只用 ImageGen 图生图：`image1`=该宠物睁眼原图，`image2`=闭眼范本，prompt 强调「**只有眼睛从睁眼变成弯月的笑眼（closed happy smile eyes），其余每个像素完全不变**」。
  2. 闭眼范本（牛哥提供）：睁眼 `clipboard-2026-11-21T14-11-34-423Z-70f2ece6.jpg` / 闭眼 `...425Z-5f7f277c.jpg`。范本只是参考，**每只保留自己的毛色和造型**，不是照搬。
  3. 处理管线（生成的是白底图）：
     `AI 白底图 → flood fill 抠白底 → 用原图形状的 alpha 当模具，套上 AI 图的 RGB → 存 blink/<name>.png`
     （这样闭眼帧和睁眼底图像素级重合，前端 `clip-path` 从上往下压的动画天然自然、无断层。）
  4. 海鱼原图 `1024×1536`，AI 只会出 `1024×1024`，要 **resize 回原尺寸**再套模具。
  5. 本来就是眯眼/wink 的宠物（梅花、蓝狐、小狸）不进 blink 覆盖逻辑，单走睁眼层或干脆不眨。

### ⑤ 睁眼层（ eyes/ ，仅笑眯眼宠物）

- 梅花（sd_meihua）、蓝狐（sd_fox）原图是 `^ ^` 笑眯眼，要求平时睁眼、还能眨眼：
  - `eyes/<name>.png` = 睁眼层，**常驻显示**盖住原来的 `^ ^`；
  - `blink/<name>.png` = 闭眼帧，眨眼时从上往下压。
- ⚠️ **不能靠「隐藏睁眼层」来眨眼**——那样会露出原图的 `^ ^`，会有断层；必须盖一张闭眼帧。
- 做法：ImageGen 照范本重画大圆眼（深色眼线 + 左上大高光 + 右下小高光、眼尾微翘），取**旧 eyes 层 alpha 膨胀约 21px + 羽化**当遮罩，抠出 AI 新眼区存回 `eyes/`。
- 蓝狐保留蓝色眼睛（牛哥明确：保留蓝色改画法，不照搬棕狐）。

### ⑥ 宠物蛋图（ eggs/ ）

- 每只作品宠物对应一枚蛋 + 1 枚随机问号蛋（`suiji-question.png`），共 13 枚。
- **做蛋经验**：用整行裁图当 `image1` 容易串图；改**用纯文字 prompt 描述图案**更稳，蛋型风格靠 prompt 固定套话统一。
- 蛋图同样白底 → 抠白底。蛋的主色/浅色记进 `STUDIO_EGGS` 的 `color`/`soft`，创意馆「未孵化问号卡」用它做渐变背景（**不是直接贴蛋图**）。
- 图纸真相要看整张蛋图（尤其底部角色旁边），别只数三行标注——海鱼蛋就是补做的。

### ⑦ 接入代码（ studio_data.py 核心）

在 `app/core/studio_data.py` 注册，逐项对照：

| 要改的常量 | 内容 | 备注 |
|---|---|---|
| `STUDIO_WORKS` | 加一条 `{id, name, kind:'pet', img:'/pets/x.png', series, mood, fx, designer}` | `id` 以 `sd_` 开头；`fx` 选 `FX_GLOW`/`FX_SPARKLE` |
| `MOTION_BY_ID` | `sd_x: (动画名, 周期秒)` | 动画名见 `MOVE_NAME`；没有就落 `DEFAULT_MOTION` |
| `PART_BY_ID` | 局部动作 `(部位, cx, cy, rx, ry, 角度)` | 坐标是**内容包围盒百分比 0~100**，不是画布像素 |
| `IMG_BOX` | `(x1,y1,x2,y2)` 相对 0~1 | 把上面内容坐标换算成舞台百分比用；不填则局部动作不生效 |
| `IMG_SIZE` | 非正方形才填，如 `sd_fish:(1024,1536)` | 其余默认 1024×1024 |
| `BLINK_OF` | `sd_x:'x'` | 原图睁眼、要盖闭眼帧的 |
| `EYES_OF` | `sd_x:'x'` | 笑眯眼、要常驻睁眼层的 |
| `STUDIO_EGGS` | 加蛋 `{id, name, pet:'sd_x', img, color, soft}` | `id` 用 `sd_egg_`；`color/soft` 取蛋图主色 |
| `IMG_VER` | 换戳，如 `?v=20261121j` | **每次换图都要换**，否则浏览器缓存旧图 |

- `PET_IDS`、`EGG_BY_ID`、`BY_ID` 都是从上面列表**自动派生**，不用手改。
- `public_cfg()` 负责把 img / blink / eyes / parts / motion / egg / designer 打包下发前端，一般不用动。
- 隐私红线：2.0.76 起 `designer` **只下发自愿填的笔名 `pen`**，绝不下发真实姓名/班级/学校/介绍（初中生多不满 14 周岁，属敏感个人信息）。

### ⑧ 前端自动读取 + 验收

前端 `studio-ui.js` 读 `public_cfg` 的字段自动渲染（眨眼 `clip-path` 压帧、局部动作遮罩、金标、光效），通常不用改 JS。验收清单：

```bash
# 1. 语法检查
py -3 -m py_compile app/server.py app/core/studio_data.py
node --check app/typing/studio-ui.js

# 2. 图片引用完整性：确认 STUDIO_WORKS/STUDIO_EGGS 里每个 img、blink、eyes 都能对上真实文件
py -3 -c "
import sys,os; sys.path.insert(0,'app')
import core.studio_data as S
root='app/typing/studio'; miss=[]
for w in S.STUDIO_WORKS:
    if not os.path.exists(root+w['img']): miss.append(w['img'])
for n in list(S.BLINK_OF.values())+list(S.EYES_OF.values()):
    for sub in ('blink','eyes'):
        p=f'{root}/{sub}/{n}.png'
        if os.path.exists(p): break
    else: miss.append(sub+'/'+n)
for e in S.STUDIO_EGGS:
    if not os.path.exists(root+e['img']): miss.append(e['img'])
print('缺失:', miss or '无')
"

# 3. 浏览器实测：创意馆卡片加载、闭眼层出现、眨眼动画无断层、深色舞台无白边
#    一次性 Edge 脚本用 playwright-core（见下方"环境"），跑完即关
```

改完记得重启服务：`Stop-ScheduledTask WbLanServer` → **等 20020 端口 Listen 消失** → `Start-ScheduledTask WbLanServer`（Stop 是异步的，不等就 Start 新进程会秒退）。

### ⑨ 创作者笔名（教师端）

- 位置：教师端 →「打字」→「创意宠物」页签，每只作品一个「创作者」输入框。
- 只填自愿公开的笔名，留空则不显示署名。**不要填真实姓名/班级/学校**——公开接口（含未登录演示页）只下发 `pen`。

---

## 4. 现有 14 只作品宠物（作样例参考）

| id | 名字 | 系列 | 闭眼方式 |
|---|---|---|---|
| sd_meihua | 小狐狸·梅花 | 花色六宠 | 睁眼层 eyes/（原图笑眯眼）|
| sd_fangkuai | 小兔子·方块 | 花色六宠 | blink 覆盖 |
| sd_hongxin | 小猫·红心 | 花色六宠 | blink 覆盖 |
| sd_heitao | 小老鼠·黑桃 | 花色六宠 | blink 覆盖 |
| sd_dawang | 小蛇·大王 | 花色六宠 | blink 覆盖 |
| sd_xiaowang | 小龙·小王 | 花色六宠 | blink 覆盖 |
| sd_cat | 小猫 | 手绘八宠 | blink 覆盖（星眼）|
| sd_deer | 小鹿 | 手绘八宠 | blink 覆盖 |
| sd_zilong | 紫龙 | 手绘八宠 | blink 覆盖 |
| sd_tanuki | 小狸 | 手绘八宠 | wink，不常规眨 |
| sd_bird | 青鸟 | 手绘八宠 | blink 覆盖 |
| sd_fox | 蓝狐 | 手绘八宠 | 睁眼层 eyes/（原图眯眼笑）|
| sd_fish | 海鱼 | 手绘八宠 | blink 覆盖（1024×1536）|
| sd_jelly | 水母娜娜 | 手绘八宠 | blink 覆盖 |

---

## 5. 坑位速查（按疼程度排序）

1. **程序化画闭眼 = 白干**。补皮肤/压扁/模糊全试过都丑，已全否。闭眼/表情改动一律 AI 照原图重绘。
2. **3D / 拟物 / 手绘线稿 = 全被否**。定版只有「2D 游戏角色风 + 全身」。
3. **闭眼帧要和原图像素级重合**——用原图 alpha 当模具套 AI 的 RGB，别直接贴 AI 的白底图。
4. **笑眯眼宠物靠"隐藏睁眼层"眨眼会露馅**，必须另盖一张闭眼帧。
5. **抠白底要 flood-fill 连通到边缘的近白区**，别用全局阈值，否则会把角色内部奶白抠掉。
6. **清白边只动最外 2px**，深色描边和彩色主体别碰；已有抗锯齿的 eyes 层跳过。
7. **海鱼是 1024×1536**，AI 出图后 resize 回原尺寸再套模具；`IMG_SIZE` 也要登记。
8. **换图必换 `IMG_VER` 戳**，否则浏览器缓存旧图，看起来"没改"。
9. **蛋图用纯文字 prompt**，整行裁图会串图；图纸要看整张（含底部角色旁）。
10. **随机蛋只抽作品宠物**（`PET_IDS`），不孵系统默认宠物。
11. **隐私**：创作者只留笔名，真实姓名/班级/学校/介绍一律不下发公网接口。
12. **服务重启**：Stop 后等端口释放再 Start，否则新进程秒退（state 看着 Running 但没监听）。

---

## 6. 环境备忘（本机硬约束）

- Python 一律 `py -3`（真 Python `C:/Users/WIT_User/Python311/python.exe`；`where python` 命中 0 字节商店占位符，退出码 9009）。
- 浏览器实测用一次性 Edge 脚本 + playwright-core，模块在 `C:/Users/WIT_User/.workbuddy/skills/playwright-browser-use/node_modules`，跑脚本时带 `NODE_PATH=...`；跑完即退，不要起常驻 daemon。
- 内联 JS 语法检查：Python 正则抽 `<script>` 块写到**相对路径** `_chk_*.js` 再 `node --check`（用 `/tmp` 路径会被 Git Bash 解释成 Git 安装目录）。
- 直连本机服务用 `urllib.request.build_opener(ProxyHandler({}))` 绕过代理（本机 FastGithub/HTTP_PROXY 可能拦截 curl 的 127.0.0.1 请求返回旧缓存）。

---

*本文基于 2026-11-18 ~ 11-22 实际制作记录整理。若日后再改眨眼方案，务必同步更新第 ④ 节和"坑位 1"。*
