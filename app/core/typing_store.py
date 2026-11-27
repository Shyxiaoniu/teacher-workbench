#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
班级工作台 2.0 · 打字冒险岛数据层（typing_store.py）

设计要点：
  · 独立 JSON 存储（DATA_DIR/typing.json），不混入 29 表同步体系 ——
    打字成绩是"游戏数据"，写入频率高、可整体重建，走独立通道最干净；
  · 原子落盘（临时文件 + os.replace），多线程安全（一把锁）；
  · 星级/奖励由服务端按公式复核，不信任客户端上报；
  · 学生档案只存 student_id，姓名/班级由 server.py 按 STORE 实时补齐，
    学生改名 / 转班后排行榜自动跟着变。
"""
import hashlib
import json
import os
import time
import threading
try:
    import studio_data as SD          # 学生设计作品（宠物/蛋/主题）
except Exception:
    SD = None

# 星级规则（与 app/typing/typing-data.js 的 STAR_RULE 保持一致）
# 7=数字小键盘、8=中英混排：2026-11-02 新增的两个篇章，缺了它们
# 这两章的进度会因为 fallback 到 24 而和服务端判星对不上。
SPEED_BASE = {1: 26, 2: 34, 3: 26, 4: 22, 5: 16, 6: 12, 7: 30, 8: 15}
PASS_ACC = 90
PERFECT_ACC = 97
# 篇章满星上限（2026-11-17，与 typing-data.js 的 STAR_RULE.starCap 同一份）：
#   键位熟悉 / 字母位置 = 1 星；英文单词 / 大小写标点 / 数字小键盘 = 2 星；
#   中文词语 / 文章阅读 / 中英混排 = 3 星。未登记篇章按 3 星。
STAR_CAP = {1: 1, 2: 1, 3: 2, 4: 2, 7: 2, 5: 3, 6: 3, 8: 3}


def max_stars(stage):
    """某篇章的满星上限（1/2/3）。"""
    try:
        v = STAR_CAP.get(int(stage))
    except Exception:
        v = None
    return v if v in (1, 2, 3) else 3


WPM_CAP = 160          # 速度封顶：超过视为异常，直接压线（与前端星级/排行榜口径一致）
MIN_DURATION = 3       # 秒：太短的成绩不收

# 装扮解锁里程碑（与 typing-data.js 的 DECOS need 一致）
DECO_NEEDS = [10, 20, 40, 60, 80, 100, 130, 160]

# 装扮部位表（与 typing-data.js 的 DECOS.cat 一致）
# 同一部位只能穿一件；整套（suit）同时占上衣和下衣。
DECO_CAT = {
    "hat-red": "head", "crown": "head", "bow": "head", "headset": "head",
    "cap": "head", "flower": "head", "party": "head",
    "sunglass": "face", "glasses": "face", "earmuffs": "face", "mask": "face",
    "scarf": "top", "vest": "top", "hoodie": "top", "tee": "top",
    "overall": "bottom", "skirt": "bottom", "shorts": "bottom",
    "bg-star": "suit", "robe": "suit", "spacesuit": "suit",
}

# 装扮售价（与 typing-data.js 的 DECOS.price 一致）
# 价格只在这里生效 —— 客户端传来的 price 一律不认，否则改一下请求就能白拿。
# 2026-11-05 起全店统一用「积分」结算：原金币按 1:1 折算进积分，价格数值不变。
DECO_PRICE = {
    "hat-red": 150, "crown": 300, "sunglass": 400, "glasses": 380,
    "scarf": 500, "vest": 450, "overall": 550, "skirt": 550,
    "bow": 650, "headset": 750, "robe": 800, "bg-star": 1000,
    "cap": 320, "earmuffs": 260,
    # 2026-11-17：小红领结（bowtie）下架（看着像内衣），上衣补卫衣 / 条纹 T 恤；
    # 另新增花环、派对帽、口罩、短裤、太空服。
    "hoodie": 460, "tee": 300, "flower": 360, "party": 340,
    "mask": 240, "shorts": 420, "spacesuit": 1200,
}

# 道具价格与携带上限（与 typing-data.js 的 ITEMS 一致）
ITEMS = {
    "hint":   {"price": 30, "max": 3},
    "extra":  {"price": 45, "max": 3},
    "shield": {"price": 80, "max": 2},
    "double": {"price": 60, "max": 2},
}

# 牧场背景售价（积分，与 typing-data.js 的 BACKGROUNDS 一致）
# '' 是默认草地，免费，不在表里 = 不可买也不需要买。
BG_PRICE = {
    "meadow": 200, "beach": 350, "night": 500,
    "candy": 800, "space": 1200,
}

# 出战特效售价（积分，与 typing-data.js 的 EFFECTS 一致）
# 特效是全店最贵的奢侈品，攒 2000 分要打很久。
EFFECT_PRICE = {
    "sparkle": 200, "bubbles": 400, "hearts": 650,
    "snow": 900, "aurora": 2000,
}

# 用积分直接兑一颗宠物蛋（2026-11-19 新增，与 typing-data.js 的 EGG_PRICE 一致）。
# 背景：宠物蛋的主渠道是「BOSS 关满星」，但满星上限分 1/2/3 三档、差一星还有
# 55% 的运气成分，总有学生卡在「一直差一颗星、一直押不中」上，图鉴永远开不完。
# 所以补一条兜底渠道：攒够积分直接换一颗。
# 定价高于一件中档装扮（300），保证「练得好」仍然是最快的路 ——
# 满星掉蛋是白拿，靠积分换要打十几关才够。
EGG_PRICE = 300

DEFAULT_RECORD = {
    "best": {},            # levelKey -> {wpm, acc, stars, ts}
    "stars": 0,            # 累计星数（经验）
    "week": {"k": "", "stars": 0},
    "pets": [],            # 已拥有宠物 id（系统自带 SVG 宠物）
    "studio_pets": [],     # 已拥有的学生设计宠物 id（sd_ 前缀，PNG 出战）
    "studio_themes": [],   # 已拥有的学生设计界面主题 id（只能积分兑换）
    "studio_free_used": 0, # 已用掉的学生作品蛋免费孵化次数（2.0.68，按账号永久记录）
    "decos": [],           # 已解锁装扮 id
    "eggs": 0,             # 待孵化宠物蛋
    "egg_keys": [],        # 已经掉过蛋的关卡（levelKey），一关只掉一颗、成绩提升可补掉
    "equip": {"pet": "", "studio_pet": "", "studio_theme": "",
              "decos": [], "bg": "", "fx": ""},
    "pts": 0,              # 统一积分：闯关 / 教师发放获得，全店通用
    "bgs": [],             # 已拥有牧场背景 id
    "effects": [],         # 已拥有特效 id
    "items": {},           # 道具库存 {hint:1, extra:2...}
    "pending": {},         # 已用但尚未结算的道具（double / shield）
    "daily": {"d": "", "n": {}},   # 当天重复练习计次（防刷积分）
    "draft": None,         # 练习中断草稿（教师关闭练习时学生进度不丢）
    "updated": 0
}


def base_pts_for(stars, level, repeat):
    """每关基础积分（原金币公式，与 levels.js 的 basePtsFor 完全一致）。

    · 1 星不发基础积分（想攒分得打出 2 星以上）；
    · 首通 2 星 5 / 3 星 8，BOSS 首通再 +5；
    · 重复练每天每关只结算一次，2 分（BOSS 3）。
    """
    if not stars:
        return 0
    if repeat:
        return 3 if level == 10 else 2
    if stars < 2:
        return 0
    return (8 if stars >= 3 else 5) + (5 if level == 10 else 0)


def bonus_pts_for(stars, stage, chapter, repeat):
    """难度积分（原积分公式，与 levels.js 的 bonusPtsFor 完全一致）。

    越难的篇章越值钱：基础键位篇（stage 1）为 0，防止刷基础关；
    卷数每推进一卷 +1 分。重复练每天一次 1 分（需 2 星）。
    """
    if not stars:
        return 0
    if repeat:
        return 1 if stars >= 2 else 0
    table = {1: 0, 2: 1, 3: 1, 4: 1, 5: 2, 6: 2, 7: 1, 8: 2}
    return stars * table.get(int(stage), 1) + max(0, int(chapter) - 1)


def pts_for(stars, stage, chapter, level, repeat):
    """统一积分 = 基础积分 + 难度积分。

    两套数值体系 1:1 合并后，每关收益严格等于原来「金币 + 积分」之和，
    学生不会因为改版变穷或变富；加倍卡仍只翻「基础积分」那一部分。
    """
    return (base_pts_for(stars, level, repeat)
            + bonus_pts_for(stars, stage, chapter, repeat))


def clean_equip(eq):
    """把客户端传来的阵容收敛成固定四字段 {pet, decos, bg, fx}。

    排行榜 / 教师行统一从这里取值 —— 以前排行榜只搬 pet+decos，
    背景和特效（2026-11-05 新增）压根没往后传，学生端榜单里
    宠物是「裸的」，跟牧场里穿好的一模一样对不上。
    """
    e = eq if isinstance(eq, dict) else {}
    decos = e.get("decos")
    if not isinstance(decos, (list, tuple)):
        decos = []
    return {
        "pet": str(e.get("pet") or ""),
        "decos": [str(d) for d in decos if d],
        "bg": str(e.get("bg") or ""),
        "fx": str(e.get("fx") or ""),
    }


def _equip_for_lb(stu):
    """排行榜用的阵容：没手动选过出战宠物时，默认用已拥有的第一只，
    这样「孵出来了但忘了点出战」的同学在榜上也看得见自己的宠物，
    而不是清一色 🐾 爪印。

    2.0.65：学生作品宠物出战时也要把 studio_pet 带进榜单，否则榜上
    只剩爪印；bg/fx 走 clean_equip 的四字段，作品宠物同样吃这套装扮。"""
    eq = clean_equip(stu.get("equip"))
    sp = str((stu.get("equip") or {}).get("studio_pet") or "")
    if sp:
        eq["studio_pet"] = sp
        eq["pet"] = ""
        eq["decos"] = []
    elif not eq["pet"]:
        pets = stu.get("pets") or []
        eq["pet"] = str(pets[0]) if pets else ""
    if not eq["pet"] and not sp:
        eq["decos"] = []
    return eq


def week_key(ts=None):
    """ISO 周标识，如 2026-W45；每周一自动切换 = 周榜自动清零

    ⚠️ 只有「本周星光」这一项跟着周走，别的都不动。
    每周重置清单（改代码前先读这段）：
      · 会重置：week.stars（本周星光，周榜用）
      · 不重置：pts 积分、pets 宠物、decos 装扮、bgs 背景、effects 特效、
                items 道具、stars 累计星（解锁装扮的依据）、best 最好成绩、
                eggs / egg_keys 宠物蛋
    学生攒的积分和宠物是长期资产，任何「周切换」逻辑都不许碰它们。
    """
    t = time.localtime(ts or time.time())
    return time.strftime("%G-W%V", t)


# ---------------- 宠物蛋掉落（2026-11-19 难度上调） ----------------
# 旧规则：BOSS 关首通只要拿到 1 星就必掉一颗，八个篇章一周能凑八颗，
# 宠物几乎不用练就到手，也没人再回头看自己没满星的 BOSS。
# 新规则（只在 BOSS 关 level==10 生效，按「相对满星」判定）：
#   · 满星        —— 必掉；
#   · 差一星      —— 55% 概率（按「学生 + 关卡」哈希固定，不会刷出来/刷不掉）；
#   · 差两星以上  —— 不掉，回去把速度和准确率练上来还能补。
# 满星上限因篇章而异（1 星 / 2 星 / 3 星三档），所以拿 stage 1 的 1 星
# 和 stage 6 的 3 星是同一件事 —— 都是「这一章你已经打到头了」。
EGG_ROLL_RATE = 0.55


def egg_roll(uid, key, rate=EGG_ROLL_RATE):
    """确定性「抽签」：同一学生对同一关永远得到同一结果。

    用 md5 而不是 random —— 服务端可能因为补发、重算、换机器等原因
    对同一关反复求值，真随机会出现「刚才掉了现在又没掉」的灵异现象。
    """
    seed = ("%s|%s|egg-v2" % (uid, key)).encode("utf-8")
    h = hashlib.md5(seed).hexdigest()
    return (int(h[:8], 16) % 10000) < int(round(rate * 10000))


def egg_drop(stars, uid, key, stage):
    """这一关（BOSS）这次该不该掉蛋。stars 传本次复核后的星级。"""
    if stars <= 0:
        return False
    cap = max_stars(stage)
    if stars >= cap:
        return True
    if stars >= cap - 1:
        return egg_roll(uid, key)
    return False


def speed_line(stage, chapter):
    """速度线必须与 levels.js 的 speedLine 完全一致，否则会出现
    「学生端显示 2 星、服务端判 1 星」的对不上账。
    2026-11-02 同步放缓：每卷 +2%、最多 1.6 倍、第一卷打 95 折。"""
    base = SPEED_BASE.get(int(stage), 24)
    ch = max(1, int(chapter))
    grow = min(ch - 1, 30) * 0.02
    warm = 0.95 if ch == 1 else 1
    return round(base * (1 + grow) * warm, 1)


def stars_for(stage, chapter, acc, wpm):
    """服务端星级复核（与 levels.js 同款公式，含篇章满星上限）"""
    if acc < PASS_ACC:
        return 0
    cap = max_stars(stage)
    line = speed_line(stage, chapter)
    if acc >= PERFECT_ACC and wpm >= line * 1.3:
        return min(3, cap)
    if wpm >= line:
        return min(2, cap)
    return min(1, cap)


class TypingStore:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        # teacher 段（2026-11-03）：教师练习成绩，按班级存 ——
        # {class_id: {name, best:{levelKey:{stars,wpm}}, week:{k,stars}, stars, best_wpm, updated}}
        # 开启「计入班级排行榜」才写入；不占用学生档案，排行榜组装时单独成行。
        self.data = {"students": {}, "teacher": {}}
        self._load()

    # ---------------- 底层 ----------------
    def _load(self):
        self._locked = False
        try:
            import enc_ctx as ENC
            d = ENC.read_json_file(self.path, domain="json")
            if isinstance(d, dict) and isinstance(d.get("students"), dict):
                self.data = d
        except Exception:
            try:
                self._locked = bool(ENC.is_encrypted_path(self.path))
            except Exception:
                self._locked = False
        if not isinstance(self.data.get("teacher"), dict):
            self.data["teacher"] = {}
        # 2026-11-05 统一积分体系：老档案里的 coins 按 1:1 折进 pts，
        # 总价值不变；教师发放记录 coin_log 原样改名为 pts_log（不丢历史）。
        # 只在真的发现老字段时落盘，避免每次启动都写一遍。
        migrated = False
        for stu in self.data["students"].values():
            if not isinstance(stu, dict) or "coins" not in stu:
                continue
            try:
                old_coins = int(stu.get("coins") or 0)
            except Exception:
                old_coins = 0
            try:
                old_pts = int(stu.get("pts") or 0)
            except Exception:
                old_pts = 0
            stu["pts"] = old_pts + old_coins
            stu.pop("coins", None)
            migrated = True
        # 2026-11-19 宠物蛋难度上调：老档案补 egg_keys。
        # 旧规则是「BOSS 首通 1 星必掉」，所以历史上打通过的 BOSS 关都算已掉过；
        # 这里只补一个「已掉过」记录，防止新逻辑对老玩家再发一遍 ——
        # 不扣蛋、不改星级、不动积分和宠物。
        for stu in self.data["students"].values():
            if not isinstance(stu, dict) or "egg_keys" in stu:
                continue
            owned = []
            for k, b in (stu.get("best") or {}).items():
                try:
                    if str(k).endswith("l10") and int((b or {}).get("stars") or 0) >= 1:
                        owned.append(str(k))
                except Exception:
                    continue
            stu["egg_keys"] = owned
            migrated = True
        if "coin_log" in self.data:
            if not isinstance(self.data.get("pts_log"), list):
                self.data["pts_log"] = self.data.pop("coin_log")
            else:
                self.data.pop("coin_log", None)
            migrated = True
        # 2026-11-21 学生设计作品：老档案补 studio_pets / studio_themes 和出战槽。
        # 只补空字段，不动原有宠物 / 积分 / 装扮。
        for stu in self.data["students"].values():
            if not isinstance(stu, dict):
                continue
            touched = False
            for f in ("studio_pets", "studio_themes"):
                if not isinstance(stu.get(f), list):
                    stu[f] = []
                    touched = True
            try:
                used = int(stu.get("studio_free_used", 0))
            except (TypeError, ValueError):
                used = 0
            if used < 0 or stu.get("studio_free_used") != used:
                stu["studio_free_used"] = max(0, used)
                touched = True
            eq = stu.get("equip")
            if isinstance(eq, dict):
                for f in ("studio_pet", "studio_theme"):
                    if f not in eq:
                        eq[f] = ""
                        touched = True
            if touched:
                migrated = True
        if migrated:
            try:
                self._save()
            except Exception:
                pass

    def _save(self):
        import enc_ctx as ENC
        if getattr(self, "_locked", False):
            if not ENC.get_fek():
                raise ENC.NoFekError("数据密钥不可用（请重新登录后再保存）")
            self._load()
            self._locked = False
        ENC.write_json_file(self.path, self.data, domain="json")

    def _stu(self, uid):
        s = self.data["students"].get(uid)
        if s is None:
            s = json.loads(json.dumps(DEFAULT_RECORD))
            self.data["students"][uid] = s
        return s

    # ---------------- 练习成绩 ----------------
    def record(self, uid, body):
        """提交一关成绩。返回 {ok, stars, new_best, gained_stars, rewards, stu}"""
        try:
            stage = int(body.get("stage", 0))
            chapter = int(body.get("chapter", 0))
            level = int(body.get("level", 0))
            acc = float(body.get("acc", 0))
            chars = int(body.get("chars", 0))
            duration = float(body.get("duration", 0))
        except Exception:
            return {"ok": False, "msg": "成绩数据不完整"}
        # 1..8：1-6 是原有篇章，7=数字小键盘、8=中英混排（2026-11-02 新增）
        if not (1 <= stage <= 8) or chapter < 1 or not (1 <= level <= 10):
            return {"ok": False, "msg": "关卡不存在"}
        if duration < MIN_DURATION or chars < 1:
            return {"ok": False, "msg": "用时太短，成绩不计入"}
        acc = max(0.0, min(100.0, acc))
        wpm = min(WPM_CAP, round(chars / duration * 60, 1))
        stars = stars_for(stage, chapter, acc, wpm)
        key = "s%dc%dl%d" % (stage, chapter, level)

        with self.lock:
            stu = self._stu(uid)
            # 每天第一次记录时重置重复练习计次表
            today = time.strftime("%Y-%m-%d")
            d = stu.get("daily") or {}
            if d.get("d") != today:
                d = {"d": today, "n": {}}
                stu["daily"] = d
            prev = stu["best"].get(key)
            # 容错卡：本关准确率 ≥87% 也算通关，但只补到 1 星。
            # 2/3 星的速度线与准确率完全不变 —— 道具只救"卡住"，不代替练习。
            pending = stu.get("pending") or {}
            if not stars and pending.get("shield") and acc >= 87:
                stars = 1
            new_best = (prev is None or stars > prev["stars"]
                        or (stars == prev["stars"] and wpm > prev["wpm"]))
            gained = 0
            base_pts = 0
            pts = 0
            rewards = {"egg": False, "decos": []}
            if new_best:
                gained = stars - (prev["stars"] if prev else 0)
                if gained < 0:
                    gained = 0
                stu["best"][key] = {"wpm": wpm, "acc": round(acc, 1),
                                    "stars": stars, "ts": int(time.time())}
                base_pts = base_pts_for(stars, level, False)
                pts = base_pts + bonus_pts_for(stars, stage, chapter, False)
                wk = week_key()
                if stu["week"].get("k") != wk:
                    stu["week"] = {"k": wk, "stars": 0}
                stu["week"]["stars"] += gained
                stu["stars"] += gained
                # 星数里程碑 → 解锁装扮
                owned = stu["decos"]
                deco_pool = ["hat-red", "crown", "sunglass", "glasses",
                             "scarf", "bow", "headset", "bg-star"]
                for i, need in enumerate(DECO_NEEDS):
                    did = deco_pool[i] if i < len(deco_pool) else None
                    if did and stu["stars"] >= need and did not in owned:
                        owned.append(did)
                        rewards["decos"].append(did)
            elif stars > 0:
                # 重复练习：同一关每天只结算一次保底积分，刷不出分来
                n = d["n"]
                if n.get(key, 0) < 1:
                    n[key] = n.get(key, 0) + 1
                    base_pts = base_pts_for(stars, level, True)
                    pts = base_pts + bonus_pts_for(stars, stage, chapter, True)
            if pts:
                # 加倍卡：只把基础积分翻一倍（难度积分不翻），合并前后口径一致。
                # 由 use_item 记在 pending 上，结算完立刻清空，绝不跨关。
                if pending.get("double"):
                    pts += base_pts
                stu["pts"] = stu.get("pts", 0) + pts
            # Boss 关掉宠物蛋（2026-11-19 难度上调，见 egg_drop 注释）：
            # 不再要求「首通」，也允许「首通没掉、后来打高了再补」——
            # 唯一的铁律是同一关只掉一颗，记在 egg_keys 里。
            if level == 10 and key not in stu.setdefault("egg_keys", []):
                if egg_drop(stars, uid, key, stage):
                    stu["egg_keys"].append(key)
                    stu["eggs"] = stu.get("eggs", 0) + 1
                    rewards["egg"] = True
            # 这一关打完了：练习草稿随之作废（只有草稿还停在「本关没打完」）
            dr = stu.get("draft")
            if isinstance(dr, dict) and dr.get("k") == key:
                stu["draft"] = None
            stu["pending"] = {}
            stu["updated"] = int(time.time())
            try:
                self._save()
            except Exception:
                pass
            return {"ok": True, "stars": stars, "new_best": new_best,
                    "gained": gained, "pts_gained": pts,
                    "rewards": rewards,
                    "wpm": wpm, "stu": self.summary(stu)}

    # ---------------- 学生进度 ----------------
    def summary(self, stu):
        return {"stars": stu.get("stars", 0), "week_stars": stu["week"].get("stars", 0),
                "pets": stu.get("pets", []), "decos": stu.get("decos", []),
                "studio_pets": stu.get("studio_pets", []),
                "studio_themes": stu.get("studio_themes", []),
                "studio_free_used": int(stu.get("studio_free_used") or 0),
                "eggs": stu.get("eggs", 0), "equip": stu.get("equip", {}),
                "pts": stu.get("pts", 0),
                "bgs": stu.get("bgs", []), "effects": stu.get("effects", []),
                "items": stu.get("items", {}),
                "draft": stu.get("draft"),
                "best": stu.get("best", {})}

    def progress(self, uid):
        with self.lock:
            return self.summary(self._stu(uid))

    def unlocked_count(self, uid):
        """已通关（≥1 星）的关卡数 —— 学生端据此画地图解锁态"""
        with self.lock:
            stu = self._stu(uid)
            return {k: v["stars"] for k, v in stu.get("best", {}).items()}

    # ---------------- 宠物与装扮 ----------------
    def claim_egg(self, uid, pet_ids, studio_ids=None):
        """孵蛋：普通宠物 + 学生设计宠物一起抽，但学生作品权重极低（彩蛋）。

        普通宠物 NORMAL_PET_WEIGHT=20 / 只，学生作品 STUDIO_PET_WEIGHT=1 / 只，
        22 只普通 + 14 只作品 ≈ 3% 概率孵出学生作品 —— 符合「最低概率获得」。
        抽中学生作品时写进 studio_pets，抽中普通宠物写进 pets，两套图鉴互不污染。
        全部集齐才折算 50 积分。
        """
        if not pet_ids:
            return {"ok": False, "msg": "宠物图鉴为空"}
        nw = getattr(SD, "NORMAL_PET_WEIGHT", 20) if SD else 20
        sw = getattr(SD, "STUDIO_PET_WEIGHT", 1) if SD else 1
        studio_ids = studio_ids or []
        with self.lock:
            stu = self._stu(uid)
            if stu.get("eggs", 0) <= 0:
                return {"ok": False, "msg": "还没有宠物蛋，去闯 BOSS 关赢一颗吧！"}
            owned_normal = set(stu.get("pets") or [])
            owned_studio = set(stu.get("studio_pets") or [])
            unowned = [p for p in pet_ids if p not in owned_normal]
            unowned_studio = [p for p in studio_ids if p not in owned_studio]
            stu["eggs"] -= 1
            if not unowned and not unowned_studio:
                stu["pts"] = stu.get("pts", 0) + 50
                self._save()
                return {"ok": True, "pet": None, "studio": False, "pts": 50,
                        "msg": "图鉴已集齐！蛋化作 50 积分落入囊中"}
            # 加权抽签：普通 Cys 权重 nw，学生作品权重 sw。用毫秒时间戳做种子打散。
            pool = [(p, False, nw) for p in unowned] + \
                   [(p, True, sw) for p in unowned_studio]
            total = sum(w for _, _, w in pool)
            seed = int(time.time() * 1000)
            pick = seed % total if total > 0 else 0
            pet, is_studio, _ = pool[0]
            acc = 0
            for p, s, w in pool:
                acc += w
                if pick < acc:
                    pet, is_studio = p, s
                    break
            if is_studio:
                stu.setdefault("studio_pets", []).append(pet)
                if not stu["equip"].get("pet") and not stu["equip"].get("studio_pet"):
                    stu["equip"]["studio_pet"] = pet
            else:
                stu.setdefault("pets", []).append(pet)
                if not stu["equip"].get("pet") and not stu["equip"].get("studio_pet"):
                    stu["equip"]["pet"] = pet
            self._save()
            return {"ok": True, "pet": pet, "studio": is_studio, "stu": self.summary(stu)}

    def equip(self, uid, pet, decos, bg=None, fx=None,
              studio_pet=None, studio_theme=None):
        """装备出战阵容：pet + 装扮（部位互斥）+ 牧场背景 bg + 出战特效 fx。
        bg / fx 只允许穿「衣柜里已有的」；传 None 表示这一项保持不变，
        传 '' 表示取下来（背景回到默认草地 / 特效关闭）。
        学生作品宠物 / 主题走独立字段，且 pet 与 studio_pet 只能选一个出战。"""
        with self.lock:
            stu = self._stu(uid)
            if pet and pet not in stu["pets"]:
                return {"ok": False, "msg": "还没有这只宠物"}
            if studio_pet and studio_pet not in stu.get("studio_pets", []):
                return {"ok": False, "msg": "还没有这只学生作品宠物"}
            if studio_theme and studio_theme not in stu.get("studio_themes", []):
                return {"ok": False, "msg": "还没有兑换这个界面主题"}
            # 2.0.63：作品宠物「收回」时若前端没指定系统宠物，自动回到已拥有的
            # 第一只（默认宠物），顶栏和牧场不会出现空爪印。
            if studio_pet is not None and not studio_pet and not pet:
                pet = (stu.get("pets") or [None])[0] or ""
            # 出战位二选一：选了学生作品宠物就把系统宠物的出战位清空，反之亦然
            if studio_pet:
                pet = ""
            elif pet:
                studio_pet = ""
            ds = [d for d in (decos or []) if d in stu["decos"]]
            # 还没有任何宠物时，装扮无处可穿：直接清空。
            # 以前这里不判断，学生没孵化过宠物却能"已装备墨镜和皇冠"，
            # 前端看着像 bug，其实是后端把没有载体的装扮也存下来了。
            if not stu["pets"]:
                ds = []
            # 服务端再执行一次部位互斥：客户端传来的数组可能被手动改过，
            # 不能让它一次戴上三顶帽子
            kept = []
            for d in ds:
                cd = DECO_CAT.get(d)
                bad = False
                for k in kept:
                    kc = DECO_CAT.get(k)
                    if kc == cd:
                        bad = True
                    elif cd == "suit" and kc in ("top", "bottom"):
                        bad = True
                    elif kc == "suit" and cd in ("top", "bottom"):
                        bad = True
                if not bad:
                    kept.append(d)
            ep = stu.get("equip") or {}
            ep = {"pet": pet or "", "decos": kept,
                  "bg": ep.get("bg", ""), "fx": ep.get("fx", ""),
                  "studio_pet": ep.get("studio_pet", ""),
                  "studio_theme": ep.get("studio_theme", "")}
            # None=保持；''=取下；非空=换上
            if studio_pet is not None:
                ep["studio_pet"] = str(studio_pet)
            if studio_theme is not None:
                ep["studio_theme"] = str(studio_theme)
            if bg is not None:
                if bg and bg not in stu.get("bgs", []):
                    return {"ok": False, "msg": "还没有这个背景"}
                ep["bg"] = str(bg)
            if fx is not None:
                if fx and fx not in stu.get("effects", []):
                    return {"ok": False, "msg": "还没有这个特效"}
                ep["fx"] = str(fx)
            stu["equip"] = ep
            self._save()
            return {"ok": True, "equip": stu["equip"]}

    # ---------------- 积分商店（全店统一货币） ----------------
    def _spend_pts(self, stu, price):
        """统一的积分扣款检查。返回错误文案，None 表示余额够。"""
        pts = stu.get("pts", 0)
        if pts < price:
            return "积分还差 %d 分，打出好成绩来攒" % (price - pts)
        stu["pts"] = pts - price
        return None

    def buy_deco(self, uid, deco_id):
        """用积分买装扮。星数里程碑解锁的那八件照样能买，先买就先穿。
        价格以服务端 DECO_PRICE 为准，不接受客户端传值。"""
        if deco_id not in DECO_PRICE:
            return {"ok": False, "msg": "没有这件装扮"}
        price = DECO_PRICE[deco_id]
        with self.lock:
            stu = self._stu(uid)
            if deco_id in stu["decos"]:
                return {"ok": False, "msg": "这件装扮已经在衣柜里了"}
            err = self._spend_pts(stu, price)
            if err:
                return {"ok": False, "msg": err}
            stu["decos"].append(deco_id)
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "deco": deco_id}

    def buy_item(self, uid, item_id):
        """用积分买道具。价格与上限由服务端定，不信任客户端传来的价格。"""
        spec = ITEMS.get(item_id)
        if not spec:
            return {"ok": False, "msg": "没有这个道具"}
        with self.lock:
            stu = self._stu(uid)
            items = stu.get("items") or {}
            have = int(items.get(item_id, 0))
            if have >= spec["max"]:
                return {"ok": False, "msg": "这类道具最多带 %d 个，先用掉再买" % spec["max"]}
            err = self._spend_pts(stu, spec["price"])
            if err:
                return {"ok": False, "msg": err}
            items[item_id] = have + 1
            stu["items"] = items
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "item": item_id,
                    "count": have + 1}

    def buy_bg(self, uid, bg_id):
        """用积分买牧场背景，买下即自动换上。价格以服务端 BG_PRICE 为准。"""
        if bg_id not in BG_PRICE:
            return {"ok": False, "msg": "没有这个背景"}
        with self.lock:
            stu = self._stu(uid)
            owned = stu.get("bgs", [])
            if bg_id in owned:
                return {"ok": False, "msg": "这个背景已经有了"}
            err = self._spend_pts(stu, BG_PRICE[bg_id])
            if err:
                return {"ok": False, "msg": err}
            owned.append(bg_id)
            stu["bgs"] = owned
            ep = stu.get("equip") or {}
            ep["bg"] = bg_id
            stu["equip"] = ep
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "bg": bg_id}

    def buy_effect(self, uid, fx_id):
        """用积分买出战特效，买下即自动开启。价格以服务端 EFFECT_PRICE 为准。"""
        if fx_id not in EFFECT_PRICE:
            return {"ok": False, "msg": "没有这个特效"}
        with self.lock:
            stu = self._stu(uid)
            owned = stu.get("effects", [])
            if fx_id in owned:
                return {"ok": False, "msg": "这个特效已经有了"}
            err = self._spend_pts(stu, EFFECT_PRICE[fx_id])
            if err:
                return {"ok": False, "msg": err}
            owned.append(fx_id)
            stu["effects"] = owned
            ep = stu.get("equip") or {}
            ep["fx"] = fx_id
            stu["equip"] = ep
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "fx": fx_id}

    def buy_egg(self, uid, pet_ids=None):
        """用积分直接兑一颗宠物蛋（BOSS 满星掉蛋之外的兜底渠道）。

        图鉴集齐后不再出售 —— claim_egg 会把多余的蛋折成 50 积分，
        这时还花 300 换一颗等于白扔 250 分，不如直接拦住。
        """
        with self.lock:
            stu = self._stu(uid)
            if pet_ids:
                unowned = [p for p in pet_ids if p not in stu.get("pets", [])]
                if not unowned:
                    return {"ok": False, "msg": "宠物图鉴已经集齐，不用再换蛋啦"}
            err = self._spend_pts(stu, EGG_PRICE)
            if err:
                return {"ok": False, "msg": err}
            stu["eggs"] = stu.get("eggs", 0) + 1
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "eggs": stu["eggs"],
                    "price": EGG_PRICE}

    # ---------------- 学生设计作品兑换（最高档积分） ----------------
    def buy_studio(self, uid, work_id):
        """用积分兑换**界面主题**。

        2.0.56 起作品宠物不再支持直兑：即使客户端伪造 id 走这里，也会被拒。
        唯一合法入口是 buy_studio_egg(uid, egg_id) —— 先兑蛋，蛋里出宠物。
        """
        if not SD or work_id not in SD.BY_ID:
            return {"ok": False, "msg": "没有这个学生作品"}
        work = SD.BY_ID[work_id]
        if work["kind"] != "theme":
            return {"ok": False, "msg": "作品宠物不能直接兑换，请兑换对应的宠物蛋"}
        price = SD.STUDIO_THEME_PRICE
        with self.lock:
            stu = self._stu(uid)
            owned = stu.setdefault("studio_themes", [])
            if work_id in owned:
                return {"ok": False, "msg": "这个主题已经兑换过了"}
            err = self._spend_pts(stu, price)
            if err:
                return {"ok": False, "msg": err}
            owned.append(work_id)
            ep = stu.get("equip") or {}
            ep["studio_theme"] = work_id
            stu["equip"] = ep
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "id": work_id,
                    "kind": "theme", "price": price}

    def buy_studio_egg(self, uid, egg_id, free_quota=1):
        """兑换学生设计作品蛋 —— 作品宠物的唯一合法兑换入口。

        · 指定蛋 → 拆出对应作品宠物；「随机蛋」在未拥有的作品里随机抽一只；
        · 2.0.68：每个账号先扣免费孵化额度（默认学生 1 次），用完后才按
          STUDIO_EGG_PRICE 扣积分；用过几次写在档案 studio_free_used 里，
          教师改额度不会重置历史用量；
        · 已拥有的宠物不能重复兑换；抽中即写 studio_pets 并自动出战；
        · 这里**不增加普通宠物蛋 eggs 计数**：作品蛋是一次性兑换券，
          不走 claim_egg 的普通图鉴抽签池，避免两套图鉴互相污染。
        """
        if not SD or egg_id not in SD.EGG_BY_ID:
            return {"ok": False, "msg": "没有这枚作品蛋"}
        egg = SD.EGG_BY_ID[egg_id]
        with self.lock:
            stu = self._stu(uid)
            owned = set(stu.get("studio_pets") or [])
            if egg.get("pet"):
                pet = egg["pet"]
                if pet not in SD.BY_ID or SD.BY_ID[pet]["kind"] != "pet":
                    return {"ok": False, "msg": "这枚蛋对应的作品不存在"}
                if pet in owned:
                    return {"ok": False, "msg": "这只宠物已经拥有，不用再兑蛋啦"}
            else:
                pool = [p for p in SD.PET_IDS if p not in owned]
                if not pool:
                    return {"ok": False, "msg": "作品宠物已经集齐，不用再兑蛋啦"}
                pet = pool[int(time.time() * 1000) % len(pool)]
            try:
                quota = max(0, int(free_quota))
            except (TypeError, ValueError):
                quota = 1
            try:
                used = max(0, int(stu.get("studio_free_used") or 0))
            except (TypeError, ValueError):
                used = 0
            free = used < quota
            if free:
                stu["studio_free_used"] = used + 1
                charged = 0
            else:
                err = self._spend_pts(stu, SD.STUDIO_EGG_PRICE)
                if err:
                    return {"ok": False, "msg": err, "freeLeft": 0,
                            "freeUsed": used, "freeQuota": quota}
                charged = SD.STUDIO_EGG_PRICE
            stu.setdefault("studio_pets", []).append(pet)
            ep = stu.get("equip") or {}
            ep["studio_pet"] = pet
            ep["pet"] = ""           # 普通宠物 / 作品宠物出战位二选一
            stu["equip"] = ep
            stu["updated"] = int(time.time())
            self._save()
            work = SD.BY_ID[pet]
            return {"ok": True, "stu": self.summary(stu), "id": pet,
                    "egg": egg_id, "kind": "pet", "price": SD.STUDIO_EGG_PRICE,
                    "free": free, "charged": charged,
                    "freeUsed": used + 1 if free else used,
                    "freeQuota": quota,
                    "freeLeft": max(0, quota - (used + 1 if free else used)),
                    "petName": work["name"]}

    def use_item(self, uid, item_id):
        """消耗一个道具（进入关卡时用）。道具只影响本关，不写进档案。"""
        if item_id not in ITEMS:
            return {"ok": False, "msg": "没有这个道具"}
        with self.lock:
            stu = self._stu(uid)
            items = stu.get("items") or {}
            have = int(items.get(item_id, 0))
            if have <= 0:
                return {"ok": False, "msg": "这个道具用完了，去牧场商店买一个"}
            items[item_id] = have - 1
            stu["items"] = items
            # 加倍 / 容错要跨请求生效（进关时用、结算时才读），记在 pending 上，
            # record 结算完立刻清空，绝不跨关
            if item_id in ("double", "shield"):
                p = stu.get("pending") or {}
                p[item_id] = 1
                stu["pending"] = p
            self._save()
            return {"ok": True, "stu": self.summary(stu), "item": item_id,
                    "count": have - 1}

    # ---------------- 教师积分管理 ----------------
    PTS_LOG_MAX = 500

    def _pts_log(self):
        log = self.data.get("pts_log")
        if not isinstance(log, list):
            log = []
            self.data["pts_log"] = log
        return log

    def adjust_points(self, targets, delta, note, by):
        """教师对学生（或全班）发放 / 扣除积分。

        targets: [(uid, name), ...] —— 由 server.py 按 roster 解析；
        delta: 正数发放、负数扣除（余额扣到 0 为止，不出现负积分）；
        note: 教师填写的操作说明；by: 操作教师名。
        每一笔都记进 typing.json 的 pts_log（随快照整体导出导入，
        换机不丢）；只保留最近 500 条，防止无限膨胀。
        """
        try:
            delta = int(delta)
        except Exception:
            return {"ok": False, "msg": "积分数不合法"}
        if delta == 0:
            return {"ok": False, "msg": "积分数不能为 0"}
        if abs(delta) > 10000:
            return {"ok": False, "msg": "一次最多操作 10000 积分"}
        rows = []
        with self.lock:
            log = self._pts_log()
            for uid, name in targets:
                # 没练过的同学也必须发到：_stu 会把默认档案建出来，
                # 等他第一次打开牧场时就能看到老师发的积分
                stu = self._stu(str(uid))
                before = stu.get("pts", 0)
                after = max(0, before + delta)
                real = after - before
                stu["pts"] = after
                stu["updated"] = int(time.time())
                rows.append({"uid": str(uid), "name": name,
                             "before": before, "after": after, "delta": real})
                _note = str(note or "")
                if real != delta:
                    # 余额最低到 0：老师看到「班务-1分×10=打字-10」却记了 0，
                    # 以为同步错了。把「应扣/实际」直接写进备注，不用猜。
                    _act = ("+" + str(real)) if real > 0 else str(real)
                    _want = ("+" + str(delta)) if delta > 0 else str(delta)
                    _note += "（应记" + _want + "，余额限制实际" + _act + "）"
                log.append({"ts": int(time.time()), "by": str(by or "老师"),
                            "uid": str(uid), "name": name,
                            "delta": real, "note": _note})
            if len(log) > self.PTS_LOG_MAX:
                del log[:len(log) - self.PTS_LOG_MAX]
            if rows:
                try:
                    self._save()
                except Exception:
                    pass
        if not rows:
            return {"ok": False, "msg": "这个班里没有学生档案"}
        return {"ok": True, "count": len(rows), "rows": rows}

    def points_log(self, limit=100):
        """最近的积分操作记录（新的在前）。"""
        with self.lock:
            log = self.data.get("pts_log")
            if not isinstance(log, list):
                return []
            return list(reversed(log[-int(limit):]))

    def delete_points_log(self, ts, uid):
        """删除一条打字积分流水，并把对应积分冲回（最低到0）。
        积分页 / 打字页共用同一份权威数据，删完两边自然同步。 """
        try:
            ts = int(ts)
        except Exception:
            return {"ok": False, "msg": "流水标识不对"}
        uid = str(uid or "")
        with self.lock:
            log = self._pts_log()
            idx = -1
            for i, e in enumerate(log):
                try:
                    if int(e.get("ts", 0)) == ts and str(e.get("uid") or "") == uid:
                        idx = i
                        break
                except Exception:
                    continue
            if idx < 0:
                return {"ok": False, "msg": "这条流水已经不存在"}
            e = log.pop(idx)
            delta = int(e.get("delta", 0) or 0)
            stu = self.data["students"].get(uid)
            if stu and delta:
                stu["pts"] = max(0, int(stu.get("pts", 0) or 0) - delta)
                stu["updated"] = int(time.time())
            try:
                self._save()
            except Exception:
                pass
        return {"ok": True, "name": e.get("name") or "", "delta": delta}

    def students_points(self, uids):
        """按 roster 顺序取每个学生的积分余额（没有档案的按 0 算）。"""
        out = []
        with self.lock:
            for uid, name, student_no in uids:
                stu = self.data["students"].get(str(uid)) or {}
                out.append({"id": str(uid), "name": name, "student_no": student_no,
                           "pts": stu.get("pts", 0)})
        return out

    # ---------------- 练习草稿（教师关闭练习时进度不丢，2026-11-05） ----
    def save_draft(self, uid, body):
        """学生端练习中自动保存的「当前关卡草稿」。

        关键设计：教师把打字练习关掉时，record 接口会拒收，
        但 draft 必须照收 —— 关练习恰恰是学生最需要存档的时刻。
        text 存的是引擎此刻正在打的那份文本，恢复时直接还原，
        随机生成的关卡也能续上原来那份内容。
        """
        if body.get("clear"):
            # 学生主动「从头开始」：清掉这一关的草稿
            with self.lock:
                stu = self._stu(uid)
                stu["draft"] = None
                try:
                    self._save()
                except Exception:
                    pass
            return {"ok": True, "cleared": True}
        try:
            stage = int(body.get("stage", 0))
            chapter = int(body.get("chapter", 0))
            level = int(body.get("level", 0))
            idx = int(body.get("idx", 0))
        except Exception:
            return {"ok": False, "msg": "草稿数据不完整"}
        if not (1 <= stage <= 8) or chapter < 1 or not (1 <= level <= 10):
            return {"ok": False, "msg": "关卡不存在"}
        text = str(body.get("text") or "")
        if not text or idx < 0 or idx > len(text):
            return {"ok": False, "msg": "草稿内容为空"}
        key = "s%dc%dl%d" % (stage, chapter, level)
        draft = {"k": key, "stage": stage, "chapter": chapter, "level": level,
                 "idx": idx,
                 "err": int(body.get("err", 0) or 0),
                 "keys": int(body.get("keys", 0) or 0),
                 "dur": float(body.get("dur", 0) or 0),
                 "lines": body.get("lines") or None,
                 "text": text[:30000],
                 "ts": int(time.time())}
        with self.lock:
            stu = self._stu(uid)
            old = stu.get("draft")
            # 只往前走：草稿回退（重打本关开头）时不覆盖更靠前的进度
            if (isinstance(old, dict) and old.get("k") == key
                    and old.get("idx", 0) > idx):
                return {"ok": True, "kept": True}
            stu["draft"] = draft
            try:
                self._save()
            except Exception:
                pass
        return {"ok": True}

    def clear_draft(self, uid):
        with self.lock:
            stu = self._stu(uid)
            stu["draft"] = None
            self._save()
        return {"ok": True}

    # ---------------- 教师练习成绩（2026-11-03） ----------------
    def teacher_record(self, cid, name, body):
        """教师练习一关的成绩。星数/速度用与学生完全相同的公式复核。
        教师成绩按班级独立存放在 teacher 段，不写进任何学生档案；
        排行榜组装时以「老师」一行参与排序。"""
        try:
            stage = int(body.get("stage", 0))
            chapter = int(body.get("chapter", 0))
            level = int(body.get("level", 0))
            acc = float(body.get("acc", 0))
            chars = int(body.get("chars", 0))
            duration = float(body.get("duration", 0))
        except Exception:
            return {"ok": False, "msg": "成绩数据不完整"}
        if not str(cid or "").strip():
            return {"ok": False, "msg": "请先选择要计入的班级"}
        if not (1 <= stage <= 8) or chapter < 1 or not (1 <= level <= 10):
            return {"ok": False, "msg": "关卡不存在"}
        if duration < MIN_DURATION or chars < 1:
            return {"ok": False, "msg": "用时太短，成绩不计入"}
        acc = max(0.0, min(100.0, acc))
        wpm = min(WPM_CAP, round(chars / duration * 60, 1))
        stars = stars_for(stage, chapter, acc, wpm)
        key = "s%dc%dl%d" % (stage, chapter, level)
        with self.lock:
            t = (self.data.setdefault("teacher", {}).setdefault(str(cid), {}))
            if not isinstance(t, dict):
                t = {}
                self.data["teacher"][str(cid)] = t
            if not t.get("name"):
                t["name"] = (str(name).strip() if name else "") or "老师"
            best = t.setdefault("best", {})
            prev = best.get(key)
            new_best = (prev is None or stars > prev.get("stars", 0)
                        or (stars == prev.get("stars", 0) and wpm > prev.get("wpm", 0)))
            gained = 0
            if new_best:
                gained = max(0, stars - (prev.get("stars", 0) if prev else 0))
                best[key] = {"stars": stars, "wpm": wpm, "ts": int(time.time())}
                wk = week_key()
                if t.get("week", {}).get("k") != wk:
                    t["week"] = {"k": wk, "stars": 0}
                t["week"]["stars"] = t["week"].get("stars", 0) + gained
            # 总星口径与学生一致：各关最高星之和（week 已是累计口径）
            t["stars"] = sum(b.get("stars", 0) for b in best.values())
            t["best_wpm"] = max([b.get("wpm", 0) for b in best.values()], default=0)
            # 2026-11-05 晚：老师这一行也要有自己的宠物 / 装扮 / 背景 / 特效。
            # 以前排行榜里「老师」是写死 pet="" 的，老师在牧场里打扮了半天，
            # 榜上永远是个灰爪印。
            if isinstance(body.get("equip"), dict):
                t["equip"] = clean_equip(body.get("equip"))
            t["updated"] = int(time.time())
            try:
                self._save()
            except Exception:
                pass
            return {"ok": True, "stars": stars, "new_best": new_best,
                    "gained": gained, "wpm": wpm}

    def teacher_lb_value(self, cid, scope):
        """排行榜里「老师」这一行的取值（scope 与学生榜一致：week/total/speed）。
        没有成绩返回 None；week 星为 0 时不参与本周榜。"""
        with self.lock:
            t = (self.data.get("teacher") or {}).get(str(cid))
            if not isinstance(t, dict) or not t.get("best"):
                return None
            if scope == "week":
                wk = t.get("week") or {}
                v = wk.get("stars", 0) if wk.get("k") == week_key() else 0
            elif scope == "speed":
                ws = [float(b.get("wpm", 0) or 0) for b in t.get("best", {}).values()]
                v = round(sum(ws) / len(ws), 1) if ws else 0
            else:
                v = t.get("stars", 0)
            return {"value": v, "stars": t.get("stars", 0),
                    "name": t.get("name") or "老师",
                    **_equip_for_lb({"equip": t.get("equip")})}

    def teacher_equip(self, cid, equip):
        """老师换宠物 / 换装 / 换背景特效时同步一份到该班档案。

        老师是在自己的游戏页里打扮的（本机存），不上榜时根本不走
        teacher_record，所以单独给一个轻量入口，让排行榜那行实时跟上。
        """
        if not str(cid or "").strip():
            return {"ok": False, "msg": "缺少班级"}
        # 2.1.9：clean_equip 只留四字段会丢 studio_pet，导致教师出战创意宠物
        # 在班级排行榜那行退回到系统宠物；这里单独把 studio_pet 补回去。
        eq = clean_equip(equip)
        sp = str((equip or {}).get("studio_pet") or "")
        if sp:
            eq["studio_pet"] = sp
            eq["pet"] = ""
            eq["decos"] = []
        with self.lock:
            t = self.data.setdefault("teacher", {}).setdefault(str(cid), {})
            if not isinstance(t, dict):
                t = {}
                self.data["teacher"][str(cid)] = t
            t["equip"] = eq
            try:
                self._save()
            except Exception:
                pass
            return {"ok": True, "equip": eq}

    # ---------------- 排行榜 ----------------
    def leaderboard_data(self, scope):
        """scope: week / total / speed；返回 {uid: {value, extra}}"""
        wk = week_key()
        out = {}
        with self.lock:
            for uid, stu in self.data["students"].items():
                # 2026-11-05：教师给全班发积分也会把没练过的学生档案建出来
                #（否则他们第一次打开牧场看不到余额）。这些 0 星档案
                # 不能进排行榜 —— 保持「练过才上榜」的原有口径。
                if not stu.get("best") and not stu.get("stars"):
                    continue
                if scope == "week":
                    v = stu["week"]["stars"] if stu.get("week", {}).get("k") == wk else 0
                elif scope == "speed":
                    # 2.1.20：牛哥定版——排行榜速度取各关平均，不再取单关最高
                    ws = [float(b.get("wpm", 0) or 0)
                          for b in stu.get("best", {}).values()]
                    v = round(sum(ws) / len(ws), 1) if ws else 0
                else:
                    v = stu.get("stars", 0)
                out[uid] = {"value": v,
                            "stars": stu.get("stars", 0),
                            **_equip_for_lb(stu)}
        return out

    def reset_week(self):
        """教师手动清零本周星光榜（新的一周本来也会自动清）"""
        with self.lock:
            for stu in self.data["students"].values():
                stu["week"] = {"k": week_key(), "stars": 0}
            self._save()

    def overview(self):
        """参与统计：uid -> {levels, stars, best_wpm, updated}"""
        out = {}
        with self.lock:
            for uid, stu in self.data["students"].items():
                best = stu.get("best", {})
        wpm = max([b["wpm"] for b in best.values()], default=0)
        ws = [float(b.get("wpm", 0) or 0) for b in best.values()]
        avg = round(sum(ws) / len(ws), 1) if ws else 0
        out[uid] = {"levels": len(best), "stars": stu.get("stars", 0),
                    "best_wpm": wpm, "avg_wpm": avg,
                    "updated": stu.get("updated", 0)}
        return out

    def wipe_student(self, uid):
        with self.lock:
            self.data["students"].pop(uid, None)
            self._save()
