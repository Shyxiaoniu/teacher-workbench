/* 学生端 · 单页应用（v3.0.0 三页签版，2026-11-02）
   ------------------------------------------------------------
   布局：左侧侧边栏 + 右侧主内容区，窄屏（≤860px）侧栏收起换底部 Tab。
   页签精简为三个（v2.x 的 概览/成绩/考勤 页全部收进左下角账号卡
   弹出的个人信息面板 #suPop，含学习摘要 + 成绩/考勤/积分明细弹层）：
   · 今日   = 签到 + 本课下发的资料 + 待交作业 + 值日
   · 作业   = 本班全部作业与我的提交状态（已提交·待批改 / 已批改 / 逾期）
   · 课程   = 老师设置的课程 + 每门课的资源文件，点击在线预览
              （/cw/<id> 闸门按登录身份 + 本班 + 已下发放行，内联输出，
               PDF / 图片 / 音视频 / 文本直接浏览器里看，不用下载）

   提交反馈：每一步操作（签到 / 作业提交 / 学习单提交）都有明确状态——
   toast 即时提示 + 列表标签（待交 / 已提交·待批改 / 已批改 / 逾期）+ 回执卡。

   数据全部来自 /api/v2/sync?scope=student —— 服务端已经裁剪过，
   这里拿不到别人的手机号、别人的分数，也拿不到没下发的资源。 */
(function () {
  'use strict';

  var S = { me: null, rev: 0, T: {}, flags: {}, vis: {}, view: 'today', es: null,
            now: null,   /* 服务端「现在第几节」（loadNow 定期刷新，2026-11-04） */
            loading: true, seatmap: null,
            /* H5 网页（2.0.39；2.0.40 定名「网页」）：老师开服务 + 允许添加，
               学生点一次入口卡才把「网页」页签加进导航（记在 localStorage）。 */
            designOn: false, designAdd: true, designSubmit: true, designAdded: false,
            designSeen: false,   /* 进来过没有：只影响卡片文案，不影响入口是否显示 */
            /* 像素画（2.0.47）：与网页设计同一套判断（服务开着 + 允许使用） */
            pixelOn: false, pixelAdd: true, pixelSubmit: true, pixelSeen: false,
            /* 测验/检测（2.0.38）：不进 sync 快照（试卷含答案），独立接口按会话班级下发 */
            quiz: { list: null, loading: false } };
  /* 右下角页脚：设计者署名 + ICP 备案号（2026-11-23 定版）。
     学生端三处会重写 body（登录/改密/主界面），以前备案号写在 index.html 里
     被 innerHTML 抹掉 → 任何页面都看不到。现在把署名+备案号做成一段，
     每次 body 重建都带上，且并排同一行、永远不丢。 */
  var FOOT_HTML =
    '<span class="af-credit corner-credit" title="班级工作台 · 设计者">🐮 牛老师设计</span>' +
    '<span class="beian-foot"><a href="https://beian.miit.gov.cn/" target="_blank" ' +
    'rel="noopener">豫ICP备2026058674号-1</a>' +
    '<a href="https://beian.mps.gov.cn/" target="_blank" rel="noopener" hidden>' +
    '粤公网安备 XXXXXXXXXXXXXX号</a></span>';
  var WD = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
  /* 学生自己「添加」H5 网页设计页签的标记：老师开放 + 学生点过入口卡才为 1。
     换设备要重新添加一次，避免把入口强塞给还没准备用的同学。 */
  var DESIGN_ADD_KEY = 'wb-design-added';
  /* 像素画同理（2.0.47）：只记「来过」，用来把按钮文案换成「继续画 ›」 */
  var PIXEL_ADD_KEY = 'wb-pixel-seen';

  /* ---------------- 工具 ---------------- */
  function $(s) { return document.querySelector(s); }
  /* 2026-11-25 账号隔离：学生专属入口令牌 e。
     URL 里带 e（老师发的链接）就用它并存本地；被踢下线 / 改密跳转后 URL
     丢了 e，从本地取回 —— 令牌本身不敏感（只是账号签名），只用于证明
     「我从哪个老师的入口进来」。换设备没有本地缓存时必须重新扫入口码。 */
  var ENTRY_E = (function () {
    var m = /[?&]e=([^&#]*)/.exec(location.search);
    var v = m ? decodeURIComponent(m[1]) : '';
    if (!v) { try { v = localStorage.getItem('wb-entry-e') || ''; } catch (e) {} }
    if (v) { try { localStorage.setItem('wb-entry-e', v); } catch (e) {} }
    return v;
  })();
  function entryE() { return ENTRY_E; }
  /* 站内跳转回 /s/ 时把入口令牌一起带上，避免 URL 丢了 e（localStorage 仍兜底）。 */
  function sUrl(extra) {
    var q = [];
    if (entryE()) q.push('e=' + encodeURIComponent(entryE()));
    if (extra) q.push(extra);
    return '/s/' + (q.length ? '?' + q.join('&') : '');
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function api(url, opt) {
    return fetch(url, Object.assign({
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }
    }, opt || {})).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        j._status = r.status;
        return j;
      });
    });
  }
  function todayIdx() { return (new Date().getDay() + 6) % 7; }   // 周一=0
  /* ---------------- 网页 / 学习单一律新标签打开（2026-11-05 晚定版） ----------------
     上午的做法是「站内同标签、站外新标签」，下午牛哥要求改回来：
     学习单网页、其他网页文件都要在**新标签页**里独立打开，学生端页面不能被顶掉
     —— 学生要能随时切回来翻别的资料、看今天的任务。

     既要「原页不关」又要「别越点越多」，所以按绝对地址记住已经开过的标签：
     同一份资料再点一次 = 切到那个标签（focus），不会又开一个。
       · 站内 / 站外一视同仁（学习单 /g/mat、.url、课件 html 都在站内）；
       · noopener：新标签拿不到学生端的 window；
       · 被浏览器拦下时只提示，绝不用 location.href 把学生端顶掉（那正是要避免的）。 */
  var _wbTabs = {};                     /* 绝对地址 → window 引用 */
  function openNewTab(url) {
    var abs = String(url || '');
    try { abs = new URL(abs, location.href).href; } catch (e) {}
    var old = abs ? _wbTabs[abs] : null;
    if (old && !old.closed) {
      try { old.focus(); return true; } catch (e) {}
    }
    var w = null;
    try { w = window.open(abs, '_blank'); } catch (e) { w = null; }
    if (!w) {
      toast('浏览器拦住了新标签页：请允许本站弹出窗口，或长按链接选「在新标签页打开」');
      return false;
    }
    try { w.opener = null; } catch (e) {}   // 断开引用，别人拿不到本页的 window
    if (abs) _wbTabs[abs] = w;
    return true;
  }
  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  }
  /* 统一提示：全部走 WBUI（app/student/ui-dialog.js），
     弹窗/吐司的配色、圆角、动效与整站护眼主题保持一套。
     组件没加载成功时退回旧的 .toast，保证提示不丢。 */
  function toast(msg, tone) {
    if (window.WBUI && WBUI.toast) { WBUI.toast(msg, tone); return; }
    var t = document.createElement('div');
    t.className = 'toast';
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(function () { t.classList.add('on'); });
    setTimeout(function () { t.classList.remove('on'); setTimeout(function () { t.remove(); }, 250); }, 2200);
  }

  /* ---------------- 同步 ---------------- */
  function merge(local, rows) {
    var idx = {};
    local.forEach(function (r, i) { idx[String(r.id)] = i; });
    rows.forEach(function (r) {
      var k = String(r.id);
      if (r.deleted) {
        if (idx[k] !== undefined) { local.splice(idx[k], 1); idx = {}; local.forEach(function (x, j) { idx[String(x.id)] = j; }); }
      } else if (idx[k] !== undefined) {
        local[idx[k]] = r;
      } else {
        local.push(r);
        idx[k] = local.length - 1;
      }
    });
    return local;
  }

  function apply(tables, snapshot) {
    if (snapshot) S.T = tables || {};
    else {
      Object.keys(tables || {}).forEach(function (t) {
        S.T[t] = merge(S.T[t] || [], tables[t]);
      });
    }
    /* 座位表是单独接口取的（整间教室的网格，同步接口只下发本人那一行），
       座位一有变动就作废缓存，下次进「座位」页重新拉（2026-11-03）。 */
    if (tables && tables.seats) S.seatmap = null;
  }

  function pull(since, snapshot) {
    return api('/api/v2/sync?since=' + since + '&scope=student').then(function (j) {
      if (j._status === 401) {
        /* 被踢的场合交给 kickOut 走 showLogin（带原因），别在这里 reload 把原因冲掉 */
        if (S.kicked) return;
        location.reload();
        return;
      }
      if (!j.ok) return;
      apply(j.tables, snapshot);
      S.rev = j.rev;
      S.flags = j.modules || S.flags;
      S.vis = j.visibility || S.vis;
      if (snapshot) S.loading = false;
    });
  }

  /* 三个功能 iframe（打字 / 网页 / 像素画）内的配置不走外层同步：老师开关后由这里
     转发一条通知，画板收到才重拉 /api/v2/pixel/me，不必让学生刷新整页。
     2.0.55：网页页原本没有这条通道，老师改「网页」开关/额度后要点一下才更新，
     这里和像素画一起补上。 */
  function notifyPixelCfg() {
    var f = document.querySelector('iframe[data-pixel-embed]');
    if (f && f.contentWindow) {
      try { f.contentWindow.postMessage({ type: 'wb-pixel-cfg' }, '*'); } catch (e) {}
    }
  }
  function notifyDesignCfg() {
    var f = document.querySelector('iframe[data-design-embed]');
    if (f && f.contentWindow) {
      try { f.contentWindow.postMessage({ type: 'wb-design-cfg' }, '*'); } catch (e) {}
    }
  }
  function notifyTypingCfg() {
    var f = document.querySelector('iframe[data-typing-embed]');
    if (f && f.contentWindow) {
      try { f.contentWindow.postMessage({ type: 'wb-typing-cfg' }, '*'); } catch (e) {}
    }
  }
  function notifyAllEmbedCfg() {
    notifyTypingCfg();
    notifyDesignCfg();
    notifyPixelCfg();
  }
  /* 2.0.56：打字页删掉自己的夜色按钮，外壳改主题时主动通知所有 iframe。
     storage 事件本来也能到 iframe，但这里再推一条，切换瞬间不闪旧色。 */
  function notifyThemeEmbeds() {
    document.querySelectorAll('iframe').forEach(function (f) {
      if (!f.contentWindow) return;
      try { f.contentWindow.postMessage({ type: 'wb-theme' }, '*'); } catch (e) {}
    });
  }

  /* 画板 iframe 里把像素画设为头像后，学生端各头像位即时刷新 */
  window.addEventListener('message', function (e) {
    if (e && e.data && e.data.type === 'wb-avatar-changed') {
      if (e.data.avatar && S.me) S.me.avatar = e.data.avatar;   // 服务端已落盘，即时改内存
      if (typeof refreshAvatars === 'function') refreshAvatars();
    }
  });

  function watch() {
    try {
      S.es = new EventSource('/api/v2/events?since=' + S.rev);
      S.es.addEventListener('rev', function (e) {
        var r = parseInt(e.data, 10) || 0;
        if (r > S.rev) pull(S.rev, false).then(render);
      });
      /* 老师改了「学生能用哪些功能 / 可见字段」：配置不产生数据 rev，
         要单独收这个事件，否则学生端入口要等手动刷新才变。 */
      S.es.addEventListener('cfg', function () {
        notifyAllEmbedCfg();
        refreshTyping();
        /* 关键（2.0.55）：老师开关网页/像素/打字时，学生多半正停在打字页。
           refreshTyping→render() 命中 iframe 保护会提前 return，导航就不重绘，
           于是「要点一下才生效」。这里在重绘之外显式再刷一次导航，保证入口即时增减。 */
        paintNav();
        pull(S.rev, false).then(render);
      });
      /* 老师发布 / 关闭测验、或有新提交：事件里不带任何名单与答案，
         只是让本页把缓存作废，重新按会话班级拉一次可见测验。 */
      S.es.addEventListener('quiz', function () {
        S.quiz.list = null;
        if (S.view === 'quiz') render();
      });
      /* 被踢下线（2026-11-17 单设备登录 / 老师重置密码）：
         服务端在断开 SSE 前会发一个 kick 事件，这里立刻回到登录页并说明原因。
         学生共用机房时最常发生 —— 停在旧页面上会以为"还能用"。 */
      S.es.addEventListener('kick', function (e) {
        var d = {};
        try { d = JSON.parse(e.data || '{}') || {}; } catch (err) {}
        kickOut(d.reason || '');
      });
    } catch (e) {}
    setInterval(function () {
      if (!S.es || S.es.readyState === 2) {
        /* 轮询兜底（SSE 断了的情况）：同步接口不返回功能开关，
           所以顺手把 session 拉一次 —— 否则老师开了「网页」，
           这位同学要手动刷新才看得见入口（2.0.41）。 */
        notifyAllEmbedCfg();
        refreshTyping();
        paintNav();
        pull(S.rev, false).then(render);
      }
    }, 10000);
    /* 服务端「现在第几节」每分钟对一次表：跨过节的边界（下课/上课）
       签到卡自动切到新一节，不用学生手动刷新 */
    setInterval(loadNow, 60000);
  }

  /* 功能开关（2.0.39 起统一入口）：老师改开关后学生端入口实时增减。
     打字 / H5 网页都在这里刷新，别各写一份。
     2.0.41：网页页签改成**服务端驱动**——老师一开启，入口自动出现，
     不再要学生先去「今日」页点一次「添加」。localStorage 里那个键降级成
     「进来过」的记号，只用来说「去写 / 继续写」，不再决定页签显示与否。
     判据两个都要满足：enabled（服务开着）且 add（允许学生用）。 */
  function applyDesignFlag(d) {
    d = d || {};
    S.designOn = !!d.enabled;
    S.designAdd = d.add !== false;
    S.designSubmit = d.submit !== false;
    S.designAdded = !!(S.designOn && S.designAdd);
    try { S.designSeen = localStorage.getItem(DESIGN_ADD_KEY) === '1'; } catch (e) { S.designSeen = false; }
  }
  /* 像素画（2.0.47）：字段与 design 一模一样，判断逻辑也照抄一份。
     别把两者合并成一个「工具开关」——它们各自独立启停，老师今天只开像素画
     不开网页是常见情况。 */
  function applyPixelFlag(d) {
    d = d || {};
    S.pixelOn = !!d.enabled;
    S.pixelAdd = d.add !== false;
    S.pixelSubmit = d.submit !== false;
    try { S.pixelSeen = localStorage.getItem(PIXEL_ADD_KEY) === '1'; } catch (e) { S.pixelSeen = false; }
  }
  function refreshTyping() {
    api('/api/v2/auth/session').then(function (j) {
      if (j && j.ok) {
        S.typingOn = !!(j.typing && j.typing.enabled);
        applyDesignFlag(j.design);
        applyPixelFlag(j.pixel);
        render();
      }
    }).catch(function () {});
  }

  /* ---------------- 数据选择器 ---------------- */
  function T(name) { return S.T[name] || []; }
  /* 2.0.71 多班：学生可兼属多个班（主班 class_id + 兼属班 class_ids）。
     服务端投影已把各班数据下发，这里按集合判定，避免兼属班内容被主班过滤掉。 */
  function myClasses() {
    var ids = [];
    var main = S.me && S.me.class_id;
    if (main !== undefined && main !== null && main !== '') ids.push(Number(main));
    ((S.me && S.me.class_ids) || []).forEach(function (x) {
      var n = Number(x);
      if (isFinite(n) && ids.indexOf(n) < 0) ids.push(n);
    });
    return ids;
  }
  function inMyClass(cid) { return myClasses().indexOf(Number(cid)) >= 0; }
  function myClass() { return T('classes').filter(function (c) { return c.id === S.me.class_id; })[0] || {}; }
  function mySeat() { return T('seats').filter(function (s) { return s.student_id === S.me.id; })[0] || null; }
  /** 我的座位「在座位表上」的排/列（2026-11-03）。
      讲台在下方时，排号要从最靠近讲台的一排数起，才和老师那张表、
      以及老师嘴里说的「你坐第 3 排」一致；列号沿用老师那张表的列号
      （座位表在屏幕上左右镜像，表头列号同步镜像，文字对得上）。 */
  function mySeatPos() {
    var m = S.seatmap;
    if (m && m.ok && m.me) {
      var R = Number(m.rows) || 0;
      return {
        r: String(m.stage) === 'bottom' ? R - Number(m.me.r) + 1 : Number(m.me.r),
        c: Number(m.me.c)
      };
    }
    var s = mySeat();
    return s ? { r: Number(s.row), c: Number(s.col) } : null;
  }
  /* 我所在的小组（服务端投影已按“我是组员”过滤，每人最多命中自己那一行）。
     _members 是服务端算好的名单：[{name, role}]，role 为「组长」或「组员」。 */
  function myGroup() { return T('groups')[0] || null; }
  function periodMap() {
    var m = {};
    T('periods').forEach(function (p) { m[p.id] = p; });
    return m;
  }
  function courseMap() {
    var m = {};
    T('courses').forEach(function (c) { m[c.id] = c; });
    return m;
  }
  function lessonsOf(day) {
    var pm = periodMap();
    return T('lessons').filter(function (l) {
      return Number(l.day_of_week) === day && inMyClass(l.class_id);
    }).sort(function (a, b) {
      var pa = pm[a.period_id] || {}, pb = pm[b.period_id] || {};
      return (pa.seq || 0) - (pb.seq || 0);
    });
  }
  function homeworks() {
    return T('homeworks').filter(function (h) {
      return inMyClass(h.class_id);
    }).sort(function (a, b) { return String(b.date || b.published || '').localeCompare(String(a.date || a.published || '')); });
  }
  function recordOf(hw) {
    return T('homework_records').filter(function (r) {
      return String(r.homework_id) === String(hw.id) && r.student_id === S.me.id;
    })[0] || null;
  }
  function scores() {
    return T('scores').filter(function (s) { return s.student_id === S.me.id; })
      .sort(function (a, b) { return String(b.date || '').localeCompare(String(a.date || '')); });
  }
  /* 教师登记的考勤状态（学生端只读展示，不能自己改）。
     v2：登录即自动签到，迟到/缺勤由服务端按课表时间判定。 */
  var ATT_TEXT = { checked: '已签到', unchecked: '未签到', sick: '病假',
                   personal: '事假', absent: '缺勤',
                   present: '出勤', late: '迟到', early_leave: '早退' };
  function attLabel(st) { return ATT_TEXT[st] || (st ? String(st) : '未签到'); }

  /* （materials/materialById 已随 2026-11-02 导学单移除而删除；
     课件库推送走 cwPushed()。） */

  /* ---------------- 课堂活动（已下线，2026-11-04） ----------------
     旧版今日页按课表罗列每节课、按课程名匹配 activity_pushes 给「进学习单」
     入口。牛哥定版（2026-11-04）：那块按课表的模块整体下线，只展示老师下发的资料，
     所以 pushOpen / pushes / pushOfCourse 一并删除（activity_pushes 数据保留，
     教师端历史推送记录仍可查，只是学生端不再有这套入口）。 */
  /* 课件库（2026-11-01 v2）：老师在工作台课件页点「推送」的课件，按班同步。
     原来放在导学单详情里，导学单移除后改为「今日」页独立入口。 */
  function cwPushed() {
    return T('materials').filter(function (m) {
      return !m.deleted && m.from === 'courseware' &&
        inMyClass(m.class_id);
    }).sort(function (a, b) {
      return String(b.date || '').localeCompare(String(a.date || ''));
    });
  }

  /* ---------------- 课堂签到 ---------------- */
  function toMin(t) {
    var a = String(t || '').split(':');
    return (+a[0]) * 60 + (+a[1] || 0);
  }
  /** 现在第几节：找不到就取“已经过去的最近一节”。
      2026-11-04 牛哥反馈签到节数两端不一致：机房学生机时钟经常不准，
      本地算出来的节和教师端、实际签到记录对不上 —— 现在「现在第几节」
      一律以服务端 /api/v2/student/now 为准（服务端时间 = 唯一时间源），
      服务端还没答上来（刚开机 / 网络抖动）才退回本地时钟估算。 */
  function curPeriod() {
    if (S.now && S.now.period) {
      return { seq: S.now.period, label: S.now.label || '' };
    }
    var ps = T('periods').slice().sort(function (a, b) {
      return (a.seq || 0) - (b.seq || 0);
    });
    if (!ps.length) return null;
    var d = new Date(), now = d.getHours() * 60 + d.getMinutes();
    for (var i = 0; i < ps.length; i++) {
      var s = toMin(ps[i].start_time), e = toMin(ps[i].end_time);
      if (s >= 0 && e > s && now >= s && now <= e) return ps[i];
    }
    var past = ps.filter(function (p) { return toMin(p.start_time) >= 0 && toMin(p.start_time) <= now; });
    /* 2.0.52：课间算下一节的课前（与后端同一口径）。这段只在服务端
       /student/now 拿不到时兜底用，但口径不能跟服务端打架。 */
    var nxt = ps.filter(function (p) { return toMin(p.start_time) > now; })[0];
    if (nxt) return nxt;
    return past.length ? past[past.length - 1] : ps[0];
  }
  function periodSeq() {
    var p = curPeriod();
    return p ? (Number(p.seq) || Number(p.id) || 1) : 1;
  }
  /** 拉服务端当前节次；节数有变化且正在「今日」页时重画签到卡 */
  function loadNow() {
    return api('/api/v2/student/now').then(function (j) {
      if (j && j.ok) {
        var oldCk = S.now && S.now.checkin
          ? (S.now.checkin.enabled + '|' + S.now.checkin.manual_time) : '';
        var newCk = j.checkin ? (j.checkin.enabled + '|' + j.checkin.manual_time) : '';
        var changed = !S.now || S.now.period !== j.period || S.now.label !== j.label ||
          S.now.lesson !== j.lesson || S.now.checkin_ok !== j.checkin_ok ||
          S.now.checkin_time_ok !== j.checkin_time_ok ||
          S.now.can_checkin !== j.can_checkin ||
          S.now.checkin_disabled_reason !== j.checkin_disabled_reason || oldCk !== newCk;
        S.now = j;
        if (changed && S.view === 'today' && !S.loading) render();
      }
    }).catch(function () {});
  }
  /** 本节课的签到记录 */
  function myCheckin() {
    var d = todayStr(), seq = periodSeq();
    return T('attendance').filter(function (a) {
      return a.date === d && Number(a.period || 1) === seq;
    })[0] || null;
  }
  /** 今天任意一节的签到记录（给「今日」页概览用） */
  function todayAtts() {
    var d = todayStr();
    return T('attendance').filter(function (a) { return a.date === d; });
  }

  function myDutyToday() {
    var seat = mySeat();
    if (!seat) return [];
    var d = todayIdx() + 1;
    return T('dutys').filter(function (x) {
      return inMyClass(x.class_id) && Number(x.day) === d &&
        (x.seat_ids || []).map(String).indexOf(String(seat.id)) >= 0;
    });
  }

  /* ---------------- 视图：今日 ---------------- */
  function hwState(h) {
    var rec = recordOf(h);
    var done = rec && ['submitted', 'checked', 'done', 'practice'].indexOf(rec.status) >= 0;
    var late = !done && h.due && String(h.due) < todayStr();
    return { rec: rec, done: done, late: late };
  }
  function todoHomeworks() {
    return homeworks().filter(function (h) {
      var st = hwState(h);
      return !st.done && (!h.due || String(h.due) >= todayStr());
    });
  }

  /** 今天下发的资料（2026-11-04 改版）：
      旧版按课表罗列每节课 + 学习单入口，学生看到的是一堆「暂无学习单」；
      现在只列老师真正下发的资料，**当前节次课程名对应的排最前**——一进页面
      就是这节课要用的东西。今天没发过就退回到最近下发的几份，不给空白页。 */
  function todayMats() {
    var d = todayStr();
    var list = cwPushed().filter(function (m) { return String(m.date || '') === d; });
    var fresh = list.length > 0;
    if (!list.length) list = cwPushed().slice(0, 6);
    var cur = (S.now && S.now.course) ? String(S.now.course) : '';
    if (cur) {
      list = list.slice().sort(function (a, b) {
        var ka = String(a.lesson || a.subject || '') === cur ? 0 : 1;
        var kb = String(b.lesson || b.subject || '') === cur ? 0 : 1;
        return ka - kb;
      });
    }
    return { list: list, fresh: fresh };
  }

  function vToday() {
    var seat = mySeat();
    var sp = mySeatPos();
    var duty = myDutyToday();
    var hwTodo = todoHomeworks();
    var tm = todayMats();
    var matsHtml = tm.list.length
      ? tm.list.map(resRow).join('')
      : '<div class="empty">老师还没有下发资料，下发后会直接出现在这里</div>';

    var hwHtml = hwTodo.length ? hwTodo.slice(0, 4).map(function (h) {
      return '<div class="hw" data-hw="' + esc(h.id) + '"><div class="dot"></div>' +
        '<div class="m"><b>' + esc(h.title || h.subject || '作业') + '</b>' +
        '<span>' + esc(h.subject || '') + (h.due ? ' · 截止 ' + esc(h.due) : '') + '</span></div>' +
        '<div class="tag">待交</div></div>';
    }).join('') : '<div class="empty">作业都交齐了</div>';

    /* 课堂签到卡：登录时服务端已自动签到，这里展示状态；按钮只作补签。
       2026-11-25 牛哥定版：只有服务端明确回 can_checkin=true（签到开关开着 +
       这一节有排课 + 在签到时间窗内）才显示可点的「一键签到」。以前靠字段
       不等于 false 兜底，服务端还没算出节次 / 网络抖动时会误挂按钮，点了
       又被服务端拒绝。现在未确认一律只显示原因，不给按钮。 */
    var ck = myCheckin();
    var p = curPeriod();
    var pLabel = p ? (p.label || ('第' + (p.seq || '') + '节')) : '本节课';
    var noLesson = !!(S.now && S.now.checkin_ok === false);
    /* 2026-11-25：这一节没排课 ≠ 今天整天没课。前者提示等下课签下一节，
       后者才显示「今天本班没有排课，不用签到」。两者都不给签到按钮。 */
    var noLessonNow = !!(S.now && S.now.lesson === false);
    /* 2026-11-25：倒挂节次（如第9节18:30、第10节17:10）选对节但时间窗未开，
       服务端回 checkin_time_ok=false，界面显示「还没到签到时间」，不给按钮。 */
    var noTimeYet = !!(S.now && S.now.checkin_time_ok === false);
    var canSign = !!(S.now && S.now.can_checkin === true);
    var blocked = (S.now && S.now.checkin_disabled_reason) || '';
    var ckDone = ck && (ck.status === 'checked' || ck.status === 'present' || ck.status === 'late');
    var ckTxt;
    if (ckDone) {
      ckTxt = '已签到 ' + esc(ck.checked_at || '') + (ck.status === 'late' ? '（迟到）' : '');
    } else if (ck && (ck.status === 'sick' || ck.status === 'personal')) {
      ckTxt = esc(attLabel(ck.status)) + '（老师已登记）';
    } else if (ck && ck.status === 'absent') {
      ckTxt = '缺勤（本节课超15分钟未登录，已记缺勤）';
    } else if (blocked) {
      ckTxt = esc(blocked);
    } else if (!S.now) {
      ckTxt = '正在读取服务端时间…';
    } else {
      ckTxt = ck ? esc(attLabel(ck.status)) : '现在还不能签到';
    }
    /* 教师端签到配置（2026-11-05）：关掉后整张卡不显示；
       设了手动时间就在卡上写清楚，免得学生按课表时间白等。 */
    var ckCfg = (S.now && S.now.checkin) || null;
    var ckOff = !!(ckCfg && ckCfg.enabled === false);
    var ckManual = ckCfg && ckCfg.manual_time;
    if (ckManual && !ckDone && canSign) ckTxt += '（老师设定 ' + esc(ckManual) + ' 签到）';
    /* 小标签跟着服务端给的原因走：明明是「已下课/时间已过」就别写「未排课」，
       免得学生看左边原因、右边标签互相打架。 */
    var tagTxt = '暂不可签';
    if (blocked.indexOf('已下课') >= 0 || blocked.indexOf('时间已过') >= 0) tagTxt = '时间已过';
    else if (blocked.indexOf('没到签到时间') >= 0 || blocked.indexOf('还没到') >= 0) tagTxt = '未到时间';
    else if (noLesson || noLessonNow) tagTxt = '未排课';
    else if (noTimeYet) tagTxt = '未到时间';
    var ckHtml = ckOff ? '' :
      '<div class="card ckin' + (ckDone ? ' on' : '') + '">' +
      '<div class="ck-l"><b>' + esc(pLabel) + '签到</b>' +
      '<span>' + ckTxt + '</span></div>' +
      (ckDone
        ? '<div class="ck-done">' + (ck.status === 'late' ? '迟' : '✓') + '</div>'
        : (canSign
            ? '<button class="btn ck-b" id="ckBtn">一键签到</button>'
            : '<div class="tag mut">' + tagTxt + '</div>')) +
      '</div>';

    /* 打字 / 网页 / 像素 的入口卡已下线（2.0.49 牛哥要求）：
       学生端统一从左侧导航进，不在「今日」页重复一遍。
       显隐只看老师那边的服务开关（navShown），与「添加过没有」无关。 */

    return '<div class="stat-grid">' +
      '<div class="stat"><div class="stat-num">' + tm.list.length + '<small>份</small></div>' +
      '<div class="stat-label">' + (tm.fresh ? '今日资料' : '最近资料') + '</div></div>' +
      '<div class="stat"><div class="stat-num">' + hwTodo.length + '<small>项</small></div>' +
      '<div class="stat-label">待交作业</div></div>' +
      /* 座位卡点一下直接进座位表（2026-11-03）；排号用座位表的显示排，
         老师那边讲台在下方时也和他嘴里说的「第几排」对得上 */
      (seat && sp ? '<div class="stat stat-go" data-go="seat" title="点一下看整间教室的座位表">' +
        '<div class="stat-num" style="font-size:17px">' +
        esc(sp.r) + ' 排 ' + esc(sp.c) + ' 列</div>' +
        '<div class="stat-label">我的座位 ›</div></div>' : '') +
      '</div>' +
      ckHtml +
      '<div class="cols2">' +
      '<div class="card"><h3>' + (tm.fresh ? '本课资料' : '最近下发的资料') +
        '<span class="more">点一下直接打开</span></h3>' + matsHtml + '</div>' +
      (S.flags.homework ? '<div class="card"><h3>待交作业<span class="more">' + hwTodo.length + ' 项</span></h3>' + hwHtml + '</div>' : '') +
      (duty.length ? '<div class="card"><h3>今天我值日</h3>' + duty.map(function (x) {
        return '<div class="hw"><div class="dot done"></div><div class="m"><b>' + esc(x.area || '值日') + '</b>' +
          '<span>记得课后完成</span></div><div class="tag done">' + (x.done ? '已完成' : '待完成') + '</div></div>';
      }).join('') + '</div>' : '') +
      '</div>';
  }

  /* ---------------- 我的记录（2026-11-01 深夜新增） ----------------
     服务端 projection 已保证 attendance / morality_records 只下发本人行，
     这里直接过滤展示即可；只读，学生端没有任何写入入口。 */
  function moralityMine() {
    return T('morality_records').filter(function (r) { return r.student_id === S.me.id; })
      .sort(function (a, b) { return String(b.date || '').localeCompare(String(a.date || '')); });
  }

  function attMine() {
    return T('attendance').filter(function (a) { return a.student_id === S.me.id; })
      .sort(function (a, b) {
        return String(b.date || '').localeCompare(String(a.date || '')) ||
          (Number(b.period) || 0) - (Number(a.period) || 0);
      });
  }
  /** 节次序号 → 名称（attendance.period 存的是 seq，不是 periods.id） */
  function periodNameBySeq(seq) {
    var hit = T('periods').filter(function (p) {
      return Number(p.seq || p.id) === Number(seq);
    })[0];
    return hit ? (hit.label || ('第' + (hit.seq || seq) + '节')) : ('第' + seq + '节');
  }
  /** 积分记录全量明细（底部弹层） */
  function openMorSheet() {
    var moral = moralityMine();
    var sum = 0;
    moral.forEach(function (r) { sum += Number(r.score) || 0; });
    var body = moral.length ? moral.map(function (r) {
      var v = Number(r.score) || 0;
      return '<div class="hw"><div class="dot' + (v >= 0 ? ' done' : '') + '"></div>' +
        '<div class="m"><b>' + esc(r.item_name || r.reason || '课堂评价') + '</b>' +
        '<span>' + esc(r.date || '') +
        (r.reason && r.item_name ? ' · ' + esc(r.reason) : '') + '</span></div>' +
        '<div class="tag ' + (v >= 0 ? 'done' : 'late') + '">' + (v > 0 ? '+' : '') + v + '</div></div>';
    }).join('') : '<div class="empty">还没有积分记录</div>';
    showSheet('<div class="sh"><b>我的积分记录</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note">累计 ' + (sum > 0 ? '+' : '') + sum + ' 分 · 共 ' + moral.length + ' 条 · 按日期倒序</div>' +
      body);
    var c = $('#shClose'); if (c) c.onclick = hideSheet;
  }
  /** 考勤记录全量明细（底部弹层） */
  function openAttSheet() {
    var atts = attMine();
    var n = { checked: 0, late: 0, leave: 0, absent: 0 };
    atts.forEach(function (a) {
      var s = String(a.status || '');
      if (s === 'checked') n.checked++;
      else if (s === 'late' || s === 'early_leave') n.late++;
      else if (s === 'sick' || s === 'personal') n.leave++;
      else n.absent++;
    });
    var body = atts.length ? atts.map(attRow).join('') : '<div class="empty">还没有考勤记录</div>';
    showSheet('<div class="sh"><b>我的考勤记录</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note">已到 ' + n.checked + ' · 迟到/早退 ' + n.late +
      ' · 请假 ' + n.leave + ' · 旷课 ' + n.absent + ' · 按日期倒序</div>' +
      body);
    var c = $('#shClose'); if (c) c.onclick = hideSheet;
  }
  /** 成绩全量明细（底部弹层，2026-11-02）。
     sc() 拿到的本身就是服务端投影后的「本人成绩行」，班均/名次是
     服务端按全班算好再挂在自己行上的聚合值，看不到别人的分数。 */
  function openScoreSheet() {
    var sc = scores();
    var vis = S.vis || {};
    var body = sc.length ? sc.map(scoreRow).join('') : '<div class="empty">还没有发布的成绩</div>';
    showSheet('<div class="sh"><b>我的成绩</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note">共 ' + sc.length + ' 次 · 按日期倒序 · 只显示老师已发布的成绩</div>' +
      body);
    var c = $('#shClose'); if (c) c.onclick = hideSheet;
  }
  /** 全部作业明细（底部弹层，2026-11-02）。
     本班作业列表 + 我的提交状态（待交 / 已交 / 逾期），
     点任意一条进入作业详情弹层，与「今日」页的待交作业同一交互。 */
  function openHwAllSheet() {
    var list = homeworks();
    var n = { todo: 0, done: 0, late: 0 };
    list.forEach(function (h) {
      var st = hwState(h);
      if (st.done) n.done++; else if (st.late) n.late++; else n.todo++;
    });
    var body = list.length ? list.map(hwRow).join('') : '<div class="empty">老师还没有布置作业</div>';
    showSheet('<div class="sh"><b>我的作业</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note">共 ' + list.length + ' 项 · 待交 ' + n.todo +
      ' · 已交 ' + n.done + (n.late ? ' · 逾期 ' + n.late : '') +
      ' · 点一条查看详情</div>' + body);
    var c = $('#shClose'); if (c) c.onclick = hideSheet;
    /* 弹层里的行也要能点开详情（render() 只绑 #app 里的） */
    document.querySelectorAll('#sheet [data-hw]').forEach(function (el) {
      el.onclick = function () { openHw(el.getAttribute('data-hw')); };
    });
  }
  var REC_TEXT = { submitted: '已提交', checked: '已批改', done: '已完成', practice: '已练习' };
  /** 提交状态一句话（带「待批改」中间态提示） */
  function stTxt(s) {
    if (s === 'submitted') return '已提交 · 等老师批改';
    return REC_TEXT[s] || '已提交';
  }

  function fmtNum(v) {
    var n = Number(v);
    if (!isFinite(n)) return '—';
    return Math.round(n * 10) / 10;
  }
  function greet() {
    var h = new Date().getHours();
    return h < 6 ? '凌晨好' : h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
  }

  /* （课件库弹层 openCwSheet 已随 v3.0.0 移除：资源展示与预览
     统一收敛到「课程」页，避免同一份资料两个入口。） */

  /* ---------------- 壳与路由（v3.0.0：三页签 今日 / 作业 / 课程） ----------------
     概览、成绩、考勤、积分等「关于我」的内容全部收进左下角账号卡弹出的
     个人信息面板（#suPop）；课程资源独立成页，在线预览不走下载。 */
  /* ---------------- 导航图标（2.0.63） ----------------
     牛哥 2026-11-21：改成教师端同款拟物风格——彩色 SVG 线条放在带高光的圆形
     小底座上，选中时底座换主色、线条转白；每类功能一个固定颜色，一眼能认。
     沿用原来的图形：网页仍是浏览器窗口 + 尖括号，不是调色盘。 */
  var ICON = {
    /* 今日：太阳 */
    today: '<circle cx="12" cy="12" r="4.1"/><path d="M12 2.9v2.1M12 19v2.1'
      + 'M2.9 12h2.1M19 12h2.1M5.5 5.5l1.5 1.5M17 17l1.5 1.5M18.5 5.5L17 7'
      + 'M7 17l-1.5 1.5"/>',
    /* 作业：带夹子的清单 */
    homework: '<path d="M8.6 5.3H6.5a1.9 1.9 0 0 0-1.9 1.9v11.9a1.9 1.9 0 0 0 1.9 1.9'
      + 'h11a1.9 1.9 0 0 0 1.9-1.9V7.2a1.9 1.9 0 0 0-1.9-1.9h-2.1"/>'
      + '<rect x="8.6" y="2.7" width="6.8" height="4.5" rx="1.6"/>'
      + '<path d="M8.7 12.4h6.6M8.7 16.1h4.3"/>',
    /* 资料：文件夹 */
    courses: '<path d="M3.4 7.6a2.1 2.1 0 0 1 2.1-2.1h3l1.9 2.1h7.9a2.1 2.1 0 0 1 2.1 2.1'
      + 'v7.2a2.1 2.1 0 0 1-2.1 2.1H5.5a2.1 2.1 0 0 1-2.1-2.1z"/>',
    /* 测验：卷子 + 对勾 */
    quiz: '<path d="M6.3 3.5h8.2l3.8 3.8v13a.9.9 0 0 1-.9.9H6.3a.9.9 0 0 1-.9-.9'
      + 'V4.4a.9.9 0 0 1 .9-.9z"/><path d="M14.3 3.6v3.8h3.9"/>'
      + '<path d="M8.7 13.5l1.8 1.8 3.5-3.9"/>',
    /* 座位：一把椅子 */
    seat: '<path d="M7.4 11V7a2.6 2.6 0 0 1 2.6-2.6h4A2.6 2.6 0 0 1 16.6 7v4"/>'
      + '<rect x="5.2" y="11" width="13.6" height="4.8" rx="1.8"/>'
      + '<path d="M7.6 15.8v4M16.4 15.8v4"/>',
    /* 打字：键盘 */
    typing: '<rect x="2.6" y="6.6" width="18.8" height="10.8" rx="2.4"/>'
      + '<path d="M6.2 10.2h.01M9.4 10.2h.01M12.6 10.2h.01M15.8 10.2h.01M19 10.2h.01'
      + 'M6.2 13.4h.01M19 13.4h.01M9.6 13.4h4.8"/>',
    /* 网页：浏览器窗口 + 代码尖括号 */
    design: '<rect x="2.6" y="4.2" width="18.8" height="15.6" rx="2.6"/>'
      + '<path d="M2.6 8.9h18.8"/>'
      + '<path d="M9.6 12.6l-1.9 2.2 1.9 2.2M14.4 12.6l1.9 2.2-1.9 2.2"/>',
    /* 像素：四格，右下是实心（示意「涂满一格」） */
    pixel: '<rect x="3.4" y="3.4" width="7.2" height="7.2" rx="1.5"/>'
      + '<rect x="13.4" y="3.4" width="7.2" height="7.2" rx="1.5"/>'
      + '<rect x="3.4" y="13.4" width="7.2" height="7.2" rx="1.5"/>'
      + '<path d="M13.4 13.4h7.2v7.2h-7.2z" fill="currentColor" stroke="none"/>',
    /* 截图：相机 */
    shot: '<path d="M3.4 8.6a2.1 2.1 0 0 1 2.1-2.1h2.3l1.4-2.1h5.6l1.4 2.1h2.3a2.1 2.1 0 0 1 2.1 2.1v8.8a2.1 2.1 0 0 1-2.1 2.1H5.5a2.1 2.1 0 0 1-2.1-2.1z"/>'
      + '<circle cx="12" cy="13" r="3.7"/>'
  };
  /* 与教师端 ICON_C 同一套配色：[底色, 线条, 选中底色, 选中线条] */
  var ICON_C = {
    today:    ['#FFF6DC', '#B5741B', '#B5741B', '#FFFFFF'],
    homework: ['#FFF4E0', '#8A5A00', '#8A5A00', '#FFFFFF'],
    courses:  ['#E4F7E9', '#1D7A4C', '#1D7A4C', '#FFFFFF'],
    quiz:     ['#EEEDFE', '#534AB7', '#534AB7', '#FFFFFF'],
    seat:     ['#F0EFE9', '#5F6E68', '#4A5751', '#FFFFFF'],
    typing:   ['#E1F5EE', '#0F6E56', '#0F6E56', '#FFFFFF'],
    design:   ['#E3F4F1', '#0F766E', '#0F766E', '#FFFFFF'],
    pixel:    ['#FDEAF1', '#B03A62', '#B03A62', '#FFFFFF'],
    shot:     ['#E8F0FE', '#2B6CB0', '#2B6CB0', '#FFFFFF']
  };
  /** 教师端同款图标底座：平时彩色底 + 彩色线条，选中时底色加深、线条转白 */
  function navIcon(id, on) {
    var c = ICON_C[id] || ICON_C.today;
    var line = on ? c[3] : c[1];
    return '<span class="side-ico" style="background:' + (on ? c[2] : c[0]) + '">' +
      '<svg class="ico" viewBox="0 0 24 24" width="15" height="15" fill="none" ' +
      'stroke="' + line + '" stroke-width="1.8" stroke-linecap="round" ' +
      'stroke-linejoin="round" aria-hidden="true" focusable="false">' +
      (ICON[id] || '') + '</svg></span>';
  }
  var NAV = [
    { id: 'today', name: '今日' },
    { id: 'homework', name: '作业', flag: 'homework' },
    /* 2026-11-04 牛哥定版：原「科目」改为「资料」——只列老师下发的资料，
       不再显示课表里的科目（课表那套对学生不再有意义）。 */
    { id: 'courses', name: '资料' },
    /* 测验/检测（2.0.38）：按登录会话的班级自动匹配老师下发的试卷 */
    { id: 'quiz', name: '测验' },
    /* 座位表（2.0.74）：牛哥要求侧栏不再单列座位页签，
       改从「我的 → 座位表」入口进（suPop 的 mSeat 保留），让侧栏只留功能页。 */
    /* 打字冒险岛：老师开放后显示，站内 iframe 内嵌（2026-11-02） */
    { id: 'typing', name: '打字' },
    /* H5 网页（2.0.40 定名，原「设计」）：老师开放 + 学生自己添加后出现；
       点开在本页框架内打开设计器，不跳新标签页。 */
    { id: 'design', name: '网页' },
    /* 像素画（2.0.47）：老师开放即出现，点开在站内 iframe 里画，不跳新标签 */
    { id: 'pixel', name: '像素' },
    /* 截图工具（2.1.6）：站内 iframe 内嵌，抓屏 → 框选 → 批注 → 存本地 */
    { id: 'shot', name: '截图' }
  ];
  var VIEWS = ['today', 'homework', 'courses', 'quiz', 'seat', 'typing', 'design', 'pixel', 'shot'];
  var TITLES = {
    today: ['今日', '签到 · 课堂学习单 · 要交的作业'],
    homework: ['我的作业', '全部作业与提交状态 · 点一条查看详情'],
    courses: ['我的资料', '老师下发的资料 · 点一下直接打开，不用下载'],
    quiz: ['我的测验', '老师下发给本班的检测 · 点开作答，按设置显示答案与分数'],
    seat: ['我的座位', '座位表 · 讲台固定在上方 · 高亮你自己的位置'],
    typing: ['打字冒险岛', '闯关练打字 · 赢星星 · 孵宠物 · 上排行榜'],
    design: ['我的网页', '左边写 HTML/CSS · 右边实时预览 · 点一次提交就交给老师'],
    pixel: ['我的像素画', '一格一格涂 · 边画边存 · 画好一键交给老师'],
    shot: ['截图工具', '截取任意画面 · 拖动框选 · 批注修改 · 保存到本地文件夹']
  };

  function pendingCount() { return todoHomeworks().length; }
  function badgeFor(id) {
    if (id === 'today' || id === 'homework') {
      var n = pendingCount();
      if (n > 0) return '<span class="bdg">' + n + '</span>';
    }
    return '';
  }

  function go(v) {
    /* 每次进测验页重新拉一次，老师刚发布的卷子不用等刷新页面 */
    if (v === 'quiz') S.quiz.list = null;
    /* 设计页 / 像素画：老师没开（或关了「允许使用」）就别给一块空白。
       2.0.49 起不再有「先去今日页添加」这一步 —— 页签只看老师开关。 */
    if (v === 'design' && !designReady()) {
      toast('老师还没开启「网页」，等开启后这里就能写');
      v = 'today';
    }
    if (v === 'pixel' && !pixelReady()) {
      toast('老师还没开启像素画，等开启后这里就能画了');
      v = 'today';
    }
    S.view = v;
    try { location.hash = v; } catch (e) {}
    render();
  }

  function sideHtml() {
    return '<div class="side-brand"><span class="side-dot"></span>班级工作台' +
      '<small>学生端</small></div>' +
      '<div class="side-nav" id="sideNav"></div>' +
      /* 左下角概览 / 账号卡（教师端 .side-user 同款）：点开上弹个人信息面板。
         v3.0.1：学号跟在姓名旁（页头不再重复显示姓名，这里就是唯一身份位） */
      '<div class="side-user">' +
      '<button class="su-btn" id="suBtn" aria-haspopup="true" aria-expanded="false">' +
      '<span class="av-slot" data-av="">' + avHtml('') + '</span>' +
      '<span class="su-txt"><b>' + esc(S.me.name) +
      '<em class="su-no">学号 ' + esc(S.me.student_no || '—') + '</em></b>' +
      '<i>' + esc(myClass().name || '') + '</i></span>' +
      '<span class="su-gear" aria-hidden="true">⚙</span></button>' +
      '</div>';
  }

  function render() {
    if (!S.me) return;
    var app = $('#app');
    if (!app) return;
    if (S.loading) {
      app.innerHTML = '<main class="main" style="padding-top:24px">' +
        '<div class="skel"></div><div class="skel"></div><div class="skel"></div></main>';
      return;
    }
    if (VIEWS.indexOf(S.view) < 0) S.view = 'today';
    /* 设计页没权限时不能停在那儿（老师刚关掉 / 还没添加） */
    if (S.view === 'design' && !designReady()) S.view = 'today';
    /* 像素画同理（2.0.47）：老师一关，学生不能停在一块进不去的页面 */
    if (S.view === 'pixel' && !pixelReady()) S.view = 'today';
    /* 打字页防「闪退」（2026-11-04 牛哥反馈）：打字页是 iframe 内嵌，
       render() 每次整体重画 #app 会把 iframe 连根拔掉重建——游戏整页重载、
       正在打的关直接跳回首页。SSE 推送 / 10 秒轮询 / 配置刷新都会触发
       render，时间一随机必然「不定时闪退」。正在打字视图里（iframe 还活着、
       开关也没被老师关掉）一律不再动 DOM，切走后自动恢复。 */
    /* 2.0.55：iframe 保护分支不能「什么都不做」地 return —— 老师开关功能时
       refreshTyping 会走到这里，直接 return 会让导航入口漏重绘，学生要「点一下」
       才看到入口增减（打字之所以正常，是因为关掉时 S.typingOn=false 不成立，
       能往下走到 paintNav）。所以这里在保住 iframe 的前提下单独重绘导航。 */
    if (S.view === 'typing' && S.typingOn &&
        document.querySelector('iframe[data-typing-embed]')) { paintNav(); return; }
    /* 设计页同理（2.0.39）：正在写代码时被 SSE 重绘 = 输入框重建、代码全丢，
       这比打字页「跳首页」还严重。iframe 在就绝不重建。 */
    if (S.view === 'design' && designReady() &&
        document.querySelector('iframe[data-design-embed]')) { paintNav(); return; }
    /* 像素画同理（2.0.47）：画到一半被重绘 = 画布重建、未存的笔画丢掉 */
    if (S.view === 'pixel' && pixelReady() &&
        document.querySelector('iframe[data-pixel-embed]')) { paintNav(); return; }
    /* 截图页同理（2.1.6）：框选/批注中若被 SSE 重绘，iframe 重建会前功尽弃 */
    if (S.view === 'shot' &&
        document.querySelector('iframe[data-shot-embed]')) { paintNav(); return; }
    /* 座位表走独立接口（整间教室的网格），首次进页面时拉一次；
       拿到数据后 loadSeatmap 自己再 render 一遍（2026-11-03）。 */
    if (S.view === 'seat' && !S.seatmap) loadSeatmap();
    var map = { today: vToday, homework: vHwAll, courses: vCourses,
                quiz: vQuiz, seat: vSeat, typing: vTyping, design: vDesign,
                pixel: vPixel, shot: vShot };
    var t = TITLES[S.view] || TITLES.today;
    app.innerHTML =
      '<aside class="sidebar">' + sideHtml() + '</aside>' +
      '<main class="main">' +
      '<div class="page-head"><div>' +
      '<div class="page-title">' + esc(t[0]) + '</div>' +
      '<div class="page-sub">' + esc(t[1]) + '</div></div>' +
      '</div>' +
      '<div class="offline" id="off"></div>' +
      map[S.view]() +
      '</main>' +
      /* 窄屏：侧栏隐藏，左下角悬浮头像键打开同一个面板 */
      '<button class="su-fab" id="suFab" aria-label="个人信息与设置">' +
      '<span class="av-slot" data-av="">' + avHtml('') + '</span></button>' +
      suPopHtml() +
      '<nav class="tabbar" id="tabbar"></nav>';
    bind();
    bindSu();
    paintNav();
    smapAutoSize();
    // 今日页里待交作业可直接点开
    document.querySelectorAll('[data-hw]').forEach(function (el) {
      el.onclick = function () { openHw(el.getAttribute('data-hw')); };
    });
    /* 打字 / 网页 / 像素三张入口卡已下线（2.0.49 牛哥要求）：
       学生端统一从左侧导航进入，不再在「今日」页重复摆一遍。
       导航项的显隐只看老师那边的开关，见 navShown()。 */
    /* 课堂活动入口（data-act / data-locked）已随按课表的旧模块下线
       （2026-11-04）：学生端不再有按课节的学习单入口，只留资料打开。 */
    // 资料页 / 今日「本课资料」：点开在线预览或一键打开
    document.querySelectorAll('[data-mat]').forEach(function (el) {
      el.onclick = function () { openMatSheet(el.getAttribute('data-mat')); };
    });
    /* 「加到网页」按钮已下线（2.0.41）：图片素材统一在「网页」页的图片抽屉里选，
       同一件事只留一个入口。资料行现在只有「打开 / 预览 / 下载」。 */
    // 测验/检测（2.0.38）：点「开始检测」在独立新标签里作答，原页不关
    document.querySelectorAll('[data-quiz]').forEach(function (el) {
      el.onclick = function () { quizOpen(el.getAttribute('data-quiz')); };
    });
    // 「我的」里已完成的活动可回看详情
    document.querySelectorAll('[data-rec]').forEach(function (el) {
      el.onclick = function () { openHw(el.getAttribute('data-rec')); };
    });
    // 我的积分明细（成绩 / 作业 / 考勤已升级为独立页签）
    document.querySelectorAll('[data-mor]').forEach(function (el) {
      el.onclick = function () { openMorSheet(); };
    });
  }

  /* 「网页」页签可用 = 老师开了服务 + 允许学生用。
     2.0.41 起不再看「学生自己添加过没有」—— 老师开启即自动出现。 */
  function designReady() { return !!(S.designOn && S.designAdd); }
  /* 像素画可用（2.0.47）：同样是「服务开着 + 允许学生用」两个都满足 */
  function pixelReady() { return !!(S.pixelOn && S.pixelAdd); }
  /** 导航项要不要显示（侧栏与底部 Tab 共用一套判断，别写两遍） */
  function navShown(n) {
    if (n.id === 'typing' && !S.typingOn) return false;
    if (n.id === 'design' && !designReady()) return false;
    if (n.id === 'pixel' && !pixelReady()) return false;
    if (n.flag && S.flags[n.flag] === false) return false;
    return true;
  }
  /* 2.0.49：「今日」入口卡下线后，进入这两个页面只走左侧导航（go('design') /
     go('pixel')），原来的 openDesign/openPixel 已无调用点，一并删掉。 */


  /* ---------------- 图片素材（2.0.41） ----------------
     老师在「资料」里下发的图片，学生在这里点「加到网页」，
     就写进 localStorage 的 wb-design-assets；编辑器（同源 iframe）
     读同一个键，所以点开「网页」→ 🖼 图片 立刻能看到。 */
  var WB_ASSET_KEY = 'wb-design-assets';
  function addDesignAsset(mid) {
    var m = cwPushed().filter(function (x) { return String(x.id) === String(mid); })[0];
    if (!m) { toast('这张图片已经失效了，请老师重新下发'); return; }
    var fid = String(m.src_id || '').trim();
    if (!fid) {
      var u = String(m.url || '');
      if (u.indexOf('/cw/') === 0) {
        try { fid = decodeURIComponent(u.slice(4).replace(/^\/+|\/+$/g, '')); } catch (e) { fid = u.slice(4); }
      }
    }
    if (!fid) { toast('这张图片没有可用地址，请老师重新下发一次'); return; }
    var list = [];
    try { list = JSON.parse(localStorage.getItem(WB_ASSET_KEY) || '[]') || []; } catch (e) { list = []; }
    if (!Array.isArray(list)) list = [];
    if (!list.some(function (x) { return String(x.id) === String(fid); })) {
      list.unshift({ id: fid, title: String(m.title || '图片'),
                     url: '/cw/' + encodeURI(fid), at: Date.now() });
      try { localStorage.setItem(WB_ASSET_KEY, JSON.stringify(list.slice(0, 80))); } catch (e) {}
    }
    toast('已加到「网页」素材：打开网页页签 → 🖼 图片 就能插进代码', 'ok');
    render();
  }

  function paintNav() {
    /* 侧边栏（宽屏） */
    var side = $('#sideNav');
    if (side) {
      side.innerHTML = NAV.map(function (n) {
        if (n.group) return '<div class="nav-gt">' + n.group + '</div>';
        if (!navShown(n)) return '';
        return '<div class="side-item' + (S.view === n.id ? ' active' : '') + '" data-go="' + n.id + '">' +
          navIcon(n.id, S.view === n.id) + n.name + badgeFor(n.id) + '</div>';
      }).join('');
    }
    /* 底部 Tab（窄屏） */
    var bar = $('#tabbar');
    if (bar) {
      bar.innerHTML = NAV.filter(function (n) {
          return n.id && navShown(n);
        })
        .map(function (n) {
          return '<button class="tab' + (S.view === n.id ? ' on' : '') + '" data-go="' + n.id + '">' +
            navIcon(n.id, S.view === n.id) + n.name + '</button>';
        }).join('');
    }
    document.querySelectorAll('[data-go]').forEach(function (b) {
      b.onclick = function () { go(b.getAttribute('data-go')); };
    });
  }

  /* ---------------- 视图：打字冒险岛（2026-11-02 站内内嵌） ----------------
     游戏本身是独立单页应用，放进 iframe 嵌在学生端页面框架内显示，
     不再跳出新窗口；?embed=1 让它收窄边距、隐藏页脚，视觉融入本页。
     打字时键盘焦点始终在 iframe 内部，不会干扰外层页面。 */
  function vTyping() {
    if (!S.typingOn) {
      return '<div class="card" style="text-align:center;padding:44px 20px">' +
        '<div style="font-size:40px">🚧</div>' +
        '<div style="font-weight:600;margin-top:8px">老师还没开放打字练习</div>' +
        '<div style="font-size:12.5px;color:var(--ink3);margin-top:4px">等老师打开开关，这里就是打字冒险岛</div></div>';
    }
    return '<iframe data-typing-embed="1" src="/typing/?embed=1" title="打字冒险岛" ' +
      'style="width:100%;height:calc(100vh - 150px);min-height:460px;' +
      'border:1.5px solid var(--line,#eee);border-radius:16px;background:var(--bg-surface,#F8F2E4);' +
      'box-shadow:0 6px 20px rgba(60,30,10,.06)"></iframe>';
  }

  /* ---------------- 视图：H5 网页（2.0.39；2.0.40 改名「网页」） ----------------
     设计器本身是独立单页（/design/student.html），放进 iframe 嵌在
     学生端框架里显示，不跳新标签页；?embed=1 去掉页面外边距。
     编辑、预览、提交都在 iframe 内部完成，外层只负责「给一块地方」。 */
  function vDesign() {
    if (!S.designOn) {
      return '<div class="card" style="text-align:center;padding:44px 20px">' +
        '<div style="font-size:40px">🚧</div>' +
        '<div style="font-weight:600;margin-top:8px">老师还没开启网页</div>' +
        '<div style="font-size:12.5px;color:var(--ink3);margin-top:4px">开启后这里就能写网页、交作品</div></div>';
    }
    if (!S.designAdd) {
      return '<div class="card">' + emptyBox('老师暂未开放',
        '这一页还没对同学开放，等老师在教师端点一下开关') + '</div>';
    }
    return '<iframe data-design-embed="1" src="/design/student.html?embed=1" title="网页" ' +
      'style="width:100%;height:calc(100vh - 150px);min-height:520px;' +
      'border:1.5px solid var(--line,#eee);border-radius:16px;background:var(--card,#FFFCF4);' +
      'box-shadow:0 6px 20px rgba(60,30,10,.06)"></iframe>';
  }

  /* ---------------- 视图：像素画（2.0.47） ----------------
     画板是独立单页（/pixel/student.html），放进 iframe 嵌在学生端框架里，
     不跳新标签页；?embed=1 去掉底部留白。绘画、保存、提交都在 iframe 里完成，
     外层只负责「给一块地方」——和网页设计完全同一套接法。 */
  function vPixel() {
    if (!S.pixelOn) {
      return '<div class="card" style="text-align:center;padding:44px 20px">' +
        '<div style="font-size:40px">🚧</div>' +
        '<div style="font-weight:600;margin-top:8px">老师还没开启像素画</div>' +
        '<div style="font-size:12.5px;color:var(--ink3);margin-top:4px">开启后这里就能一格一格画、交给老师</div></div>';
    }
    if (!S.pixelAdd) {
      return '<div class="card">' + emptyBox('老师暂未开放',
        '这一页还没对同学开放，等老师在教师端点一下开关') + '</div>';
    }
    return '<iframe data-pixel-embed="1" src="/pixel/student.html?embed=1" title="像素画" ' +
      'style="width:100%;height:calc(100vh - 150px);min-height:560px;' +
      'border:1.5px solid var(--line,#eee);border-radius:16px;background:var(--card,#FFFCF4);' +
      'box-shadow:0 6px 20px rgba(60,30,10,.06)"></iframe>';
  }

  /* 视图：截图工具（2.1.6）——站内 iframe 内嵌纯前端截图页，
     学生抓屏→框选→批注→存本地，不接提交链路（提交走学习单文件上传）。 */
  function vShot() {
    return '<iframe data-shot-embed="1" src="/shot/" title="截图工具" allow="fullscreen" ' +
      'style="width:100%;height:calc(100vh - 150px);min-height:560px;' +
      'border:1.5px solid var(--line,#eee);border-radius:16px;background:var(--bg-surface,#F8F2E4);' +
      'box-shadow:0 6px 20px rgba(60,30,10,.06)"></iframe>';
  }

  /* ---------------- 视图：座位表（2026-11-03 新增） ----------------
     整间教室的座位网格由服务端算好（/api/v2/student/seatmap），
     这里只负责「画」：讲台永远摆最上方；老师那边讲台在下方时，
     行序整体倒转，让最靠近讲台的那一排（老师口中的「第 1 排」）落到最上面。
     列序不动 —— 两端都按「第 1 列在最左」数，学生照号找位才不会左右反。 */
  function loadSeatmap() {
    if (S.seatmap && S.seatmap._loading) return;
    S.seatmap = { _loading: true };
    var redo = function () { if (S.view === 'seat' || S.view === 'today') render(); };
    api('/api/v2/student/seatmap').then(function (j) {
      S.seatmap = (j && j.ok) ? j : { ok: false, msg: (j && j.msg) || '读不到座位表' };
      redo();
    }).catch(function () {
      S.seatmap = { ok: false, msg: '连不上服务端，稍后再试' };
      redo();
    });
  }

  function vSeat() {
    var m = S.seatmap;
    if (!m || m._loading) {
      return '<div class="card"><div class="skel" style="height:180px"></div></div>';
    }
    if (!m.ok) {
      return '<div class="card">' + emptyBox('暂时读不到座位表', m.msg || '刷新页面再试一次') + '</div>';
    }
    var R = Number(m.rows) || 0, C = Number(m.cols) || 0;
    if (!R || !C || !m.seats || !m.seats.length) {
      return '<div class="card">' +
        emptyBox('老师还没有排座位', '排好之后，这里会显示整间教室的座位表，并高亮你自己的位置') +
        '</div>';
    }
    var flip = String(m.stage) === 'bottom';
    /* 数据行 → 屏幕第几行（也就是「第几排」） */
    function srow(r) { return flip ? R - Number(r) + 1 : Number(r); }
    /* 数据列 → 屏幕第几列（2026-11-04 牛哥反馈）：
       老师那张表讲台在下方，学生序号从最靠近讲台那一排「从左到右」排；
       学生站在自己的位置上往讲台看，左右是反的 —— 所以整张表要转 180°：
       行反 + 列反，屏幕上就变成「最靠讲台那排从右到左」按序号排。
       表头列号跟着一起镜像，学生看到的「第 N 列」还是老师嘴里的第 N 列。 */
    function scol(c) { return flip ? C - Number(c) + 1 : Number(c); }
    var map = {}, placed = 0;
    m.seats.forEach(function (s) {
      var r = Number(s.r), c = Number(s.c);
      if (!r || !c || r > R || c > C) return;
      map[srow(r) + '-' + scol(c)] = s;
      if (s.name) placed++;
    });
    var cells = '';
    /* 轴标注：第一行 = 左上角「排╲列」+ 各列号；之后每行开头 = 排号。
       无论讲台翻不翻转，屏幕上看到的排号就是「第几排」，与老师那张表一致。 */
    cells += '<div class="smap-ax corner"><i>排</i><i>╲</i><i>列</i></div>';
    for (var c0 = 1; c0 <= C; c0++)
      cells += '<div class="smap-ax axc">' + (flip ? C - c0 + 1 : c0) + '</div>';
    /* 手机上格子只有 37px 宽：列多时组条右侧只放「★ / 组员号」，
       组名才挤得下；格子宽（列少或宽屏）时与教师端一样写全「组长 / 组员 01」 */
    var cellW = (document.body.clientWidth - 64 - 20 - (C - 1) * 6) / C;
    var compact = cellW < 58;
    for (var r = 1; r <= R; r++) {
      cells += '<div class="smap-ax axr">' + r + '</div>';
      for (var c = 1; c <= C; c++) {
        var s = map[r + '-' + c];
        if (!s) { cells += '<div class="smap-cell empty"></div>'; continue; }
        if (!s.name) { cells += '<div class="smap-cell blank"></div>'; continue; }
        var g = s.g || null;
        var roleTxt = '';
        if (g) {
          if (g.lead) roleTxt = compact ? '★' : '组长';
          else {
            var mm = String(g.mem || '').replace(/[^\d]/g, '');
            roleTxt = compact ? (mm || '组') : ('组员 ' + (mm ? String(mm).padStart(2, '0') : ''));
          }
        }
        cells += '<div class="smap-cell' + (s.me ? ' me' : '') + '"' +
          (s.me ? ' title="这就是你的座位"' : '') +
          (g ? ' style="--gc:' + esc(g.color || '') + '"' : '') + '>' +
          (g ? '<span class="smap-g"><b class="gn-t">' + esc(g.name) + '</b>' +
              '<i class="' + (g.lead ? 'lead' : '') + '">' + esc(roleTxt) + '</i></span>' : '') +
          '<span class="smap-nm">' + esc(s.name) + '</span>' +
          '<span class="smap-no">' + esc(s.no || '') + '</span>' +
          (s.seq ? '<i class="smap-seq">' + Number(s.seq) + '</i>' : '') +
          (s.pos ? '<i class="smap-pos" title="班干部职务：' + esc(s.pos) + '">' +
            esc(s.pos) + '</i>' : '') +
          '</div>';
      }
    }
    var stageCn = { top: '上方', bottom: '下方', left: '左侧', right: '右侧' }[m.stage] || '上方';
    var turned = String(m.stage) !== 'top';
    var mine = m.me
      ? '<div class="smap-mine"><span class="pin">📍</span>你的座位：<b>第 ' +
        srow(m.me.r) + ' 排第 ' + Number(m.me.c) + ' 列</b></div>'
      : '<div class="smap-mine none"><span class="pin">📍</span>老师还没有把你排到座位上</div>';
    return '<div class="card smap-card">' +
      '<h3>座位表<span class="more">共 ' + R + ' 排 ' + C + ' 列</span></h3>' +
      '<div class="smap-stage">讲 台</div>' +
      (turned ? '<div class="smap-note">老师设置的讲台在' + stageCn +
        '，这里统一翻到上方显示：整张表转了 180°，所以最靠讲台那一排的座位号是从右往左数的（和老师那张表一致）</div>' : '') +
      '<div class="smap-scroll"><div class="smap-grid" style="--c:' + (C + 1) +
        ';--fs:13px">' +
        cells + '</div></div>' +
      '<div class="smap-note smap-axis-note">左侧数字是第几排 · 顶部数字是第几列' +
        ' · 格子左下角是座位号</div>' +
      mine +
      '<div class="smap-foot">' +
        '<span class="sk"><i class="me"></i>我的座位</span>' +
        '<span class="sk"><i></i>同学</span>' +
        '<span class="sk"><i class="blank"></i>空位</span>' +
        '<span class="sk"><i class="pos"></i>班干部</span>' +
        '<span class="sk">组条右上 <b class="smap-lg-lead">★</b> = 组长</span>' +
        '<span class="smap-cnt">已排 ' + placed + ' / ' + (Number(m.total) || placed) + ' 人</span>' +
      '</div>' +
      '</div>';
  }

  /* 座位卡字号自适应（2026-11-03 两轮调整）：
     第一轮：不再按「列数」定字号，改按「格子实际宽度」换算；
     第二轮（本次）：比例 0.115→0.15、夹取 10.5~14 → 12.5~17 ——
     老师反馈 PC 宽屏 8 列时还是看不清，直接放大一档；
     手机 8 列格子 37px 也能拿到 12.5px（原来只有 10.5px）。 */
  function smapAutoSize() {
    var grid = document.querySelector('.smap-grid');
    if (!grid) return;
    var cell = grid.querySelector('.smap-cell');
    if (!cell) return;
    var w = cell.getBoundingClientRect().width;
    if (!w) return;
    var fs = Math.max(12.5, Math.min(17, w * 0.15));
    grid.style.setProperty('--fs', fs.toFixed(1) + 'px');
  }
  window.addEventListener('resize', smapAutoSize);

  /* ---------------- 明细行 / 小工具（页面与弹层共用，v2.1.0） ---------------- */
  function statCard(num, label) {
    return '<div class="stat"><div class="stat-num">' + num + '</div>' +
      '<div class="stat-label">' + esc(label) + '</div></div>';
  }
  function emptyBox(title, desc) {
    return '<div class="empty"><div style="font-weight:600;margin-bottom:4px">' + esc(title) + '</div>' +
      (desc ? '<div style="font-size:12px">' + esc(desc) + '</div>' : '') + '</div>';
  }
  function scoreRow(s) {
    var full = Number(s.full_score) || 100;
    var v = Number(s.score);
    var pct = isFinite(v) ? (v / full) * 100 : null;
    var tagCls = pct == null ? 'mut' : (pct >= 85 ? 'done' : (pct >= 60 ? 'mut' : 'late'));
    var vis = S.vis || {};
    return '<div class="score"><div class="m"><b>' + esc(s.title || '考试') + '</b><span>' +
      esc(s.date || '') + (s._avg != null ? ' · 班均 ' + esc(s._avg) : '') +
      (vis.rank && s._rank ? ' · 第 ' + esc(s._rank) + ' 名' : '') + '</span></div>' +
      '<div class="v">' + esc(fmtNum(s.score)) + '<small>/' + esc(full) + '</small></div>' +
      '<span class="tag ' + tagCls + '">' + (pct == null ? '—' : Math.round(pct) + '%') + '</span></div>';
  }
  function hwRow(h) {
    var st = hwState(h);
    var txt, cls;
    if (st.done) {
      /* 提交后的状态一眼可辨：已提交=待老师批改，已批改/已完成=有结果 */
      txt = st.rec.status === 'submitted' ? '已提交 · 待批改' : (REC_TEXT[st.rec.status] || '已完成');
      cls = 'done';
    }
    else if (st.late) { txt = '逾期未交'; cls = 'late'; }
    else { txt = '待交'; cls = ''; }
    return '<div class="hw" data-hw="' + esc(h.id) + '"><div class="dot' + (st.done ? ' done' : '') + '"></div>' +
      '<div class="m"><b>' + esc(h.title || h.subject || '作业') + '</b>' +
      '<span>' + esc(h.subject || '') + (h.due ? ' · 截止 ' + esc(h.due) : '') +
      (st.rec && st.rec.note ? ' · ' + esc(st.rec.note) : '') + '</span></div>' +
      '<div class="tag ' + cls + '">' + txt + '</div></div>';
  }
  function attRow(a) {
    var ok = a.status === 'checked';
    return '<div class="hw"><div class="dot' + (ok ? ' done' : '') + '"></div>' +
      '<div class="m"><b>' + esc(a.date || '') + ' · ' + esc(periodNameBySeq(a.period || 1)) + '</b>' +
      '<span>' + esc(a.reason || '') + '</span></div>' +
      '<div class="tag ' + (ok ? 'done' : (String(a.status) === 'absent' ? 'late' : 'mut')) + '">' +
      esc(attLabel(a.status)) + '</div></div>';
  }

  /* ---------------- 视图：作业（v3.0.0 保留；成绩/考勤移入个人信息面板） ---------------- */
  function vHwAll() {
    var list = homeworks();
    var n = { todo: 0, done: 0, late: 0 };
    list.forEach(function (h) {
      var st = hwState(h);
      if (st.done) n.done++; else if (st.late) n.late++; else n.todo++;
    });
    var html = '<div class="stat-grid">' +
      statCard(list.length, '作业总数') + statCard(n.done, '已完成') +
      (n.todo ? statCard(n.todo, '待交') : '') +
      (n.late ? statCard(n.late, '逾期') : '') + '</div>';
    html += '<div class="card"><h3>作业列表<span class="more">点一条查看详情</span></h3>' +
      (list.length ? list.map(hwRow).join('')
        : emptyBox('老师还没有布置作业', '布置后这里会第一时间出现')) +
      '</div>';
    return html;
  }
  /* ---------------- 视图：资料（2026-11-04 改版） ----------------
     旧版按课表列科目，学生看到的是一堆空壳（语文/信息科技…）；
     现在改成「资料」：只列老师真正下发的资料，按**课程资料名称**分组
     （老师下发时不再选课节，直接取文件所属的课程文件夹名，两边名称必然一致）。
     文件仍走 /cw/<id> 闸门：服务端按「登录身份 + 本班 + 已下发」放行。 */
  /** 按课程资料名分组：[{name, mats:[...]}]，没有课程名的归到「其他资料」 */
  function matGroups() {
    var map = {}, order = [];
    cwPushed().forEach(function (m) {
      var k = String(m.lesson || m.subject || '').trim() || '其他资料';
      if (!map[k]) { map[k] = []; order.push(k); }
      map[k].push(m);
    });
    return order.map(function (k) { return { name: k, mats: map[k] }; });
  }
  /** 浏览器能不能直接预览：按文件名扩展判断（推送记录的 title 就是原始文件名） */
  var MAT_IMG = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'avif'];
  var MAT_AUDIO = ['mp3', 'wav', 'm4a', 'aac', 'ogg', 'flac'];
  var MAT_VIDEO = ['mp4', 'webm', 'ogv', 'mov', 'm4v', 'mkv'];
  var MAT_DOC = ['doc', 'docx', 'ppt', 'pptx', 'xls', 'xlsx'];   // Office：经 WPS 转 PDF 后预览
  var MAT_TEXT = ['txt', 'md', 'csv', 'json', 'xml', 'htm', 'html'];
  function matExt(m) {
    var t = String(m.title || m.url || '');
    var i = t.lastIndexOf('.');
    return i >= 0 ? t.slice(i + 1).toLowerCase() : '';
  }
  /** 文件预览类型：image / audio / video / pdf / doc / text / other */
  function matKind(m) {
    var e = matExt(m);
    if (MAT_IMG.indexOf(e) >= 0) return 'image';
    if (MAT_AUDIO.indexOf(e) >= 0) return 'audio';
    if (MAT_VIDEO.indexOf(e) >= 0) return 'video';
    if (e === 'pdf') return 'pdf';
    if (MAT_DOC.indexOf(e) >= 0) return 'doc';
    if (MAT_TEXT.indexOf(e) >= 0) return 'text';
    return 'other';
  }
  function matPreviewable(m) { return matKind(m) !== 'other'; }
  /** 网页类（html/htm）：一键打开时走 /g/mat/<id>，服务端把学生身份挂上去，
      页面自动识别「你是谁」，不用再填学号姓名 */
  function matIsWeb(m) {
    var e = matExt(m);
    return e === 'html' || e === 'htm';
  }
  /** .url 是 Windows 网页快捷方式（纯文本里写着 URL=…）：不能内嵌预览，
      服务端会直接跳到那个网址 */
  function matIsUrl(m) { return matExt(m) === 'url'; }
  /** 资料行（今日「本课资料」与「资料」页共用）
      命名统一（2026-11-05）：网页学习单（.html）和网页快捷方式（.url）走的是
      同一条链路 —— 都进 /g/mat/<资料id>，由服务端分流后在新标签页打开；
      原来一个叫「一键打开」一个叫「打开」，学生看不出差别，纯属两套叫法。
      现在按钮只按「打开方式」分两档：
        · 打开 = 离开本页 / 进新标签页（网页学习单、.url 快捷方式）
        · 预览 = 留在本页，弹层里直接看（图片 / 音视频 / PDF / Office / 文本）
      身份识别这类差别放进行内说明文字，不再占用按钮位。 */
  function resRow(m) {
    var web = matIsWeb(m), link = matIsUrl(m);
    var ok = m.url && matPreviewable(m);
    var txt = link ? '点一下直接打开网址（新标签页）'
      : (web ? '点一下直接打开 · 自动带上你的学号姓名'
        : (m.date ? esc(m.date) + ' 推送 · ' : '') + (ok ? '点开在本页预览' : (m.url ? '点开下载查看' : '老师已收回')));
    var tag = (web || link) ? '打开' : (ok ? '预览' : (m.url ? '下载' : '已失效'));
    /* 强调色只给「能直接打开」的那一档；本页预览用浅底，层级更清楚 */
    var cls = (web || link) ? ' hot' : (ok ? ' done' : ' mut');
    /* 2.0.41：这里原来有个「加到网页」按钮，已按牛哥要求撤掉 ——
       图片素材统一在「网页」功能页的图片抽屉里选，资料页只负责打开/预览。 */
    return '<div class="hw res-row" data-mat="' + esc(m.id) + '"><div class="dot done"></div>' +
      '<div class="m"><b>' + esc(m.title || '资源') + '</b>' +
      '<span>' + txt + '</span></div>' +
      '<div class="tag' + cls + '">' + tag + '</div></div>';
  }
  function vCourses() {
    var gs = matGroups();
    var html = '<div class="stat-grid">' +
      statCard(gs.length, '资料分组') + statCard(cwPushed().length, '资料文件') + '</div>';
    if (!gs.length) {
      return html + emptyBox('还没有资料', '老师在教师端「资料」里点「推送」后，这里就会出现');
    }
    html += '<div class="cols2">';
    gs.forEach(function (g) {
      html += '<div class="card course-card"><h3><span class="cc-name">' + esc(g.name) + '</span>' +
        '<span class="more">' + g.mats.length + ' 份资料</span></h3>' +
        g.mats.map(resRow).join('') + '</div>';
    });
    html += '</div>';
    return html;
  }
  /* ---------------- 视图：测验 / 检测（2.0.38） ----------------
     列表在站内、作答在独立页 /s/quiz.html（新标签）。这样学生答题时
     不会被 SSE / 10 秒轮询触发的 render() 整块重建 DOM 清空答案 ——
     打字冒险岛 P-026 那个「闪退」就是这么来的。
     试卷与可见班级全部由服务端按会话判定，前端不传 class_id。 */
  function vQuiz() {
    if (!S.quiz.list && !S.quiz.loading) {
      S.quiz.loading = true;
      api('/api/v2/quiz/student/list').then(function (j) {
        S.quiz.loading = false;
        S.quiz.list = j.ok ? j : { ok: false, publishes: [], msg: j.msg || '读取失败' };
        if (S.view === 'quiz') render();
      }).catch(function () {
        S.quiz.loading = false;
        S.quiz.list = { ok: false, publishes: [], msg: '连不上老师电脑，请确认在同一 Wi-Fi' };
        if (S.view === 'quiz') render();
      });
    }
    var d = S.quiz.list;
    var head = '<div class="card"><div class="note" style="margin:0">'
      + '这里只显示<b>老师下发给本班</b>的测验，班级按你的登录身份自动匹配。'
      + '开放与截止时间、还能不能重交，都以老师电脑上的时间为准。'
      + '</div></div>';
    if (!d) return head + '<div class="card"><div class="empty">正在读取测验列表…</div></div>';
    if (!d.ok) return head + '<div class="card"><div class="empty">' + esc(d.msg || '读取失败') + '</div></div>';
    var pubs = d.publishes || [];
    if (!pubs.length) return head + '<div class="card"><div class="empty">'
      + '本班暂时没有测验。老师发布后刷新这里就能看到。'
      + '<button class="btn" style="width:auto;margin-top:10px" onclick="quizRefreshList()">刷新列表</button></div></div>';
    var stName = { open: '进行中', before: '未开始', expired: '已截止', closed: '已关闭' };
    var rows = pubs.map(function (p) {
      var can = p.open && (p.allow_multiple ? (Number(p.attempts) < Number(p.max_attempts)) : !Number(p.attempts));
      var why = !p.open ? (stName[p.state] || '暂不可作答')
        : (!can ? (p.allow_multiple ? '已达提交上限' : '本次测验只能提交一次') : '');
      var last = p.last ? '<div class="note" style="margin-top:6px">上次提交：'
        + esc(String(p.last.submitted_at || '').slice(5, 16)) + '（第 ' + p.last.attempt + ' 次）'
        + (p.last.show_score && p.last.score != null ? ' · 得分 ' + p.last.score + ' / ' + p.last.auto_full : '')
        + (p.last.needs_grade ? ' · 主观题待老师批阅' : '') + '</div>' : '';
      return '<div class="card"><h3>' + esc(p.title) + ' <span class="more">' + esc(stName[p.state] || '') + '</span></h3>'
        + '<div class="note">' + esc(p.paper_title || '') + ' · '
        + (p.open_at ? '开放 ' + esc(p.open_at) : '不限开放时间')
        + (p.close_at ? ' · 截止 ' + esc(p.close_at) : '')
        + ' · 已交 ' + (p.attempts || 0) + ' 次</div>'
        + last
        + (can ? '<button class="btn" style="width:auto;margin-top:10px" data-quiz="' + esc(p.id) + '">开始检测 →</button>'
               : '<div class="note" style="margin-top:10px;color:var(--ink3)">' + esc(why) + '</div>')
        + '</div>';
    }).join('');
    return head + '<div class="cols2">' + rows + '</div>'
      + '<div class="card"><button class="btn" style="width:auto" onclick="quizRefreshList()">🔄 刷新列表</button></div>';
  }
  function quizRefreshList() { S.quiz.list = null; render(); }
  window.quizRefreshList = quizRefreshList;
  function quizOpen(id) {
    if (!id) return;
    openNewTab('/s/quiz.html?pub=' + encodeURIComponent(id));
  }
  window.quizOpen = quizOpen;

  /** 资源在线预览：按文件类型选最合适的浏览器内联方式（2026-11-02 重写）
      · 图片 → <img>   · 音频 → <audio>   · 视频 → <video>
      · PDF / 文本 / HTML → <iframe>（/cwpv 闸门原样输出）
      · Office（Word/Excel/PPT）→ <iframe> 指向 /cwpv，服务端把文档渲染成 HTML 内联
      所有预览都走 /cwpv/<id> 闸门（与 /cw 同权限，「下载原文件/在新窗口打开」
      仍指向原始 /cw/<id>）。 */
  function openMatSheet(id) {
    var m = cwPushed().filter(function (x) { return String(x.id) === String(id); })[0];
    if (!m || !m.url) { toast('这份资源暂时打不开，可能已被老师收回'); return; }
    /* 一键打开（2026-11-04）：/g/mat/<资料id> 由服务端校验收件人后，把
       「票 + 学号/姓名/班级」挂到地址上再跳转 —— 网页学习单里的登录浮层
       会被自动摘掉，直接显示「已识别：X 班 · 姓名」。
       .url（Windows 网页快捷方式）也在这一步解析出真实网址直接跳。
       ★ 网页 / .url 一律「点一下直接跳」，不再先弹预览弹窗再点第二次：
         学习单要立刻能答题，中间隔一层预览纯属多余步骤（牛哥 2026-11-04）。 */
    var openUrl = '/g/mat/' + encodeURIComponent(m.id);
    if (matIsUrl(m) || matIsWeb(m)) { openNewTab(openUrl); return; }
    var kind = matKind(m), e = matExt(m);
    var pv = String(m.url).replace('/cw/', '/cwpv/');   // 预览闸门（Office 由服务端渲染）
    var dl = m.url;                                      // 原始文件（下载 / 新窗口）
    var body;
    if (kind === 'image') {
      body = '<img class="mat-img" src="' + esc(pv) + '" alt="' + esc(m.title || '') + '">';
    } else if (kind === 'audio') {
      body = '<div class="mat-audio"><div class="note" style="margin:0 0 8px">音频可以直接播放，拖动进度条可以跳着听</div>' +
        '<audio class="mat-audio-el" src="' + esc(pv) + '" controls preload="metadata"></audio></div>';
    } else if (kind === 'video') {
      body = '<video class="mat-media" src="' + esc(pv) + '" controls preload="metadata" playsinline></video>';
    } else if (kind === 'doc') {
      body = '<div class="note" style="margin:10px 0 0">在线还原的版面（动画、母版装饰不显示；要原样效果可下载原文件）</div>' +
        '<iframe class="mat-frame" src="' + esc(pv) + '" title="' + esc(m.title || '资源') + '"></iframe>';
    } else if (kind === 'pdf' || kind === 'text') {
      body = '<iframe class="mat-frame" src="' + esc(pv) + '" title="' + esc(m.title || '资源') + '"></iframe>';
    } else {
      body = '<div class="empty" style="margin:10px 0">「' + esc(e ? e.toUpperCase() : '未知格式') +
        '」暂不支持在线预览，可点下面按钮下载到本机，用对应软件打开。</div>';
    }
    var isDoc = (kind === 'doc');
    var isOther = (kind === 'other');
    /* 2026-11-25：不支持预览的格式也要给真正的下载按钮——原来这里渲染成空字符串，
       文案却说「点下面按钮下载」，学生点了没反应。?download=1 让服务端发
       Content-Disposition: attachment，强制下载而不是内联打开。 */
    var mainBtn = '<a class="btn line" style="display:block;text-align:center;text-decoration:none;margin-top:12px" href="'
      + esc(isOther ? (dl + (dl.indexOf('?') >= 0 ? '&' : '?') + 'download=1') : dl)
      + '"' + (isOther ? ' download' : ' target="_blank" rel="noopener"')
      + '>' + (isOther || isDoc ? '下载原文件' : '在新窗口打开') + '</a>';
    showSheet('<div class="sh"><b>' + esc(m.title || '资源预览') + '</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note">' + (m.lesson || m.subject ? esc(m.lesson || m.subject) + ' · ' : '') +
      (m.date ? esc(m.date) + ' 推送 · ' : '') + (isOther ? '需要下载到本机打开' : '在线查看，不用下载') + '</div>' +
      body + mainBtn,
      isOther ? null : 'preview');
    var cl = $('#shClose'); if (cl) cl.onclick = hideSheet;
  }

  /* ---------------- 作业详情 / 提交 ---------------- */
  function openHw(id) {
    var h = homeworks().filter(function (x) { return String(x.id) === String(id); })[0];
    if (!h) return;
    var rec = recordOf(h);
    var done = rec && ['submitted', 'checked', 'done', 'practice'].indexOf(rec.status) >= 0;
    var html = '<div class="sh"><b>' + esc(h.title || h.subject || '作业') + '</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note">' + esc(h.subject || '') + (h.due ? ' · 截止 ' + esc(h.due) : '') + '</div>' +
      (h.note ? '<div style="font-size:13.6px;color:var(--ink2);margin:10px 0">' + esc(h.note) + '</div>' : '') +
      (done ? '<div class="card" style="margin:12px 0;box-shadow:none"><b style="color:var(--brand)">' +
        esc(stTxt(rec.status)) + '</b>' +
        '<div style="font-size:13px;color:var(--ink3);margin-top:4px">' +
        esc(rec.note || (rec.status === 'submitted'
          ? '老师已收到，批改后这里会显示结果'
          : '老师已收到你的作业')) +
        (rec.updated_at ? '<br>提交时间：' + esc(String(rec.updated_at).slice(0, 16)) : '') + '</div></div>'
        : '<label>给老师留言（选填）</label><textarea id="hwNote" rows="3" placeholder="例如：写在作业本第 12 页"></textarea>' +
          '<div style="height:12px"></div><button class="btn" id="hwSend">确认提交</button>');
    showSheet(html);
    var c = $('#shClose'); if (c) c.onclick = hideSheet;
    var s = $('#hwSend');
    if (s) s.onclick = function () {
      s.disabled = true; s.textContent = '提交中…';
      api('/api/v2/student/submission', {
        method: 'POST',
        body: JSON.stringify({ homework_id: h.id, status: 'submitted', note: ($('#hwNote') || {}).value || '' })
      }).then(function (j) {
        if (!j.ok) { toast(j.msg || '提交失败，请再试一次'); s.disabled = false; s.textContent = '确认提交'; return; }
        hideSheet();
        toast('提交成功！老师已收到，批改后状态会更新');
        return pull(S.rev, false).then(render);
      }).catch(function () {
        s.disabled = false; s.textContent = '确认提交';
        toast('网络不通，提交没有成功，请再试一次');
      });
    };
  }

  function showSheet(html, variant) {
    var mask = $('#mask'), sheet = $('#sheet');
    if (!sheet) return;
    // 清掉上一次拖拽 / 缩放留下的内联定位，回到 CSS 默认（居中 / 默认尺寸）
    sheet.style.transform = '';
    sheet.style.left = ''; sheet.style.top = '';
    sheet.style.right = ''; sheet.style.bottom = '';
    sheet.style.width = ''; sheet.style.height = '';
    sheet.style.maxHeight = '';
    sheet.className = 'sheet' + (variant === 'preview' ? ' sheet--preview' : '');
    sheet.innerHTML = html;
    mask.classList.add('on');
    sheet.classList.add('on');
    mask.onclick = hideSheet;
    bindSheetChrome();
  }
  function hideSheet() {
    $('#mask').classList.remove('on');
    $('#sheet').classList.remove('on');
  }

  /* ---------------- 弹层拖拽 / 缩放（2026-11-02） ----------------
     预览窗口支持鼠标与触摸：拖标题栏移动、拖右下角缩放。
     只在标题栏（.sh，排除关闭按钮 / 链接 / 输入）与缩放手柄上触发，
     不干扰弹层内正文与表单。 */
  function bindSheetChrome() {
    var sheet = $('#sheet');
    if (!sheet) return;
    var head = sheet.querySelector('.sh');
    if (head && !head._wbDrag) {
      head._wbDrag = true;
      head.addEventListener('pointerdown', function (ev) {
        if (ev.target.closest('.x,button,a,input,textarea,select')) return;
        if (ev.button != null && ev.button !== 0) return;
        ev.preventDefault();
        sheetDragStart(ev, sheet);
      });
    }
    // 缩放手柄只给预览弹层：普通弹层是 overflow:auto 的滚动容器，
    // 绝对定位的手柄会跟着内容滚走，反而碍事。
    if (!sheet.classList.contains('sheet--preview')) return;
    var rs = sheet.querySelector('.sheet-rs');
    if (!rs) {
      rs = document.createElement('div');
      rs.className = 'sheet-rs';
      rs.title = '拖动缩放窗口';
      sheet.appendChild(rs);
    }
    if (!rs._wbRs) {
      rs._wbRs = true;
      rs.addEventListener('pointerdown', function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        sheetResizeStart(ev, sheet);
      });
    }
  }
  function sheetDragStart(ev, sheet) {
    var rect = sheet.getBoundingClientRect();
    sheet.style.transform = 'none';
    sheet.style.left = rect.left + 'px';
    sheet.style.top = rect.top + 'px';
    sheet.style.right = 'auto';
    sheet.style.bottom = 'auto';
    sheet.style.maxHeight = 'none';
    sheet.style.width = rect.width + 'px';
    sheet.style.height = rect.height + 'px';
    var sx = ev.clientX, sy = ev.clientY, ox = rect.left, oy = rect.top;
    function move(e) {
      var nx = ox + (e.clientX - sx), ny = oy + (e.clientY - sy);
      nx = Math.max(6, Math.min(nx, window.innerWidth - rect.width - 6));
      ny = Math.max(6, Math.min(ny, window.innerHeight - rect.height - 6));
      sheet.style.left = nx + 'px';
      sheet.style.top = ny + 'px';
    }
    function up() {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }
  function sheetResizeStart(ev, sheet) {
    var rect = sheet.getBoundingClientRect();
    sheet.style.transform = 'none';
    sheet.style.left = rect.left + 'px';
    sheet.style.top = rect.top + 'px';
    sheet.style.maxHeight = 'none';
    sheet.style.width = rect.width + 'px';
    sheet.style.height = rect.height + 'px';
    var sx = ev.clientX, sy = ev.clientY, ow = rect.width, oh = rect.height;
    function move(e) {
      var nw = Math.max(320, ow + (e.clientX - sx));
      var nh = Math.max(240, oh + (e.clientY - sy));
      nw = Math.min(nw, window.innerWidth - rect.left - 6);
      nh = Math.min(nh, window.innerHeight - rect.top - 6);
      sheet.style.width = nw + 'px';
      sheet.style.height = nh + 'px';
    }
    function up() {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    }
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  /* ---------------- 个人中心（v2.3.0：左下角概览 / 预设头像 / 主题 / 改密 / 报告） ----------------
     与教师端同一套交互语言：左下角账号卡（.side-user/.su-btn，教师端同款）
     点击上弹个人信息面板（#suPop，教师端 .su-pop 同款出入场），主题切换带
     高光（themeFlash 同款）。窄屏侧栏隐藏时由左下角悬浮头像键打开同一面板。
     头像不支持上传，提供教师端同款的预设样式（文字 / emoji / 动态），
     只存本机 localStorage。2.0.64 修串号：旧版键名 wb-savatar 是全局的，
     机房同一台电脑换人登录就会看到上一位同学选的头像；现在按
     「教师账号 + 班级 + 学生 id」分键，每人各存各的。 */
  var AV_KEY_BASE = 'wb-savatar';     // 与教师端 wb-avatar 分开
  var MODE_KEY = 'wb-smode';     // 'light' / 'dark'（缺省浅色，与教师端 wb-mode 默认一致）
  /* 预设头像样式（教师端 avatarPickerHtml 同思路）：text=姓名首字，
     emoji=静态表情，anim=CSS 动画底色（v 值同时是样式类名） */
  var AV_PRESETS = [
    { kind: 'text', v: '', label: '默认' },
    { kind: 'emoji', v: '😊', label: '微笑' },
    { kind: 'emoji', v: '🚀', label: '火箭' },
    { kind: 'emoji', v: '🐱', label: '小猫' },
    { kind: 'emoji', v: '🌟', label: '星星' },
    { kind: 'emoji', v: '⚽', label: '足球' },
    { kind: 'emoji', v: '🎨', label: '画画' },
    { kind: 'anim', v: 'uav-aurora', label: '极光' },
    { kind: 'anim', v: 'uav-pulse', label: '心跳' },
    { kind: 'anim', v: 'uav-wave', label: '流彩' }
  ];

  /** 本机头像偏好按当前登录学生分键：同一个浏览器换账号不再串头像。
     身份三项都取服务端下发的身份，前端无法伪造到别人的键。 */
  function avKey() {
    var me = S.me || {};
    var who = [me.account || '', me.class_id || '', me.id || me.student_id || '']
      .join('|');
    return AV_KEY_BASE + ':' + who;
  }
  function avPref() {
    try { return JSON.parse(localStorage.getItem(avKey()) || 'null'); } catch (e) { return null; }
  }
  /** 渲染头像 span（cls: '' / sm / lg / xl）
      2026-11-24 修：服务端头像（创意宠物/像素画设的）必须优先显示。
      2.1.7 曾把本地预设提到服务端之前，导致「先选 emoji、再把创意宠物设为头像」
      时宠物头像被 emoji 盖住、看起来设了没用。选预设时 openAvatarSheet 已经
      调 /api/v2/avatar 清了服务端头像，所以「服务端还有 avatar」= 用户后来设了
      宠物/像素画头像，理应盖过本地预设。 */
  function avHtml(cls) {
    var p = avPref(), c = 'acct-av ' + (cls || ''), ch = esc(((S.me && S.me.name) || '学').slice(0, 1));
    var srv = (S.me && S.me.avatar) || '';
    if (srv) return '<span class="' + c + '"><img src="' + srv + '" alt="头像"></span>';
    if (p && p.kind === 'emoji' && p.v) {
      return '<span class="' + c + ' uav-emoji">' + esc(p.v) + '</span>';
    }
    if (p && p.kind === 'anim' && p.v) {
      return '<span class="' + c + ' ' + esc(p.v) + '">' + ch + '</span>';
    }
    if (p && p.kind === 'img' && p.v) {           // 兼容旧版本机上传数据
      return '<span class="' + c + '"><img src="' + p.v + '" alt="头像"></span>';
    }
    if (p && p.kind === 'text') return '<span class="' + c + '">' + ch + '</span>';
    return '<span class="' + c + '">' + ch + '</span>';
  }
  /** 头像变化后刷新页面上所有 .av-slot（页头胶囊 / 账号卡 / 面板 / 悬浮键），带弹跳进场 */
  function refreshAvatars() {
    document.querySelectorAll('.av-slot').forEach(function (slot) {
      slot.innerHTML = avHtml(slot.getAttribute('data-av') || '');
      var sp = slot.firstChild;
      if (sp) sp.classList.add('av-pop');
    });
  }

  function modeState() {
    /* 缺省浅色（与教师端一致）；旧版本存过的 '' / 异常值一律归一为 light */
    try { return localStorage.getItem(MODE_KEY) === 'dark' ? 'dark' : 'light'; } catch (e) { return 'light'; }
  }
  /* 护眼皮肤（2.0.41）：暖陶 warm（默认）/ 竹绿 green / 纸黄 paper / 雾蓝 blue。
     只换 CSS 变量，布局、组件、交互一律不动。 */
  var THEME_KEY = 'wb-stheme';
  var THEMES = [
    { k: 'warm', n: '暖陶', dot: '#B45B33', tip: '默认暖色，陶土橙主色' },
    { k: 'green', n: '竹绿', dot: '#4E7D5A', tip: '护眼绿，蓝光最低' },
    { k: 'paper', n: '纸黄', dot: '#8A6A32', tip: '纸张感，读长文最柔' },
    { k: 'blue', n: '雾蓝', dot: '#3F6E86', tip: '低饱和蓝，冷静不刺眼' }
  ];
  function themeState() {
    var v = '';
    try { v = localStorage.getItem(THEME_KEY) || ''; } catch (e) {}
    return THEMES.some(function (t) { return t.k === v; }) ? v : 'warm';
  }
  function applyMode() {
    var dark = modeState() === 'dark';
    var root = document.documentElement;
    root.setAttribute('data-mode', dark ? 'dark' : 'light');
    var th = themeState();
    if (th === 'warm') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', th);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      var bg = dark ? { warm: '#191210', green: '#101713', paper: '#17130D', blue: '#0D1417' }
                    : { warm: '#F8F2E4', green: '#EDF3EA', paper: '#F4EEDC', blue: '#EDF2F5' };
      meta.setAttribute('content', bg[th] || bg.warm);
    }
  }
  function setMode(m) {
    try { localStorage.setItem(MODE_KEY, m); } catch (e) {}
    /* 教师端同款：切换瞬间铺一层品牌色高光，过渡不生硬 */
    document.body.classList.add('theme-switching');
    setTimeout(function () { document.body.classList.remove('theme-switching'); }, 450);
    applyMode();
    notifyThemeEmbeds();
    paintModeBtns();
    paintThemeBtns();
    toast(m === 'dark' ? '已切换到夜间模式' : '已切换到浅色模式');
  }
  function setTheme(k) {
    try { localStorage.setItem(THEME_KEY, k); } catch (e) {}
    document.body.classList.add('theme-switching');
    setTimeout(function () { document.body.classList.remove('theme-switching'); }, 450);
    applyMode();
    notifyThemeEmbeds();
    paintThemeBtns();
    var t = THEMES.filter(function (x) { return x.k === k; })[0];
    toast('已切换主题：' + (t ? t.n : k));
  }
  function paintModeBtns() {
    var cur = modeState();
    /* 2.0.74：主题按钮现在在「主题和头像设置」弹层里，按全局选 */
    document.querySelectorAll('[data-mode]').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-mode') === cur);
    });
  }
  function paintThemeBtns() {
    var cur = themeState();
    document.querySelectorAll('[data-theme]').forEach(function (b) {
      b.classList.toggle('on', b.getAttribute('data-theme') === cur);
    });
  }

  /** 个人信息面板（教师端 .su-pop 同款）：概览摘要 + 详情 + 设置。
      v3.0.0：原「概览/成绩/考勤」页签的内容全部收进这个面板——
      头部是学习摘要（待交作业 / 已到课 / 课堂评价），下面是成绩、
      考勤、积分等明细弹层入口。fixed 定位停靠左下角。 */
  function suPopHtml() {
    var seat = mySeat();
    var sp = mySeatPos();
    var hwTodo = todoHomeworks().length;
    var atts = attMine(), ck = 0;
    atts.forEach(function (a) { if (a.status === 'checked' || a.status === 'present') ck++; });
    var moral = moralityMine(), mSum = 0;
    moral.forEach(function (r) { mSum += Number(r.score) || 0; });
    var sc = scores();
    /* 小组成员（v3.0.1）：名单由服务端投影算好下发，这里只负责展示；
       自己的名字加高亮，组长挂小标。没分组时不占版面。 */
    var grp = myGroup(), grpHtml = '';
    if (grp && grp.name) {
      var mems = grp._members || [];
      var chips = mems.map(function (m) {
        return '<span class="gmem-chip' + (m.name === S.me.name ? ' me' : '') + '">' +
          esc(m.name) + (m.role === '组长' ? '<em>组长</em>' : '') + '</span>';
      }).join('');
      grpHtml = '<div class="sup-group"><div class="sup-g-head">👥 ' + esc(grp.name) +
        '<span>' + mems.length + ' 人</span></div>' +
        '<div class="sup-g-chips">' +
        (chips || '<span class="gmem-empty">老师还没有安排组员</span>') +
        '</div></div>';
    }
    return '<div class="acct-pop" id="suPop" hidden>' +
      '<div class="sup-head"><span class="av-slot" data-av="lg">' + avHtml('lg') + '</span>' +
      '<div style="min-width:0"><b>' + esc(S.me.name) + '</b>' +
      '<i>' + esc(myClass().name || '') + ' · 学号 ' + esc(S.me.student_no || '—') + '</i></div></div>' +
      '<div class="sup-info">' +
      '<span>座位 <b>' + esc(sp ? (sp.r + ' 排 ' + sp.c + ' 列') : '未排座') + '</b></span>' +
      '<span>待交作业 <b>' + hwTodo + '</b></span>' +
      '<span>已到课 <b>' + ck + '</b>/' + atts.length + '</span>' +
      '<span>课堂评价 <b>' + (mSum > 0 ? '+' : '') + mSum + '</b></span></div>' +
      grpHtml +
      /* 2.0.75：「我的记录」折叠菜单，标题点一下展开/收起，默认折叠。
         2.1.8：排在「头像和主题」上方（牛哥指定顺序）。 */
      supSecHtml('records', '我的记录',
        (S.flags.scores !== false ? '<button type="button" class="sup-item" id="mScore">📊 我的成绩<span class="si-n">' + sc.length + ' 次</span></button>' : '') +
        '<button type="button" class="sup-item" id="mReport">📋 我的学习报告</button>' +
        (S.flags.morality !== false ? '<button type="button" class="sup-item" id="mMor">🧭 积分记录</button>' : '') +
        '<button type="button" class="sup-item" id="mAtt">🕗 考勤记录<span class="si-n">' + atts.length + ' 条</span></button>' +
        '<button type="button" class="sup-item" id="mSeat">🪑 座位表<span class="si-n">' +
          (sp ? ('第 ' + sp.r + ' 排 ' + sp.c + ' 列') : '未排座') + '</span></button>') +
      /* 2.1.8：头像和主题、修改密码提成常驻按钮——
         学生一点账号就能看到，不用再展开「设置」。设置分区整体删掉。 */
      '<div class="sup-live-items">' +
        '<button type="button" class="sup-item" id="mAvatar">🎨 头像和主题</button>' +
        '<button type="button" class="sup-item" id="mPwd">🔑 修改密码</button>' +
      '</div>' +
      /* 退出登录作为面板底部常驻按钮 */
      '<div class="sup-logout-wrap"><button type="button" class="sup-item danger" id="mLogout">退出登录</button></div>' +
      '</div>';
  }
  /* 2.0.75：「我的」面板分区折叠头 + 内容容器。
     状态存 localStorage（默认折叠）；点标题整块切换，不影响里面按钮。
     登录成功时会清掉本地记忆，保证每次进来都是折叠态。 */
  var SU_FOLD_KEY = 'wb-stu-sup-fold';
  function supFoldMap() {
    try {
      var v = JSON.parse(localStorage.getItem(SU_FOLD_KEY) || '{}');
      return (v && typeof v === 'object') ? v : {};
    } catch (e) { return {}; }
  }
  function supSecHtml(key, title, inner) {
    var open = supFoldMap()[key] === true;  // 默认折叠，仅显式展开过才打开
    return '<div class="sup-sec' + (open ? ' open' : '') + '" data-sec="' + key + '">' +
      '<button type="button" class="sup-sec-t" data-sec-toggle="' + key + '" ' +
        'aria-expanded="' + (open ? 'true' : 'false') + '">' + title +
        '<i class="sup-sec-ar"></i></button>' +
      '<div class="sup-sec-b"' + (open ? '' : ' hidden') + '>' + inner + '</div></div>';
  }
  /** 2.0.74：主题设置（明暗 + 护眼皮肤）HTML，从「我的」面板搬进
     「主题和头像设置」弹层复用；选择器不写死 #suPop，两个容器都能点亮。 */
  function themePickerHtml() {
    return '<div class="sup-grid">' +
      '<button type="button" class="sup-q" data-mode="light">☀ 浅色</button>' +
      '<button type="button" class="sup-q" data-mode="dark">🌙 夜间</button></div>' +
      '<div class="sup-theme" role="group" aria-label="护眼主题">' +
      THEMES.map(function (t) {
        return '<button type="button" class="sup-dot" data-theme="' + t.k + '" title="' + esc(t.tip) + '">'
          + '<i style="background:' + t.dot + '"></i>' + esc(t.n) + '</button>';
      }).join('') + '</div>';
  }
  function bindSu() {
    var pop = $('#suPop');
    if (!pop) return;
    /* 2.0.75：「我的」面板分区折叠——点标题切换，只动 DOM 不整面板重绘 */
    pop.querySelectorAll('[data-sec-toggle]').forEach(function (h) {
      h.onclick = function (ev) {
        ev.stopPropagation();
        var key = h.getAttribute('data-sec-toggle');
        var sec = h.closest ? h.closest('.sup-sec') : h.parentNode;
        var box = sec ? sec.querySelector('.sup-sec-b') : null;
        if (!box) return;
        var open = box.hidden;
        box.hidden = !open;
        sec.classList.toggle('open', open);
        h.setAttribute('aria-expanded', open ? 'true' : 'false');
        var m = supFoldMap();
        m[key] = open;
        try { localStorage.setItem(SU_FOLD_KEY, JSON.stringify(m)); } catch (e) {}
      };
    });
    /* 两个触发点：侧栏账号卡（桌面）+ 左下角悬浮键（窄屏） */
    ['#suBtn', '#suFab'].forEach(function (sel) {
      var btn = $(sel);
      if (!btn) return;
      btn.onclick = function (ev) {
        ev.stopPropagation();
        pop.hidden = !pop.hidden;
        btn.setAttribute('aria-expanded', pop.hidden ? 'false' : 'true');
        /* 2.0.74：主题按钮已搬进头像弹层，这里只需刷新头像显示 */
      };
    });
    /* 点面板与触发点之外的地方收起（只挂一次全局监听） */
    if (!bindSu._doc) {
      bindSu._doc = true;
      document.addEventListener('click', function (ev) {
        var p = $('#suPop');
        if (p && !p.hidden &&
            !p.contains(ev.target) &&
            !(ev.target.closest && ev.target.closest('#suBtn,#suFab'))) {
          p.hidden = true;
        }
      });
    }
    /* 2.0.74：主题按钮已搬进头像弹层，由 openAvatarSheet 自己绑定 */
    var ms = $('#mScore'); if (ms) ms.onclick = function () { pop.hidden = true; openScoreSheet(); };
    var mseat = $('#mSeat');
    if (mseat) mseat.onclick = function () { pop.hidden = true; go('seat'); };
    var ma = $('#mAtt'); if (ma) ma.onclick = function () { pop.hidden = true; openAttSheet(); };
    var mav = $('#mAvatar'); if (mav) mav.onclick = function () { pop.hidden = true; openAvatarSheet(); };
    var mr = $('#mReport'); if (mr) mr.onclick = function () { pop.hidden = true; openReportSheet(); };
    var mm = $('#mMor'); if (mm) mm.onclick = function () { pop.hidden = true; openMorSheet(); };
    var mp = $('#mPwd'); if (mp) mp.onclick = function () { pop.hidden = true; openPwdSheet(); };
    var ml = $('#mLogout');
    if (ml) ml.onclick = function () {
      pop.hidden = true;
      /* 退出登录会丢失当前页面状态，属于「需要二次确认」的操作 —— 用统一确认框 */
      var ask = (window.WBUI && WBUI.confirm)
        ? WBUI.confirm({
            title: '退出登录', tone: 'warn', confirmText: '退出登录',
            message: '退出后需要重新输入账号和密码才能进来，确定要退出吗？'
          })
        : Promise.resolve(true);
      ask.then(function (ok) {
        if (!ok) return;
        api('/api/v2/auth/logout', { method: 'POST', body: '{}' }).then(function () { location.reload(); });
      });
    };
  }

  /** 头像样式（教师端 avatarPicker 同款交互）：预设可选，点选即换，
      不支持上传；带实时预览（弹层里的大号头像就是最终效果）。 */
  function openAvatarSheet() {
    var p = avPref();
    var cur = p ? (p.kind + ':' + (p.v || '')) : 'text:';
    var opts = AV_PRESETS.map(function (o) {
      var on = (o.kind + ':' + (o.v || '')) === cur ? ' on' : '';
      var inner;
      if (o.kind === 'emoji') inner = '<span class="acct-av uav-emoji">' + esc(o.v) + '</span>';
      else if (o.kind === 'anim') inner = '<span class="acct-av ' + esc(o.v) + '">' +
        esc(((S.me && S.me.name) || '学').slice(0, 1)) + '</span>';
      else inner = '<span class="acct-av">' + esc(((S.me && S.me.name) || '学').slice(0, 1)) + '</span>';
      return '<button type="button" class="av-opt' + on + '" data-kind="' + o.kind +
        '" data-v="' + esc(o.v) + '">' + inner + '<i>' + esc(o.label) + '</i></button>';
    }).join('');
    showSheet('<div class="sh"><b>头像和主题</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      /* 2.0.74：主题设置（明暗 + 护眼皮肤）从「我的」面板搬进来 */
      '<div class="set-sub-t">外观主题</div>' + themePickerHtml() +
      '<div class="set-sub-sep"></div>' +
      '<div class="set-sub-t">头像样式</div>' +
      '<div class="av-preview"><span class="av-slot" data-av="xl" id="avBig">' + avHtml('xl') + '</span></div>' +
      '<div class="av-picker" id="avPicker" style="margin-top:10px">' + opts + '</div>');
    var cl = $('#shClose'); if (cl) cl.onclick = hideSheet;
    /* 主题按钮绑定 + 点亮当前选中态 */
    document.querySelectorAll('#sheet [data-mode]').forEach(function (b) {
      b.onclick = function () { setMode(b.getAttribute('data-mode')); };
    });
    document.querySelectorAll('#sheet [data-theme]').forEach(function (b) {
      b.onclick = function () { setTheme(b.getAttribute('data-theme')); };
    });
    paintModeBtns(); paintThemeBtns();
    /* 点选即生效 + 大图预览同步 + 选中态高亮（教师端 .av-opt.on 同款） */
    document.querySelectorAll('#avPicker .av-opt').forEach(function (b) {
      b.onclick = function () {
        var kind = b.getAttribute('data-kind'), v = b.getAttribute('data-v');
        var pref = kind === 'text' ? { kind: 'text', v: '' } : { kind: kind, v: v };
        try { localStorage.setItem(avKey(), JSON.stringify(pref)); } catch (e) { toast('保存失败，本机存储不可用'); return; }
        document.querySelectorAll('#avPicker .av-opt').forEach(function (x) { x.classList.remove('on'); });
        b.classList.add('on');
        /* 清掉服务端头像（宠物/像素画设的），否则刷新后又盖回预设。
           2026-11-24：本地先渲染；服务端清理失败要明确提示，否则用户以为换好了、
           一刷新又变回宠物头像（牛哥反馈的「改头像不起作用」）。 */
        if (S.me) S.me.avatar = '';
        refreshAvatars();
        api('/api/v2/avatar', { method: 'POST', body: JSON.stringify({ png: '' }) })
          .then(function (j) {
            if (!j || !j.ok) toast('头像已换，但服务器同步失败，刷新后可能变回原头像');
            else toast(kind === 'text' ? '已恢复默认文字头像' : '头像已更新');
          })
          .catch(function () { toast('头像已换，但服务器同步失败，刷新后可能变回原头像'); });
      };
    });
  }

  /** 修改密码（登录后的常规入口）：当前密码 / 新密码 / 确认新密码，
      三者校验规则一致，错误就地提示；首次登录改密仍走 showChangePwd 全屏流程。 */
  function openPwdSheet() {
    showSheet('<div class="sh"><b>修改密码</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note" style="margin-top:0">新密码至少 4 位，修改成功后下次登录请用新密码。</div>' +
      '<label>当前密码</label><input id="p0" type="password" autocomplete="current-password" placeholder="现在用的密码">' +
      '<label>新密码（至少 4 位）</label><input id="p1" type="password" autocomplete="new-password" placeholder="想好用什么新密码">' +
      '<label>确认新密码</label><input id="p2" type="password" autocomplete="new-password" placeholder="再输一遍新密码">' +
      '<div class="err" id="pErr"></div>' +
      '<button class="btn" id="pOk">保存新密码</button>');
    var cl = $('#shClose'); if (cl) cl.onclick = hideSheet;
    var err = $('#pErr'), ok = $('#pOk');
    function fail(msg) { err.textContent = msg; ok.disabled = false; ok.textContent = '保存新密码'; }
    ok.onclick = function () {
      err.textContent = '';
      var o = $('#p0').value, n1 = $('#p1').value, n2 = $('#p2').value;
      if (!o) { err.textContent = '请先输入当前密码'; return; }
      if (n1.length < 4) { err.textContent = '新密码至少 4 位'; return; }
      if (n1 === o) { err.textContent = '新密码不能和当前密码一样'; return; }
      if (n1 !== n2) { err.textContent = '两次输入的新密码不一致'; return; }
      ok.disabled = true; ok.textContent = '保存中…';
      api('/api/v2/student/password', {
        method: 'POST', body: JSON.stringify({ old: o, new: n1 })
      }).then(function (j) {
        if (j._status === 401) { fail(j.msg || '当前密码不正确，再想想'); return; }
        if (!j.ok) { fail(j.msg || '修改失败，请稍后再试'); return; }
        hideSheet();
        toast('密码已修改，下次登录请用新密码');
      }).catch(function () {
        fail('连不上服务器，请确认网络后再试');
      });
    };
  }

  /** 我的学习报告：把同步快照里「我自己的」数据汇总成一页只读报告 */
  function openReportSheet() {
    var list = homeworks(), doneN = 0, lateN = 0;
    list.forEach(function (h) {
      var st = hwState(h);
      if (st.done) doneN++; else if (st.late) lateN++;
    });
    var sc = scores(), pctSum = 0, pctN = 0, best = null;
    sc.forEach(function (s) {
      var full = Number(s.full_score) || 100, v = Number(s.score);
      if (isFinite(v) && full > 0) {
        pctSum += v / full * 100; pctN++;
        if (!best || v / full > Number(best.score) / (Number(best.full_score) || 100)) best = s;
      }
    });
    var atts = attMine(), ck = 0;
    atts.forEach(function (a) { if (a.status === 'checked' || a.status === 'present') ck++; });
    var moral = moralityMine(), mSum = 0;
    moral.forEach(function (r) { mSum += Number(r.score) || 0; });

    var hwRate = list.length ? Math.round(doneN / list.length * 100) : null;
    var avg = pctN ? Math.round(pctSum / pctN * 10) / 10 : null;
    /* 一句话点评：只依据本人数据，不与他人比较排名 */
    var words = !list.length && !sc.length
      ? '新学期刚开始，先在「今日」里签个到、看看今天的课程学习单吧！'
      : (lateN > 0 ? '作业要抓紧啦，别让逾期拖了后腿，赶一赶就上来了！'
        : hwRate !== null && hwRate >= 90
          ? '作业又快又齐' + (avg != null && avg >= 85 ? '，成绩也稳稳的，继续保持！' : '，继续保持这个节奏！')
          : '按自己的节奏一步一步来，每天进步一点点就很棒！');

    var stat = function (num, unit, label) {
      return '<div class="stat"><div class="stat-num">' + num + (unit ? '<small>' + unit + '</small>' : '') +
        '</div><div class="stat-label">' + label + '</div></div>';
    };
    var body =
      '<div class="stat-grid" style="margin-top:2px">' +
      stat(hwRate === null ? '—' : hwRate, '%', '作业完成率') +
      stat(avg === null ? '—' : avg, '', '平均得分率') +
      stat(atts.length ? ck + '/' + atts.length : '—', '', '出勤次数') +
      stat((mSum > 0 ? '+' : '') + mSum, '', '课堂评价') + '</div>' +
      '<div class="note" style="margin-top:0">' + words + '</div>' +
      (best ? '<div class="score"><div class="m"><b>最佳一次 · ' + esc(best.title || '考试') + '</b>' +
        '<span>' + esc(best.date || '') +
        (S.vis && S.vis.rank && best._rank ? ' · 第 ' + esc(best._rank) + ' 名' : '') +
        (best._avg != null ? ' · 班均 ' + esc(best._avg) : '') + '</span></div>' +
        '<div class="v">' + esc(fmtNum(best.score)) + '<small>/' + esc(Number(best.full_score) || 100) + '</small></div></div>' : '') +
      '<div class="hw stat"><div class="dot' + (lateN ? '' : ' done') + '"></div>' +
      '<div class="m"><b>作业台账</b><span>共 ' + list.length + ' 项 · 已交 ' + doneN +
      (lateN ? ' · 逾期 ' + lateN : ' · 无逾期') + '</span></div>' +
      '<div class="tag' + (lateN ? ' late' : (doneN ? ' done' : '')) + '">' +
      (list.length ? Math.round(doneN / list.length * 100) + '%' : '—') + '</div></div>' +
      '<div class="empty slim" style="font-size:11.5px">报告生成于 ' + todayStr() +
      ' · 数据来自老师发布的学习记录，仅本人可见</div>';
    showSheet('<div class="sh"><b>我的学习报告</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="page-sub" style="margin-bottom:10px">' + esc(S.me.name) + ' · ' +
      esc(myClass().name || '') + '</div>' + body);
    var cl = $('#shClose'); if (cl) cl.onclick = hideSheet;
  }

  function bind() {
    var ck = $('#ckBtn');
    if (ck) ck.onclick = function () {
      /* 二次闸门：按钮是按上一次服务端快照画的，分钟级轮询之间状态可能变。
         没有显式 can_checkin=true 就别发请求，免得学生点了又被服务端拒。 */
      if (!S.now || S.now.can_checkin !== true) {
        toast((S.now && S.now.checkin_disabled_reason) || '现在还不能签到');
        return;
      }
      ck.disabled = true; ck.textContent = '签到中…';
      api('/api/v2/student/checkin', { method: 'POST', body: '{}' }).then(function (j) {
        if (!j.ok) {
          ck.disabled = false; ck.textContent = '一键签到';
          toast(j.msg || '签到失败');
          return;
        }
        toast(j.already ? '本节课已经签过到了' : '签到成功');
        /* 签到响应里带着服务端认定的节次：立即用它校正本地显示，
           不等下一次 loadNow 轮询（避免签到后卡片还挂在旧的一节上） */
        if (j.period) {
          S.now = Object.assign({}, S.now || {},
            { period: Number(j.period) || S.now && S.now.period,
              label: j.period_label || (S.now && S.now.label) });
        }
        return pull(S.rev, false).then(render);
      }).catch(function () {
        ck.disabled = false; ck.textContent = '一键签到';
        toast('连不上老师电脑，请确认在同一 Wi-Fi');
      });
    };
  }

  /* ---------------- 登录 ---------------- */
  /* 被踢下线（2026-11-17）：单设备登录 / 老师重置密码后，服务端删掉旧会话，
     SSE 推 kick 事件 → 这里断开连接、回到登录页，并把原因写在登录框里。
     防抖：SSE 可能收到不止一次 kick，只处理第一回。 */
  function kickOut(reason) {
    if (S.kicked) return;
    S.kicked = true;
    try { if (S.es) { S.es.close(); S.es = null; } } catch (e) {}
    showLogin(reason || '你的账号已在别处登录');
  }

  function showLogin(msg) {
    document.body.innerHTML =
      '<div class="login"><div class="logo">班</div><h1>进入班级</h1>' +
      '<p>选班级、选自己的学号，再输密码。名单来自老师的工作台，不用手打。</p>' +
      '<label>班级</label><select id="cls"><option value="">加载中…</option></select>' +
      '<label>学号</label><select id="stu" disabled><option value="">请先选班级</option></select>' +
      '<label>姓名</label><input id="nm" readonly placeholder="选学号后自动带出">' +
      '<label>密码</label><input id="pwd" type="password" autocomplete="current-password" placeholder="老师给你的密码">' +
      '<div class="err" id="err">' + esc(msg || '') + '</div>' +
      '<button class="btn" id="go">进入</button>' +
      '<div class="empty" style="font-size:13px;padding-top:16px">' +
      '第一次登录用老师设的初始密码；进去后点左下角头像 →「修改密码」可更换。</div>' +
      /* 署名+备案号放卡片内部（.login 是 relative），窄屏不会跑出卡片边框 */
      FOOT_HTML +
      '</div>';

    var cls = $('#cls'), stu = $('#stu'), nm = $('#nm');
    function loadClasses() {
      if (!entryE()) {
        cls.innerHTML = '<option value="">需要老师发的新入口</option>';
        stu.disabled = true;
        $('#err').textContent = '请打开老师发的学生入口链接或二维码进入（旧入口已失效）';
        return;
      }
      api('/api/v2/public/classes?x=1&e=' + encodeURIComponent(entryE())).then(function (j) {
        if (!j.ok) {
          $('#err').textContent = j.msg || '名单加载失败，稍后再试';
          return;
        }
        if (!j.classes || !j.classes.length) {
          cls.innerHTML = '<option value="">还没有班级，等老师建班</option>';
          $('#err').textContent = '老师的班级还没建好，稍后再试';
          return;
        }
        cls.innerHTML = '<option value="">请选择班级</option>' + j.classes.map(function (c) {
          return '<option value="' + esc(c.id) + '">' + esc(c.name) + '</option>';
        }).join('');
        if (j.classes.length === 1) {
          cls.value = j.classes[0].id;
          loadStudents(j.classes[0].id);
        }
      }).catch(function () {
        $('#err').textContent = '连不上老师电脑，确认在同一 Wi-Fi';
      });
    }
    loadClasses();

    function loadStudents(cid) {
      stu.disabled = true;
      stu.innerHTML = '<option value="">加载中…</option>';
      api('/api/v2/public/students?class_id=' + encodeURIComponent(cid)
        + '&e=' + encodeURIComponent(entryE())).then(function (j) {
        if (!j.ok) {
          stu.innerHTML = '<option value="">' + esc(j.msg || '名单加载失败') + '</option>';
          $('#err').textContent = j.msg || '名单加载失败';
          return;
        }
        var list = (j.students || []);
        if (!list.length) {
          stu.innerHTML = '<option value="">这个班还没有学生</option>';
          return;
        }
        stu.innerHTML = '<option value="">请选择你的学号</option>' + list.map(function (x) {
          /* 2026-11-04 牛哥要求：下拉里只显示学号、不显示姓名（名单不互相可见）；
             选中后姓名自动匹配到下面只读的「姓名」框里。 */
          return '<option value="' + esc(x.id) + '" data-name="' + esc(x.name) + '">' +
            esc(x.no || '（无学号）') + '</option>';
        }).join('');
        stu.disabled = false;
      });
    }

    cls.onchange = function () {
      nm.value = '';
      if (cls.value) loadStudents(cls.value);
      else { stu.innerHTML = '<option value="">请先选班级</option>'; stu.disabled = true; }
    };
    stu.onchange = function () {
      var o = stu.options[stu.selectedIndex];
      nm.value = o ? (o.getAttribute('data-name') || '') : '';
    };

    var submit = function () {
      var err = $('#err');
      if (!cls.value) { err.textContent = '请选择班级'; return; }
      if (!stu.value) { err.textContent = '请选择你的学号'; return; }
      var pwd = $('#pwd').value;
      if (!pwd) { err.textContent = '请输入密码'; return; }
      var btn = $('#go');
      btn.disabled = true; btn.textContent = '正在进入…';
      api('/api/v2/auth/student', {
        method: 'POST',
        body: JSON.stringify({ class_id: cls.value, student_id: stu.value,
                               password: pwd, e: entryE() })
      }).then(function (j) {
        if (!j.ok) {
          err.textContent = j.msg || '进入失败';
          btn.disabled = false; btn.textContent = '进入';
          return;
        }
        /* 2.0.75：每次登录都从折叠态开始，清掉上一人/上次手动展开的记忆 */
        try { localStorage.removeItem(SU_FOLD_KEY); } catch (e) {}
        if (j.must_change) location.href = sUrl('setpwd=1');
        /* 2.1.8：用初始密码登录且未被强制改密时，带标记进去弹「建议改密码」。 */
        else if (j.suggest_pwd) location.href = sUrl('suggestpwd=1');
        else location.reload();
      }).catch(function () {
        err.textContent = '连不上老师电脑，确认在同一 Wi-Fi';
        btn.disabled = false; btn.textContent = '进入';
      });
    };
    $('#go').onclick = submit;
    $('#pwd').addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(); });
  }

  function showChangePwd(isFirst) {
    document.body.innerHTML =
      '<div class="login"><div class="logo">班</div><h1>设置新密码</h1>' +
      '<p>' + (isFirst ? '第一次登录，请把初始密码换成只有你知道的。' : '改完下次用新密码登录。') + '</p>' +
      '<label>当前密码</label><input id="o" type="password" autocomplete="current-password">' +
      '<label>新密码（至少 4 位）</label><input id="n1" type="password" autocomplete="new-password">' +
      '<label>再输一遍</label><input id="n2" type="password" autocomplete="new-password">' +
      '<div class="err" id="err"></div>' +
      '<button class="btn" id="ok">保存并进入</button>' +
      (isFirst ? '' : '<div style="height:10px"></div><button class="btn line" id="back">返回</button>') +
      /* 署名+备案号放卡片内部，窄屏不越界 */
      FOOT_HTML +
      '</div>';
    $('#ok').onclick = function () {
      var o = $('#o').value, n1 = $('#n1').value, n2 = $('#n2').value;
      if (n1.length < 4) { $('#err').textContent = '新密码至少 4 位'; return; }
      if (n1 !== n2) { $('#err').textContent = '两次输入不一致'; return; }
      api('/api/v2/student/password', {
        method: 'POST', body: JSON.stringify({ old: o, new: n1 })
      }).then(function (j) {
        if (!j.ok) { $('#err').textContent = j.msg || '修改失败'; return; }
        toast('密码已更新');
        /* 2.0.75：改密后重新进入，同样回到折叠默认态 */
        try { localStorage.removeItem(SU_FOLD_KEY); } catch (e) {}
        location.href = sUrl();
      });
    };
    var b = $('#back');
    if (b) b.onclick = function () { location.href = sUrl(); };
  }

  /* 2.1.8：用初始密码登录后的「建议改密码」提示（可关闭，不强制）。
     与 showChangePwd 的强制改密页不同，这个只是提醒，学生可选「稍后再说」。
     URL 参数 ?suggestpwd=1 由登录响应带过来，弹过就用 replaceState 抹掉，
     避免刷新页面反复弹。 */
  function suggestChangePwd() {
    if (!/[?&]suggestpwd=1/.test(location.search)) return;
    try {
      history.replaceState(null, '', location.pathname + location.hash);
    } catch (e) {}
    /* 等主界面渲染稳定再弹，避免和今日卡/SSE 首次刷新抢 DOM */
    setTimeout(function () {
      showSheet(
        '<div class="sh"><b>建议修改密码</b>' +
        '<button type="button" class="x" data-act="later" aria-label="关闭">×</button></div>' +
        '<div class="note" style="font-size:14px;line-height:1.7;color:var(--text-secondary)">' +
        '你现在用的是老师发的初始密码，其他人知道学号就能登录你的账号。<br>' +
        '建议换成只有自己知道的密码，保护你的打字进度和积分。</div>' +
        '<div style="display:flex;gap:10px;margin-top:14px">' +
          '<button type="button" class="btn line" data-act="later" style="flex:1;width:auto">稍后再说</button>' +
          '<button type="button" class="btn" data-act="go" style="flex:1;width:auto">去修改</button>' +
        '</div>');
      var sheet = $('#sheet');
      if (!sheet) return;
      sheet.querySelectorAll('[data-act]').forEach(function (b) {
        b.onclick = function () {
          if (b.getAttribute('data-act') === 'go') {
            /* 2.1.9：直接用账号面板自带的修改密码弹层，原地切换内容，
               不再跳 ?setpwd=1 那个单独页面（学生改完不用重新走一遍）。 */
            try { openPwdSheet(); } catch (e) { location.href = sUrl('setpwd=1'); }
          } else {
            hideSheet();
          }
        };
      });
    }, 600);
  }
  window.suggestChangePwd = suggestChangePwd;

  /* ---------------- 启动 ---------------- */
  /* 2.1.7（#11）：30 分钟无操作自动退出。
     - 用户活动（鼠标/键盘/触摸/滚动）刷新计时；同源 iframe（打字/设计/像素/截图）内的活动也算，
       否则学生在打字页一直打字、父页面收不到事件会被误退。
     - 只在登录态计时（S.me 存在且未被踢）；用时间戳差值判断，浏览器后台节流 setInterval 也不影响。
     - 阈值可用 localStorage wb-idle-min（单位分钟）覆盖，默认 30，方便现场调试。 */
  function setupIdleLogout() {
    if (window.__wbIdleOn) return;
    window.__wbIdleOn = true;
    var last = Date.now();
    var limitMs = 30 * 60 * 1000;
    try { var mm = parseFloat(localStorage.getItem('wb-idle-min')); if (mm > 0) limitMs = mm * 60000; } catch (e) {}
    var evts = ['mousedown', 'keydown', 'touchstart', 'wheel', 'scroll', 'pointermove'];
    function touch() { last = Date.now(); }
    evts.forEach(function (ev) { document.addEventListener(ev, touch, { passive: true, capture: true }); });
    /* 同源 iframe 内的活动转发：load 捕获阶段给 contentWindow 挂监听（重建后会重新 load） */
    document.addEventListener('load', function (e) {
      var f = e.target;
      if (!f || f.tagName !== 'IFRAME') return;
      try {
        var d = f.contentDocument; if (!d) return;
        evts.forEach(function (ev) { d.addEventListener(ev, touch, { passive: true, capture: true }); });
      } catch (err) { /* 跨域忽略 */ }
    }, true);
    window.addEventListener('message', function (e) {
      if (e && e.data && (e.data.type === 'wb-active' || e.data.wbActive)) touch();
    });
    setInterval(function () {
      if (!S.me || S.kicked) { last = Date.now(); return; }   // 未登录 / 已被踢：不计时
      if (document.hidden) return;                             // 后台标签页跳过，回来按差值算
      if (Date.now() - last >= limitMs) {
        try { if (S.es) { S.es.close(); S.es = null; } } catch (e) {}
        api('/api/v2/auth/logout', { method: 'POST', body: '{}' }).then(function () {
          location.reload();
        }).catch(function () { location.reload(); });
      }
    }, 30000);
  }

  function boot() {
    applyMode();   // 主题模式在登录页就生效（浅色/夜间/跟随系统）
    /* 登录成功是 location.reload() 进来的：reload 后第一条请求偶发撞上
       浏览器回收旧连接（实测 ERR 连接重置，稍后手动重发即成功）。
       教室 Wi-Fi 也常有这类抖动，所以网络级失败先静默重试一次，
       仍失败才落到登录页并提示，避免一次抖动把学生挡在门外。 */
    var getSession = function () { return api('/api/v2/auth/session'); };
    getSession().catch(function () {
      return new Promise(function (res) { setTimeout(res, 800); }).then(getSession);
    }).then(function (j) {
      if (!j.ok || j.role !== 'student') {
        if (/[?&]setpwd=1/.test(location.search) || j.must_change) {
          // 未登录但带着改密标记：先正常登录，不在登录页卡住
        }
        showLogin(j._status === 401 ? '' : (j.msg || ''));
        return;
      }
      if (j.must_change || /[?&]setpwd=1/.test(location.search)) {
        showChangePwd(true);
        return;
      }
      S.me = j.student;
      S.me.id = j.student.student_id;   // 统一字段：recordOf 等处用 me.id 比对
      setupIdleLogout();                 // 2.1.7：登录后启动 30 分钟空闲自动退出
      S.flags = j.modules || {};
      S.typingOn = !!(j.typing && j.typing.enabled);
      applyDesignFlag(j.design);
      applyPixelFlag(j.pixel);
      S.rev = j.rev || 0;
      return pull(0, true).then(function () {
        document.body.innerHTML =
          '<div id="app"></div><div id="mask" class="sheet-mask"></div>' +
          '<div id="sheet" class="sheet"></div><div id="tabbar" class="tabbar"></div>' +
          /* 署名 + ICP 备案号：放 #app 外，render() 重画主区不会丢
             注意这里直接拼 FOOT_HTML，前面那个 + 已在上一段末尾——
             再写一个 + 会变成一元加号把字符串转成 NaN（2.1.4 踩过） */
          FOOT_HTML;
        var h = (location.hash || '').replace('#', '');
        S.view = VIEWS.indexOf(h) >= 0 ? h : 'today';
        S.loading = false;
        /* 座位表提前取一份：今日页「我的座位」卡也要用它的行列换算排号
           （讲台在下方时排号要倒过来数），不能等进了座位页才拉 */
        loadSeatmap();
        render();
        watch();
        loadNow();     // 服务端当前节次：签到卡节数以服务端为准
        suggestChangePwd();   // 2.1.8：初始密码登录 → 弹「建议改密码」（可关闭）
      });
    }).catch(function (e) {
      if (window.console && console.error) console.error('启动失败', e);
      showLogin('连不上老师电脑，确认手机和电脑在同一个 Wi-Fi');
    });
  }

  /* 全局兜底（2026-11-05）：学生端页面上任何 <a href> 都走 openNewTab ——
     网页链接统一在新标签打开，学生端页面始终留着，随时能切回来。
     覆盖：外链 URL、站内页面（学习单 / 课件 html / 资料入口）、弹窗里动态拼出来的
     链接，以及以后新加的任何链接——不用逐个记得写 target。
     跳过四类：① #锚点（本页内跳转）；② javascript:（脚本动作）；
     ③ 已显式写 target 的（它自己声明了打开方式，如「下载原文件」的 _blank）；
     ④ download 下载（下载不该占一个标签页）。
     用冒泡阶段而不是捕获：业务代码先跑，它若已 preventDefault（自定义
     预览器等），这里就不再插手，避免把别人的交互吃掉。 */
  document.addEventListener('click', function (e) {
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    if (e.defaultPrevented) return;
    if (a.target && a.target !== '_self') return;
    if (a.hasAttribute('download')) return;
    var href = a.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#' || /^javascript:/i.test(href)) return;
    e.preventDefault();
    openNewTab(a.href);
  });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
