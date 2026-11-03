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
    "sunglass": "face", "glasses": "face",
    "scarf": "top", "vest": "top",
    "overall": "bottom", "skirt": "bottom",
    "bg-star": "suit", "robe": "suit",
}

# 装扮售价（与 typing-data.js 的 DECOS.price 一致）
# 价格只在这里生效 —— 客户端传来的 price 一律不认，否则改一下请求就能白拿。
DECO_PRICE = {
    "hat-red": 60, "crown": 120, "sunglass": 160, "glasses": 150,
    "scarf": 200, "vest": 180, "overall": 220, "skirt": 220,
    "bow": 260, "headset": 300, "robe": 320, "bg-star": 400,
}

# 道具价格与携带上限（与 typing-data.js 的 ITEMS 一致）
ITEMS = {
    "hint":   {"price": 15, "max": 3},
    "extra":  {"price": 20, "max": 3},
    "shield": {"price": 35, "max": 2},
    "double": {"price": 25, "max": 2},
}

DEFAULT_RECORD = {
    "best": {},            # levelKey -> {wpm, acc, stars, ts}
    "stars": 0,            # 累计星数（经验）
    "week": {"k": "", "stars": 0},
    "pets": [],            # 已拥有宠物 id
    "decos": [],           # 已解锁装扮 id
    "eggs": 0,             # 待孵化宠物蛋
    "equip": {"pet": "", "decos": []},
    "coins": 0,
    "items": {},           # 道具库存 {hint:1, extra:2...}
    "pending": {},         # 已用但尚未结算的道具（double / shield）
    "daily": {"d": "", "n": {}},   # 当天重复练习计次（防刷金币）
    "updated": 0
}


def coins_for(stars, level, repeat):
    """每关金币（与 levels.js 的 coinsFor 完全一致，两边必须同步改）

    原来的「星数×10」让 1 星和 3 星差三倍，学生会为了金币反复刷同一个
    三星关。现在首通 8+星×4（12/16/20），差距收窄；重复练固定 3，
    且同一关每天只结算一次重复奖励 —— 承认「你又练了一遍」，但不鼓励刷。
    """
    if not stars:
        return 0
    if repeat:
        return 3 + (5 if level == 10 else 0)
    return 8 + stars * 4 + (15 if level == 10 else 0)


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
            rewards = {"egg": False, "decos": []}
            if new_best:
                gained = stars - (prev["stars"] if prev else 0)
                if gained < 0:
                    gained = 0
                stu["best"][key] = {"wpm": wpm, "acc": round(acc, 1),
                                    "stars": stars, "ts": int(time.time())}
                coins = coins_for(stars, level, False)
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
                # 重复练习：同一关每天只结算一次保底金币，刷不出币来
                n = d["n"]
                if n.get(key, 0) < 1:
                    n[key] = n.get(key, 0) + 1
                    coins = coins_for(stars, level, True)
            if coins:
                # 加倍卡：本关金币 ×2（由 use_item 记在 pending 上，用完即清）
                if pending.get("double"):
                    coins *= 2
                stu["coins"] = stu.get("coins", 0) + coins
            stu["pending"] = {}
            stu["updated"] = int(time.time())
            try:
                self._save()
            except Exception:
                pass
            return {"ok": True, "stars": stars, "new_best": new_best,
                    "gained": gained, "coins_gained": coins, "rewards": rewards,
                    "wpm": wpm, "stu": self.summary(stu)}

    # ---------------- 学生进度 ----------------
    def summary(self, stu):
        return {"stars": stu.get("stars", 0), "week_stars": stu["week"].get("stars", 0),
                "pets": stu.get("pets", []), "decos": stu.get("decos", []),
                "eggs": stu.get("eggs", 0), "equip": stu.get("equip", {}),
                "coins": stu.get("coins", 0),
                "items": stu.get("items", {}),
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

    def equip(self, uid, pet, decos):
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
            stu["equip"] = {"pet": pet or "", "decos": kept}
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
                    "name": t.get("name") or "老师"}

    # ---------------- 排行榜 ----------------
    def leaderboard_data(self, scope):
        """scope: week / total / speed；返回 {uid: {value, extra}}"""
        wk = week_key()
        out = {}
        with self.lock:
            for uid, stu in self.data["students"].items():
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
                            "pet": (stu.get("equip") or {}).get("pet", ""),
                            "decos": (stu.get("equip") or {}).get("decos", [])}
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
