/* ============================================================
   打字冒险岛 · 无限关卡引擎（levels.js）
   伪随机种子：(篇章, 卷号, 关号, 文章, 尝试号 nonce) 共同决定。
   2026-11-04 起 genLevel 支持 nonce：调用方每次进关传一个随机
   nonce，重玩 / 重置 / 刷新都会换一套内容；不传（=0）保持旧
   确定性（第 6 篇章读文章仍按句序推进，不参与随机）。
   依赖：typing-data.js（先加载）
   ============================================================ */
window.TL = (function () {
  'use strict';

  /* ---------------- 伪随机种子（变参：全参与混合） ---------------- */
  function hashSeed() {
    var h = 2166136261 >>> 0;
    for (var ai = 0; ai < arguments.length; ai++) {
      var s = String(arguments[ai] == null ? '' : arguments[ai]);
      for (var i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i);
        h = Math.imul(h, 16777619);
      }
    }
    return h >>> 0;
  }
  function mulberry32(seed) {
    var t = seed >>> 0;
    return function () {
      t += 0x6D2B79F5;
      var r = Math.imul(t ^ (t >>> 15), 1 | t);
      r ^= r + Math.imul(r ^ (r >>> 7), 61 | r);
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }
  function pick(rng, arr) { return arr[Math.floor(rng() * arr.length)]; }
  /* 不放回抽样：一关里出现两个「456」或两个「English」，
     学生会以为是自己点错了关卡，也白白少练一个词条。
     先按值去重再抽 —— 内容池里本来就有重复条目（456 在不同关复用），
     只按索引去重是挡不住的。 */
  function sample(rng, arr, n) {
    var seen = {}, uniq = [];
    arr.forEach(function (v) { if (!seen[v]) { seen[v] = 1; uniq.push(v); } });
    if (n >= uniq.length) return uniq.slice();
    var pool = uniq, out = [];
    for (var i = 0; i < n; i++) out.push(pool.splice(Math.floor(rng() * pool.length), 1)[0]);
    return out;
  }

  function levelKey(stage, chapter, level) { return 's' + stage + 'c' + chapter + 'l' + level; }
  function isBoss(level) { return level === 10; }

  /** 本章速度线（字/分）
      —— 2026-11-02 放缓节奏：原来每卷 +5%，第 20 卷就是基础值的 1.95 倍，
         学生练到第 8 卷左右普遍掉星，练不下去。
         现在改成：
           · 每卷只 +2%，且最多涨到基础值的 1.6 倍（第 31 卷封顶）；
           · 第 1 卷打 95 折，给刚上手的学生一个适应期。
         一学期 20 周、每周一卷的话，一个学期里速度线只从 0.95 倍走到
         1.33 倍 —— 学生每两周只需快 2~3 字/分，是能跟上的斜率。 */
  function speedLine(stage, chapter) {
    var base = (window.TD.STAR_RULE.speedBase[stage]) || 24;
    var grow = Math.min(Math.max(0, chapter - 1), 30) * 0.02;
    var warm = chapter === 1 ? 0.95 : 1;
    return Math.round(base * (1 + grow) * warm * 10) / 10;
  }

  /** 卷内难度系数：前 3 关热身、中间正常、8~9 关加压、BOSS 最重。
      不为难而难 —— 只是让同一卷里有一个缓坡，而不是十关一样重。 */
  function levelFactor(level) {
    if (level <= 3) return 0.85;
    if (level <= 7) return 1;
    if (level <= 9) return 1.1;
    return 1.3;
  }

  /** 给界面看的难度标签（地图上那一行小字） */
  function difficulty(stage, chapter) {
    var line = speedLine(stage, chapter);
    var base = (window.TD.STAR_RULE.speedBase[stage]) || 24;
    var r = line / base;
    if (chapter <= 2) return { n: 1, name: '入门' };
    if (r < 1.2) return { n: 2, name: '进阶' };
    return { n: 3, name: '挑战' };
  }

  /* ---------------- 内容池 ---------------- */
  function enPool(chapter) {
    var W = window.TD.EN_WORDS;
    if (chapter <= 2) return W.easy;
    if (chapter <= 4) return W.easy.concat(W.mid);
    if (chapter <= 6) return W.mid;
    if (chapter <= 8) return W.mid.concat(W.hard);
    return W.hard;
  }
  function cnWordPool(grades) {
    var W = window.TD.CN_WORDS, out = [], seen = {};
    var gs = (grades && grades.length) ? grades : Object.keys(W);
    gs.forEach(function (g) {
      (W[g] || []).forEach(function (w) { if (!seen[w]) { seen[w] = 1; out.push(w); } });
    });
    return out.length ? out : W['3'];
  }
  function cnSentPool(grades) {
    var S = window.TD.CN_SENT, G = window.TD.GRADE_GROUP, out = [], seen = {};
    var gs = (grades && grades.length) ? grades : [1, 3, 5, 7];
    gs.forEach(function (g) {
      var grp = G[g] || 'mid';
      (S[grp] || []).forEach(function (s) { if (!seen[s]) { seen[s] = 1; out.push(s); } });
    });
    return out.length ? out : S.mid;
  }
  var SYMBOLS = ['1','2','3','4','5','6','7','8','9','0','-','=','[',']',';',"'",',','.','/','!','@','#','$','%','^','&','*','(',')','_','+','{','}',':','"','<','>','?'];
  /* 第 1 篇章每一关在练哪组键（与 KEY_LESSONS 顺序一一对应） */
  var KEY_TOPIC = ['基准键 F J', '基准键 D K', '上排 Q-P', '上排巩固',
                   '下排 Z-/', '下排巩固', '上下排连打', '全字母综合',
                   '数字行 + Shift 大写'];

  /* 地图卡片上那行小字：这一关练什么。
     单独抽成纯函数，是为了「地图」和「关卡」两处永远说同一句话 ——
     以前地图写「输入挑战」、进去才发现是打大写，学生对不上。 */
  function topicOf(stage, level) {
    var lv = Math.min(level, 10), boss = level === 10;
    var D = window.TD;
    if (stage === 1) return boss ? '全键盘挑战' : (KEY_TOPIC[lv - 1] || '');
    if (stage === 4) return boss ? '综合挑战' : (D.S4_TOPIC[lv - 1] || '');
    if (stage === 7) return boss ? '混合长串' : (D.S7_TOPIC[lv - 1] || '');
    if (stage === 8) return boss ? '综合挑战' : (D.S8_TOPIC[lv - 1] || '');
    return '';
  }

  /* ---------------- 关卡生成 ----------------
     返回：
       type='keys' → items:[{k, hint}]，逐键作答
       type='text' → text:'...'，逐字作答
     speedLine：两星速度线（字/分）；timeLimit：秒 */
  function genLevel(stage, chapter, level, grades, articleId, nonce) {
    var TD = window.TD;
    /* 文章维度 + 尝试号参与种子：nonce 每次进关随机 → 重玩/重置/刷新
       内容都不同；不传 nonce（读文章模式）则同一关内容恒定 */
    var rng = mulberry32(hashSeed('wb-typing', stage, chapter, level, articleId || '', nonce || 0));
    var boss = isBoss(level);
    /* 篇章元信息按 id 取，不能用数组下标 —— 新增篇章后 id 与顺序不再一致
       （小键盘 id=7 排在中文前面），用下标会取错篇章、甚至取到 undefined */
    var st = TD.stageById(stage) || TD.STAGES[0];
    var f = levelFactor(level);
    var L = {
      key: levelKey(stage, chapter, level),
      stage: stage, chapter: chapter, level: level,
      boss: boss, type: st.type, mode: st.lang,
      title: (boss ? 'BOSS · ' : '第' + chapter + '卷 第' + level + '关'),
      speedLine: speedLine(stage, chapter),
      /* topic：这一关到底在练什么（地图卡片上那行小字） */
      topic: '',
      /* kbzone='num' 时键盘图上高亮右手小键盘，而不是主键区数字行 */
      kbzone: '',
      /* ime：界面提示该用哪种输入法 · en=英文 / cn=中文 / mix=来回切 */
      ime: st.lang === 'cn' ? 'cn' : 'en',
      items: null, text: ''
    };
    if (articleId) L.article = articleId;

    if (stage === 1) {
      /* 键位：按关号选课程组，boss 混合全部 */
      var li = boss ? TD.KEY_LESSONS.length - 1 : (level - 1) % (TD.KEY_LESSONS.length - 1);
      var lesson = TD.KEY_LESSONS[li];
      L.topic = boss ? '全键盘挑战' : (KEY_TOPIC[li] || '键位训练');
      /* 内容量随卷增长但要封顶：原来第 20 卷要敲 30 个键，手腕先累垮，
         现在最多 20 个，且卷内按 f 缓坡 */
      var n = Math.round(Math.min(8 + chapter * 0.7, 20) * (boss ? 1.3 : f));
      var items = [];
      for (var i = 0; i < n; i++) {
        var kv = pick(rng, lesson);          /* 课程条目是 [键, 指法提示] 数组 */
        items.push({ k: kv[0], hint: kv[1] });
      }
      L.items = items;
      L.timeLimit = Math.max(60, Math.round(n * 5));
      return L;
    }

    var parts = [];
    if (stage === 2) {
      var letters = 'abcdefghijklmnopqrstuvwxyz'.split('');
      /* 字母流：16+4×卷 → 最多 60 个字母，等于一屏四行，新手直接劝退。
         现在 12+2×卷，封顶 40。 */
      var n2 = Math.round(Math.min(12 + chapter * 2, 40) * f);
      for (var j = 0; j < n2; j++) parts.push(pick(rng, letters));
    } else if (stage === 3) {
      var pool = enPool(chapter);
      var n3 = Math.round(Math.min(5 + chapter / 1.6, 14) * f);
      for (var w = 0; w < n3; w++) parts.push(pick(rng, pool));
      L.text = parts.join(' ');
    } else if (stage === 4) {
      /* 大小写与标点：按关号取固定知识点，十关走完一遍完整技能链。
         BOSS 关把前面几池混起来，考的是"看到就知道该不该按 Shift"。 */
      var lvIdx = Math.min(level, 10);
      var s4pool = boss
        ? TD.S4_MAP[1].concat(TD.S4_MAP[4], TD.S4_MAP[6], TD.S4_MAP[7], TD.S4_MAP[8])
        : TD.S4_MAP[lvIdx];
      L.topic = boss ? '综合挑战' : TD.S4_TOPIC[lvIdx - 1];
      var n4 = Math.min(3 + Math.floor((chapter - 1) / 2) + (boss ? 2 : 0), 8);
      L.text = sample(rng, s4pool, n4).join(' ');
    } else if (stage === 7) {
      /* 数字小键盘：一组 3~5 个数字串，组间空格分开（空格用拇指，也算练） */
      var lv7 = Math.min(level, 10);
      var s7pool = boss
        ? TD.S7_MAP[3].concat(TD.S7_MAP[5], TD.S7_MAP[7], TD.S7_MAP[9])
        : TD.S7_MAP[lv7];
      L.topic = boss ? '混合长串' : TD.S7_TOPIC[lv7 - 1];
      L.kbzone = 'num';
      var n7 = Math.min(3 + Math.floor((chapter - 1) / 3) + (boss ? 2 : 0), 7);
      L.text = sample(rng, s7pool, n7).join(' ');
    } else if (stage === 5) {
      var cn = cnWordPool(grades);
      var n5 = Math.round(Math.min(4 + Math.ceil(chapter / 2), 12) * f);
      for (var v = 0; v < n5; v++) parts.push(pick(rng, cn));
      L.text = parts.join('，');
      if (boss) L.text += '。';
    } else if (stage === 8) {
      /* 中英混排：整句照打，中文用输入法、英文数字切回半角。
         一关一句，卷号越大越可能两句连打。 */
      var lv8 = Math.min(level, 10);
      var s8pool = boss
        ? TD.S8_MAP[1].concat(TD.S8_MAP[5], TD.S8_MAP[7], TD.S8_MAP[9])
        : TD.S8_MAP[lv8];
      L.topic = boss ? '综合挑战' : TD.S8_TOPIC[lv8 - 1];
      L.ime = 'mix';
      var s8n = Math.min(1 + Math.floor((chapter - 1) / 4) + (boss ? 1 : 0), 2);
      L.text = sample(rng, s8pool, s8n).join('');
    } else {
      /* 第 6 篇章：文章阅读 —— 2026-11-04 牛哥定版：取消「读一句」，
         整篇文章连续练习（一关就是全文，越往后的卷速度线越高）。
         L.long 让练习页收起键盘与指法图：长文要的是屏幕空间和手指记忆，
         一直盯着键盘反而打不快。 */
      var art = (window.TA && window.TA.get) ? window.TA.get(articleId) : null;
      if (art && art.lines.length) {
        var lines = art.lines;
        L.text = lines.join('');
        L.article = art.id;
        L.articleTitle = art.title;
        L.articleCount = lines.length;
        L.readAt = 0;
        L.long = true;
        L.topic = '全文 · ' + lines.length + ' 句 / ' + L.text.length + ' 字';
      } else {
        var sent = cnSentPool(grades);
        L.text = pick(rng, sent);
        if (boss) {
          var s3 = pick(rng, sent);
          if (s3 !== L.text) L.text += s3;
        }
      }
    }
    /* 只有字母流需要把字符数组拼成串；单词关（3）、大小写标点关（4）、
       小键盘关（7）、中文关（5/6/8）的 L.text 已在各自分支里生成 */
    if (stage === 2) L.text = parts.join('');
    /* 限时给得宽一点（0.35→0.30）：限时是防卡死的保险，不是催促，
       真正的速度要求只写在「星级速度线」里，让学生自己决定要不要冲。 */
    L.timeLimit = Math.max(75, Math.round(L.text.length / (L.speedLine * 0.30)));
    return L;
  }

  /* ---------------- 星级评定（服务端同款公式） ---------------- */
  function starsFor(stage, chapter, acc, speed) {
    var R = window.TD.STAR_RULE;
    var line = speedLine(stage, chapter);
    if (acc < R.passAcc) return 0;
    if (acc >= R.perfectAcc && speed >= line * 1.3) return 3;
    if (speed >= line) return 2;
    return 1;
  }

  /* 每关金币（2026-11-02 重定）
     —— 为什么要改：原来「星数×10」意味着拿 1 星只有 10 金币，3 星 30，
        差距太大，学生会为了金币反复刷同一个三星关，练习变成刷币。
        现在：
          · 首通：8 + 星数×4（1 星 12 / 2 星 16 / 3 星 20），差距收窄；
          · BOSS 首通额外 +15（推进度的奖励，不是刷的奖励）；
          · 重复打：固定 3 金币（打得好也是 3）—— 承认「你又练了一遍」，
            但绝不鼓励原地刷。
     这份公式在 app/core/typing_store.py 里有一份同样的实现，两边必须同步改。 */
  function coinsFor(stars, level, repeat) {
    if (!stars) return 0;
    if (repeat) return 3 + (isBoss(level) ? 5 : 0);
    return 8 + stars * 4 + (isBoss(level) ? 15 : 0);
  }

  return {
    genLevel: genLevel, starsFor: starsFor, speedLine: speedLine,
    difficulty: difficulty, levelFactor: levelFactor, topicOf: topicOf,
    levelKey: levelKey, isBoss: isBoss, coinsFor: coinsFor
  };
})();
