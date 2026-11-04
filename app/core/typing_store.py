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
import json
import os
import time
import threading

# 星级规则（与 app/typing/typing-data.js 的 STAR_RULE 保持一致）
# 7=数字小键盘、8=中英混排：2026-11-02 新增的两个篇章，缺了它们
# 这两章的进度会因为 fallback 到 24 而和服务端判星对不上。
SPEED_BASE = {1: 26, 2: 34, 3: 26, 4: 22, 5: 16, 6: 12, 7: 30, 8: 15}
PASS_ACC = 90
PERFECT_ACC = 97
WPM_CAP = 120          # 速度封顶：超过视为异常，直接压线
MIN_DURATION = 3       # 秒：太短的成绩不收

# 装扮解锁里程碑（与 typing-data.js 的 DECOS need 一致）
DECO_NEEDS = [10, 20, 40, 60, 80, 100, 130, 160]

# 装扮部位表（与 typing-data.js 的 DECOS.cat 一致）
# 同一部位只能穿一件；整套（suit）同时占上衣和下衣。
DECO_CAT = {
    "hat-red": "head", "crown": "head", "bow": "head", "headset": "head",
    "cap": "head",
    "sunglass": "face", "glasses": "face", "earmuffs": "face",
    "scarf": "top", "vest": "top", "bowtie": "top",
    "overall": "bottom", "skirt": "bottom",
    "bg-star": "suit", "robe": "suit",
}

# 装扮售价（与 typing-data.js 的 DECOS.price 一致）
# 价格只在这里生效 —— 客户端传来的 price 一律不认，否则改一下请求就能白拿。
# 2026-11-05 经济系统收紧：全店涨价（约 2.5 倍），金币收入同步调低。
DECO_PRICE = {
    "hat-red": 150, "crown": 300, "sunglass": 400, "glasses": 380,
    "scarf": 500, "vest": 450, "overall": 550, "skirt": 550,
    "bow": 650, "headset": 750, "robe": 800, "bg-star": 1000,
    "cap": 320, "earmuffs": 260, "bowtie": 280,
}

# 道具价格与携带上限（与 typing-data.js 的 ITEMS 一致）
ITEMS = {
    "hint":   {"price": 30, "max": 3},
    "extra":  {"price": 45, "max": 3},
    "shield": {"price": 80, "max": 2},
    "double": {"price": 60, "max": 2},
}

# 牧场背景售价（金币，与 typing-data.js 的 BACKGROUNDS 一致）
# '' 是默认草地，免费，不在表里 = 不可买也不需要买。
BG_PRICE = {
    "meadow": 200, "beach": 350, "night": 500,
    "candy": 800, "space": 1200,
}

# 出战特效售价（积分，与 typing-data.js 的 EFFECTS 一致）
# 特效是全店最贵的奢侈品：1 星积分在基础篇章为 0，攒 2000 分要打很久。
EFFECT_PRICE = {
    "sparkle": 200, "bubbles": 400, "hearts": 650,
    "snow": 900, "rainbow": 1400, "aurora": 2000,
}

DEFAULT_RECORD = {
    "best": {},            # levelKey -> {wpm, acc, stars, ts}
    "stars": 0,            # 累计星数（经验）
    "week": {"k": "", "stars": 0},
    "pets": [],            # 已拥有宠物 id
    "decos": [],           # 已解锁装扮 id
    "eggs": 0,             # 待孵化宠物蛋
    "equip": {"pet": "", "decos": [], "bg": "", "fx": ""},
    "coins": 0,
    "pts": 0,              # 积分（2026-11-05）：只用于购买特效
    "bgs": [],             # 已拥有牧场背景 id
    "effects": [],         # 已拥有特效 id
    "items": {},           # 道具库存 {hint:1, extra:2...}
    "pending": {},         # 已用但尚未结算的道具（double / shield）
    "daily": {"d": "", "n": {}},   # 当天重复练习计次（防刷金币/积分）
    "draft": None,         # 练习中断草稿（教师关闭练习时学生进度不丢）
    "updated": 0
}


def coins_for(stars, level, repeat):
    """每关金币（与 levels.js 的 coinsFor 完全一致，两边必须同步改）

    2026-11-05 二次收紧：全店涨价后金币必须更难赚 ——
      · 1 星不发金币（想赚钱打出 2 星以上）；
      · 首通 2 星 5 / 3 星 8，BOSS 首通再 +5；
      · 重复练每天每关只结算一次，2 金币（BOSS 3）。
    """
    if not stars:
        return 0
    if repeat:
        return 3 if level == 10 else 2
    if stars < 2:
        return 0
    return (8 if stars >= 3 else 5) + (5 if level == 10 else 0)


def pts_for(stars, stage, chapter, repeat):
    """每关积分（与 levels.js 的 ptsFor 完全一致，两边必须同步改）

    积分只用于购买特效（全店最贵）。难度越大越值钱：
    基础键位篇（stage 1）为 0 分，防止刷基础关；
    卷数每推进一卷 +1 分。重复练每天一次 1 分（需 2 星）。
    """
    if not stars:
        return 0
    if repeat:
        return 1 if stars >= 2 else 0
    table = {1: 0, 2: 1, 3: 1, 4: 1, 5: 2, 6: 2, 7: 1, 8: 2}
    return stars * table.get(int(stage), 1) + max(0, int(chapter) - 1)


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
    而不是清一色 🐾 爪印。"""
    eq = clean_equip(stu.get("equip"))
    if not eq["pet"]:
        pets = stu.get("pets") or []
        eq["pet"] = str(pets[0]) if pets else ""
    if not eq["pet"]:
        eq["decos"] = []
    return eq


def week_key(ts=None):
    """ISO 周标识，如 2026-W45；每周一自动切换 = 周榜自动清零"""
    t = time.localtime(ts or time.time())
    return time.strftime("%G-W%V", t)


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
    """服务端星级复核（与 levels.js 同款公式）"""
    if acc < PASS_ACC:
        return 0
    line = speed_line(stage, chapter)
    if acc >= PERFECT_ACC and wpm >= line * 1.3:
        return 3
    if wpm >= line:
        return 2
    return 1


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
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("students"), dict):
                self.data = d
        except Exception:
            pass
        if not isinstance(self.data.get("teacher"), dict):
            self.data["teacher"] = {}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False)
        os.replace(tmp, self.path)

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
            coins = 0
            pts = 0
            rewards = {"egg": False, "decos": []}
            if new_best:
                gained = stars - (prev["stars"] if prev else 0)
                if gained < 0:
                    gained = 0
                stu["best"][key] = {"wpm": wpm, "acc": round(acc, 1),
                                    "stars": stars, "ts": int(time.time())}
                coins = coins_for(stars, level, False)
                pts = pts_for(stars, stage, chapter, False)
                wk = week_key()
                if stu["week"].get("k") != wk:
                    stu["week"] = {"k": wk, "stars": 0}
                stu["week"]["stars"] += gained
                stu["stars"] += gained
                # Boss 首通掉宠物蛋
                if level == 10 and prev is None and stars > 0:
                    stu["eggs"] = stu.get("eggs", 0) + 1
                    rewards["egg"] = True
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
                # 重复练习：同一关每天只结算一次保底金币/积分，刷不出币来
                n = d["n"]
                if n.get(key, 0) < 1:
                    n[key] = n.get(key, 0) + 1
                    coins = coins_for(stars, level, True)
                    pts = pts_for(stars, stage, chapter, True)
            if coins:
                # 加倍卡：本关金币 ×2（由 use_item 记在 pending 上，用完即清）
                if pending.get("double"):
                    coins *= 2
                stu["coins"] = stu.get("coins", 0) + coins
            if pts:
                stu["pts"] = stu.get("pts", 0) + pts
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
                    "gained": gained, "coins_gained": coins, "pts_gained": pts,
                    "rewards": rewards,
                    "wpm": wpm, "stu": self.summary(stu)}

    # ---------------- 学生进度 ----------------
    def summary(self, stu):
        return {"stars": stu.get("stars", 0), "week_stars": stu["week"].get("stars", 0),
                "pets": stu.get("pets", []), "decos": stu.get("decos", []),
                "eggs": stu.get("eggs", 0), "equip": stu.get("equip", {}),
                "coins": stu.get("coins", 0), "pts": stu.get("pts", 0),
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
    def claim_egg(self, uid, pet_ids):
        """孵蛋：从还没有的宠物里随机（按种子确定性）挑一只"""
        if not pet_ids:
            return {"ok": False, "msg": "宠物图鉴为空"}
        with self.lock:
            stu = self._stu(uid)
            if stu.get("eggs", 0) <= 0:
                return {"ok": False, "msg": "还没有宠物蛋，去闯 BOSS 关赢一颗吧！"}
            unowned = [p for p in pet_ids if p not in stu["pets"]]
            stu["eggs"] -= 1
            if not unowned:
                stu["coins"] = stu.get("coins", 0) + 50
                self._save()
                return {"ok": True, "pet": None, "coins": 50,
                        "msg": "图鉴已集齐！蛋化作 50 金币落入囊中"}
            seed = int(time.time() * 1000) % len(unowned)
            pet = unowned[seed]
            stu["pets"].append(pet)
            if not stu["equip"].get("pet"):
                stu["equip"]["pet"] = pet
            self._save()
            return {"ok": True, "pet": pet, "stu": self.summary(stu)}

    def equip(self, uid, pet, decos, bg=None, fx=None):
        """装备出战阵容：pet + 装扮（部位互斥）+ 牧场背景 bg + 出战特效 fx。
        bg / fx 只允许穿「衣柜里已有的」；传 None 表示这一项保持不变，
        传 '' 表示取下来（背景回到默认草地 / 特效关闭）。"""
        with self.lock:
            stu = self._stu(uid)
            if pet and pet not in stu["pets"]:
                return {"ok": False, "msg": "还没有这只宠物"}
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
            ep = stu.get("equip") or {"pet": "", "decos": [], "bg": "", "fx": ""}
            ep = {"pet": pet or "", "decos": kept,
                  "bg": ep.get("bg", ""), "fx": ep.get("fx", "")}
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

    # ---------------- 金币商店 ----------------
    def buy_deco(self, uid, deco_id):
        """用金币买装扮。星数里程碑解锁的那八件照样能买，先买就先穿。
        价格以服务端 DECO_PRICE 为准，不接受客户端传值。"""
        if deco_id not in DECO_PRICE:
            return {"ok": False, "msg": "没有这件装扮"}
        price = DECO_PRICE[deco_id]
        with self.lock:
            stu = self._stu(uid)
            if deco_id in stu["decos"]:
                return {"ok": False, "msg": "这件装扮已经在衣柜里了"}
            coins = stu.get("coins", 0)
            if coins < price:
                return {"ok": False, "msg": "金币还差 %d 个" % (price - coins)}
            stu["coins"] = coins - price
            stu["decos"].append(deco_id)
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "deco": deco_id}

    def buy_item(self, uid, item_id):
        """用金币买道具。价格与上限由服务端定，不信任客户端传来的价格。"""
        spec = ITEMS.get(item_id)
        if not spec:
            return {"ok": False, "msg": "没有这个道具"}
        with self.lock:
            stu = self._stu(uid)
            items = stu.get("items") or {}
            have = int(items.get(item_id, 0))
            if have >= spec["max"]:
                return {"ok": False, "msg": "这类道具最多带 %d 个，先用掉再买" % spec["max"]}
            coins = stu.get("coins", 0)
            if coins < spec["price"]:
                return {"ok": False, "msg": "金币还差 %d 个" % (spec["price"] - coins)}
            stu["coins"] = coins - spec["price"]
            items[item_id] = have + 1
            stu["items"] = items
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "item": item_id,
                    "count": have + 1}

    def buy_bg(self, uid, bg_id):
        """用金币买牧场背景，买下即自动换上。价格以服务端 BG_PRICE 为准。"""
        if bg_id not in BG_PRICE:
            return {"ok": False, "msg": "没有这个背景"}
        with self.lock:
            stu = self._stu(uid)
            owned = stu.get("bgs", [])
            if bg_id in owned:
                return {"ok": False, "msg": "这个背景已经有了"}
            coins = stu.get("coins", 0)
            price = BG_PRICE[bg_id]
            if coins < price:
                return {"ok": False, "msg": "金币还差 %d 个" % (price - coins)}
            stu["coins"] = coins - price
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
            pts = stu.get("pts", 0)
            price = EFFECT_PRICE[fx_id]
            if pts < price:
                return {"ok": False, "msg": "积分还差 %d 分，打出好成绩来攒" % (price - pts)}
            stu["pts"] = pts - price
            owned.append(fx_id)
            stu["effects"] = owned
            ep = stu.get("equip") or {}
            ep["fx"] = fx_id
            stu["equip"] = ep
            stu["updated"] = int(time.time())
            self._save()
            return {"ok": True, "stu": self.summary(stu), "fx": fx_id}

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

    # ---------------- 教师金币管理（2026-11-05） ----------------
    COIN_LOG_MAX = 500

    def _coin_log(self):
        log = self.data.get("coin_log")
        if not isinstance(log, list):
            log = []
            self.data["coin_log"] = log
        return log

    def adjust_coins(self, targets, delta, note, by):
        """教师对学生（或全班）发放 / 扣除金币。

        targets: [(uid, name), ...] —— 由 server.py 按 roster 解析；
        delta: 正数发放、负数扣除（余额扣到 0 为止，不出现负数金币）；
        note: 教师填写的操作说明；by: 操作教师名。
        每一笔都记进 typing.json 的 coin_log（随快照整体导出导入，
        换机不丢）；coin_log 只保留最近 500 条，防止无限膨胀。
        """
        try:
            delta = int(delta)
        except Exception:
            return {"ok": False, "msg": "金币数不合法"}
        if delta == 0:
            return {"ok": False, "msg": "金币数不能为 0"}
        if abs(delta) > 10000:
            return {"ok": False, "msg": "一次最多操作 10000 金币"}
        rows = []
        with self.lock:
            log = self._coin_log()
            for uid, name in targets:
                # 没练过的同学也必须发到：_stu 会把默认档案建出来，
                # 等他第一次打开牧场时就能看到老师发的金币
                stu = self._stu(str(uid))
                before = stu.get("coins", 0)
                after = max(0, before + delta)
                real = after - before
                stu["coins"] = after
                stu["updated"] = int(time.time())
                rows.append({"uid": str(uid), "name": name,
                             "before": before, "after": after, "delta": real})
                log.append({"ts": int(time.time()), "by": str(by or "老师"),
                            "uid": str(uid), "name": name,
                            "delta": real, "note": str(note or "")})
            if len(log) > self.COIN_LOG_MAX:
                del log[:len(log) - self.COIN_LOG_MAX]
            if rows:
                try:
                    self._save()
                except Exception:
                    pass
        if not rows:
            return {"ok": False, "msg": "这个班里没有学生档案"}
        return {"ok": True, "count": len(rows), "rows": rows}

    def coin_log(self, limit=100):
        """最近的金币操作记录（新的在前）。"""
        with self.lock:
            log = self.data.get("coin_log")
            if not isinstance(log, list):
                return []
            return list(reversed(log[-int(limit):]))

    def students_coins(self, uids):
        """按 roster 顺序取每个学生的金币 / 积分余额（没有档案的按 0 算）。"""
        out = []
        with self.lock:
            for uid, name, student_no in uids:
                stu = self.data["students"].get(str(uid)) or {}
                out.append({"id": str(uid), "name": name, "student_no": student_no,
                           "coins": stu.get("coins", 0), "pts": stu.get("pts", 0)})
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
                v = t.get("best_wpm", 0)
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
        with self.lock:
            t = self.data.setdefault("teacher", {}).setdefault(str(cid), {})
            if not isinstance(t, dict):
                t = {}
                self.data["teacher"][str(cid)] = t
            t["equip"] = clean_equip(equip)
            try:
                self._save()
            except Exception:
                pass
            return {"ok": True, "equip": t["equip"]}

    # ---------------- 排行榜 ----------------
    def leaderboard_data(self, scope):
        """scope: week / total / speed；返回 {uid: {value, extra}}"""
        wk = week_key()
        out = {}
        with self.lock:
            for uid, stu in self.data["students"].items():
                # 2026-11-05：教师给全班发金币也会把没练过的学生档案建出来
                #（否则他们第一次打开牧场看不到余额）。这些 0 星档案
                # 不能进排行榜 —— 保持「练过才上榜」的原有口径。
                if not stu.get("best") and not stu.get("stars"):
                    continue
                if scope == "week":
                    v = stu["week"]["stars"] if stu.get("week", {}).get("k") == wk else 0
                elif scope == "speed":
                    v = 0
                    for b in stu.get("best", {}).values():
                        if b["wpm"] > v:
                            v = b["wpm"]
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
                out[uid] = {"levels": len(best), "stars": stu.get("stars", 0),
                            "best_wpm": wpm, "updated": stu.get("updated", 0)}
        return out

    def wipe_student(self, uid):
        with self.lock:
            self.data["students"].pop(uid, None)
            self._save()
