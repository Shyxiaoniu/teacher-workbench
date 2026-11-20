/* 班级工作台 · 像素画板（pixel.js，2.0.47）
   ------------------------------------------------------------
   学生在这里一格一格画，边画边自动存草稿，画好一键交给老师。
   数据形状与 app/core/pixel_store.py 一一对应：
     w / h       画布边长（16 24 32 48 64）
     pal         调色板数组，最多 32 色，元素形如 '#a1b2c3'
     px          长度 w*h 的字符串，一格一个字符：
                   '.'  = 空格子
                   '0'-'v' = 调色板下标（base36，正好 32 色）
   内存里不用字符串（不断拼接很慢），改成 Uint8Array：0=空，n=pal[n-1]。
   只在保存 / 提交那一刻转成字符串。
   ------------------------------------------------------------ */
(function () {
  'use strict';

  var PAL_CHARS = '0123456789abcdefghijklmnopqrstuv';
  var SIZES = [16, 24, 32, 48, 64];
  var MAX_PAL = 32;
  var MAX_UNDO = 60;

  /* 默认 16 色：和教师端 / 学生端同一套暖陶土配色打头，后面跟常用纯色。
     学生点「＋ 加进调色板」或自己挑颜色就往后加，最多 32 个。 */
  var PAL_DEFAULT = [
    '#2f2a26', '#ffffff', '#b45b33', '#e0a458',
    '#7d9a6a', '#5f8fa8', '#8d6e9e', '#d46a6a',
    '#f2d9b1', '#c9b28a', '#6b5b4b', '#3d5a5b',
    '#a8c0a0', '#f4c2c2', '#9aa7b8', '#4a4a4a'
  ];

  /* ---------------- 模板（2.0.49） ----------------
     给「照着练」用的现成图案，一律 16×16 起步：格子少、学生数得清、
     画完有成就感。px 与草稿同一套编码（'.' = 空，字符 = 调色板下标），
     下标只指向前 16 个默认色 —— 学生只能往调色板末尾追加颜色，
     前 16 个不会被改，所以载入模板不需要重映射。
     新增一个模板：照格式加一条，行数必须 = h、每行字符数必须 = w。
     ------------------------------------------------------------ */
  var TEMPLATES = [
    { id: 'heart', name: '爱心', w: 16, h: 16, px: [
      '................',
      '...777....777...',
      '..77777..77777..',
      '.77777777777777.',
      '7777777777777777',
      '7777777777777777',
      '7777777777777777',
      '.77777777777777.',
      '..777777777777..',
      '...7777777777...',
      '....77777777....',
      '.....777777.....',
      '......7777......',
      '.......77.......',
      '................',
      '................'
    ].join('') },

    { id: 'smile', name: '笑脸', w: 16, h: 16, px: [
      '................',
      '....33333333....',
      '..333333333333..',
      '.33333333333333.',
      '3333333333333333',
      '3333003333003333',
      '3333003333003333',
      '3333333333333333',
      '3333333333333333',
      '3333333333333333',
      '3333033333303333',
      '3333300000003333',
      '.33333333333333.',
      '..333333333333..',
      '....33333333....',
      '................'
    ].join('') },

    { id: 'house', name: '小房子', w: 16, h: 16, px: [
      '................',
      '......7777......',
      '.....777777.....',
      '....77777777....',
      '...7777777777...',
      '..777777777777..',
      '.77777777777777.',
      '.88888888888888.',
      '.88555888855588.',
      '.88555888855588.',
      '.88888888888888.',
      '.88888833388888.',
      '.88888833388888.',
      '.88888833388888.',
      '.88888833388888.',
      '4444444444444444'
    ].join('') },

    { id: 'star', name: '星星', w: 16, h: 16, px: [
      '................',
      '.......33.......',
      '......3333......',
      '......3333......',
      '3333333333333333',
      '.33333333333333.',
      '..333333333333..',
      '...3333333333...',
      '....33333333....',
      '....33333333....',
      '...3333333333...',
      '..3333....3333..',
      '.3333......3333.',
      '.33..........33.',
      '................',
      '................'
    ].join('') },

    { id: 'cat', name: '小猫', w: 16, h: 16, px: [
      '................',
      '..e..........e..',
      '..ee........ee..',
      '..eee......eee..',
      '..eeeeeeeeeeee..',
      '.eeeeeeeeeeeeee.',
      '.ee00eeeeee00ee.',
      '.ee00eeeeee00ee.',
      '.eeeeeeeeeeeeee.',
      '.eeeeeeddeeeeee.',
      '.eeeeeeeeeeeeee.',
      '..eeeeeeeeeeee..',
      '..eeeeeeeeeeee..',
      '...eeeeeeeeee...',
      '................',
      '................'
    ].join('') },

    { id: 'apple', name: '苹果', w: 16, h: 16, px: [
      '................',
      '........2.......',
      '.......2.44.....',
      '......2..444....',
      '.....7777.44....',
      '....7777777.....',
      '...777777777....',
      '..77777777777...',
      '..77777777777...',
      '..77777777777...',
      '..77777777777...',
      '...777777777....',
      '....7777777.....',
      '.....77777......',
      '................',
      '................'
    ].join('') },

    { id: 'fish', name: '小鱼', w: 16, h: 16, px: [
      '................',
      '................',
      '............5...',
      '...........55...',
      '..5555555..5....',
      '.555555555.55...',
      '555550555555....',
      '5555555555555...',
      '5555555555.55...',
      '.555555555.5....',
      '..5555555..55...',
      '...........55...',
      '............5...',
      '................',
      '................',
      '................'
    ].join('') },

    { id: 'tree', name: '小树', w: 16, h: 16, px: [
      '................',
      '.......44.......',
      '......4444......',
      '.....444444.....',
      '....44444444....',
      '...4444444444...',
      '..444444444444..',
      '.44444444444444.',
      '4444444444444444',
      '.44444444444444.',
      '..444444444444..',
      '...4444444444...',
      '.......22.......',
      '.......22.......',
      '.......22.......',
      '................'
    ].join('') }
  ];

  var S = {
    me: null, role: null, demo: false, practice: false,
    cfg: { enabled: false, allow_add: true, allow_submit: true,
           course: '像素画', window: '像素画作品', intro: '', size: 32 },
    w: 32, h: 32, cells: null, pal: [], cur: 0,
    tool: 'pen', brush: 1, mirror: false, grid: true,
    title: '', dirty: false, saving: false, submitting: false,
    lastSavedAt: 0, last: null, loaded: false, ref: null,
    undo: [], redo: [], stroke: null, saveTimer: 0, rafId: 0
  };

  function $(s) { return document.querySelector(s); }
  function $$(s) { return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  var toastTimer = null;
  function toast(msg, tone) {
    var t = $('#toast');
    if (!t) return;
    t.textContent = msg;
    t.className = 'ds-toast on' + (tone ? ' ' + tone : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.className = 'ds-toast'; }, 2600);
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

  function busy(text) {
    var box = $('#busyBox');
    if (!box) return;
    if (!text) { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="ds-busy"><div class="box"><div class="ring"></div>' +
      '<p>' + esc(text) + '</p></div></div>';
  }

  /* 主题跟随外层（同源 iframe 读父窗口变量）：教师端 / 学生端切主题，画板跟着变 */
  function syncTheme() {
    try {
      var p = window.parent;
      if (!p || p === window || !p.document) return;
      var pr = p.document.documentElement;
      var cs = p.getComputedStyle(pr);
      [
        ['--brand', '--brand-primary'], ['--brand-2', '--brand-hover'],
        ['--brand-soft', '--brand-soft'], ['--bg', '--bg-page'],
        ['--card', '--bg-surface'], ['--card-2', '--bg-page'],
        ['--ink', '--text-primary'], ['--ink2', '--text-secondary'],
        ['--line', '--border'], ['--line-2', '--border']
      ].forEach(function (it) {
        var v = cs.getPropertyValue(it[1]);
        if (v && String(v).trim()) {
          document.documentElement.style.setProperty(it[0], String(v).trim());
        }
      });
      var mode = pr.getAttribute('data-mode') || '';
      if (mode === 'dark' || mode === 'light') {
        document.documentElement.setAttribute('data-mode', mode);
      }
    } catch (e) {}
  }
  function watchTheme() {
    try {
      var p = window.parent;
      if (!p || p === window || !p.document) return;
      new MutationObserver(syncTheme).observe(p.document.documentElement,
        { attributes: true, attributeFilter: ['data-theme', 'data-mode', 'class', 'style'] });
    } catch (e) {}
  }

  /* ================= 画布 ================= */
  function cellCount() { return S.w * S.h; }

  function resetCells(w, h, keep) {
    var old = S.cells, ow = S.w, oh = S.h;
    S.cells = new Uint8Array(w * h);
    S.w = w; S.h = h;
    if (keep && old) {
      // 换尺寸时把重叠区域搬过来：改大不丢、改小裁掉右边和下边，
      // 比直接清空友好——学生只是想把画布放大一点接着画。
      var mw = Math.min(ow, w), mh = Math.min(oh, h);
      for (var y = 0; y < mh; y++) {
        for (var x = 0; x < mw; x++) {
          S.cells[y * w + x] = old[y * ow + x];
        }
      }
    }
  }

  function pxString() {
    var out = [];
    for (var i = 0; i < S.cells.length; i++) {
      var v = S.cells[i];
      out.push(v ? PAL_CHARS.charAt((v - 1) % PAL_CHARS.length) : '.');
    }
    return out.join('');
  }

  function loadPx(str, w, h) {
    var n = w * h;
    S.cells = new Uint8Array(n);
    str = String(str || '');
    for (var i = 0; i < n && i < str.length; i++) {
      var k = PAL_CHARS.indexOf(str.charAt(i));
      S.cells[i] = k < 0 ? 0 : (k + 1);
    }
  }

  function filledCount() {
    var n = 0;
    for (var i = 0; i < S.cells.length; i++) if (S.cells[i]) n++;
    return n;
  }

  /* 画布尺寸：舞台里取最大的正方形，铺满可用空间。
     每格用浮点边界再取整，避免整数除法累积出 1px 的缝。 */
  function layout() {
    var cv = $('#cv');
    var holder = $('.px-holder');
    if (!cv || !holder) return;
    var aw = holder.clientWidth || 320;
    var ah = holder.clientHeight || 0;
    // 高度拿不到（窄屏上下排列、布局还没算完）时退回按宽度算，
    // 否则 min(aw, 0) 会把画布压成一个几十像素的小方块。
    var side = Math.floor(Math.max(96, Math.min(aw, ah || aw)));
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.style.width = side + 'px';
    cv.style.height = side + 'px';
    cv.width = Math.round(side * dpr);
    cv.height = Math.round(side * dpr);
    var ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cv._side = side;
    paint();
  }

  function schedulePaint() {
    if (S.rafId) return;
    S.rafId = requestAnimationFrame(function () {
      S.rafId = 0;
      paint();
    });
  }

  function paint() {
    var cv = $('#cv');
    if (!cv || !S.cells) return;
    var side = cv._side || 320;
    var ctx = cv.getContext('2d');
    var cell = side / S.w;
    ctx.clearRect(0, 0, side, side);

    // 白底：像素画在纸上是白的，屏幕上也白，导出图片才不会「透明一片」
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, side, side);

    for (var i = 0; i < S.cells.length; i++) {
      var v = S.cells[i];
      if (!v) continue;
      var x = i % S.w, y = (i - x) / S.w;
      ctx.fillStyle = S.pal[v - 1] || '#000000';
      var x0 = Math.round(x * cell), y0 = Math.round(y * cell);
      ctx.fillRect(x0, y0, Math.round((x + 1) * cell) - x0,
                   Math.round((y + 1) * cell) - y0);
    }

    // 网格线：格子太小时不画，否则整块变成灰色
    if (S.grid && cell >= 5) {
      ctx.strokeStyle = 'rgba(60,40,25,.14)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (var g = 1; g < S.w; g++) {
        var p = Math.round(g * cell) + 0.5;
        ctx.moveTo(p, 0); ctx.lineTo(p, side);
      }
      for (var g2 = 1; g2 < S.h; g2++) {
        var q = Math.round(g2 * cell) + 0.5;
        ctx.moveTo(0, q); ctx.lineTo(side, q);
      }
      ctx.stroke();
    }

    var m = $('#mCells');
    if (m) m.textContent = String(filledCount());
    var ms = $('#mSize');
    if (ms) ms.textContent = S.w + '×' + S.h;
  }

  /* ================= 绘制操作 ================= */
  function pushUndo() {
    S.undo.push(Uint8Array.from(S.cells));
    if (S.undo.length > MAX_UNDO) S.undo.shift();
    S.redo.length = 0;
  }

  function setCell(x, y, v) {
    if (x < 0 || y < 0 || x >= S.w || y >= S.h) return false;
    var i = y * S.w + x;
    if (S.cells[i] === v) return false;
    S.cells[i] = v;
    return true;
  }

  /** 画一个笔刷点（笔刷是方形，以点击格为中心） */
  function dab(x, y, v) {
    var n = Math.max(1, Math.min(3, S.brush | 0));
    var off = Math.floor((n - 1) / 2);
    var hit = false;
    for (var dy = 0; dy < n; dy++) {
      for (var dx = 0; dx < n; dx++) {
        var xx = x + dx - off, yy = y + dy - off;
        if (setCell(xx, yy, v)) hit = true;
        if (S.mirror && setCell(S.w - 1 - xx, yy, v)) hit = true;
      }
    }
    return hit;
  }

  /** 两点之间补点：鼠标甩得快时不会画成一串断开的点 */
  function lineTo(x0, y0, x1, y1, v) {
    var dx = Math.abs(x1 - x0), dy = Math.abs(y1 - y0);
    var sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1;
    var err = dx - dy, hit = false, guard = 0;
    while (guard++ < 4096) {
      if (dab(x0, y0, v)) hit = true;
      if (x0 === x1 && y0 === y1) break;
      var e2 = 2 * err;
      if (e2 > -dy) { err -= dy; x0 += sx; }
      if (e2 < dx) { err += dx; y0 += sy; }
    }
    return hit;
  }

  /** 油漆桶：四邻域把相连的同色区域整片换掉 */
  function floodFill(x, y, v) {
    if (x < 0 || y < 0 || x >= S.w || y >= S.h) return false;
    var from = S.cells[y * S.w + x];
    if (from === v) return false;
    var stack = [y * S.w + x], hit = false, guard = 0;
    while (stack.length && guard++ < 100000) {
      var i = stack.pop();
      if (S.cells[i] !== from) continue;
      S.cells[i] = v;
      hit = true;
      var cx = i % S.w, cy = (i - cx) / S.w;
      if (cx > 0) stack.push(i - 1);
      if (cx < S.w - 1) stack.push(i + 1);
      if (cy > 0) stack.push(i - S.w);
      if (cy < S.h - 1) stack.push(i + S.w);
    }
    return hit;
  }

  /** 吸管：取这一格的颜色放进当前色 */
  function pickAt(x, y) {
    if (x < 0 || y < 0 || x >= S.w || y >= S.h) return;
    var v = S.cells[y * S.w + x];
    if (!v) { toast('这一格是空的，换个地方吸'); return; }
    selectColor(v - 1);
    toast('已取色 ' + (S.pal[v - 1] || ''));
  }

  function cellAt(ev) {
    var cv = $('#cv');
    var r = cv.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    var x = Math.floor((ev.clientX - r.left) / r.width * S.w);
    var y = Math.floor((ev.clientY - r.top) / r.height * S.h);
    if (x < 0 || y < 0 || x >= S.w || y >= S.h) return null;
    return { x: x, y: y };
  }

  function applyAt(pt, first) {
    if (!pt) return;
    var v = S.tool === 'eraser' ? 0 : (S.cur + 1);
    if (S.tool === 'fill') {
      pushUndo();
      if (floodFill(pt.x, pt.y, v)) afterEdit();
      return;
    }
    if (S.tool === 'pick') { pickAt(pt.x, pt.y); return; }
    var prev = S.stroke;
    S.stroke = pt;
    if (prev && !first) {
      if (lineTo(prev.x, prev.y, pt.x, pt.y, v)) afterEdit();
    } else if (dab(pt.x, pt.y, v)) {
      afterEdit();
    }
  }

  function afterEdit() {
    S.dirty = true;
    schedulePaint();
    setSavedText('');
    scheduleSave();
  }

  function setSavedText(txt, tone) {
    var el = $('#mSaved');
    if (!el) return;
    el.textContent = txt || '未保存';
    el.className = tone || '';
  }

  /* ================= 草稿保存 ================= */
  function scheduleSave() {
    if (S.demo) return;
    clearTimeout(S.saveTimer);
    S.saveTimer = setTimeout(function () { saveDraft(false); }, 1400);
  }

  function saveDraft(manual) {
    if (S.demo) {
      if (manual) toast('演示模式不保存，从学生端进来才存得住', 'err');
      return Promise.resolve(false);
    }
    if (S.saving) return Promise.resolve(false);
    S.saving = true;
    setSavedText('保存中…');
    var payload = { w: S.w, h: S.h, px: pxString(), pal: S.pal,
                    title: S.title, grid: S.grid ? 1 : 0 };
    return api('/api/v2/pixel/draft', { method: 'POST', body: JSON.stringify(payload) })
      .then(function (j) {
        S.saving = false;
        if (!j.ok) {
          setSavedText('保存失败', 'err');
          if (manual) toast(j.msg || '保存失败', 'err');
          return false;
        }
        S.lastSavedAt = j.updated || Math.floor(Date.now() / 1000);
        S.dirty = false;
        setSavedText('已保存 ' + hhmm(), 'ok');
        if (manual) {
          toast(S.practice ? '练习进度已保存，下次接着画'
                           : '草稿已保存，换台电脑登录也能接着画', 'ok');
        }
        return true;
      }).catch(function () {
        S.saving = false;
        setSavedText('连不上服务器', 'err');
        if (manual) toast('连不上服务器，草稿没保存', 'err');
        return false;
      });
  }

  function hhmm() {
    var d = new Date();
    function p(n) { return (n < 10 ? '0' : '') + n; }
    return p(d.getHours()) + ':' + p(d.getMinutes());
  }

  /* ================= 导出与提交 ================= */
  /** 导出成 PNG：每格放大到 768px 见方，白底，最近邻放大（像素边缘不糊） */
  function exportPng() {
    var n = Math.max(S.w, S.h);
    var scale = Math.max(8, Math.round(768 / n));
    var cv = document.createElement('canvas');
    cv.width = S.w * scale;
    cv.height = S.h * scale;
    var g = cv.getContext('2d');
    g.fillStyle = '#ffffff';
    g.fillRect(0, 0, cv.width, cv.height);
    g.imageSmoothingEnabled = false;
    for (var i = 0; i < S.cells.length; i++) {
      var v = S.cells[i];
      if (!v) continue;
      var x = i % S.w, y = (i - x) / S.w;
      g.fillStyle = S.pal[v - 1] || '#000000';
      g.fillRect(x * scale, y * scale, scale, scale);
    }
    return cv.toDataURL('image/png');
  }

  function downloadPng() {
    var a = document.createElement('a');
    a.href = exportPng();
    a.download = (S.title || '我的像素画') + '.png';
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast('作品图片已导出到下载文件夹', 'ok');
  }

  function submitWork() {
    if (S.submitting) return;
    if (S.practice) { downloadPng(); return; }
    if (S.demo) { toast('演示模式不能提交，请从学生端进入', 'err'); return; }
    if (S.role !== 'student') { toast('只有学生能提交作品', 'err'); return; }
    if (!S.cfg.enabled) { toast('老师还没有开启像素画', 'err'); return; }
    if (!S.cfg.allow_submit) {
      toast('老师暂未开放提交，先自己画、保存草稿', 'err');
      return;
    }
    if (!filledCount()) { toast('画布还是空的，先画几笔再交', 'err'); return; }

    S.submitting = true;
    busy('正在生成作品图…');
    setTimeout(function () {
      var png;
      try {
        png = exportPng();
      } catch (e) {
        S.submitting = false;
        busy('');
        toast('作品图生成失败，请重试', 'err');
        return;
      }
      busy('正在提交并归档…');
      api('/api/v2/pixel/submit', {
        method: 'POST',
        body: JSON.stringify({ w: S.w, h: S.h, px: pxString(), pal: S.pal,
                               title: S.title, png: png, grid: S.grid ? 1 : 0 })
      }).then(function (j) {
        if (!j.ok) throw new Error(j.msg || '提交失败');
        S.last = { at: j.at, url: j.url };
        S.dirty = false;
        setSavedText('已提交 ' + hhmm(), 'ok');
        busy('');
        receipt(j, png);
        toast('作品已提交', 'ok');
      }).catch(function (e) {
        busy('');
        toast((e && e.message) || '提交失败，请重试', 'err');
      }).then(function () {
        S.submitting = false;
      });
    }, 30);
  }

  function receipt(j, png) {
    var box = $('#busyBox');
    box.innerHTML = '<div class="ds-receipt"><div class="box">' +
      '<div class="big">🎉</div>' +
      '<h3>作品已提交</h3>' +
      '<p>' + esc((S.me && S.me.name) || '') + ' · ' + esc(j.at || '') + '<br>' +
      '已归档到「' + esc(j.course || S.cfg.course) + '」' +
      (j.archived ? '（' + j.archived + ' 个文件）' : '') + '<br>' +
      '老师在大屏选「' + esc(j.course || S.cfg.course) + '」就能看到你的作品</p>' +
      '<img src="' + png + '" alt="作品预览">' +
      '<div class="row">' +
      '<button class="btn" id="rcClose">继续修改</button>' +
      '<button class="btn primary" id="rcAgain">再交一版</button>' +
      '</div></div></div>';
    $('#rcClose').onclick = function () { box.innerHTML = ''; };
    $('#rcAgain').onclick = function () { box.innerHTML = ''; submitWork(); };
  }

  /* ================= 调色板 ================= */
  function paintSwatches() {
    var box = $('#swatches');
    if (!box) return;
    var html = '';
    for (var i = 0; i < S.pal.length; i++) {
      html += '<button type="button" class="px-sw' + (i === S.cur ? ' on' : '') + '"' +
        ' data-c="' + i + '" title="' + esc(S.pal[i]) + '"' +
        ' style="background:' + esc(S.pal[i]) + '"></button>';
    }
    if (S.pal.length < MAX_PAL) {
      html += '<button type="button" class="px-sw empty" data-c="add" title="加入当前颜色">＋</button>';
    }
    box.innerHTML = html;
  }

  function selectColor(i) {
    if (i < 0 || i >= S.pal.length) return;
    S.cur = i;
    var sw = $('#curSw'), hx = $('#curHex'), cp = $('#colorPick');
    if (sw) sw.style.background = S.pal[i];
    if (hx) hx.textContent = String(S.pal[i]).toUpperCase();
    if (cp) cp.value = S.pal[i];
    paintSwatches();
  }

  /** 把当前颜色加进调色板：已有的就选中它，满了就替换当前格 */
  function addCurColor(hex) {
    hex = String(hex || '').toLowerCase();
    if (!/^#[0-9a-f]{6}$/.test(hex)) return;
    var at = S.pal.indexOf(hex);
    if (at >= 0) { selectColor(at); toast('这个颜色调色板里已经有了'); return; }
    if (S.pal.length < MAX_PAL) {
      S.pal.push(hex);
      selectColor(S.pal.length - 1);
      S.dirty = true;
      scheduleSave();
      toast('已加入调色板', 'ok');
    } else {
      S.pal[S.cur] = hex;
      selectColor(S.cur);
      S.dirty = true;
      scheduleSave();
      toast('调色板满了，已替换当前这一格', 'warn');
    }
  }

  /* ================= 模板库（2.0.49） ================= */
  /** 把模板画进任意 canvas（缩略图 / 参考图共用）：最近邻放大，边缘不糊 */
  function drawTpl(cv, tpl, side) {
    if (!cv || !tpl) return;
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var n = tpl.w;
    var cell = side / n;
    cv.style.width = side + 'px';
    cv.style.height = side + 'px';
    cv.width = Math.round(side * dpr);
    cv.height = Math.round(side * dpr);
    var ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, side, side);
    for (var i = 0; i < tpl.px.length; i++) {
      var ch = tpl.px.charAt(i);
      if (ch === '.') continue;
      var k = PAL_CHARS.indexOf(ch);
      if (k < 0) continue;
      var x = i % n, y = (i - x) / n;
      ctx.fillStyle = PAL_DEFAULT[k] || '#000000';
      var x0 = Math.round(x * cell), y0 = Math.round(y * cell);
      ctx.fillRect(x0, y0, Math.round((x + 1) * cell) - x0,
                   Math.round((y + 1) * cell) - y0);
    }
  }

  function renderTplGrid() {
    var g = $('#tplGrid');
    if (!g) return;
    g.innerHTML = TEMPLATES.map(function (t, i) {
      return '<div class="px-tpl-card" data-i="' + i + '">' +
        '<canvas class="px-tpl-cv" aria-label="' + esc(t.name) + '"></canvas>' +
        '<div class="px-tpl-nm">' + esc(t.name) + '</div>' +
        '<div class="px-tpl-ops">' +
          '<button type="button" class="px-mini" data-act="look">照着画</button>' +
          '<button type="button" class="px-mini primary" data-act="load">载入</button>' +
        '</div></div>';
    }).join('');
    $$('#tplGrid .px-tpl-card').forEach(function (card) {
      drawTpl(card.querySelector('canvas'),
              TEMPLATES[+card.getAttribute('data-i')], 96);
    });
  }

  function openTpl() {
    var m = $('#tplModal');
    if (!m) return;
    renderTplGrid();
    m.hidden = false;
  }
  function closeTpl() {
    var m = $('#tplModal');
    if (m) m.hidden = true;
  }

  /** 参考图：只在工具面板里显示一张小图，不动画布，学生照着描 */
  function setRef(tpl) {
    S.ref = tpl;
    var box = $('#pxRef');
    if (!box) return;
    box.hidden = false;
    var nm = $('#refName');
    if (nm) nm.textContent = tpl.name;
    drawTpl($('#refCv'), tpl, 76);
  }
  function clearRef() {
    S.ref = null;
    var box = $('#pxRef');
    if (box) box.hidden = true;
  }

  /* 模板下标基于 PAL_DEFAULT 前 16 色，学生只能往后追加，理论上不会变。
     万一老草稿对不上（或以后默认色改过），此时画布已清空，
     重排调色板不会污染已有内容。 */
  function ensureTplPal() {
    var changed = false;
    for (var i = 0; i < PAL_DEFAULT.length; i++) {
      if (String(S.pal[i] || '').toLowerCase() !== PAL_DEFAULT[i].toLowerCase()) {
        S.pal[i] = PAL_DEFAULT[i];
        changed = true;
      }
    }
    if (changed) {
      if (S.cur >= S.pal.length) S.cur = 0;
      selectColor(S.cur);
    }
  }

  /** 把模板直接画到画布上（可继续改，也能 Ctrl+Z 撤回）。返回是否真的画了 */
  function applyTemplate(tpl) {
    if (filledCount() && !confirm('把「' + tpl.name + '」画到画布上？\n' +
        '现在画的会被替换，载入后按 Ctrl+Z 还能撤回。')) {
      return false;
    }
    pushUndo();
    resetCells(tpl.w, tpl.h, false);
    ensureTplPal();
    loadPx(tpl.px, tpl.w, tpl.h);
    markSize(tpl.w);
    afterEdit();
    layout();
    toast('已载入「' + tpl.name + '」，接着改就行', 'ok');
    return true;
  }

  /* ================= 交互绑定 ================= */
  function bindCanvas() {
    var cv = $('#cv');
    cv.addEventListener('pointerdown', function (e) {
      if (e.button !== undefined && e.button !== 0) return;
      var pt = cellAt(e);
      if (!pt) return;
      // 每次落笔前拍一张快照，撤销按「一笔」回退，不是按「一格」
      if (S.tool === 'pen' || S.tool === 'eraser') pushUndo();
      S.stroke = null;
      try { cv.setPointerCapture(e.pointerId); } catch (err) {}
      applyAt(pt, true);
      e.preventDefault();
    });
    cv.addEventListener('pointermove', function (e) {
      if (S.stroke === null) return;          // 没按下就不画
      if (S.tool === 'fill' || S.tool === 'pick') return;
      var pt = cellAt(e);
      if (!pt) return;
      if (pt.x === S.stroke.x && pt.y === S.stroke.y) return;
      applyAt(pt, false);
      e.preventDefault();
    });
    function endStroke(e) {
      if (S.stroke === null) return;
      S.stroke = null;
      try { cv.releasePointerCapture(e.pointerId); } catch (err) {}
    }
    cv.addEventListener('pointerup', endStroke);
    cv.addEventListener('pointercancel', endStroke);
    // 右键当橡皮：画错了顺手擦，比来回切工具快
    cv.addEventListener('contextmenu', function (e) {
      e.preventDefault();
      var pt = cellAt(e);
      if (!pt) return;
      pushUndo();
      dab(pt.x, pt.y, 0);
      afterEdit();
    });
  }

  function bindUi() {
    $$('#tools .px-tool').forEach(function (b) {
      b.onclick = function () {
        S.tool = b.getAttribute('data-tool');
        $$('#tools .px-tool').forEach(function (x) { x.classList.toggle('on', x === b); });
        var cv = $('#cv');
        cv.style.cursor = S.tool === 'pick' ? 'copy'
          : (S.tool === 'fill' ? 'cell' : 'crosshair');
      };
    });
    $$('#brushBtns button').forEach(function (b) {
      b.onclick = function () {
        S.brush = parseInt(b.getAttribute('data-b'), 10) || 1;
        $$('#brushBtns button').forEach(function (x) { x.classList.toggle('on', x === b); });
      };
    });
    $$('#sizeSeg button').forEach(function (b) {
      b.onclick = function () { changeSize(parseInt(b.getAttribute('data-sz'), 10)); };
    });
    $('#ckMirror').onchange = function () { S.mirror = this.checked; };

    $('#btnGrid').onclick = function () {
      S.grid = !S.grid;
      this.setAttribute('aria-pressed', S.grid ? 'true' : 'false');
      this.style.opacity = S.grid ? '' : '.55';
      S.dirty = true;
      schedulePaint();
      scheduleSave();
    };

    $('#btnUndo').onclick = undo;
    $('#btnRedo').onclick = redo;
    $('#btnClear').onclick = function () {
      if (!filledCount()) { toast('画布已经是空的'); return; }
      if (!confirm('清空整张画布？清空后可以用「↶ 撤销」找回来。')) return;
      pushUndo();
      S.cells = new Uint8Array(cellCount());
      afterEdit();
      toast('已清空');
    };
    $('#btnSave').onclick = function () { saveDraft(true); };
    $('#btnSubmit').onclick = function () { submitWork(); };

    $('#swatches').onclick = function (e) {
      var b = e.target.closest('.px-sw');
      if (!b) return;
      var c = b.getAttribute('data-c');
      if (c === 'add') { addCurColor($('#colorPick').value); return; }
      selectColor(parseInt(c, 10) || 0);
    };
    $('#colorPick').oninput = function () {
      var hex = String(this.value || '').toLowerCase();
      // 拖动取色时不改调色板，只在松手（change）时才落地，避免一路塞满
      var sw = $('#curSw'), hx = $('#curHex');
      if (sw) sw.style.background = hex;
      if (hx) hx.textContent = hex.toUpperCase();
    };
    $('#colorPick').onchange = function () { addCurColor(this.value); };

    $('#btnAddColor').onclick = function () { addCurColor($('#colorPick').value); };

    /* -------- 模板库（2.0.49） -------- */
    var btnTpl = $('#btnTpl');
    if (btnTpl) btnTpl.onclick = openTpl;
    var tplClose = $('#tplClose');
    if (tplClose) tplClose.onclick = closeTpl;
    var tplModal = $('#tplModal');
    if (tplModal) {
      // 点浮层空白处关掉（和作品大图那个浮层同一手感），点卡片本身不关
      tplModal.onclick = function (ev) { if (ev.target === tplModal) closeTpl(); };
    }
    var tplGrid = $('#tplGrid');
    if (tplGrid) {
      tplGrid.onclick = function (ev) {
        var btn = ev.target.closest ? ev.target.closest('button[data-act]') : null;
        if (!btn) return;
        var card = btn.closest('.px-tpl-card');
        if (!card) return;
        var t = TEMPLATES[+card.getAttribute('data-i')];
        if (!t) return;
        if (btn.getAttribute('data-act') === 'look') {
          setRef(t);
          closeTpl();
          toast('参考图在右侧「模板」栏，照着它一格一格画');
        } else if (applyTemplate(t)) {
          closeTpl();       // 用户在 confirm 里点了取消，就留在模板库
        }
      };
    }
    var refClose = $('#refClose');
    if (refClose) refClose.onclick = clearRef;
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') closeTpl();
    });

    $('#inpTitle').oninput = function () {
      S.title = this.value;
      S.dirty = true;
      scheduleSave();
    };

    $('#btnBack').onclick = function () {
      if (S.dirty) saveDraft(false);
      location.href = '/pixel/teacher.html';
    };

    document.addEventListener('keydown', function (e) {
      var mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      var k = (e.key || '').toLowerCase();
      if (k === 's') { e.preventDefault(); saveDraft(true); }
      else if (k === 'z' && !e.shiftKey) { e.preventDefault(); undo(); }
      else if (k === 'y' || (k === 'z' && e.shiftKey)) { e.preventDefault(); redo(); }
      else if (k === 'enter') { e.preventDefault(); submitWork(); }
    });

    // 切走 / 关页面前把没存的草稿补一次：keepalive 让请求在页面卸载后仍能发出
    window.addEventListener('pagehide', flushSave);
    window.addEventListener('beforeunload', flushSave);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') flushSave();
    });

    if (window.ResizeObserver) {
      new ResizeObserver(function () {
        if (S.rafId) return;
        S.rafId = requestAnimationFrame(function () {
          S.rafId = 0;
          layout();
        });
      }).observe($('#stage'));
    } else {
      window.addEventListener('resize', layout);
    }
  }

  function flushSave() {
    if (S.demo || !S.dirty || !S.loaded) return;
    try {
      fetch('/api/v2/pixel/draft', {
        method: 'POST', keepalive: true, credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ w: S.w, h: S.h, px: pxString(), pal: S.pal,
                               title: S.title, grid: S.grid ? 1 : 0 })
      });
      S.dirty = false;
    } catch (e) {}
  }

  function undo() {
    if (!S.undo.length) { toast('没有可撤销的了'); return; }
    S.redo.push(Uint8Array.from(S.cells));
    S.cells = S.undo.pop();
    afterEdit();
  }
  function redo() {
    if (!S.redo.length) { toast('没有可重做的了'); return; }
    S.undo.push(Uint8Array.from(S.cells));
    S.cells = S.redo.pop();
    afterEdit();
  }

  function changeSize(n) {
    if (!n || SIZES.indexOf(n) < 0 || (n === S.w && n === S.h)) return;
    if (filledCount() && !confirm('换成 ' + n + '×' + n + '？已画的部分会按左上角对齐保留，' +
        '超出新画布的部分会被裁掉。')) {
      return;
    }
    pushUndo();
    resetCells(n, n, true);
    markSize(n);
    afterEdit();
    layout();
  }

  function markSize(n) {
    $$('#sizeSeg button').forEach(function (b) {
      b.classList.toggle('on', parseInt(b.getAttribute('data-sz'), 10) === n);
    });
  }

  /* ================= 初始化 ================= */
  function applyCfg(cfg) {
    if (!cfg) return;
    S.cfg = Object.assign(S.cfg, cfg);
    var intro = $('#intro');
    if (intro) intro.textContent = S.cfg.intro || '';
    var sub = $('#brandSub');
    if (sub && S.cfg.course) sub.textContent = '一格一格画 · 做好一键交给老师';
  }

  function initPalette(pal) {
    S.pal = (pal && pal.length ? pal : PAL_DEFAULT.slice(0, 16)).map(function (c) {
      c = String(c || '').toLowerCase();
      return /^#[0-9a-f]{6}$/.test(c) ? c : '#2f2a26';
    }).filter(function (c, i, a) { return a.indexOf(c) === i; }).slice(0, MAX_PAL);
    if (!S.pal.length) S.pal = PAL_DEFAULT.slice(0, 16);
    S.cur = 0;
  }

  function load() {
    var qs = location.search || '';
    var practice = /[?&]practice=1/.test(qs);
    var backParam = /[?&]mode=teacher/.test(qs);
    if (backParam) {
      var bb = $('#btnBack');
      if (bb) bb.hidden = false;
    }
    // embed：嵌在学生端框架里，去掉底部留白
    if (/[?&]embed=1/.test(qs)) $('#app').classList.add('embed');
    if (practice) { S.practice = true; $('#btnSubmit').textContent = '导出图片'; }

    api('/api/v2/pixel/me' + (practice ? '?practice=1' : ''))
      .then(function (j) {
        if (!j.ok) throw new Error(j.msg || '读取失败');
        applyCfg(j.cfg);
        S.role = j.role || null;
        S.me = j.me || null;
        S.demo = !j.role || (j.role === 'teacher' && !j.practice);
        S.last = j.last || null;

        var d = j.draft || {};
        var size = S.cfg.size || 32;
        var w = SIZES.indexOf(d.w) >= 0 ? d.w : size;
        var h = SIZES.indexOf(d.h) >= 0 ? d.h : w;
        initPalette(d.pal);
        if (d.px && d.w && d.h) {
          resetCells(w, h, false);
          loadPx(d.px, w, h);
          S.title = d.title || '';
          S.grid = d.grid === 0 ? false : true;
        } else {
          resetCells(w, h, false);
          S.title = '';
          S.grid = true;
        }
        markSize(w);
        $('#inpTitle').value = S.title;
        $('#btnGrid').setAttribute('aria-pressed', S.grid ? 'true' : 'false');
        $('#btnGrid').style.opacity = S.grid ? '' : '.55';
        selectColor(0);
        S.loaded = true;

        paintDemo();
        layout();
        paintSwatches();
        if (d.updated) setSavedText('已保存 ' + hhmmFrom(d.updated), 'ok');
        else if (S.last) setSavedText('上次提交 ' + String(S.last.at || '').slice(5, 16), '');
        else setSavedText('未保存');
      })
      .catch(function (e) {
        // 连不上也要能画：进演示模式，至少不白屏
        S.demo = true;
        initPalette(null);
        resetCells(S.cfg.size || 32, S.cfg.size || 32, false);
        S.loaded = true;
        paintDemo();
        layout();
        paintSwatches();
        toast((e && e.message) || '读取失败，已切换到演示模式', 'err');
      });
  }

  function hhmmFrom(ts) {
    try {
      var d = new Date(ts * 1000);
      function p(n) { return (n < 10 ? '0' : '') + n; }
      return p(d.getHours()) + ':' + p(d.getMinutes());
    } catch (e) { return ''; }
  }

  function paintDemo() {
    var tip = $('#demoTip');
    if (!S.demo) { if (tip) tip.hidden = true; return; }
    if (!tip) return;
    tip.hidden = false;
    tip.textContent = S.practice
      ? '练习模式：随便画，进度会存到你自己名下；「导出图片」可存到本机。'
      : '演示模式：可以随便画，但不会保存、也不能提交。请从学生端「像素画」页签进入。';
  }

  syncTheme();
  watchTheme();
  bindCanvas();
  bindUi();
  load();
})();
