/* 学生端 · 单页应用（无框架，约 500 行）
   数据全部来自 /api/v2/sync?scope=student —— 服务端已经裁剪过，
   这里拿不到别人的手机号、别人的分数，也拿不到没发布的考试。 */
(function () {
  'use strict';

  var S = { me: null, rev: 0, T: {}, flags: {}, vis: {}, view: 'today', es: null, loading: true };
  var WD = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];

  /* ---------------- 工具 ---------------- */
  function $(s) { return document.querySelector(s); }
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
  function todayStr() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' +
      String(d.getDate()).padStart(2, '0');
  }
  function toast(msg) {
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
  }

  function pull(since, snapshot) {
    return api('/api/v2/sync?since=' + since + '&scope=student').then(function (j) {
      if (j._status === 401) { location.reload(); return; }
      if (!j.ok) return;
      apply(j.tables, snapshot);
      S.rev = j.rev;
      S.flags = j.modules || S.flags;
      S.vis = j.visibility || S.vis;
      if (snapshot) S.loading = false;
    });
  }

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
        pull(S.rev, false).then(render);
      });
    } catch (e) {}
    setInterval(function () {
      if (!S.es || S.es.readyState === 2) {
        pull(S.rev, false).then(render);
      }
    }, 10000);
  }

  /* ---------------- 数据选择器 ---------------- */
  function T(name) { return S.T[name] || []; }
  function myClass() { return T('classes').filter(function (c) { return c.id === S.me.class_id; })[0] || {}; }
  function mySeat() { return T('seats').filter(function (s) { return s.student_id === S.me.id; })[0] || null; }
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
      return Number(l.day_of_week) === day && Number(l.class_id) === S.me.class_id;
    }).sort(function (a, b) {
      var pa = pm[a.period_id] || {}, pb = pm[b.period_id] || {};
      return (pa.seq || 0) - (pb.seq || 0);
    });
  }
  function homeworks() {
    return T('homeworks').filter(function (h) {
      return Number(h.class_id) === S.me.class_id;
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
  /* 教师登记的考勤状态（学生端只读展示，不能自己改） */
  var ATT_TEXT = { checked: '已签到', unchecked: '未签到', sick: '病假',
                   personal: '事假', absent: '旷课',
                   present: '出勤', late: '迟到', early_leave: '早退' };
  function attLabel(st) { return ATT_TEXT[st] || (st ? String(st) : '未签到'); }

  /* 学习资料：老师按班下发，学生只读 */
  function materials() {
    return T('materials').filter(function (m) {
      return Number(m.class_id) === S.me.class_id;
    }).sort(function (a, b) {
      return String(b.date || b.created_at || '').localeCompare(String(a.date || a.created_at || ''));
    });
  }

  /* ---------------- 课堂签到 ---------------- */
  function toMin(t) {
    var a = String(t || '').split(':');
    return (+a[0]) * 60 + (+a[1] || 0);
  }
  /** 现在第几节：找不到就取“已经过去的最近一节”，和教师端算法保持一致 */
  function curPeriod() {
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
    return past.length ? past[past.length - 1] : ps[0];
  }
  function periodSeq() {
    var p = curPeriod();
    return p ? (Number(p.seq) || Number(p.id) || 1) : 1;
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

  function vMaterials() {
    var list = materials();
    if (!S.flags.courseware) {
      return '<div class="hero"><div class="nm">资料</div></div>' +
        '<div class="card"><div class="empty">老师还没有开放学习资料</div></div>';
    }
    if (!list.length) {
      return '<div class="hero"><div class="nm">资料</div><div class="hi">' +
        esc(myClass().name || '') + '</div></div>' +
        '<div class="card"><div class="empty">老师还没有下发学习资料</div></div>';
    }
    return '<div class="hero"><div class="nm">资料</div><div class="hi">共 ' +
      list.length + ' 份 · 点开查看</div></div>' +
      '<div class="card">' + list.map(function (m) {
        var kind = m.kind === 'file' ? '文件' : m.kind === 'text' ? '文字' : '链接';
        return '<div class="hw" data-mat="' + esc(m.id) + '">' +
          '<div class="dot done"></div>' +
          '<div class="m"><b>' + esc(m.title || '学习资料') + '</b>' +
          '<span>' + esc(kind) + (m.subject ? ' · ' + esc(m.subject) : '') +
          (m.date ? ' · ' + esc(m.date) : '') + '</span></div>' +
          '<div class="tag">查看</div></div>';
      }).join('') + '</div>' +
      '<div class="note">资料由老师下发，只能在课堂上看，不能修改。</div>';
  }

  function openMaterial(id) {
    var m = materials().filter(function (x) { return String(x.id) === String(id); })[0];
    if (!m) return;
    var body = '';
    if (m.note) body += '<div style="font-size:13.6px;color:var(--ink2);margin:10px 0">' + esc(m.note) + '</div>';
    if (m.kind === 'text') {
      body += '<div class="card" style="margin:10px 0;box-shadow:none;white-space:pre-wrap">' +
        esc(m.content || '') + '</div>';
    } else if (m.url) {
      body += '<div style="height:12px"></div><a class="btn" style="display:block;text-align:center;' +
        'text-decoration:none;line-height:42px" href="' + esc(m.url) + '" target="_blank" rel="noopener">打开资料</a>';
    } else {
      body += '<div class="empty">这份资料没有可打开的地址</div>';
    }
    showSheet('<div class="sh"><b>' + esc(m.title || '学习资料') + '</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note">' + esc(m.subject || '') + (m.date ? ' · ' + esc(m.date) : '') + '</div>' + body);
    var c = $('#shClose'); if (c) c.onclick = hideSheet;
  }

  function myDutyToday() {
    var seat = mySeat();
    if (!seat) return [];
    var d = todayIdx() + 1;
    return T('dutys').filter(function (x) {
      return Number(x.class_id) === S.me.class_id && Number(x.day) === d &&
        (x.seat_ids || []).map(String).indexOf(String(seat.id)) >= 0;
    });
  }

  /* ---------------- 视图 ---------------- */
  function vToday() {
    var d = new Date();
    var pm = periodMap(), cm = courseMap();
    var ls = lessonsOf(todayIdx());
    var now = d.getHours() * 60 + d.getMinutes();
    var seat = mySeat();
    var duty = myDutyToday();
    var hwTodo = homeworks().filter(function (h) {
      var due = String(h.due || '');
      var rec = recordOf(h);
      var done = rec && (rec.status === 'submitted' || rec.status === 'checked' || rec.status === 'done');
      return !done && (!due || due >= todayStr());
    });

    var lessonHtml = ls.length ? ls.map(function (l) {
      var p = pm[l.period_id] || {}, c = cm[l.course_id] || {};
      var st = p.start_time || '', et = p.end_time || '';
      var toMin = function (t) { var a = String(t).split(':'); return (+a[0]) * 60 + (+a[1] || 0); };
      var isNow = st && et && now >= toMin(st) && now <= toMin(et);
      return '<div class="lesson' + (isNow ? ' now' : '') + '">' +
        '<div class="t"><b>' + esc(p.label || ('第' + (p.seq || '') + '节')) + '</b>' + esc(st) + '</div>' +
        '<div class="m"><b>' + esc(c.name || l.course_name || '课程') + '</b>' +
        '<span>' + esc(l.room_override || c.location || '') + '</span></div></div>';
    }).join('') : '<div class="empty">今天没有课，好好休息</div>';

    var hwHtml = hwTodo.length ? hwTodo.slice(0, 4).map(function (h) {
      return '<div class="hw" data-hw="' + esc(h.id) + '"><div class="dot"></div>' +
        '<div class="m"><b>' + esc(h.title || h.subject || '作业') + '</b>' +
        '<span>' + esc(h.subject || '') + (h.due ? ' · 截止 ' + esc(h.due) : '') + '</span></div>' +
        '<div class="tag">待交</div></div>';
    }).join('') : '<div class="empty">作业都交齐了</div>';

    /* 课堂签到卡：登录后一键签到，教师端座位表实时看到 */
    var ck = myCheckin();
    var p = curPeriod();
    var pLabel = p ? (p.label || ('第' + (p.seq || '') + '节')) : '本节课';
    var ckHtml = '<div class="card ckin' + (ck && ck.status === 'checked' ? ' on' : '') + '">' +
      '<div class="ck-l"><b>' + esc(pLabel) + '签到</b>' +
      '<span>' + (ck && ck.status === 'checked'
        ? ('已签到 ' + esc(ck.checked_at || ''))
        : (ck ? esc(attLabel(ck.status)) : '还没签到，点右边按钮')) + '</span></div>' +
      (ck && ck.status === 'checked'
        ? '<div class="ck-done">✓</div>'
        : '<button class="btn ck-b" id="ckBtn">一键签到</button>') +
      '</div>';

    return '<div class="hero"><div class="hi">' + esc(greet()) + '，' + esc(WD[todayIdx()]) + '</div>' +
      '<div class="nm">' + esc(S.me.name) + '<small>' + esc(myClass().name || '') + '</small></div>' +
      '<div class="meta">' +
      (seat ? '<span class="pill">座位 <b>' + esc(seat.row) + ' 排 ' + esc(seat.col) + ' 座</b></span>' : '') +
      '<span class="pill">今日 <b>' + ls.length + '</b> 节课</span>' +
      (hwTodo.length ? '<span class="pill">待交 <b>' + hwTodo.length + '</b> 项</span>' : '') +
      '</div></div>' +
      ckHtml +
      '<div class="offline" id="off"></div>' +
      '<div class="card"><h3>今日课表</h3>' + lessonHtml + '</div>' +
      (S.flags.homework ? '<div class="card"><h3>待交作业<span class="more">' + hwTodo.length + ' 项</span></h3>' + hwHtml + '</div>' : '') +
      (duty.length ? '<div class="card"><h3>今天我值日</h3>' + duty.map(function (x) {
        return '<div class="hw"><div class="dot done"></div><div class="m"><b>' + esc(x.area || '值日') + '</b>' +
          '<span>记得课后完成</span></div><div class="tag done">' + (x.done ? '已完成' : '待完成') + '</div></div>';
      }).join('') + '</div>' : '');
  }

  function vHomework() {
    var list = homeworks();
    if (!list.length) return '<div class="hero"><div class="nm">作业</div></div><div class="card"><div class="empty">老师还没有布置作业</div></div>';
    return '<div class="hero"><div class="nm">作业</div><div class="hi">共 ' + list.length + ' 项 · 点开可提交</div></div>' +
      '<div class="card">' + list.map(function (h) {
        var rec = recordOf(h);
        var done = rec && ['submitted', 'checked', 'done'].indexOf(rec.status) >= 0;
        var late = !done && h.due && String(h.due) < todayStr();
        return '<div class="hw" data-hw="' + esc(h.id) + '">' +
          '<div class="dot' + (done ? ' done' : '') + '"></div>' +
          '<div class="m"><b>' + esc(h.title || h.subject || '作业') + '</b>' +
          '<span>' + esc(h.subject || '') + (h.due ? ' · 截止 ' + esc(h.due) : '') +
          (rec && rec.note ? ' · 已提交' : '') + '</span></div>' +
          '<div class="tag' + (done ? ' done' : (late ? ' late' : '')) + '">' +
          (done ? '已交' : (late ? '已逾期' : '待交')) + '</div></div>';
      }).join('') + '</div>';
  }

  function vScores() {
    var list = scores();
    if (!S.flags.scores) return '<div class="hero"><div class="nm">成绩</div></div><div class="card"><div class="empty">老师还没有开放成绩查看</div></div>';
    if (!list.length) return '<div class="hero"><div class="nm">成绩</div></div><div class="card"><div class="empty">还没有发布的成绩</div></div>';
    return '<div class="hero"><div class="nm">成绩</div><div class="hi">只显示你自己的分数</div></div>' +
      '<div class="card">' + list.map(function (s) {
        var full = s.full_score || 100;
        return '<div class="score"><div class="m"><b>' + esc(s.title || '考试') + '</b>' +
          '<span>' + esc(s.date || '') +
          (s._avg != null ? ' · 班均 ' + s._avg : '') +
          (S.vis.rank && s._rank ? ' · 第 ' + s._rank + ' 名' : '') + '</span></div>' +
          '<div class="v">' + esc(fmtNum(s.score)) + '<small>/' + esc(full) + '</small></div></div>';
      }).join('') + '</div>' +
      '<div class="note">分数由老师录入并发布。如果和你知道的不一样，直接问老师，别在这里猜。</div>';
  }

  function vTimetable() {
    var pm = periodMap(), cm = courseMap();
    var out = '<div class="hero"><div class="nm">课表</div><div class="hi">' + esc(myClass().name || '') + '</div></div>';
    for (var d = 0; d < 7; d++) {
      var ls = lessonsOf(d);
      if (!ls.length && d > 4) continue;
      out += '<div class="card day"><div class="dh"><b>' + WD[d] + '</b>' +
        (d === todayIdx() ? '<span class="tdy">今天</span>' : '') + '</div>' +
        (ls.length ? ls.map(function (l) {
          var p = pm[l.period_id] || {}, c = cm[l.course_id] || {};
          return '<span class="chip"><i>' + esc(p.label || '') + '</i>' + esc(c.name || '课程') +
            (p.start_time ? ' ' + esc(p.start_time) : '') + '</span>';
        }).join('') : '<div class="empty" style="padding:6px 0;text-align:left">没有课</div>') +
        '</div>';
    }
    return out;
  }

  function vMe() {
    var seat = mySeat();
    return '<div class="hero"><div class="nm">我的</div></div>' +
      '<div class="card"><h3>身份</h3>' +
      row('姓名', S.me.name) + row('学号', S.me.student_no || '—') +
      row('班级', myClass().name || '—') +
      row('座位', seat ? (seat.row + ' 排 ' + seat.col + ' 座') : '未排座') +
      '</div>' +
      '<div class="card"><h3>服务</h3>' +
      row('数据版本', 'v' + S.rev) + row('连接', '同一局域网') +
      '<div style="height:12px"></div><button class="btn line" id="chpwd">修改密码</button>' +
      '<div style="height:8px"></div><button class="btn line" id="logout">退出登录</button></div>' +
      '<div class="empty" style="font-size:12px;padding:8px 0 24px">班级工作台 2.0 · 数据存在老师电脑上</div>';
  }

  function row(k, v) {
    return '<div style="display:flex;justify-content:space-between;padding:8px 0;' +
      'border-bottom:1px solid rgba(15,110,86,.07);font-size:14px">' +
      '<span style="color:var(--ink3)">' + esc(k) + '</span><b>' + esc(v == null ? '—' : v) + '</b></div>';
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

  /* ---------------- 壳与路由 ---------------- */
  var TABS = [
    { id: 'today', name: '今日', ic: '☀' },
    { id: 'homework', name: '作业', ic: '✎', flag: 'homework' },
    { id: 'materials', name: '资料', ic: '📚', flag: 'courseware' },
    { id: 'scores', name: '成绩', ic: '◎', flag: 'scores' },
    { id: 'timetable', name: '课表', ic: '▦' },
    { id: 'me', name: '我的', ic: '☺' }
  ];

  function go(v) {
    S.view = v;
    try { location.hash = v; } catch (e) {}
    render();
  }

  function render() {
    if (!S.me) return;
    var app = $('#app');
    if (!app) return;
    if (S.loading) {
      app.innerHTML = '<div class="wrap" style="padding-top:24px">' +
        '<div class="skel"></div><div class="skel"></div><div class="skel"></div></div>';
      return;
    }
    var map = { today: vToday, homework: vHomework, materials: vMaterials,
                scores: vScores, timetable: vTimetable, me: vMe };
    var fn = map[S.view] || vToday;
    app.innerHTML = '<div class="wrap">' + fn() + '</div>';
    bind();
    paintTabs();
    // 今日页里待交作业可直接点开
    document.querySelectorAll('[data-hw]').forEach(function (el) {
      el.onclick = function () { openHw(el.getAttribute('data-hw')); };
    });
    // 资料页里点开资料详情
    document.querySelectorAll('[data-mat]').forEach(function (el) {
      el.onclick = function () { openMaterial(el.getAttribute('data-mat')); };
    });
  }

  function paintTabs() {
    var box = $('#tabbar');
    if (!box) return;
    box.innerHTML = TABS.filter(function (t) { return !t.flag || S.flags[t.flag] !== false; })
      .map(function (t) {
        return '<button class="tab' + (S.view === t.id ? ' on' : '') + '" data-go="' + t.id + '">' +
          '<span class="ic">' + t.ic + '</span>' + t.name + '</button>';
      }).join('');
    box.querySelectorAll('[data-go]').forEach(function (b) {
      b.onclick = function () { go(b.getAttribute('data-go')); };
    });
  }

  /* ---------------- 作业详情 / 提交 ---------------- */
  function openHw(id) {
    var h = homeworks().filter(function (x) { return String(x.id) === String(id); })[0];
    if (!h) return;
    var rec = recordOf(h);
    var done = rec && ['submitted', 'checked', 'done'].indexOf(rec.status) >= 0;
    var html = '<div class="sh"><b>' + esc(h.title || h.subject || '作业') + '</b>' +
      '<button class="x" id="shClose">×</button></div>' +
      '<div class="note">' + esc(h.subject || '') + (h.due ? ' · 截止 ' + esc(h.due) : '') + '</div>' +
      (h.note ? '<div style="font-size:13.6px;color:var(--ink2);margin:10px 0">' + esc(h.note) + '</div>' : '') +
      (done ? '<div class="card" style="margin:12px 0;box-shadow:none"><b style="color:var(--brand)">已提交</b>' +
        '<div style="font-size:13px;color:var(--ink3);margin-top:4px">' +
        esc(rec.note || '老师已收到你的作业') + '</div></div>'
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
        if (!j.ok) { toast(j.msg || '提交失败'); s.disabled = false; s.textContent = '确认提交'; return; }
        hideSheet();
        toast('已提交，老师那边会看到');
        return pull(S.rev, false).then(render);
      });
    };
  }

  function showSheet(html) {
    var mask = $('#mask'), sheet = $('#sheet');
    sheet.innerHTML = html;
    mask.classList.add('on');
    sheet.classList.add('on');
    mask.onclick = hideSheet;
  }
  function hideSheet() {
    $('#mask').classList.remove('on');
    $('#sheet').classList.remove('on');
  }

  function bind() {
    var ck = $('#ckBtn');
    if (ck) ck.onclick = function () {
      ck.disabled = true; ck.textContent = '签到中…';
      api('/api/v2/student/checkin', { method: 'POST', body: '{}' }).then(function (j) {
        if (!j.ok) {
          ck.disabled = false; ck.textContent = '一键签到';
          toast(j.msg || '签到失败');
          return;
        }
        toast(j.already ? '本节课已经签过到了' : '签到成功');
        return pull(S.rev, false).then(render);
      }).catch(function () {
        ck.disabled = false; ck.textContent = '一键签到';
        toast('连不上老师电脑，请确认在同一 Wi-Fi');
      });
    };
    var cp = $('#chpwd');
    if (cp) cp.onclick = function () { showChangePwd(false); };
    var lo = $('#logout');
    if (lo) lo.onclick = function () {
      api('/api/v2/auth/logout', { method: 'POST', body: '{}' }).then(function () { location.reload(); });
    };
  }

  /* ---------------- 登录 ---------------- */
  function showLogin(msg) {
    document.body.innerHTML =
      '<div class="login"><div class="logo">班</div><h1>进入班级</h1>' +
      '<p>选班级、选自己的学号，再输密码。名单来自老师的工作台，不用手打。</p>' +
      '<label>班级</label><select id="cls"><option value="">加载中…</option></select>' +
      '<label>学号 / 姓名</label><select id="stu" disabled><option value="">请先选班级</option></select>' +
      '<label>姓名</label><input id="nm" readonly placeholder="选学号后自动带出">' +
      '<label>密码</label><input id="pwd" type="password" autocomplete="current-password" placeholder="老师给你的密码">' +
      '<div class="err" id="err">' + esc(msg || '') + '</div>' +
      '<button class="btn" id="go">进入</button>' +
      '<div class="empty" style="font-size:12px;padding-top:16px">' +
      '第一次登录用老师设的初始密码，进去后可以在「我的」里改。</div></div>';

    var cls = $('#cls'), stu = $('#stu'), nm = $('#nm');
    api('/api/v2/public/classes').then(function (j) {
      if (!j.ok || !j.classes || !j.classes.length) {
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

    function loadStudents(cid) {
      stu.disabled = true;
      stu.innerHTML = '<option value="">加载中…</option>';
      api('/api/v2/public/students?class_id=' + encodeURIComponent(cid)).then(function (j) {
        var list = (j.students || []);
        if (!list.length) {
          stu.innerHTML = '<option value="">这个班还没有学生</option>';
          return;
        }
        stu.innerHTML = '<option value="">请选择你的学号</option>' + list.map(function (x) {
          return '<option value="' + esc(x.id) + '" data-name="' + esc(x.name) + '">' +
            esc((x.no || '—') + ' · ' + x.name) + '</option>';
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
        body: JSON.stringify({ class_id: cls.value, student_id: stu.value, password: pwd })
      }).then(function (j) {
        if (!j.ok) {
          err.textContent = j.msg || '进入失败';
          btn.disabled = false; btn.textContent = '进入';
          return;
        }
        if (j.must_change) location.href = '/s/?setpwd=1';
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
        location.href = '/s/';
      });
    };
    var b = $('#back');
    if (b) b.onclick = function () { location.href = '/s/'; };
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    api('/api/v2/auth/session').then(function (j) {
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
      S.flags = j.modules || {};
      S.rev = j.rev || 0;
      return pull(0, true).then(function () {
        document.body.innerHTML =
          '<div id="app"></div><div id="mask" class="sheet-mask"></div>' +
          '<div id="sheet" class="sheet"></div><div id="tabbar" class="tabbar"></div>';
        var h = (location.hash || '').replace('#', '');
        S.view = ['today', 'homework', 'materials', 'scores', 'timetable', 'me'].indexOf(h) >= 0 ? h : 'today';
        S.loading = false;
        render();
        watch();
      });
    }).catch(function () {
      showLogin('连不上老师电脑，确认手机和电脑在同一个 Wi-Fi');
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
