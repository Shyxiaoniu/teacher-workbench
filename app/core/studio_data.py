#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 学生设计作品数据层（studio_data.py）

设计要点：
  · 这里登记的是「学生自己设计的作品」——宠物 / 宠物蛋 / 界面主题，
    与 typing-data.js 里系统自带的 22 只 SVG 宠物是两套东西。
  · 学生作品用 PNG 原图（工作目录 typing-pet-v1 / typing-ui-themes 设计的），
    不参与 SVG 渲染；出战时单独走 PNG 小像。
  · 身份标记：所有学生作品 id 以 sd_ 开头，存在学生档案的 studio_pets /
    studio_themes 字段，和 pets / decos 分开 —— 互不污染。
  · 设计者信息（姓名 / 班级 / 一句话介绍）存在 config.json 的 studio.designers，
    教师端可改、学生端只读；这里的 DEFAULT_DESIGNERS 只是缺省值。
  · 价格与孵化权重全部服务端权威：作品宠物 / 主题都是全场最高档积分；
    作品宠物在孵蛋时用极低权重抽取（彩蛋），也可高价直接兑换。
"""
import os

# 图片静态根：/typing/studio/... 由 server.py 的 _typing_static 直接服务
IMG_ROOT = "/typing/studio"
# 宠物原图统一去掉抠图白边，换戳让浏览器立刻拿新素材
IMG_VER = "?v=20261121i"


def _img(rel):
    return IMG_ROOT + rel + IMG_VER

# ---- 全场最高档定价（与现有最贵特效 aurora 2000 并列天花板）----
STUDIO_PET_PRICE = 2000      # 学生作品宠物：价格挂在「蛋」上，宠物本身不可直兑
STUDIO_THEME_PRICE = 2000    # 学生作品主题：只能积分兑换
STUDIO_EGG_PRICE = 2000      # 作品蛋：唯一合法的作品宠物兑换入口（2.0.56 定版）

# ---- 孵化权重：普通宠物每只 NORMAL_WEIGHT，学生作品每只 STUDIO_WEIGHT ----
# 22 只普通 * 20 = 440，14 只作品 * 1 = 14 —— 作品整体命中率约 3%，最低档。
NORMAL_PET_WEIGHT = 20
STUDIO_PET_WEIGHT = 1

# ---- 特殊标签 & 效果动画：所有学生作品统一带「学生设计」金标 + 流光 ----
STUDIO_TAG = "学生设计"
STUDIO_TAG_COLOR = "#F5B301"

# 效果动画类型：glow(流光) / sparkle(星点) —— 前端据此套 CSS
FX_GLOW = "glow"
FX_SPARKLE = "sparkle"

# 每只宠物的专属动作（CSS 动画名）+ 周期。
# 2.0.56 起不再是「整张图上下飘」的通用动作：每只宠物有自己的动作语义，
# 前端把动画作用在宠物图本身（.sd-stack），不作用在卡片；睁眼宠物再叠加
# 一张同尺寸眨眼覆盖层，每隔几秒闪一下，看起来就是宠物在眨眼睛。
MOTION_BY_ID = {
    "sd_meihua":   ("m-tail",  3.0),   # 大尾巴甩动
    "sd_fangkuai": ("m-ear",   2.8),   # 兔耳轻抖
    "sd_hongxin":  ("m-tail2", 2.6),   # 心形尾巴摆
    "sd_heitao":   ("m-blink", 4.4),   # 紫眼眨眼 + 小点头
    "sd_dawang":   ("m-nod",   3.4),   # 大王点头
    "sd_xiaowang": ("m-tail3", 2.9),   # 蓝色卷尾摇
    "sd_cat":      ("m-blink", 4.8),   # 星眼眨眼
    "sd_deer":     ("m-ear2",  3.2),   # 鹿耳 / 鹿角轻晃
    "sd_zilong":   ("m-tail4", 3.6),   # 紫卷尾摆
    "sd_tanuki":   ("m-ear3",  3.0),   # 狸耳抖动（本来就在 wink）
    "sd_bird":     ("m-flap",  1.9),   # 青鸟扇翅膀
    "sd_fox":      ("m-tail5", 3.3),   # 蓝尾轻摇（本来眯眼笑）
    "sd_fish":     ("m-swim",  3.8),   # 海鱼 S 形游动
    "sd_jelly":    ("m-jelly", 3.5),   # 水母伞盖呼吸 + 触手飘
    "sd_theme_farm": ("m-float", 5.0),
    "sd_theme_flip": ("m-tilt",  5.6),
}
DEFAULT_MOTION = ("m-float", 4.0)
MOVE_NAME = {
    "m-tail": "摇尾巴", "m-tail2": "甩尾巴", "m-tail3": "卷尾摇", "m-tail4": "摆尾巴",
    "m-tail5": "轻摇尾", "m-ear": "抖耳朵", "m-ear2": "晃鹿角", "m-ear3": "抖耳朵",
    "m-nod": "点头", "m-blink": "眨眼点头", "m-flap": "扇翅膀", "m-swim": "游动",
    "m-jelly": "呼吸漂浮", "m-float": "轻轻漂浮", "m-tilt": "左右摇摆",
}
FX_NAME = {"glow": "流光", "sparkle": "星点"}

# ---- 局部动作（2.0.58）----
# 整张图动之外，再让「翅膀/尾巴/耳朵/触手」自己动：前端把同一张图再叠一层，
# 用羽化椭圆遮罩圈住该部位，绕根部小幅旋转 —— 原图不动，局部像活了一样。
# 坐标是相对「内容包围盒」的百分比（0~100），不是 1024 画布，图有留白也能对上。
# 元组：部位名, 中心x, 中心y, 半径x, 半径y, 最大角度
PART_BY_ID = {
    "sd_bird":     [("翅膀", 12, 45, 10, 13, -18), ("翅膀", 88, 45, 10, 13, 18)],
    "sd_cat":      [("耳朵", 30, 7, 9, 8, -10), ("耳朵", 69, 7, 9, 8, 10),
                    ("尾巴", 90, 46, 9, 14, 10)],
    "sd_dawang":   [("头", 50, 18, 22, 13, -4)],
    "sd_deer":     [("鹿角", 26, 7, 10, 9, -9), ("鹿角", 74, 7, 10, 9, 9)],
    "sd_fangkuai": [("耳朵", 36, 6, 9, 11, -8), ("耳朵", 66, 6, 9, 11, 8)],
    "sd_fish":     [("尾鳍", 68, 12, 14, 11, 10)],
    "sd_fox":      [("尾巴", 18, 82, 15, 14, 8)],
    "sd_heitao":   [("铜钱", 28, 80, 11, 11, 8),
                    ("耳朵", 20, 10, 9, 8, -8), ("耳朵", 80, 10, 9, 8, 8)],
    "sd_hongxin":  [("尾巴", 16, 48, 13, 17, 10)],
    "sd_jelly":    [("触手", 50, 82, 30, 15, 5)],
    "sd_meihua":   [("尾巴", 80, 62, 17, 18, 8)],
    "sd_tanuki":   [("尾巴", 16, 62, 13, 15, 8)],
    "sd_xiaowang": [("卷尾", 82, 58, 13, 16, 10)],
    "sd_zilong":   [("尾巴", 55, 82, 15, 16, 10)],
}

# 原图里内容的包围盒（相对宽高 0~1），用来把上面的「内容坐标」换算成画布坐标
IMG_BOX = {
    "sd_bird":     (0.236, 0.038, 0.707, 1.000),
    "sd_cat":      (0.183, 0.121, 0.856, 0.936),
    "sd_dawang":   (0.123, 0.066, 0.902, 0.940),
    "sd_deer":     (0.121, 0.061, 0.900, 0.940),
    "sd_fangkuai": (0.168, 0.114, 0.866, 0.947),
    "sd_fish":     (0.160, 0.117, 0.888, 0.930),
    "sd_fox":      (0.247, 0.132, 0.768, 1.000),
    "sd_heitao":   (0.146, 0.123, 0.812, 0.935),
    "sd_hongxin":  (0.080, 0.000, 0.794, 0.949),
    "sd_jelly":    (0.147, 0.092, 0.859, 0.926),
    "sd_meihua":   (0.192, 0.155, 0.895, 0.915),
    "sd_tanuki":   (0.113, 0.050, 0.779, 0.898),
    "sd_xiaowang": (0.171, 0.022, 0.943, 0.973),
    "sd_zilong":   (0.269, 0.076, 0.740, 1.000),
}
IMG_SIZE = {"sd_fish": (1024, 1536)}     # 其余都是 1024×1024


def _fit(W, H):
    """object-fit:contain 到正方形舞台后的缩放与偏移（0~1）。"""
    if W >= H:
        dw, dh = 1.0, H / float(W)
        return dw, dh, 0.0, (1 - dh) / 2
    dw, dh = W / float(H), 1.0
    return dw, dh, (1 - dw) / 2, 0.0


def _parts_of(wid):
    """内容坐标 → 舞台百分比（含 object-fit 留白），给前端 CSS 遮罩直接用。"""
    box = IMG_BOX.get(wid)
    if not box or wid not in PART_BY_ID:
        return []
    W, H = IMG_SIZE.get(wid, (1024, 1024))
    dw, dh, ox, oy = _fit(W, H)
    x1, y1, x2, y2 = box
    out = []
    for label, cx, cy, rx, ry, rot in PART_BY_ID[wid]:
        px = ox + (x1 + (x2 - x1) * cx / 100.0) * dw
        py = oy + (y1 + (y2 - y1) * cy / 100.0) * dh
        prx = (x2 - x1) * rx / 100.0 * dw
        pry = (y2 - y1) * ry / 100.0 * dh
        out.append({"label": label, "x": round(px * 100, 2), "y": round(py * 100, 2),
                    "rx": round(prx * 100, 2), "ry": round(pry * 100, 2), "rot": rot})
    return out


# 本来就是笑眯眼的宠物：叠一张「睁眼」层，平时显示，眨眼时隐藏 140ms
EYES_OF = {"sd_meihua": "meihua", "sd_fox": "fox"}

# 需要眨眼覆盖层的宠物（对应 app/typing/studio/blink/<名字>.png，
# 由 tools/make_blink_overlays.py 生成）。本来就是眯眼 / wink 的不列。
BLINK_OF = {
    "sd_fangkuai": "fangkuai", "sd_hongxin": "hongxin",
    "sd_heitao": "heitao", "sd_dawang": "dawang", "sd_cat": "cat",
    "sd_deer": "deer", "sd_zilong": "zilong", "sd_bird": "bird",
    "sd_tanuki": "tanuki", "sd_fish": "fish", "sd_jelly": "jelly",
    "sd_xiaowang": "xiaowang",
}

# ---- 作品清单 ----
# kind: pet=宠物 / theme=界面主题。蛋不单列（蛋即宠物的孵化前形态，随作品宠物展示）。
# img: 相对 IMG_ROOT 的路径。fx: 专属效果动画。designer: 缺省设计者信息。
STUDIO_WORKS = [
    # ---- 第一批·扑克花色六宠 ----
    {"id": "sd_meihua",   "name": "小狐狸·梅花", "kind": "pet", "img": "/pets/meihua.png",
     "series": "花色六宠", "mood": "梅花落在键盘上的小狐狸", "fx": FX_GLOW,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_fangkuai", "name": "小兔子·方块", "kind": "pet", "img": "/pets/fangkuai.png",
     "series": "花色六宠", "mood": "蹦蹦跳跳的方块兔", "fx": FX_SPARKLE,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_hongxin",  "name": "小猫·红心", "kind": "pet", "img": "/pets/hongxin.png",
     "series": "花色六宠", "mood": "大尾巴甩出一颗心", "fx": FX_SPARKLE,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_heitao",   "name": "小老鼠·黑桃", "kind": "pet", "img": "/pets/heitao.png",
     "series": "花色六宠", "mood": "捧着五边形孔图案币", "fx": FX_GLOW,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_dawang",   "name": "小蛇·大王", "kind": "pet", "img": "/pets/dawang.png",
     "series": "花色六宠", "mood": "大王出场，键盘让路", "fx": FX_GLOW,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_xiaowang", "name": "小龙·小王", "kind": "pet", "img": "/pets/xiaowang.png",
     "series": "花色六宠", "mood": "亮蓝角与尾巴的小王", "fx": FX_GLOW,
     "designer": {"name": "", "cls": "", "intro": ""}},
    # ---- 第二批·手绘八宠 ----
    {"id": "sd_cat",      "name": "小猫", "kind": "pet", "img": "/pets/cat.png",
     "series": "手绘八宠", "mood": "黄眼睛，双拳举胸前", "fx": FX_SPARKLE,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_deer",     "name": "小鹿", "kind": "pet", "img": "/pets/deer.png",
     "series": "手绘八宠", "mood": "长角、蝴蝶结、举着小杯", "fx": FX_GLOW,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_zilong",   "name": "紫龙", "kind": "pet", "img": "/pets/zilong.png",
     "series": "手绘八宠", "mood": "紫角、吐舌、披风、紫卷尾", "fx": FX_SPARKLE,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_tanuki",   "name": "小狸", "kind": "pet", "img": "/pets/tanuki.png",
     "series": "手绘八宠", "mood": "wink 宝石眼，抱着月牙", "fx": FX_SPARKLE,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_bird",     "name": "青鸟", "kind": "pet", "img": "/pets/bird.png",
     "series": "手绘八宠", "mood": "红星眼、粉围巾、青绿羽翼", "fx": FX_GLOW,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_fox",      "name": "蓝狐", "kind": "pet", "img": "/pets/fox.png",
     "series": "手绘八宠", "mood": "蓝尖耳鬃毛、眯眼笑、蓝宝石", "fx": FX_GLOW,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_fish",     "name": "海鱼", "kind": "pet", "img": "/pets/fish.png",
     "series": "手绘八宠", "mood": "细长 S 形飘逸身体", "fx": FX_GLOW,
     "designer": {"name": "", "cls": "", "intro": ""}},
    {"id": "sd_jelly",    "name": "水母娜娜", "kind": "pet", "img": "/pets/jelly.png",
     "series": "手绘八宠", "mood": "蓝伞盖、深蓝大眼、飘逸触手", "fx": FX_SPARKLE,
     "designer": {"name": "", "cls": "", "intro": ""}},
    # 2.0.58：界面皮肤主题（宠物农场 / 翻牌乐）整体下架 —— 牛哥反馈换了看不出效果，
    # 与其留个点了没反应的入口，不如去掉。老档案里的 studio_themes 字段原样保留，
    # 不删数据，只是不再出现在列表里。
]

# 作品蛋清单：与宠物一一对应，仅做展示（图案随作品宠物呈现，不单卖）
# color/soft：蛋的主色与浅色，创意馆「未孵化问号卡」按它俩做渐变背景，
# 和蛋区一一呼应（梅花粉 / 方块金 / 黑桃紫 …），不直接贴蛋图。
STUDIO_EGGS = [
    {"id": "sd_egg_meihua",   "name": "梅花蛋",   "pet": "sd_meihua",   "img": "/eggs/meihua.png",   "color": "#E887A8", "soft": "#FDE8F0"},
    {"id": "sd_egg_fangkuai", "name": "方块蛋",   "pet": "sd_fangkuai", "img": "/eggs/fangkuai.png", "color": "#E8AE3C", "soft": "#FFF2CE"},
    {"id": "sd_egg_hongxin",  "name": "红心蛋",   "pet": "sd_hongxin",  "img": "/eggs/hongxin.png",  "color": "#E8757A", "soft": "#FFE4E4"},
    {"id": "sd_egg_heitao",   "name": "黑桃蛋",   "pet": "sd_heitao",   "img": "/eggs/heitao.png",   "color": "#9B7CD0", "soft": "#EFE6FB"},
    {"id": "sd_egg_dawang",   "name": "大王蛋",   "pet": "sd_dawang",   "img": "/eggs/dawang.png",   "color": "#6FBF73", "soft": "#E3F5E2"},
    {"id": "sd_egg_xiaowang", "name": "小王蛋",   "pet": "sd_xiaowang", "img": "/eggs/xiaowang.png", "color": "#6AA5DC", "soft": "#E2EFFB"},
    {"id": "sd_egg_cat",      "name": "小猫蛋",   "pet": "sd_cat",      "img": "/eggs/xiaomao.png", "color": "#D89A6C", "soft": "#FBEAD8"},
    {"id": "sd_egg_zilong",   "name": "紫龙蛋",   "pet": "sd_zilong",   "img": "/eggs/zilong.png",   "color": "#A66BCB", "soft": "#F1E3FA"},
    {"id": "sd_egg_qingniao", "name": "青鸟蛋",   "pet": "sd_bird",     "img": "/eggs/qingniao.png", "color": "#4EB3A6", "soft": "#DDF2EE"},
    {"id": "sd_egg_shuimu",   "name": "水母蛋",   "pet": "sd_jelly",    "img": "/eggs/shuimu.png",   "color": "#5B9FD6", "soft": "#DDEDFA"},
    {"id": "sd_egg_tanuki",   "name": "小狸蛋",   "pet": "sd_tanuki",   "img": "/eggs/tanuki.png",   "color": "#C08557", "soft": "#F4E3D2"},
    {"id": "sd_egg_haiyu",    "name": "海鱼蛋",   "pet": "sd_fish",     "img": "/eggs/haiyu.png",    "color": "#3FA9CE", "soft": "#D9F1F8"},
    {"id": "sd_egg_random",   "name": "随机蛋",   "pet": "",            "img": "/eggs/suiji-question.png", "color": "#F0A93B", "soft": "#FFF0D2"},
]

# 索引：id -> 作品
BY_ID = {w["id"]: w for w in STUDIO_WORKS}
EGG_BY_ID = {e["id"]: e for e in STUDIO_EGGS}
PET_IDS = [w["id"] for w in STUDIO_WORKS if w["kind"] == "pet"]
THEME_IDS = [w["id"] for w in STUDIO_WORKS if w["kind"] == "theme"]


def default_designers():
    """缺省设计者信息（教师端可覆盖，存进 config.json 的 studio.designers）。"""
    return {w["id"]: dict(w["designer"]) for w in STUDIO_WORKS}


def public_cfg(designers=None, unlocked=False, free=None, school=""):
    """给学生端的只读作品清单：设计者信息 + 动作 + 价格 + 标签；拥有态由前端合并。

    2.0.56：作品宠物**不再下发可购买标记**，`buyable=False`；唯一入口是
    eggs 里的作品蛋。蛋价 = 旧宠物价（2000），服务端只认 egg_id。
    2.0.68：附 free={student,teacher} 免费孵化额度（只读展示，真校验在服务端）。
    2.0.72：附 school=全局学校名（设置页填的 school_name），创作者署名带上学校；
            这是展示用的只读字段，教师端「设计者信息」接口不接受改 school。
    2.0.76（隐私脱敏）：designer **只下发自愿填写的笔名（pen）**，不再下发
            真实姓名 / 班级 / 学校 / 介绍 —— 初中生多不满 14 周岁，属敏感个人信息，
            公开接口（含未登录演示页）不能带可定位到具体学生的信息。教师端填写的
            旧真实数据仍留在 config.json（不下发），需要时可在服务端手工取用。
    绝不下发任何可写配置。"""
    designers = designers if isinstance(designers, dict) else {}
    free = free if isinstance(free, dict) else {}
    school = str(school or "")
    # 2.0.75：学校名改成每个作品独立（不同学校的学生可能创作不同作品）。
    # 作品自己没填 school 时，回退到旧的全局 school_name，兼容老数据不丢署名。
    def _school_of(d):
        return str(d.get("school") or school)
    try:
        free_student = max(0, min(9, int(free.get("student", 1))))
    except (TypeError, ValueError):
        free_student = 1
    try:
        free_teacher = max(0, min(9, int(free.get("teacher", 2))))
    except (TypeError, ValueError):
        free_teacher = 2
    egg_of_pet = {}
    for e in STUDIO_EGGS:
        if e.get("pet"):
            egg_of_pet.setdefault(e["pet"], e["id"])
    works = []
    mi = 0
    for w in STUDIO_WORKS:
        d = designers.get(w["id"]) or w["designer"] or {}
        is_theme = w["kind"] == "theme"
        item = {
            "id": w["id"], "name": w["name"], "kind": w["kind"],
            "img": _img(w["img"]), "series": w["series"], "mood": w["mood"],
            "fx": w["fx"], "tag": STUDIO_TAG, "tagColor": STUDIO_TAG_COLOR,
            # 宠物卡不再显示价格，也不再发兑换请求；主题仍按原价直兑
            "price": STUDIO_THEME_PRICE if is_theme else STUDIO_EGG_PRICE,
            "buyable": is_theme,
            # 2.0.76：只下发自愿填写的笔名；cls/school/intro 留空占位，
            # 保持字段结构不变（前端零改动），但绝不带真实姓名/班级/学校/介绍。
            # 注意：这里刻意不回退到 d["name"]，旧真实姓名数据不会漏出。
            "designer": {"name": str(d.get("pen") or ""),
                         "cls": "", "school": "", "intro": ""},
        }
        if w.get("skin"):
            item["skin"] = w["skin"]
        motion, dur = MOTION_BY_ID.get(w["id"], DEFAULT_MOTION)
        item["motion"] = motion
        item["dur"] = dur
        item["delay"] = round((mi % 5) * 0.28, 2)
        # 眨眼：两套来源，但都是「一张闭眼帧从上往下压」
        #   1) BLINK_OF：原图本身就是睁眼，只需盖一张闭眼帧；
        #   2) EYES_OF：原图是笑眯眼，先叠睁眼层，再盖同一套闭眼帧——
        #      不能靠「隐藏睁眼层」来眨眼，那样会露出原图的 ^ ^。
        blink_name = BLINK_OF.get(w["id"]) or EYES_OF.get(w["id"])
        if blink_name:
            # 2.0.60：闭眼帧换成 AI 照范本重画的整图；本次连白边一起清理，换戳破缓存
            item["blink"] = _img("/blink/" + blink_name + ".png")
            item["blinkDur"] = round(dur * 1.4, 2)
        if w["id"] in EYES_OF:
            item["eyes"] = _img("/eyes/" + EYES_OF[w["id"]] + ".png")
            item["eyesDur"] = round(dur * 1.35, 2)
        parts = _parts_of(w["id"])
        if parts:
            item["parts"] = parts
        # 悬停预览时列出来的动作清单（去重，顺序固定）
        acts = []
        if w["id"] in BLINK_OF or w["id"] in EYES_OF:
            acts.append({"k": "blink", "label": "眨眼"})
        for p in parts:
            if not any(a["label"] == p["label"] for a in acts):
                acts.append({"k": "part", "label": p["label"]})
        acts.append({"k": "fx", "label": FX_NAME.get(w["fx"], "光效")})
        acts.append({"k": "move", "label": MOVE_NAME.get(motion, "动作")})
        item["acts"] = acts
        if not is_theme:
            item["egg"] = egg_of_pet.get(w["id"], "")
        mi += 1
        works.append(item)
    eggs = []
    for e in STUDIO_EGGS:
        pet = BY_ID.get(e.get("pet") or "")
        eggs.append({
            "id": e["id"], "name": e["name"], "pet": e["pet"],
            "img": IMG_ROOT + e["img"],
            "price": STUDIO_EGG_PRICE,
            "random": not bool(e.get("pet")),
            "petName": pet["name"] if pet else "随机作品宠物（不含系统宠物）",
            "petImg": _img(pet["img"]) if pet else "",
            "color": e.get("color", STUDIO_TAG_COLOR),
            "soft": e.get("soft", "#FFF4E2"),
            "motion": "m-egg" if e["id"] != "sd_egg_random" else "m-egg-random",
            "dur": 3.2 if e["id"] != "sd_egg_random" else 2.4,
        })
    return {"works": works, "eggs": eggs,
            "petPrice": STUDIO_EGG_PRICE, "themePrice": STUDIO_THEME_PRICE,
            "eggPrice": STUDIO_EGG_PRICE,
            "free": {"student": free_student, "teacher": free_teacher},
            # 2.0.76：顶层 school 也置空——前端没用它，留着只会把学校名
            # 经由公开接口送到公网。字段保留（空串）以兼容旧读取代码。
            "school": "",
            "tag": STUDIO_TAG, "tagColor": STUDIO_TAG_COLOR,
            "unlocked": bool(unlocked)}
