/* ============================================================
   班级工作台 2.0 · 服务端适配器（netsync.js）
   ------------------------------------------------------------
   它做三件事，不改主程序的任何界面代码：
     1. 进页面前，把服务端快照灌进 DB（老师看到的永远是同一份数据，
        而不是“这台浏览器里恰好存了什么”）；
     2. 每 1.5 秒扫一遍本地各表，按行指纹找差异，增量推给服务端；
     3. 收到 SSE 的版本号就拉增量，合并后重绘当前页。

   为什么用“扫描”而不是在每个写入口埋钩子：
     index.html 里有 200 多处直接 push/splice 数组，逐个改必然漏。
     行级指纹扫描是 O(行数)，一个班几千行，实际开销不到 1ms。
   ============================================================ */
(function () {
  'use strict';

  var SERVER_TABLES = [
    'todos', 'classes', 'students', 'seats', 'periods', 'courses', 'lessons',
    'attendance', 'homeworks', 'homework_records', 'morality_items',
    'morality_records', 'scores', 'exams', 'exam_scores', 'groups', 'dutys',
    'duty_areas', 'duty_positions', 'course_progress', 'progress_cols',
    'seat_tpl', 'student_notes', 'quick_links', 'courseware_links', 'materials',
    // 学生提交的请假申请：老师端也要能读到、能改（控制台审批后本机跟着变）
    'leave_requests',
    // 导学单（2.0.4）：教师端发放 / 收回，学生端「今日」消费
    'guides'
  ];
  // 这两张表在本地没有 id（课表、考试成绩），服务端用业务键合成
  var NO_ID = { lessons: 1, exam_scores: 1 };

  var NetSync = {
    rev: 0,
    selfRev: 0,
    cfgRev: 0,       // 配置版本（模块开关 / 可见字段 / 账号密码）
    mirror: {},      // {table: {rid: hash}}
    es: null,
    busy: false,
    ready: false,
    lastError: '',
    pendingRender: false,
    errTimes: {},    // {table\0rid: 连续失败次数}，避免同一条坏行无限重推
    _scanTimer: null,
    _pushTimer: null,
    _pulling: false
  };

  function notifyUser(msg) {
    try {
      if (typeof toast === 'function') toast(msg);
      else console.warn('[netsync] ' + msg);
    } catch (e) { console.warn('[netsync] ' + msg); }
  }

  /* ---------------- 基础工具 ---------------- */
  function api(url, opt) {
    return fetch(url, Object.assign({
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }
    }, opt || {})).then(function (r) {
      if (r.status === 401) {
        location.href = '/teacher/login';
        throw new Error('unauthorized');
      }
      return r.json();
    });
  }

  function rowsOf(t) {
    try {
      if (t === 'todos') return Array.isArray(state.todos) ? state.todos : (state.todos = []);
      if (typeof DB === 'undefined') return [];
      if (!Array.isArray(DB[t])) DB[t] = [];
      return DB[t];
    } catch (e) { return []; }
  }

  function sortKeys(o) {
    var out = {};
    Object.keys(o).sort().forEach(function (k) { out[k] = o[k]; });
    return out;
  }

  function hashRow(r) {
    var o = {};
    for (var k in r) {
      if (k === '_rev' || k === 'updated_at') continue;
      o[k] = r[k];
    }
    try { return JSON.stringify(sortKeys(o)); } catch (e) { return String(r); }
  }

  function ridOf(t, r) {
    if (r.id !== undefined && r.id !== null && r.id !== '') return String(r.id);
    var n;
    if (t === 'lessons') {
      n = (+r.day_of_week || 0) * 1e6 + (+r.period_id || 0) * 1e3 + (+r.class_id || 0);
      return String(Math.trunc(n));
    }
    if (t === 'exam_scores') {
      n = (+r.exam_id || 0) * 1e7 + (+r.student_id || 0) * 100 + (+r.course_id || 0);
      return String(Math.trunc(n));
    }
    // 兜底：给一个稳定 id 写回行里，下次还是同一个
    var id = 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    r.id = id;
    return id;
  }

  /* ---------------- 应用服务端数据 ---------------- */
  function applyTables(tables, isSnapshot) {
    SERVER_TABLES.forEach(function (t) {
      var incoming = tables[t];
      if (!incoming) return;
      var rows = incoming.map(function (r) {
        var c = {};
        for (var k in r) if (k !== '_rev') c[k] = r[k];
        if (NO_ID[t]) delete c.id;
        return c;
      });
      var target = (t === 'todos') ? (state.todos || []) : (DB[t] || []);
      /* 本地刚删、还没推上去的行：mirror 里有、本地数组里没有。
         这时候如果正好拉了一次增量，服务端那份会把它们“复活”，
         本地删除就白删了（老师删完学生，一刷新又回来）。 */
      var pendDel = null;
      if (!isSnapshot) {
        var mir = NetSync.mirror[t] || {};
        var have = {};
        target.forEach(function (r) { have[ridOfLoose(r)] = 1; });
        Object.keys(mir).forEach(function (rid) {
          if (rid.indexOf('__row_') === 0) return;
          if (!have[rid]) (pendDel || (pendDel = {}))[rid] = 1;
        });
      }
      if (t === 'todos') {
        state.todos = isSnapshot ? rows : mergeRows(target, rows, pendDel);
      } else {
        DB[t] = isSnapshot ? rows : mergeRows(target, rows, pendDel);
      }
    });
    // 学生端改的东西也可能落在这些表里：让渲染层看见
    try { dbMirrorToLegacy(); } catch (e) {}
  }

  function mergeRows(local, incoming, pendDel) {
    var idx = {};
    local.forEach(function (r, i) { idx[ridOfLoose(r)] = i; });
    incoming.forEach(function (r) {
      var k = ridOfLoose(r);
      if (pendDel && pendDel[k]) return;      // 本地已删，等墓碑推上去
      if (r.deleted) {
        var i = idx[k];
        if (i !== undefined) local.splice(i, 1);
        // 索引会失效，重建一次（数据量小，够用）
        idx = {};
        local.forEach(function (x, j) { idx[ridOfLoose(x)] = j; });
        return;
      }
      if (idx[k] !== undefined) local[idx[k]] = r;
      else { local.push(r); idx[k] = local.length - 1; }
    });
    return local;
  }

  function ridOfLoose(r) {
    if (!r || typeof r !== 'object') return '';
    if (r.id !== undefined && r.id !== null && r.id !== '') return String(r.id);
    if (r.day_of_week !== undefined) return String(Math.trunc((+r.day_of_week || 0) * 1e6 + (+r.period_id || 0) * 1e3 + (+r.class_id || 0)));
    if (r.exam_id !== undefined) return String(Math.trunc((+r.exam_id || 0) * 1e7 + (+r.student_id || 0) * 100 + (+r.course_id || 0)));
    return 'x' + JSON.stringify(r).length;
  }

  /* ---------------- 扫描本地 → 生成差异 ---------------- */
  function scan() {
    var changes = [];
    var total = 0;
    SERVER_TABLES.forEach(function (t) {
      var rows = rowsOf(t);
      var mir = NetSync.mirror[t] || (NetSync.mirror[t] = {});
      var seen = {};
      var dirty = [];
      rows.forEach(function (r) {
        if (!r || typeof r !== 'object') return;
        var rid = ridOf(t, r);
        var h = hashRow(r);
        seen[rid] = h;
        if (mir[rid] !== h) dirty.push(r);
      });
      Object.keys(mir).forEach(function (rid) {
        if (rid.indexOf('__row_') === 0) return;
        if (seen[rid] === undefined) {
          // 本地没了 → 发墓碑（带上原行，服务端才能算出同一个 rid）
          var old = mir['__row_' + rid];
          dirty.push(old ? Object.assign({}, old, { deleted: 1 }) : { id: rid, deleted: 1 });
        }
      });
      if (dirty.length) {
        changes.push({ table: t, rows: dirty });
        total += dirty.length;
      }
    });
    return { changes: changes, total: total };
  }

  function rememberMirror() {
    SERVER_TABLES.forEach(function (t) {
      var mir = {};
      rowsOf(t).forEach(function (r) {
        if (!r || typeof r !== 'object') return;
        var rid = ridOf(t, r);
        var h = hashRow(r);
        mir[rid] = h;
        mir['__row_' + rid] = JSON.parse(JSON.stringify(r));  // 供墓碑使用
      });
      NetSync.mirror[t] = mir;
    });
  }

  function updateMirror(changes) {
    changes.forEach(function (ch) {
      var mir = NetSync.mirror[ch.table] || (NetSync.mirror[ch.table] = {});
      ch.rows.forEach(function (r) {
        var rid = ridOfLoose(r);
        if (r.deleted) { delete mir[rid]; delete mir['__row_' + rid]; return; }
        mir[rid] = hashRow(r);
        mir['__row_' + rid] = JSON.parse(JSON.stringify(r));
      });
    });
  }

  /* ---------------- 推送 ---------------- */
  function push() {
    if (NetSync.busy || !NetSync.ready) return;
    var res = scan();
    if (!res.total) return;
    NetSync.busy = true;
    var payload = { base_rev: NetSync.rev, changes: res.changes };
    api('/api/v2/sync', { method: 'POST', body: JSON.stringify(payload) })
      .then(function (j) {
        NetSync.busy = false;
        if (!j || !j.ok) { NetSync.lastError = (j && j.msg) || 'sync failed'; return; }
        NetSync.rev = j.rev;
        NetSync.selfRev = j.rev;
        var conflicts = (j.conflicts || []).filter(function (c) { return c && c.table; });
        var errs = (j.errors || []).filter(function (e) { return e && e.table; });
        /* 写失败的行别记成“已同步”，否则这条改动就永久丢了。
           连续失败 3 次后放弃，避免同一条坏行每 1.5 秒重推一次。 */
        var skip = {};
        errs.forEach(function (e) {
          var k = e.table + '\u0000' + String(e.id);
          NetSync.errTimes[k] = (NetSync.errTimes[k] || 0) + 1;
          if (NetSync.errTimes[k] < 3) skip[k] = 1;
        });
        var applied = res.changes;
        if (errs.length) {
          applied = res.changes.map(function (ch) {
            return {
              table: ch.table,
              rows: ch.rows.filter(function (r) {
                return !skip[ch.table + '\u0000' + ridOfLoose(r)];
              })
            };
          });
        }
        updateMirror(applied);
        if (conflicts.length) {
          /* 冲突 = 另一台设备上的版本更新（服务端按 updated_at 判的）。
             以前这里只 console.warn，老师这条改动无声消失。 */
          notifyUser('有 ' + conflicts.length + ' 处改动与另一台设备的较新版本冲突，已保留较新的一版');
          setTimeout(pull, 200);
        }
        if (errs.length) {
          console.warn('[netsync] 部分行未写入', errs.slice(0, 3));
          notifyUser('有 ' + errs.length + ' 处改动没保存成功，正在自动重试');
        }
      })
      .catch(function (e) {
        NetSync.busy = false;
        NetSync.lastError = String(e && e.message || e);
      });
  }

  /* ---------------- 拉取 ---------------- */
  function pull() {
    if (NetSync._pulling) return;
    NetSync._pulling = true;
    api('/api/v2/sync?since=' + NetSync.rev + '&scope=teacher')
      .then(function (j) {
        NetSync._pulling = false;
        if (!j || !j.ok) return;
        if (j.rev === NetSync.rev) return;
        applyTables(j.tables || {}, false);
        NetSync.rev = j.rev;
        NetSync.selfRev = j.rev;
        /* 只把服务端刚下发的行记为“已同步”。
           以前这里是 rememberMirror()（按本地数据全量重建），
           会把本地还没推上去的改动一起记成已同步 —— 那些改动就永远不会推了。 */
        var inc = [];
        Object.keys(j.tables || {}).forEach(function (t) {
          inc.push({ table: t, rows: j.tables[t] });
        });
        updateMirror(inc);
        renderSoon();
        if (j.has_more) setTimeout(pull, 60);
      })
      .catch(function () { NetSync._pulling = false; });
  }

  function renderSoon() {
    if (NetSync.pendingRender) return;
    NetSync.pendingRender = true;
    setTimeout(function () {
      NetSync.pendingRender = false;
      try {
        if (typeof renderContent === 'function') renderContent();
        if (typeof syncClassCount === 'function') syncClassCount();
      } catch (e) { console.warn('[netsync] render failed', e); }
    }, 120);
  }

  /* ---------------- SSE ---------------- */
  function connect() {
    if (NetSync.es) try { NetSync.es.close(); } catch (e) {}
    try {
      NetSync.es = new EventSource('/api/v2/events?since=' + NetSync.rev);
      NetSync.es.addEventListener('rev', function (e) {
        var r = parseInt(e.data, 10) || 0;
        if (r <= NetSync.selfRev) return;   // 自己刚推的，不用回拉
        pull();
      });
      /* 设置类变更（模块开关 / 可见字段 / 账号密码 / 密保）：不产生数据 rev，
         单独走这个事件。密码被清空（管理员在教师机 reset）时直接回登录页。 */
      NetSync.es.addEventListener('cfg', function (e) {
        var v = parseInt(e.data, 10) || 0;
        if (v === NetSync.cfgRev) return;
        NetSync.cfgRev = v;
        api('/api/v2/state').then(function (j) {
          if (j && j.ok && j.has_password === false) location.href = '/teacher/login';
          else renderSoon();
        }).catch(function () {});
        pull();
      });
      NetSync.es.onerror = function () { /* 浏览器会自动重连 */ };
    } catch (e) {}
  }

  /* ---------------- 生命周期 ---------------- */
  NetSync.boot = function () {
    return api('/api/v2/sync?since=0&scope=teacher').then(function (j) {
      if (!j || !j.ok) throw new Error((j && j.msg) || 'snapshot failed');
      applyTables(j.tables || {}, true);
      NetSync.rev = j.rev || 0;
      NetSync.selfRev = j.rev || 0;
      NetSync.ready = true;
      rememberMirror();
      return j;
    });
  };

  NetSync.startWatch = function () {
    if (NetSync._scanTimer) clearInterval(NetSync._scanTimer);
    NetSync._scanTimer = setInterval(push, 1500);
    connect();
    setInterval(function () {
      if (!NetSync.es || NetSync.es.readyState === 2) connect();
    }, 8000);
    window.addEventListener('online', function () { pull(); push(); });
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && NetSync.ready) { pull(); push(); }
    });
    // 关页面前尽力推一次
    window.addEventListener('beforeunload', function () {
      var res = scan();
      if (!res.total) return;
      try {
        navigator.sendBeacon('/api/v2/sync', new Blob([JSON.stringify({
          base_rev: NetSync.rev, changes: res.changes
        })], { type: 'application/json' }));
      } catch (e) {}
    });
  };

  NetSync.status = function () {
    return { rev: NetSync.rev, ready: NetSync.ready, error: NetSync.lastError,
             tables: Object.keys(NetSync.mirror).length };
  };

  /* ---------------- 接管启动 ---------------- */
  async function serverBoot() {
    // 服务端模式下这些“本地自愈”会误改服务端数据，先关掉
    try {
      LS.set('wb-db-seeded', '');
      LS.set('wb-fix-sept30', '1');
    } catch (e) {}

    var ok = false;
    try {
      await NetSync.boot();
      ok = true;
    } catch (e) {
      NetSync.lastError = String(e && e.message || e);
      console.error('[netsync] 快照失败，改用本地缓存', e);
    }

    // 不再从 localStorage 覆盖 DB：服务端才是真相
    var origLoad = window.dbLoadLocal;
    window.dbLoadLocal = function () {};
    try {
      await window.goToApp();
    } finally {
      window.dbLoadLocal = origLoad;
    }
    if (ok) {
      // 服务端数据可能和缓存不同，重绘一次
      NetSync.startWatch();
      renderSoon();
    }
    /* ---- 同步状态指示（2026-10-31 第二次重排）----
       设计者署名已挪到顶栏右上角，右下角空出来了，所以这个胶囊回到**视口右下角**：
       right:16 / bottom:12，z-index 61（只压页脚/空白，低于迷你条 520、
       学生面板 200、弹窗 1000+）。右下角同时被迷你计时条 .mini-bar（right:18/bottom:18）
       和学生面板 .stu-panel（right:20/bottom:20）占用，所以落位时量一下它们的矩形，
       有占用就把胶囊整体上移到它们上方 10px —— 任何时候都不叠。
       登录 / 锁屏页没有 .app 外壳，退化到左下角，避开登录卡右下角的署名徽章。 */
    var bar = document.createElement('div');
    bar.id = '__syncbar';
    bar.className = 'sync-net';
    bar.title = '点击查看同步状态（数据版本 / 在线状态）';
    var snDot = document.createElement('i'); snDot.className = 'sn-dot';
    var snTxt = document.createElement('span'); snTxt.className = 'sn-txt';
    var snVer = document.createElement('span'); snVer.className = 'sn-ver';
    bar.appendChild(snDot); bar.appendChild(snTxt); bar.appendChild(snVer);
    /* 定位与配色写内联：这份脚本不依赖 index.html 的样式表也能站对位置 */
    bar.style.cssText = 'position:fixed;z-index:61;right:16px;bottom:12px;' +
      'display:inline-flex;align-items:center;gap:6px;padding:3px 10px;' +
      'border-radius:999px;border:1px solid rgba(29,158,117,.35);' +
      'font:600 11.5px/1.5 ui-monospace,Menlo,Consolas,monospace;' +
      'white-space:nowrap;max-width:46vw;overflow:hidden;cursor:pointer;' +
      'user-select:none;pointer-events:auto;opacity:.94';
    bar.onclick = function () {
      var s = NetSync.status();
      alert('数据版本 ' + s.rev + '\n状态 ' + (s.ready ? '已连接' : '未连接') +
        (s.error ? '\n错误 ' + s.error : ''));
    };
    /** 落位：有 .app 外壳 → 视口右下角（被迷你条/学生面板占了就上移）；否则左下角 */
    function placeSyncBar() {
      var shell = document.querySelector('.app');
      if (!shell) {                       /* 登录 / 锁屏：左下角，避开右下角的署名徽章 */
        bar.style.left = '16px';
        bar.style.right = 'auto';
        bar.style.bottom = '12px';
        return;
      }
      bar.style.left = 'auto';
      bar.style.right = '16px';
      var bottom = 12;
      var w = bar.offsetWidth || 150;
      var bandL = window.innerWidth - 16 - w;      /* 胶囊占据的水平范围 */
      var bandR = window.innerWidth - 16;
      ['.mini-bar', '.stu-panel'].forEach(function (sel) {
        var el = document.querySelector(sel);
        if (!el) return;
        var cs = window.getComputedStyle(el);
        if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return;
        var r = el.getBoundingClientRect();
        if (!r.width || !r.height) return;
        /* 只有真正压到胶囊水平范围、且落在下半屏的浮层才需要避让 */
        if (r.right > bandL - 10 && r.left < bandR + 10 && r.top > window.innerHeight / 2) {
          bottom = Math.max(bottom, Math.round(window.innerHeight - r.top + 10));
        }
      });
      bar.style.bottom = bottom + 'px';
    }
    /** 文案 + 配色：已连接=绿 / 离线补传=橙 / 出错=红（深浅色各一套） */
    function paintSyncBar() {
      var s = NetSync.status();
      var dark = document.documentElement.getAttribute('data-mode') === 'dark';
      snTxt.textContent = s.ready ? '已连接服务端' : '离线（本地缓存）';
      snVer.textContent = s.ready ? ('· v' + s.rev) : '';
      var c = s.error
        ? (dark ? ['rgba(226,75,74,.26)', '#FF9090', 'rgba(226,75,74,.52)']
                : ['rgba(226,75,74,.14)', '#C1332F', 'rgba(226,75,74,.36)'])
        : s.ready
        ? (dark ? ['rgba(29,158,117,.26)', '#5FDCAE', 'rgba(29,158,117,.52)']
                : ['rgba(29,158,117,.14)', '#0F7A58', 'rgba(29,158,117,.35)'])
        : (dark ? ['rgba(239,159,39,.24)', '#F2B65C', 'rgba(239,159,39,.50)']
                : ['rgba(239,159,39,.16)', '#985F13', 'rgba(239,159,39,.40)']);
      bar.style.background = c[0];
      bar.style.color = c[1];
      bar.style.borderColor = c[2];
    }
    document.body.appendChild(bar);
    paintSyncBar();
    placeSyncBar();
    /* 外壳是登录成功后才渲染的：先占位，等它出现再落位（最多试 12 秒） */
    var __sbTry = 0;
    var __sbTimer = setInterval(function () {
      placeSyncBar();
      if (document.querySelector('.app') || ++__sbTry > 40) clearInterval(__sbTimer);
    }, 300);
    window.addEventListener('resize', placeSyncBar);
    window.addEventListener('orientationchange', placeSyncBar);
    /* 迷你条出现 / 消失、学生面板开合都改变了右下角占用，跟着重算 */
    setInterval(function () { paintSyncBar(); placeSyncBar(); }, 1200);
  }

  window.NetSync = NetSync;
  window.__SERVER_MODE__ = true;

  // 主程序的 boot() 已经在上面跑过一次（渲染了本地锁屏），这里直接接管
  serverBoot();
})();
