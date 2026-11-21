/* 学生创意馆 · 独立渲染 / 交互模块（studio-ui.js）
   依赖 student.html 的全局：S / api / esc / uiToast / uiConfirm / uiAlert /
   render / refreshPts / applyTheme / demoSave。
   数据来自 /api/v2/studio/works（教师改的设计者信息即时生效），拥有态来自 S.progress。

   2.0.56：
   · 作品宠物**不能直接兑换**——宠物卡只引导去作品蛋区；
   · 作品蛋是唯一兑换入口（服务端 buy_studio_egg 二次校验）；
   · 每只宠物有专属动作，动画作用在宠物图本身；睁眼宠物叠加眨眼覆盖层。 */
(function () {
  'use strict';

  var MOTIONS = ['m-float', 'm-tilt', 'm-swim', 'm-jelly', 'm-flap'];

  // 演示模式兜底作品清单（未登录时展示用，兑换会走本机演示存档）
  var DEMO_WORKS = [
    { id: 'sd_meihua', name: '小狐狸·梅花', kind: 'pet', img: '/typing/studio/pets/meihua.png',
      series: '花色六宠', mood: '梅花落在键盘上的小狐狸', fx: 'glow',
      motion: 'm-tail', dur: 3.0, delay: 0, buyable: false, egg: 'sd_egg_meihua',
      price: 2000, tag: '学生设计', tagColor: '#F5B301',
      designer: { name: '', cls: '', intro: '' } },
    { id: 'sd_hongxin', name: '小猫·红心', kind: 'pet', img: '/typing/studio/pets/hongxin.png',
      series: '花色六宠', mood: '大尾巴甩出一颗心', fx: 'sparkle',
      motion: 'm-tail2', dur: 2.6, delay: .28, buyable: false, egg: 'sd_egg_hongxin',
      blink: '/typing/studio/blink/hongxin.png', blinkDur: 3.6,
      price: 2000, tag: '学生设计', tagColor: '#F5B301',
      designer: { name: '', cls: '', intro: '' } }
  ];
  var DEMO_EGGS = [
    { id: 'sd_egg_meihua', name: '梅花蛋', pet: 'sd_meihua', petName: '小狐狸·梅花',
      img: '/typing/studio/eggs/meihua.png', price: 2000, random: false,
      motion: 'm-egg', dur: 3.2 },
    { id: 'sd_egg_hongxin', name: '红心蛋', pet: 'sd_hongxin', petName: '小猫·红心',
      img: '/typing/studio/eggs/hongxin.png', price: 2000, random: false,
      motion: 'm-egg', dur: 3.2 },
    { id: 'sd_egg_random', name: '随机蛋', pet: '', petName: '随机作品宠物（不含系统宠物）',
      img: '/typing/studio/eggs/suiji-question.png', price: 2000, random: true,
      motion: 'm-egg-random', dur: 2.4 }
  ];

  function E(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s); }

  /* 收回作品宠物后回到默认系统宠物（已拥有的第一只）；
     一只系统宠物都没有时返回 ''，顶栏才会显示爪印占位。 */
  function defaultPetId() {
    var ps = (window.S && S.progress && S.progress.pets) || [];
    return ps.length ? ps[0] : '';
  }
  /* 演示 / 教师展示分支不走服务端，但顶栏读的是 S.ranch，必须一起同步 */
  function syncLocalEquip(id, on) {
    if (!window.S) return;
    if (S.progress) {
      S.progress.equip = S.progress.equip || {};
      S.progress.equip.studio_pet = on ? '' : id;
      S.progress.equip.pet = on ? defaultPetId() : '';
    }
    if (S.ranch) {
      S.ranch.studio_pet = on ? '' : id;
      S.ranch.pet = on ? defaultPetId() : '';
    }
  }

  function works() {
    if (window.S && S.studio && S.studio.works && S.studio.works.length) return S.studio;
    return { works: DEMO_WORKS, eggs: DEMO_EGGS, petPrice: 2000, themePrice: 2000,
             eggPrice: 2000, free: { student: 1, teacher: 2 },
             tag: '学生设计', tagColor: '#F5B301', unlocked: false };
  }

  // 教师课堂展示：服务端按 session 下发 unlocked=true，只做本地预览，不写真实档案
  function isUnlocked() { return !!works().unlocked; }
  function allIds(kind) {
    return works().works.filter(function (w) { return w.kind === kind; })
      .map(function (w) { return w.id; });
  }
  function ownedPets() {
    if (isUnlocked()) return allIds('pet');
    return (window.S && S.progress && S.progress.studio_pets) || [];
  }
  function ownedThemes() {
    if (isUnlocked()) return allIds('theme');
    return (window.S && S.progress && S.progress.studio_themes) || [];
  }
  function equip() { return (window.S && S.progress && S.progress.equip) || {}; }
  function isDemo() { return !!(window.S && S.demo); }
  function pts() { return (window.S && S.progress && S.progress.pts) || 0; }
  /* 2.0.68：免费孵化额度按账号区分（老师演示 2 次、学生 1 次），
     已用次数在工作站里是服务端权威字段 studio_free_used；演示模式用本机进度。 */
  function freeQuota() {
    var f = works().free || {};
    var n = Number(window.S && S.role === 'teacher' ? f.teacher : f.student);
    return n > 0 ? Math.floor(n) : 0;
  }
  function freeUsed() {
    var n = Number(window.S && S.progress && S.progress.studio_free_used);
    return n > 0 ? Math.floor(n) : 0;
  }
  function freeLeft() { return Math.max(0, freeQuota() - freeUsed()); }

  function motionOf(w, i) { return w.motion || MOTIONS[i % MOTIONS.length]; }
  function delayOf(w, i) {
    if (typeof w.delay === 'number') return w.delay;
    return (i % 5) * 0.28;
  }

  /* 2.0.75：作品 → 对应蛋的主题色（主色 + 浅色）。未孵化卡用它做渐变背景，
     问号才是主角；不直接贴蛋图（牛哥：背景结合蛋主题设计，别把蛋搬过来）。 */
  function eggThemeOf(w) {
    var eggs = works().eggs || [];
    for (var i = 0; i < eggs.length; i++) {
      if (eggs[i].pet && eggs[i].pet === w.id) {
        return { color: eggs[i].color || '#E8AE3C', soft: eggs[i].soft || '#FFF2CE' };
      }
    }
    return { color: '#E8AE3C', soft: '#FFF2CE' };
  }
  function designerHtml(w) {
    var d = w.designer || {};
    /* 2.0.76（隐私脱敏）：公开接口只下发自愿填写的笔名（映射在 d.name），
       真实姓名 / 班级 / 学校 / 介绍一律不再下发。笔名为空则整行不显示，
       公网演示页既看不到真实身份，也不留「待教师填写」的噪音。 */
    var pen = String(d.name || '').trim();
    if (!pen) return '';
    return '<div class="studio-designer">创作者：<b>' + E(pen) + '</b></div>';
  }

  function partHtml(w) {
    var ps = w.parts || [];
    return ps.map(function (p, i) {
      return '<span class="sd-part" style="--pcx:' + p.x + '%;--pcy:' + p.y +
        '%;--prx:' + p.rx + '%;--pry:' + p.ry + '%;--prot:' + p.rot +
        'deg;animation-duration:' + ((w.dur || 3.4) * (i ? 1.15 : 1)).toFixed(2) +
        's;animation-delay:' + (i * 0.22).toFixed(2) + 's">' +
        '<img src="' + E(w.img) + '" alt=""></span>';
    }).join('');
  }

  /* 一只作品宠物的「图层组」：底图 + 局部动作层 + 睁眼层 + 眨眼层。
     创意馆卡片、牧场大图、顶栏小像都用这一份，动作配置永远一致。 */
  function petMedia(w) {
    var h = '<img class="sd-base" src="' + E(w.img) + '" alt="' + E(w.name) + '">';
    h += partHtml(w);
    if (w.eyes) {
      // 有闭眼帧时，睁眼层只管「一直亮着」，眨眼交给闭眼帧从上往下压。
      // 两套动画时长不同（*1.35 与 *1.4），各自跑会慢慢错位，错位那一刻
      // 睁眼层先消失、闭眼帧还没盖上，就会闪出原图的 ^ ^。
      var eyesStyle = w.blink ? 'animation:none'
        : 'animation-duration:' + (w.eyesDur || (w.dur || 3.4) * 1.35) + 's';
      h += '<img class="sd-eyes" src="' + E(w.eyes) + '" alt="" aria-hidden="true" ' +
           'style="' + eyesStyle + '">';
    }
    if (w.blink) {
      h += '<img class="sd-blink" src="' + E(w.blink) + '" alt="" aria-hidden="true" ' +
           'style="animation-duration:' + (w.blinkDur || (w.dur || 3.4) * 1.4) + 's">';
    }
    return h;
  }

  function cardHtml(w, i) {
    var isTheme = w.kind === 'theme';
    var owned = isTheme ? (ownedThemes().indexOf(w.id) >= 0)
                        : (ownedPets().indexOf(w.id) >= 0);
    var eq = isTheme ? (equip().studio_theme === w.id)
                     : (equip().studio_pet === w.id);
    var motion = motionOf(w, i);
    var delay = delayOf(w, i);
    var dur = w.dur || 3.4;
    var badge = w.tag || '学生设计';
    var color = w.tagColor || '#F5B301';
    var h = '<div class="studio-card' + (owned ? ' owned' : '') + (eq ? ' equipped' : '') + '" data-sid="' + E(w.id) + '">';
    h += '<span class="studio-corner studio-badge" style="color:#7a5600;border-color:' + color + '">✦ ' + E(badge) + '</span>';
    h += '<div class="studio-pic' + (owned ? '' : ' studio-locked') + '">';
    if (owned) {
      /* 2.0.74：和牧场一致——没孵化/没解锁的宠物只露问号剪影，
         但名字、花色、描述、设计者照常显示（牛哥：先让大家看到作者）。 */
      h += '<span class="studio-fx fx-' + (w.fx || 'glow') + '"></span>';
      h += '<span class="sd-stack ' + E(motion) + '" style="animation-duration:' + dur +
           's;animation-delay:' + delay + 's">' + petMedia(w) + '</span>';
      if (w.acts && w.acts.length) {
        h += '<div class="sd-acts">' + w.acts.map(function (a) {
          return '<span class="sd-act">' + E(a.label) + '</span>';
        }).join('') + '</div>';
      }
    } else {
      /* 2.0.75：未孵化 = 大问号当主角 + 按对应蛋主题色设计的渐变背景
         （梅花粉 / 方块金 / 黑桃紫 …），不是把蛋图直接贴过来。
         名字 / 花色 / 描述 / 设计者照常显示（先让大家看到作者是谁）。 */
      var th = eggThemeOf(w);
      h += '<span class="studio-lock-bg" style="--egg-c:' + th.color +
           ';--egg-soft:' + th.soft + '">';
      h += '<span class="studio-lock-q">?</span></span>';
    }
    h += '</div>';
    h += '<div class="studio-name">' + E(w.name) + '</div>';
    if (w.series) h += '<div class="studio-series">' + E(w.series) + '</div>';
    if (w.mood) h += '<div class="studio-mood">' + E(w.mood) + '</div>';
    h += designerHtml(w);
    if (eq) {
      h += '<span class="studio-own-flag eq">' + (isTheme ? '使用中' : '出战中') + '</span>';
    } else if (owned) {
      h += '<span class="studio-own-flag have">已拥有</span>';
    }
    /* 2.0.72：按钮统一装进一排固定高度的容器，避免「1 个按钮 vs 2 个按钮」
       把卡片撑得忽高忽低、菜单大小一直变。文案也压短到 4 字以内保证并排放得下。 */
    h += '<div class="studio-card-acts">';
    if (!owned) {
      if (w.buyable === false) {
        // 作品宠物：不给兑换按钮，只能去作品蛋区（去掉 emoji 防窄卡截断）
        h += '<button class="btn ghost" data-stgoegg="' + E(w.egg || '') + '">去孵化</button>';
      } else {
        h += '<button class="btn primary" data-stbuy="' + E(w.id) + '">' +
             '<span class="studio-price">✨ ' + (w.price || 2000) + '</span> 兑换</button>';
      }
    } else {
      /* 已拥有：出战/收回 + 设头像 两个按钮固定并排，文案压短统一宽度 */
      /* 2.0.75：按钮文案统一压到 2-3 字，卡片再窄也能完整显示
         （教师练习 iframe 里一行十几张卡，4 字会被截成「收…/设…」）。 */
      var equipLabel = isUnlocked()
        ? (eq ? '收起' : '展示')
        : (eq ? (isTheme ? '取消' : '收回') : (isTheme ? '使用' : '出战'));
      h += '<button class="btn ghost" data-stequip="' + E(w.id) + '" title="' +
        (eq ? (isUnlocked() ? '收起展示' : '收回') : '出战') + '">' + equipLabel + '</button>';
      /* 2.0.68：已拥有的作品宠物可以直接设账号头像（和牧场卡片同一条服务端链路） */
      if (!isTheme) {
        h += '<button class="btn ghost studio-av" data-stavatar="' + E(w.id) +
          '" title="设为头像">设为头像</button>';
      }
    }
    h += '</div>';
    h += '</div>';
    return h;
  }

  function eggHtml(e, i) {
    var owned = e.pet && ownedPets().indexOf(e.pet) >= 0;
    var motion = e.motion || (i % 2 ? 'm-egg-random' : 'm-egg');
    var h = '<div class="studio-egg-card' + (owned ? ' owned' : '') + '" data-egg="' + E(e.id) + '">';
    h += '<div class="studio-egg-pic ' + E(motion) + '" style="animation-duration:' +
         (e.dur || 3.2) + 's;animation-delay:' + (i * .26).toFixed(2) + 's">';
    h += '<img src="' + E(e.img) + '" alt="' + E(e.name) + '"></div>';
    h += '<div class="studio-egg-name">' + E(e.name) + '</div>';
    h += '<div class="studio-egg-pet">孵出：' + E(e.petName || '随机作品宠物') + '</div>';
    if (owned) {
      h += '<span class="studio-egg-have">已孵出</span>' +
           '<button class="btn ghost" data-stpet="' + E(e.pet || '') + '" type="button">去看宠物</button>';
    } else {
      var left = freeLeft();
      h += '<button class="btn primary" data-stbuy="' + E(e.id) + '">' +
           (left
             ? '<span class="studio-price studio-free">🎁 免费孵化 ×' + left + '</span>'
             : '<span class="studio-price">✨ ' + (e.price || 2000) + '</span> 兑换蛋') +
           '</button>';
    }
    return h + '</div>';
  }

  function view() {
    var d = works();
    var pets = [], themes = [];
    d.works.forEach(function (w) { (w.kind === 'theme' ? themes : pets).push(w); });
    var h = '<div class="studio-wrap">';
    h += '<div class="studio-head"><h2>🎨 创意宠物</h2>' +
         '<span class="studio-badge">✦ 学生原创</span>' +
         (freeQuota() ? '<span class="studio-free-badge">🎁 免费孵化剩 ' + freeLeft() +
           ' / ' + freeQuota() + ' 次</span>' : '') +
         '<span class="sh-sub">这些作品由同学们设计 · 每只宠物都可以免费孵化一次</span></div>';

    h += '<div class="studio-sec">🐾 作品宠物 <small>' + pets.length +
         ' 只 · 每只都有自己的动作（眨眼 / 扇翅膀 / 摇尾巴…），自动播放</small></div>';
    h += '<div class="studio-grid">' + pets.map(cardHtml).join('') + '</div>';

    h += '<div class="studio-sec" id="studioEggSec">🥚 作品蛋 <small>' + (d.eggs ? d.eggs.length : 0) +
         ' 枚 · 这里是「学生作品宠物」的蛋，孵出的都是作品宠物；' +
         '随机蛋只在你还没拥有的作品宠物里抽，不会孵出系统默认宠物</small></div>';
    h += '<div class="studio-eggs">' + (d.eggs || []).map(eggHtml).join('') + '</div>';

    /* 界面皮肤主题（2.0.58 下架）：没有作品就不再出这一节，
       免得留一个点了没反应的入口。 */
    if (themes.length) {
      h += '<div class="studio-sec">🖼 界面主题 <small>' + themes.length +
           ' 套 · 只能积分兑换</small></div>';
      var ownedT = themes.filter(function (t) { return ownedThemes().indexOf(t.id) >= 0; });
      if (ownedT.length) {
        h += '<div class="studio-theme-pick"><span>当前皮肤：</span>' +
             '<button data-skin="" class="' + (equip().studio_theme ? '' : 'on') + '">默认</button>' +
             ownedT.map(function (t) {
               return '<button data-skin="' + E(t.id) + '" class="' +
                 (equip().studio_theme === t.id ? 'on' : '') + '">' + E(t.name) + '</button>';
             }).join('') + '</div>';
      }
      h += '<div class="studio-grid">' + themes.map(function (t, i) {
        return cardHtml(t, pets.length + i);
      }).join('') + '</div>';
    }

    h += '</div>';
    return h;
  }

  function bind() {
    document.querySelectorAll('[data-stbuy]').forEach(function (b) {
      b.onclick = function () { buy(b.getAttribute('data-stbuy')); };
    });
    /* 鼠标停在作品上：把这只宠物的动作整套快放一遍（身体动作 + 部位动作 +
       眨眼 + 光效），下面的动作清单同时浮出来，学生一眼知道它会什么。 */
    document.querySelectorAll('.studio-card').forEach(function (card) {
      var stack = card.querySelector('.sd-stack');
      if (!stack) return;
      card.addEventListener('mouseenter', function () {
        stack.classList.remove('sd-preview');
        void stack.offsetWidth;          /* 强制重排，动画从头播 */
        stack.classList.add('sd-preview');
      });
      card.addEventListener('mouseleave', function () {
        stack.classList.remove('sd-preview');
      });
    });
    document.querySelectorAll('[data-stgoegg]').forEach(function (b) {
      b.onclick = function () { goEgg(b.getAttribute('data-stgoegg')); };
    });
    document.querySelectorAll('[data-stpet]').forEach(function (b) {
      b.onclick = function () {
        var card = document.querySelector('[data-sid="' + b.getAttribute('data-stpet') + '"]');
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
      };
    });
    document.querySelectorAll('[data-stequip]').forEach(function (b) {
      b.onclick = function () { doEquip(b.getAttribute('data-stequip')); };
    });
    document.querySelectorAll('[data-stavatar]').forEach(function (b) {
      b.onclick = function () {
        var id = b.getAttribute('data-stavatar');
        var w = findWork(id);
        if (window.setPetAvatar) window.setPetAvatar('studio', id, w && w.name);
      };
    });
    document.querySelectorAll('[data-skin]').forEach(function (b) {
      b.onclick = function () { pickSkin(b.getAttribute('data-skin')); };
    });
  }

  function findWork(id) {
    var all = works().works;
    for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
    return null;
  }
  function findEgg(id) {
    var all = works().eggs || [];
    for (var i = 0; i < all.length; i++) if (all[i].id === id) return all[i];
    return null;
  }

  function goEgg(eggId) {
    var sec = document.getElementById('studioEggSec');
    if (sec) sec.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (eggId) {
      var card = document.querySelector('[data-egg="' + eggId + '"]');
      if (card) {
        card.classList.add('sd-egg-focus');
        /* 2.0.72：脉冲闪 3 次约 2.2s，高亮保留到动画结束再撤，别闪一半就消失 */
        setTimeout(function () { card.classList.remove('sd-egg-focus'); }, 2400);
      }
    }
  }

  function insufficient(name, price, have) {
    var msg = '兑换「' + name + '」需要 ' + price + ' 积分，你现在只有 ' + have +
      ' 积分，还差 ' + (price - have) + ' 分。';
    if (window.uiAlert) uiAlert({ title: '积分不足', message: msg, tone: 'warn' });
    else uiToast('积分不足，还差 ' + (price - have) + ' 分，无法兑换', 'warn');
  }

  function afterBuy(r, item, isEgg) {
    if (r && r.ok) {
      if (window.S && r.stu) S.progress = r.stu;
      applySkin();
      if (window.refreshPts) refreshPts();
      uiToast((isEgg
                ? (r.free ? '🎉 免费孵出：' : '🎉 孵出：') +
                  (r.petName || item.petName || item.name)
                : '🎉 兑换成功：' + item.name), 'ok');
      if (window.render) render();
    } else uiToast((r && r.msg) || '兑换失败', 'warn');
  }

  function buy(id) {
    var egg = findEgg(id);
    var work = egg ? null : findWork(id);
    if (!egg && !work) return;
    var isEgg = !!egg;
    var item = egg || work;
    if (!isEgg && work.buyable === false) {
      uiToast('作品宠物只能通过宠物蛋兑换', 'warn');
      goEgg(work.egg);
      return;
    }
    var price = item.price || 2000;
    var have = pts();
    var useFree = !!(isEgg && freeLeft() > 0);
    if (!useFree && have < price) { insufficient(item.name, price, have); return; }
    var title = useFree ? '免费孵化' : (isEgg ? '兑换宠物蛋' : '兑换作品');
    var message = isEgg
      ? (useFree
          ? '用 1 次免费孵化机会打开「' + item.name + '」，孵出「' +
            (item.petName || '随机作品宠物') + '」？\n这个账号的免费机会用完后才需要积分。'
          : '用 ' + price + ' 积分兑换「' + item.name + '」，孵出「' +
            (item.petName || '随机作品宠物') + '」？\n作品宠物只能通过这种方式获得。')
      : '用 ' + price + ' 积分兑换「' + item.name + '」？\n界面主题兑换后可在创意馆切换使用。';
    uiConfirm({ title: title, message: message }).then(function (ok) {
      if (!ok) return;
      // 演示 / 教师练习：只改本机进度，不碰服务端，老师可以放心现场演示兑换
      if (isDemo()) {
        if (!window.S || !S.progress) return;
        if (useFree) S.progress.studio_free_used = freeUsed() + 1;
        else S.progress.pts = Math.max(0, have - price);
        if (isEgg) {
          var petId = item.pet || '';
          if (!petId) {
            var pool = (works().works || []).filter(function (w) {
              return w.kind === 'pet' && ownedPets().indexOf(w.id) < 0;
            });
            petId = pool.length ? pool[(Date.now() / 1000 | 0) % pool.length].id : '';
          }
          if (!petId) { uiToast('作品宠物已经集齐啦', 'warn'); return; }
          (S.progress.studio_pets = S.progress.studio_pets || []).push(petId);
          var pw = findWork(petId);
          S.progress.equip = S.progress.equip || {};
          S.progress.equip.studio_pet = petId;
          S.progress.equip.pet = '';
          if (window.demoSave) demoSave();
          uiToast((useFree ? '🎉 免费孵出：' : '🎉 孵出：') +
                  ((pw && pw.name) || petId) + '（演示）', 'ok');
        } else {
          (S.progress.studio_themes = S.progress.studio_themes || []).push(item.id);
          S.progress.equip = S.progress.equip || {};
          S.progress.equip.studio_theme = item.id;
          if (window.demoSave) demoSave();
          uiToast('🎉 兑换成功：' + item.name + '（演示）', 'ok');
        }
        applySkin();
        if (window.refreshPts) refreshPts();
        if (window.render) render();
        return;
      }
      var body = isEgg
        ? { kind: 'studio_egg', id: egg.id }
        : { kind: 'studio_theme', id: work.id };
      api('/api/v2/typing/buy', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      }).then(function (r) { afterBuy(r, item, isEgg); })
        .catch(function () { uiToast('网络出错，请重试', 'warn'); });
    });
  }

  function doEquip(id) {
    var w = findWork(id);
    if (!w) return;
    if (isUnlocked()) {
      // 教师课堂展示：只改本地预览状态，绝不 POST 装备接口
      var isThemeP = w.kind === 'theme';
      var eq0 = equip();
      var on0 = isThemeP ? (eq0.studio_theme === id) : (eq0.studio_pet === id);
      if (!window.S || !S.progress) return;
      if (isThemeP) {
        S.progress.equip = S.progress.equip || {};
        S.progress.equip.studio_theme = on0 ? '' : id;
      } else syncLocalEquip(id, on0);
      applySkin();
      uiToast('课堂展示：' + w.name + (on0 ? ' 已收起' : ' 已预览'), 'ok');
      if (window.render) render();
      return;
    }
    if (isDemo()) {
      if (!window.S || !S.progress) return;
      var isThemeDemo = w.kind === 'theme';
      var eqDemo = equip();
      var onDemo = isThemeDemo ? (eqDemo.studio_theme === id) : (eqDemo.studio_pet === id);
      if (isThemeDemo) {
        S.progress.equip = S.progress.equip || {};
        S.progress.equip.studio_theme = onDemo ? '' : id;
      } else syncLocalEquip(id, onDemo);
      if (window.demoSave) demoSave();
      applySkin();
      uiToast(onDemo ? '已取消（演示）' : (isThemeDemo ? '已切换主题：' + w.name : w.name + ' 出战！（演示）'), 'ok');
      if (window.render) render();
      return;
    }
    var isTheme = w.kind === 'theme';
    var curEquip = equip();
    var isOn = isTheme ? (curEquip.studio_theme === id) : (curEquip.studio_pet === id);
    var body = isTheme
      ? { studio_theme: isOn ? '' : id }
      /* 收回作品宠物时 pet 传默认系统宠物，服务端才会把出战位还给它；
         传 '' 会让顶栏只剩一个空爪印。 */
      : { studio_pet: isOn ? '' : id,
          pet: isOn ? defaultPetId() : '',
          decos: curEquip.decos || [],
          /* 2.0.65：背景和出战特效是「整片牧场」的状态，不跟着宠物走。
             出战/收回作品宠物时必须原样带回去，否则一次点击就把背景清空，
             看起来像作品宠物不能用牧场装扮。 */
          bg: curEquip.bg || '', fx: curEquip.fx || '' };
    api('/api/v2/typing/equip', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (r && r.ok) {
        if (window.S && r.equip) { S.progress.equip = r.equip; S.ranch = window.ranchFromEquip ? ranchFromEquip(r.equip) : S.ranch; }
        applySkin();
        uiToast(isOn ? '已取消' : (isTheme ? '已切换主题：' + w.name : w.name + ' 出战！'), 'ok');
        if (window.render) render();
      } else uiToast((r && r.msg) || '操作失败', 'warn');
    }).catch(function () { uiToast('网络出错', 'warn'); });
  }

  function pickSkin(id) {
    var isTheme = !!id;
    if (isUnlocked()) {
      if (window.S && S.progress) {
        S.progress.equip = S.progress.equip || {};
        S.progress.equip.studio_theme = id;
      }
      applySkin(id);
      if (window.render) render();
      return;
    }
    if (isDemo()) {
      if (window.S && S.progress) {
        S.progress.equip = S.progress.equip || {};
        S.progress.equip.studio_theme = id;
      }
      if (window.demoSave) demoSave();
      applySkin(id);
      if (window.render) render();
      return;
    }
    var body = { studio_theme: id };
    api('/api/v2/typing/equip', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (r && r.ok) {
        if (window.S && r.equip) S.progress.equip = r.equip;
        applySkin();
        if (window.render) render();
      } else uiToast((r && r.msg) || '切换失败', 'warn');
    });
  }

  // 界面皮肤主题已在 2.0.58 下架（牛哥：换了看不出效果，不要了）。
  // 保留这个空函数只为兼容旧调用点，顺手把历史遗留的 body class 清掉。
  function applySkin() {
    if (!document.body) return;
    document.body.classList.remove('skin-farm', 'skin-flip');
  }

  function onGotStudio(data) {
    if (window.S && data && data.studio) S.studio = data.studio;
    applySkin();
  }
  window.StudioUI = {
    view: view, bind: bind, applySkin: applySkin,
    petMedia: petMedia,
    onGotStudio: onGotStudio,
    /* 进入创意馆时重拉一次，教师刚改的设计者信息无需刷新页面即可看到。
       不按 isDemo() 挡教师练习 iframe：登录态自己会做 401 守卫。 */
    reload: function () {
      if (!window.api) return;
      api('/api/v2/studio/works').then(function (d) {
        if (d && d.ok) {
          onGotStudio(d);
          if (window.S && S.view === 'studio' && window.render) render();
        }
      }).catch(function () {});
    }
  };
  /* 教师端保存设计者信息后会 postMessage 过来，即时重拉 */
  window.addEventListener('message', function (e) {
    if (e && e.data && e.data.type === 'wb-studio-reload') window.StudioUI.reload();
  });
})();
