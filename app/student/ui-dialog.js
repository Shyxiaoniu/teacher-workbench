/* ============================================================
   学生端统一弹窗组件（2026-11-02）
   ------------------------------------------------------------
   替换原生 alert / confirm / 控制台式提示，统一为护眼暖色主题的
   提示框（alert）、确认框（confirm）、警告框（warn）、轻提示（toast）。
   · 三端共用：学生端主页面 app/student/index.html、打字冒险岛
     app/typing/student.html 都引这一份，样式只此一处，改一次全站生效。
   · 视觉语言对齐 style.css：暖白卡片 + 陶土橙主按钮 + 18~20px 大圆角 +
     拟物内高光 + 柔和投影；夜间模式跟随 html[data-mode="dark"]。
   · 交互：Esc / 点遮罩 = 取消，Enter = 确认；多个弹窗自动排队，
     不会互相覆盖；带焦点管理，键盘用户可直接回车确认。
   用法：
     WBUI.alert('已提交')                       // 提示框
     WBUI.alert({ title:'提交成功', message:'老师已收到' })
     WBUI.confirm({ title:'退出登录', message:'…' }).then(ok => {…})
     WBUI.warn({ title:'网络异常', message:'…' })  // 警告框（琥珀色）
     WBUI.toast('已保存')                        // 轻提示，不打断
   ============================================================ */
(function (w, d) {
  'use strict';
  if (w.WBUI && w.WBUI.alert) return;

  /* ---------------- 样式（只注入一次） ---------------- */
  var CSS = [
    ':root{',
    '--wbd-brand:#B45B33;--wbd-brand2:#C9713F;',
    '--wbd-surface:#FFFCF4;--wbd-border:rgba(180,91,51,.18);',
    '--wbd-ink:#3B322A;--wbd-ink2:#84756A;',
    '--wbd-soft:#F6E7D6;--wbd-warn:#B4570F;--wbd-warnSoft:#FDF1E4;',
    '--wbd-ok:#5A8C5E;--wbd-okSoft:#E9F1E7;--wbd-err:#C0392B;--wbd-errSoft:#FCEDEC;',
    '--wbd-ease:cubic-bezier(.22,.61,.36,1);',
    '}',

    /* 夜间：跟随学生端「护眼夜间」变量，避免弹窗在暗色页面里刺眼 */
    'html[data-mode="dark"]{',
    '--wbd-surface:#241B15;--wbd-border:rgba(222,138,99,.22);',
    '--wbd-ink:#F5EBE4;--wbd-ink2:#C0AC9F;',
    '--wbd-soft:rgba(222,138,99,.16);--wbd-warn:#EF9F27;--wbd-warnSoft:rgba(239,159,39,.16);',
    '--wbd-ok:#8CB99A;--wbd-okSoft:rgba(140,185,154,.16);',
    '--wbd-err:#E24B4A;--wbd-errSoft:rgba(226,75,74,.16);',
    '}',

    '.wbd-mask{position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;',
    'padding:22px;background:rgba(59,48,38,.42);',
    'backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);',
    'opacity:0;transition:opacity .18s var(--wbd-ease)}',
    '.wbd-mask.on{opacity:1}',

    '.wbd-card{width:min(420px,100%);background:var(--wbd-surface);border:1px solid var(--wbd-border);',
    'border-radius:20px;padding:22px 22px 18px;text-align:center;',
    'box-shadow:0 26px 60px rgba(74,44,24,.26),0 2px 0 rgba(255,255,255,.6) inset;',
    'transform:translateY(12px) scale(.97);opacity:0;',
    'transition:transform .22s var(--wbd-ease),opacity .22s var(--wbd-ease)}',
    '.wbd-mask.on .wbd-card{transform:none;opacity:1}',

    '.wbd-ico{width:52px;height:52px;border-radius:16px;margin:0 auto 12px;display:flex;align-items:center;',
    'justify-content:center;background:var(--wbd-soft);color:var(--wbd-brand);',
    'box-shadow:inset 0 1px 0 rgba(255,255,255,.7),0 4px 10px rgba(150,95,50,.12)}',
    '.wbd-ico svg{width:27px;height:27px;display:block}',
    '.wbd-tone-warn .wbd-ico{background:var(--wbd-warnSoft);color:var(--wbd-warn)}',
    '.wbd-tone-ok .wbd-ico{background:var(--wbd-okSoft);color:var(--wbd-ok)}',
    '.wbd-tone-err .wbd-ico{background:var(--wbd-errSoft);color:var(--wbd-err)}',

    '.wbd-title{font-size:17.5px;font-weight:700;color:var(--wbd-ink);margin:0 0 6px;line-height:1.4}',
    '.wbd-msg{font-size:14.5px;line-height:1.66;color:var(--wbd-ink2);white-space:pre-wrap;word-break:break-word}',
    /* 可选插画区（例如孵化成功时把新宠物放进弹窗） */
    '.wbd-extra{margin:12px auto 0;display:flex;justify-content:center;align-items:center}',
    '.wbd-extra svg{width:104px;height:104px;display:block}',

    '.wbd-btns{display:flex;gap:10px;justify-content:center;margin-top:18px}',
    '.wbd-btn{min-width:104px;padding:11px 20px;border-radius:12px;font-size:15px;font-weight:600;',
    'font-family:inherit;border:1.5px solid transparent;cursor:pointer;',
    'transition:transform .12s var(--wbd-ease),box-shadow .12s var(--wbd-ease),background .12s var(--wbd-ease)}',
    '.wbd-btn:active{transform:translateY(1px)}',
    '.wbd-btn.primary{background:linear-gradient(135deg,var(--wbd-brand),var(--wbd-brand2));color:#fff;',
    'box-shadow:0 6px 16px rgba(180,91,51,.28)}',
    '.wbd-btn.primary:hover{box-shadow:0 9px 22px rgba(180,91,51,.34)}',
    '.wbd-btn.ghost{background:var(--wbd-surface);color:var(--wbd-ink2);border-color:var(--wbd-border)}',
    '.wbd-btn.ghost:hover{background:var(--wbd-soft)}',
    '.wbd-btn.danger{background:linear-gradient(135deg,#C0392B,#D9563F);color:#fff;',
    'box-shadow:0 6px 16px rgba(192,57,43,.26)}',

    /* 窄屏：按钮竖排、主操作放上面（拇指更容易够到） */
    '@media (max-width:520px){',
    '.wbd-btns{flex-direction:column-reverse}.wbd-btn{width:100%}',
    '.wbd-card{padding:20px 18px 16px}',
    '}',

    /* 轻提示：底部胶囊，深暖底 + 状态小圆点 */
    '.wbd-toast-wrap{position:fixed;left:50%;bottom:96px;transform:translateX(-50%);z-index:1100;',
    'display:flex;flex-direction:column;gap:8px;align-items:center;pointer-events:none}',
    '.wbd-toast{display:flex;align-items:center;gap:9px;max-width:86vw;padding:10px 16px;border-radius:12px;',
    'background:rgba(59,42,30,.94);color:#FFF8EF;font-size:14px;line-height:1.5;',
    'box-shadow:0 10px 26px rgba(60,30,10,.24);',
    'opacity:0;transform:translateY(14px);transition:opacity .2s var(--wbd-ease),transform .2s var(--wbd-ease)}',
    '.wbd-toast.on{opacity:1;transform:none}',
    '.wbd-toast .dot{width:7px;height:7px;border-radius:50%;background:#E8B77A;flex:none}',
    '.wbd-toast.warn .dot{background:#F0B24A}',
    '.wbd-toast.ok .dot{background:#8FC79A}',
    '.wbd-toast.err .dot{background:#EE7B6C}'
  ].join('');

  function ensureStyle() {
    if (d.getElementById('wbd-style')) return;
    var s = d.createElement('style');
    s.id = 'wbd-style';
    s.textContent = CSS;
    (d.head || d.documentElement).appendChild(s);
  }

  /* ---------------- 图标 ---------------- */
  var ICONS = {
    info: '<circle cx="12" cy="12" r="8.7" fill="none" stroke="currentColor" stroke-width="1.8"/>' +
          '<path d="M12 11v5.4M12 7.9v.2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    ask: '<circle cx="12" cy="12" r="8.7" fill="none" stroke="currentColor" stroke-width="1.8"/>' +
         '<path d="M9.7 9.7a2.5 2.5 0 1 1 3.1 2.5c-.6.2-.9.7-.9 1.3v.4" stroke="currentColor" ' +
         'stroke-width="1.9" stroke-linecap="round" fill="none"/>' +
         '<path d="M11.9 16.9v.2" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    warn: '<path d="M12 4.3 20.6 19.4H3.4Z" fill="none" stroke="currentColor" stroke-width="1.8" ' +
          'stroke-linejoin="round"/>' +
          '<path d="M12 9.7v4.6M12 16.9v.2" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
    err: '<circle cx="12" cy="12" r="8.7" fill="none" stroke="currentColor" stroke-width="1.8"/>' +
         '<path d="m9.2 9.2 5.6 5.6M14.8 9.2l-5.6 5.6" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>',
    ok: '<circle cx="12" cy="12" r="8.7" fill="none" stroke="currentColor" stroke-width="1.8"/>' +
        '<path d="m8 12.3 2.9 2.9 5.2-5.4" fill="none" stroke="currentColor" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round"/>'
  };
  function icoSvg(key) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[key] || ICONS.info) + '</svg>';
  }

  /* ---------------- 弹窗队列 ---------------- */
  var queue = [], active = null;

  function normalize(o) {
    if (typeof o === 'string') return { message: o };
    return o || {};
  }

  function nextUp() {
    if (active || !queue.length) return;
    var job = queue.shift();
    active = job;
    mount(job);
  }

  function mount(job) {
    ensureStyle();
    var o = job.o;
    var tone = o.tone || 'info';
    var isConfirm = !!job.confirm;

    var mask = d.createElement('div');
    mask.className = 'wbd-mask wbd-tone-' + tone;
    mask.setAttribute('role', 'dialog');
    mask.setAttribute('aria-modal', 'true');

    var title = o.title || (isConfirm ? '请确认' : (tone === 'warn' ? '注意' : '提示'));
    var msg = o.message == null ? '' : String(o.message);
    var okText = o.confirmText || (isConfirm ? '确定' : '知道了');
    var cancelText = o.cancelText || '取消';
    var icon = tone === 'ok' ? 'ok' : tone === 'err' ? 'err' : tone === 'warn' ? 'warn' : (isConfirm ? 'ask' : 'info');
    var okCls = (tone === 'err' || o.danger) ? 'danger' : 'primary';

    mask.innerHTML =
      '<div class="wbd-card">' +
        '<div class="wbd-ico">' + icoSvg(icon) + '</div>' +
        '<div class="wbd-title">' + escapeHtml(title) + '</div>' +
        (msg ? '<div class="wbd-msg">' + escapeHtml(msg) + '</div>' : '') +
        (o.html ? '<div class="wbd-extra">' + String(o.html) + '</div>' : '') +
        '<div class="wbd-btns">' +
          (isConfirm ? '<button type="button" class="wbd-btn ghost" data-x="0">' + escapeHtml(cancelText) + '</button>' : '') +
          '<button type="button" class="wbd-btn ' + okCls + '" data-x="1">' + escapeHtml(okText) + '</button>' +
        '</div>' +
      '</div>';

    d.body.appendChild(mask);

    var btns = mask.querySelectorAll('[data-x]');
    var okBtn = mask.querySelector('[data-x="1"]');

    function close(val) {
      if (!active || active !== job) return;
      active = null;
      d.removeEventListener('keydown', onKey, true);
      mask.classList.remove('on');
      setTimeout(function () {
        if (mask.parentNode) mask.parentNode.removeChild(mask);
        job.resolve(isConfirm ? !!val : undefined);
        nextUp();
      }, 190);
    }
    job.close = close;

    Array.prototype.forEach.call(btns, function (b) {
      b.onclick = function () { close(b.getAttribute('data-x') === '1'); };
    });
    /* 点遮罩 = 取消（确认框）或关闭（提示框） */
    mask.addEventListener('mousedown', function (ev) {
      if (ev.target === mask) close(false);
    });

    function onKey(ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); close(false); }
      else if (ev.key === 'Enter') { ev.preventDefault(); close(true); }
    }
    d.addEventListener('keydown', onKey, true);

    requestAnimationFrame(function () {
      mask.classList.add('on');
      if (okBtn) { try { okBtn.focus({ preventScroll: true }); } catch (e) { okBtn.focus(); } }
    });
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* ---------------- 对外 API ---------------- */
  function openDialog(o, isConfirm) {
    return new Promise(function (resolve) {
      queue.push({ o: normalize(o), resolve: resolve, confirm: !!isConfirm });
      nextUp();
    });
  }

  var WBUI = {
    /** 提示框：只有一个「知道了」 */
    alert: function (o) { return openDialog(o, false); },
    /** 确认框：返回 Promise<boolean> */
    confirm: function (o) { return openDialog(o, true); },
    /** 警告框：琥珀色图标，语义是「有风险 / 需注意」，仍只有确认 */
    warn: function (o) {
      var opt = normalize(o);
      opt.tone = opt.tone || 'warn';
      if (!opt.title) opt.title = '注意';
      return openDialog(opt, false);
    },
    /** 轻提示：不打断操作，2.4s 自动消失 */
    toast: function (msg, tone) {
      ensureStyle();
      var wrap = d.querySelector('.wbd-toast-wrap');
      if (!wrap) {
        wrap = d.createElement('div');
        wrap.className = 'wbd-toast-wrap';
        d.body.appendChild(wrap);
      }
      var t = d.createElement('div');
      t.className = 'wbd-toast' + (tone ? ' ' + tone : '');
      t.innerHTML = '<span class="dot"></span><span class="txt"></span>';
      t.querySelector('.txt').textContent = String(msg == null ? '' : msg);
      wrap.appendChild(t);
      requestAnimationFrame(function () { t.classList.add('on'); });
      setTimeout(function () {
        t.classList.remove('on');
        setTimeout(function () {
          if (t.parentNode) t.parentNode.removeChild(t);
          if (wrap.parentNode && !wrap.children.length) wrap.parentNode.removeChild(wrap);
        }, 260);
      }, 2400);
    },
    version: '1.0.0'
  };

  w.WBUI = WBUI;
})(window, document);
