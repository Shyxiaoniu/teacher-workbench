/* 班级工作台 · H5 网页设计 · 学生设计器（studio.js）
   ------------------------------------------------------------
   一句话：左边写 HTML/CSS，右边实时预览，点一次「提交作品」=
   截图 + 存档 + 归档到「资料」+ 进教师端大屏。

   几个关键决定（改之前先读）：
   1. 预览用 iframe srcdoc + sandbox="allow-same-origin"（**不给 allow-scripts**）。
      学生的 <script> 不执行 —— 既能防住「一句 alert 把课堂卡住」，
      又让我们能读 contentDocument 做截图。本功能只教 HTML/CSS。
   2. 截图不走第三方库：把预览文档序列化成 SVG <foreignObject>，
      再画到 canvas 导出 PNG。样式全部内联在文档里（<style> 标签），
      所以不需要逐个元素算 computedStyle，渲染结果和预览一致。
   3. 外链图片会在导出前尝试内联成 dataURL；跨域失败就换成占位块。
      不这么做 canvas 会被污染，toDataURL 直接抛 SecurityError。
   4. 提交是一次请求带两个文件：PNG（大屏展示用）+ HTML（老师回看/二次改）。
   ------------------------------------------------------------ */
(function () {
  'use strict';

  var $ = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };

  var MAX_H = 3600;          // 预览/截图最大高度（再长就不是网页作业了）
  var MAX_PNG = 8 * 1024 * 1024;

  var S = {
    me: null, role: null, cfg: { enabled: false, allow_add: true, allow_submit: true,
                                 course: '网页设计', window: '网页设计作品', intro: '' },
    draft: null, last: null,
    tab: 'html', width: 1000, height: 900, scale: 1,
    dirty: false, saving: false, submitting: false, demo: false,
    lastSavedAt: 0,
    autoTimer: null
  };

  var pv = $('#pv'), stage = $('#stage'), holder = $('#holder'), canvas = $('#canvas');
  var edHtml = $('#edHtml'), edCss = $('#edCss');

  /* ---------------- 基础工具 ---------------- */
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  var toastTimer = null;
  function toast(msg, tone) {
    var t = $('#toast');
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

  /* ---------------- 主题跟随外层工作台 ----------------
     同源 iframe 能读父窗口的 CSS 变量：老师/学生切换主题后，
     设计器不用重载也是同一套配色。读不到（单独打开本页）就用默认暖色。 */
  var THEME_VARS = ['--brand', '--brand-2', '--brand-soft', '--accent', '--accent-soft',
                    '--bg', '--card', '--card-2', '--line', '--line-2',
                    '--ink', '--ink2', '--ink3', '--ok', '--warn', '--err',
                    '--warn-soft', '--err-soft', '--ok-soft', '--radius', '--radius-lg',
                    '--shadow', '--shadow-lg'];
  function syncTheme() {
    try {
      var p = window.parent;
      if (!p || p === window || !p.document) return;
      var pr = p.document.documentElement;
      var cs = p.getComputedStyle(pr);
      var pairs = [
        ['--brand', '--brand-primary'], ['--brand-2', '--brand-hover'],
        ['--brand-soft', '--brand-soft'], ['--bg', '--bg-page'],
        ['--card', '--bg-surface'], ['--card-2', '--bg-page'],
        ['--ink', '--text-primary'], ['--ink2', '--text-secondary'],
        ['--line', '--border'], ['--line-2', '--border']
      ];
      pairs.forEach(function (it) {
        var v = cs.getPropertyValue(it[1]);
        if (v && String(v).trim()) document.documentElement.style.setProperty(it[0], String(v).trim());
      });
      var mode = pr.getAttribute('data-mode') ||
        (p.document.body && p.document.body.getAttribute('data-mode')) || '';
      if (mode === 'dark') document.documentElement.setAttribute('data-mode', 'dark');
      else if (mode === 'light') document.documentElement.setAttribute('data-mode', 'light');
      else {
        var isDark = pr.getAttribute('data-theme') &&
          /dark/.test(pr.getAttribute('data-theme') || '');
        document.documentElement.setAttribute('data-mode', isDark ? 'dark' : 'light');
      }
      // 主题变量被覆盖后，重新量一次预览（圆角/字号可能变）
      fit();
    } catch (e) { /* 单独打开 / 跨域：保持默认 */ }
  }
  function watchTheme() {
    try {
      var p = window.parent;
      if (!p || p === window || !p.document) return;
      var mo = new MutationObserver(function () { syncTheme(); });
      mo.observe(p.document.documentElement, { attributes: true,
        attributeFilter: ['data-theme', 'data-mode', 'class', 'style'] });
      if (p.document.body) {
        mo.observe(p.document.body, { attributes: true, attributeFilter: ['class', 'style'] });
      }
    } catch (e) {}
  }

  /* ---------------- 模板 ---------------- */
  var BASE_CSS =
    'html,body{margin:0;padding:0}\n' +
    'body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;' +
    'color:#3b322a;background:#fff;line-height:1.7}\n' +
    'img{max-width:100%}\n';

  var TPL = {
    card: {
      name: '我的名片',
      html: '<div class="wrap">\n' +
        '  <div class="card">\n' +
        '    <div class="avatar">🐮</div>\n' +
        '    <h1>牛小牛</h1>\n' +
        '    <p class="sub">桂城一中 · 七年级（3）班</p>\n' +
        '    <p class="desc">喜欢画画、打篮球，正在学做网页。<br>这是我做的第一张电子名片。</p>\n' +
        '    <div class="tags">\n' +
        '      <span>网页设计</span><span>机器人</span><span>篮球</span>\n' +
        '    </div>\n' +
        '    <button class="btn">给我留言</button>\n' +
        '  </div>\n' +
        '</div>',
      css: 'body {\n' +
        '  background: linear-gradient(135deg, #fdf3e7, #f3ece0);\n' +
        '  padding: 48px 16px;\n' +
        '}\n' +
        '.wrap {\n' +
        '  display: flex;\n' +
        '  justify-content: center;\n' +
        '}\n' +
        '.card {\n' +
        '  width: 320px;\n' +
        '  background: #fff;\n' +
        '  border-radius: 20px;\n' +
        '  padding: 32px 26px;\n' +
        '  text-align: center;\n' +
        '  box-shadow: 0 16px 36px rgba(122, 82, 50, .14);\n' +
        '}\n' +
        '.avatar {\n' +
        '  width: 84px;\n' +
        '  height: 84px;\n' +
        '  margin: 0 auto 14px;\n' +
        '  border-radius: 50%;\n' +
        '  background: linear-gradient(135deg, #f6b26b, #b45b33);\n' +
        '  font-size: 42px;\n' +
        '  line-height: 84px;\n' +
        '}\n' +
        'h1 {\n' +
        '  margin: 0;\n' +
        '  font-size: 24px;\n' +
        '  letter-spacing: 1px;\n' +
        '}\n' +
        '.sub {\n' +
        '  margin: 4px 0 16px;\n' +
        '  font-size: 13px;\n' +
        '  color: #9a8b7f;\n' +
        '}\n' +
        '.desc {\n' +
        '  margin: 0 0 18px;\n' +
        '  font-size: 14px;\n' +
        '  color: #6b5d52;\n' +
        '}\n' +
        '.tags {\n' +
        '  display: flex;\n' +
        '  justify-content: center;\n' +
        '  gap: 8px;\n' +
        '  margin-bottom: 22px;\n' +
        '}\n' +
        '.tags span {\n' +
        '  font-size: 12px;\n' +
        '  padding: 4px 12px;\n' +
        '  border-radius: 999px;\n' +
        '  background: #f6e7d6;\n' +
        '  color: #b45b33;\n' +
        '}\n' +
        '.btn {\n' +
        '  width: 100%;\n' +
        '  height: 42px;\n' +
        '  border: none;\n' +
        '  border-radius: 12px;\n' +
        '  background: linear-gradient(135deg, #b45b33, #c9713f);\n' +
        '  color: #fff;\n' +
        '  font-size: 15px;\n' +
        '  cursor: pointer;\n' +
        '  transition: transform .2s, box-shadow .2s;\n' +
        '}\n' +
        '.btn:hover {\n' +
        '  transform: translateY(-2px);\n' +
        '  box-shadow: 0 10px 20px rgba(180, 91, 51, .3);\n' +
        '}'
    },

    home: {
      name: '个人主页',
      html: '<header class="top">\n' +
        '  <b>🐮 小牛的网页</b>\n' +
        '  <nav><a href="#">首页</a><a href="#">作品</a><a href="#">关于</a></nav>\n' +
        '</header>\n' +
        '<section class="hero">\n' +
        '  <h1>你好，我是牛小牛</h1>\n' +
        '  <p>一个喜欢折腾电脑的初中生，正在学习网页设计。</p>\n' +
        '</section>\n' +
        '<main class="grid">\n' +
        '  <div class="box"><div class="ic">🎨</div><h3>我的爱好</h3><p>画画、设计、配色。</p></div>\n' +
        '  <div class="box"><div class="ic">🤖</div><h3>机器人</h3><p>校队成员，负责编程。</p></div>\n' +
        '  <div class="box"><div class="ic">🏀</div><h3>运动</h3><p>每周三下午打篮球。</p></div>\n' +
        '</main>\n' +
        '<footer>© 2026 桂城一中 · 信息科技</footer>',
      css: 'body {\n' +
        '  background: #faf7f0;\n' +
        '}\n' +
        '.top {\n' +
        '  display: flex;\n' +
        '  align-items: center;\n' +
        '  gap: 20px;\n' +
        '  padding: 14px 28px;\n' +
        '  background: #fff;\n' +
        '  box-shadow: 0 2px 10px rgba(0, 0, 0, .05);\n' +
        '}\n' +
        '.top b {\n' +
        '  font-size: 16px;\n' +
        '}\n' +
        '.top nav {\n' +
        '  margin-left: auto;\n' +
        '  display: flex;\n' +
        '  gap: 18px;\n' +
        '}\n' +
        '.top a {\n' +
        '  color: #84756a;\n' +
        '  text-decoration: none;\n' +
        '  font-size: 14px;\n' +
        '}\n' +
        '.top a:hover {\n' +
        '  color: #b45b33;\n' +
        '}\n' +
        '.hero {\n' +
        '  padding: 64px 28px 54px;\n' +
        '  text-align: center;\n' +
        '  background: linear-gradient(135deg, #fdf0e3, #eef3ea);\n' +
        '}\n' +
        '.hero h1 {\n' +
        '  margin: 0 0 10px;\n' +
        '  font-size: 34px;\n' +
        '  letter-spacing: 2px;\n' +
        '}\n' +
        '.hero p {\n' +
        '  margin: 0;\n' +
        '  color: #84756a;\n' +
        '}\n' +
        '.grid {\n' +
        '  display: grid;\n' +
        '  grid-template-columns: repeat(3, 1fr);\n' +
        '  gap: 18px;\n' +
        '  max-width: 900px;\n' +
        '  margin: 34px auto;\n' +
        '  padding: 0 28px;\n' +
        '}\n' +
        '.box {\n' +
        '  background: #fff;\n' +
        '  border-radius: 16px;\n' +
        '  padding: 22px 18px;\n' +
        '  box-shadow: 0 6px 18px rgba(60, 30, 10, .07);\n' +
        '  transition: transform .2s;\n' +
        '}\n' +
        '.box:hover {\n' +
        '  transform: translateY(-4px);\n' +
        '}\n' +
        '.box .ic {\n' +
        '  font-size: 30px;\n' +
        '}\n' +
        '.box h3 {\n' +
        '  margin: 10px 0 6px;\n' +
        '  font-size: 17px;\n' +
        '}\n' +
        '.box p {\n' +
        '  margin: 0;\n' +
        '  font-size: 13.5px;\n' +
        '  color: #84756a;\n' +
        '}\n' +
        'footer {\n' +
        '  text-align: center;\n' +
        '  padding: 26px;\n' +
        '  font-size: 12.5px;\n' +
        '  color: #9a8b7f;\n' +
        '}'
    },

    gallery: {
      name: '图片画廊',
      html: '<h1 class="title">我的摄影小展</h1>\n' +
        '<p class="sub">把喜欢的三张照片排成一面墙（这里用色块代替照片）</p>\n' +
        '<div class="wall">\n' +
        '  <figure><div class="ph p1">🌅</div><figcaption>清晨的操场</figcaption></figure>\n' +
        '  <figure><div class="ph p2">🏫</div><figcaption>放学后的走廊</figcaption></figure>\n' +
        '  <figure><div class="ph p3">🌿</div><figcaption>窗台上的绿萝</figcaption></figure>\n' +
        '</div>',
      css: 'body {\n' +
        '  background: #f6f2ea;\n' +
        '  padding: 40px 28px;\n' +
        '}\n' +
        '.title {\n' +
        '  margin: 0;\n' +
        '  font-size: 28px;\n' +
        '  text-align: center;\n' +
        '  letter-spacing: 3px;\n' +
        '}\n' +
        '.sub {\n' +
        '  margin: 6px 0 28px;\n' +
        '  text-align: center;\n' +
        '  font-size: 13px;\n' +
        '  color: #9a8b7f;\n' +
        '}\n' +
        '.wall {\n' +
        '  display: grid;\n' +
        '  grid-template-columns: repeat(3, 1fr);\n' +
        '  gap: 16px;\n' +
        '  max-width: 900px;\n' +
        '  margin: 0 auto;\n' +
        '}\n' +
        'figure {\n' +
        '  margin: 0;\n' +
        '  background: #fff;\n' +
        '  border-radius: 12px;\n' +
        '  overflow: hidden;\n' +
        '  box-shadow: 0 8px 20px rgba(60, 30, 10, .12);\n' +
        '}\n' +
        '.ph {\n' +
        '  height: 170px;\n' +
        '  display: flex;\n' +
        '  align-items: center;\n' +
        '  justify-content: center;\n' +
        '  font-size: 46px;\n' +
        '  transition: transform .3s;\n' +
        '}\n' +
        '.p1 { background: linear-gradient(135deg, #ffd9a8, #f4a261); }\n' +
        '.p2 { background: linear-gradient(135deg, #cfe3f7, #8fb8d9); }\n' +
        '.p3 { background: linear-gradient(135deg, #d8ecd4, #9cc39a); }\n' +
        'figure:hover .ph {\n' +
        '  transform: scale(1.06);\n' +
        '}\n' +
        'figcaption {\n' +
        '  padding: 10px 12px;\n' +
        '  font-size: 12.5px;\n' +
        '  color: #6b5d52;\n' +
        '  text-align: center;\n' +
        '}'
    },

    blank: {
      name: '空白页',
      html: '<h1>我的网页</h1>\n<p>从这里开始写吧。</p>',
      css: 'body {\n  padding: 40px;\n}\nh1 {\n  color: #b45b33;\n}'
    }
  };

  /* ---------------- 预览 ---------------- */
  function buildDoc(html, css) {
    return '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8">' +
      '<style>' + BASE_CSS + '</style>\n<style>' + (css || '') + '</style>\n' +
      '</head><body>\n' + (html || '') + '\n</body></html>';
  }

  var renderSeq = 0;
  function renderPreview() {
    var seq = ++renderSeq;
    var doc = buildDoc(edHtml.value, edCss.value);
    return new Promise(function (resolve) {
      var done = false;
      function fin() {
        if (done || seq !== renderSeq) return;
        done = true;
        pv.removeEventListener('load', fin);
        try { hookPreview(); } catch (e) {}
        measure();
        resolve();
      }
      pv.addEventListener('load', fin);
      pv.srcdoc = doc;
      setTimeout(fin, 1800);   // 兜底：个别情况下 load 不触发也不能卡死提交
    });
  }

  /* 预览里点链接不要跳走：学生自己测 hover 是需要的，但跳转会把预览搞成
     404 / 外站，看起来像「网页坏了」。同源才能挂这个监听，挂不上就算了。 */
  function hookPreview() {
    var d = pv.contentDocument;
    if (!d || d.__wbHooked) return;
    d.__wbHooked = true;
    d.addEventListener('click', function (e) {
      var a = e.target && e.target.closest ? e.target.closest('a') : null;
      if (a) {
        e.preventDefault();
        toast('预览里点链接不会跳转，交上去以后由老师查看');
      }
    }, true);
    d.addEventListener('submit', function (e) { e.preventDefault(); }, true);
  }

  function measure() {
    var d = pv.contentDocument;
    if (!d) return;
    var h = Math.max(d.documentElement.scrollHeight || 0,
                     d.body ? d.body.scrollHeight : 0);
    h = Math.max(360, Math.min(MAX_H, h + 2));
    S.height = h;
    pv.style.width = S.width + 'px';
    pv.style.height = h + 'px';
    canvas.style.width = S.width + 'px';
    canvas.style.height = h + 'px';
    fit();
  }

  /* 预览不是 1:1 铺满：按可视宽度等比缩放，导出时仍以设计宽度渲染，
     这样「看到什么」和「交上去的图」永远一致。 */
  function fit() {
    var avail = Math.max(240, stage.clientWidth - 32);
    var k = Math.min(1, avail / S.width);
    S.scale = k;
    canvas.style.transform = 'scale(' + k + ')';
    holder.style.width = Math.round(S.width * k) + 'px';
    holder.style.height = Math.round(S.height * k) + 'px';
    var sz = $('#pvSize');
    if (sz) sz.textContent = S.width + ' × ' + S.height + (k < 0.999 ? '（' + Math.round(k * 100) + '%）' : '');
  }

  /* ---------------- 编辑器 ---------------- */
  function paintCount() {
    var n = (edHtml.value.length + edCss.value.length);
    var el = $('#stChars');
    if (el) el.textContent = '· ' + n + ' 字';
  }
  function setDirty(v) {
    S.dirty = !!v;
    var el = $('#stSaved');
    if (!el) return;
    if (S.dirty) { el.textContent = '有改动未保存'; el.style.color = 'var(--warn)'; }
    else {
      el.textContent = S.lastSavedAt ? ('已保存 ' + fmtTime(S.lastSavedAt)) : '未保存';
      el.style.color = '';
    }
  }
  function fmtTime(ts) {
    var d = new Date((Number(ts) || 0) * 1000);
    if (!ts) return '';
    return ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2) +
      ' ' + ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }

  var previewTimer = null;
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(function () { renderPreview(); }, 320);
  }

  function bindEditors() {
    [edHtml, edCss].forEach(function (ta) {
      ta.addEventListener('input', function () {
        setDirty(true);
        paintCount();
        schedulePreview();
      });
      /* Tab 键插入两个空格，而不是把焦点移走 —— 写代码时 Tab 换焦点很抓狂 */
      ta.addEventListener('keydown', function (e) {
        if (e.key === 'Tab') {
          e.preventDefault();
          var st = ta.selectionStart, en = ta.selectionEnd;
          var v = ta.value;
          ta.value = v.slice(0, st) + '  ' + v.slice(en);
          ta.selectionStart = ta.selectionEnd = st + 2;
          setDirty(true); paintCount(); schedulePreview();
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
          e.preventDefault(); saveDraft(true);
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault(); submitWork();
        }
      });
    });

    $$('.ds-tabs button[data-tab]').forEach(function (b) {
      b.onclick = function () {
        S.tab = b.getAttribute('data-tab');
        $$('.ds-tabs button[data-tab]').forEach(function (x) {
          x.classList.toggle('on', x === b);
        });
        edHtml.hidden = S.tab !== 'html';
        edCss.hidden = S.tab !== 'css';
        $('#tabHint').textContent = S.tab === 'html' ? '只写 body 里的内容' : '只写规则，不用写 <style>';
        (S.tab === 'html' ? edHtml : edCss).focus();
      };
    });

    $$('#devSeg button').forEach(function (b) {
      b.onclick = function () {
        S.width = Number(b.getAttribute('data-w')) || 1000;
        $$('#devSeg button').forEach(function (x) { x.classList.toggle('on', x === b); });
        measure();
      };
    });

    $('#btnRefresh').onclick = function () { renderPreview().then(function () { toast('已刷新预览'); }); };
  }

  /* ---------------- 模板 ---------------- */
  function fillTemplates() {
    var sel = $('#tplSel');
    sel.innerHTML = '<option value="">选择模板…</option>' +
      Object.keys(TPL).map(function (k) {
        return '<option value="' + k + '">' + esc(TPL[k].name) + '</option>';
      }).join('');
    sel.onchange = function () {
      var k = sel.value;
      sel.value = '';
      if (!k || !TPL[k]) return;
      var has = (edHtml.value.trim() || edCss.value.trim());
      if (has && !window.confirm('换成「' + TPL[k].name + '」会覆盖左边现在的代码，确定吗？\n（建议先点「保存草稿」）')) return;
      edHtml.value = TPL[k].html;
      edCss.value = TPL[k].css;
      setDirty(true); paintCount(); renderPreview();
      toast('已载入模板：' + TPL[k].name);
    };
  }

  /* ---------------- 知识库 ---------------- */
  var kbState = { cat: 'all', q: '' };
  function kbRender() {
    var kb = window.DESIGN_KB || { cats: [], items: [] };
    var cats = $('#kbCats');
    if (cats && !cats.childElementCount) {
      cats.innerHTML = kb.cats.map(function (c) {
        return '<button type="button" data-cat="' + c[0] + '"' +
          (c[0] === 'all' ? ' class="on"' : '') + '>' + esc(c[1]) + '</button>';
      }).join('');
      cats.onclick = function (e) {
        var b = e.target.closest('button[data-cat]');
        if (!b) return;
        kbState.cat = b.getAttribute('data-cat');
        $$('#kbCats button').forEach(function (x) { x.classList.toggle('on', x === b); });
        paintKbList();
      };
    }
    paintKbList();
  }
  function paintKbList() {
    var kb = window.DESIGN_KB || { items: [] };
    var q = kbState.q.trim().toLowerCase();
    var list = kb.items.filter(function (it) {
      if (kbState.cat !== 'all' && it.cat !== kbState.cat) return false;
      if (!q) return true;
      return (it.t + ' ' + it.d + ' ' + it.code).toLowerCase().indexOf(q) >= 0;
    });
    var box = $('#kbList');
    if (!list.length) {
      box.innerHTML = '<div class="kb-empty">没有找到「' + esc(kbState.q) + '」，换个关键词试试<br>（比如 flex、圆角、渐变、表格）</div>';
      return;
    }
    box.innerHTML = list.map(function (it, i) {
      return '<details class="kb-item">' +
        '<summary><b>' + esc(it.t) + '</b><span class="lg">' + esc(it.lang) + '</span></summary>' +
        '<div class="desc">' + esc(it.d) + '</div>' +
        '<pre>' + esc(it.code) + '</pre>' +
        '<div class="row"><button class="btn sm primary" data-ins="' + i + '">↳ 插入到代码</button></div>' +
        '</details>';
    }).join('');
    box.onclick = function (e) {
      var b = e.target.closest('button[data-ins]');
      if (!b) return;
      var it = list[Number(b.getAttribute('data-ins'))];
      if (it) insertCode(it);
    };
  }
  function insertCode(it) {
    var isCss = it.lang === 'css';
    var ta = isCss ? edCss : edHtml;
    if (isCss) {
      // 在 CSS 区插入时自动切过去，学生能看到插到哪儿了
      var b = $('.ds-tabs button[data-tab="css"]');
      if (b) b.click();
    } else {
      var b2 = $('.ds-tabs button[data-tab="html"]');
      if (b2) b2.click();
    }
    var st = ta.selectionStart, en = ta.selectionEnd;
    var v = ta.value;
    var pre = (st > 0 && v[st - 1] !== '\n') ? '\n' : '';
    var post = (en < v.length && v[en] !== '\n') ? '\n' : '';
    ta.value = v.slice(0, st) + pre + it.code + post + v.slice(en);
    ta.selectionStart = ta.selectionEnd = st + pre.length + it.code.length;
    setDirty(true); paintCount(); renderPreview();
    toast('已插入：' + it.t);
  }
  function bindKb() {
    var panel = $('#kbPanel');
    function open(v) {
      panel.classList.toggle('open', v);
      panel.setAttribute('aria-hidden', v ? 'false' : 'true');
    }
    $('#btnKb').onclick = function () { open(!panel.classList.contains('open')); };
    $('#kbClose').onclick = function () { open(false); };
    $('#kbSearch').addEventListener('input', function () {
      kbState.q = this.value || '';
      paintKbList();
    });
  }

  /* ---------------- 保存草稿 ---------------- */
  function saveDraft(manual) {
    if (S.demo) { if (manual) toast('演示模式下不保存，登录学生端后再试'); return Promise.resolve(false); }
    if (S.saving) return Promise.resolve(false);
    S.saving = true;
    var payload = { html: edHtml.value, css: edCss.value };
    return api('/api/v2/design/draft', { method: 'POST', body: JSON.stringify(payload) })
      .then(function (j) {
        S.saving = false;
        if (!j.ok) { if (manual) toast(j.msg || '保存失败', 'err'); return false; }
        S.lastSavedAt = j.updated || Math.floor(Date.now() / 1000);
        setDirty(false);
        if (manual) toast('草稿已保存，换台电脑登录也能接着写', 'ok');
        return true;
      }).catch(function () {
        S.saving = false;
        if (manual) toast('连不上服务器，草稿没保存', 'err');
        return false;
      });
  }

  /* ---------------- 截图 ---------------- */
  function placeholderImg(text) {
    var t = String(text || '图片').slice(0, 12);
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="240">' +
      '<rect width="400" height="240" fill="#eee8df"/>' +
      '<text x="200" y="126" font-size="16" fill="#9a8b7f" text-anchor="middle"' +
      ' font-family="sans-serif">' + escXml(t) + '</text></svg>';
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  }
  function escXml(s) {
    return String(s || '').replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* 把网络图片读成 dataURL：同源 / 允许 CORS 的能成功，
     其它（外站没给 CORS 头）换成占位块，绝不能让它污染 canvas。 */
  function toDataUrl(src) {
    if (!src) return Promise.resolve('');
    if (/^data:/i.test(src)) return Promise.resolve(src);
    return fetch(src, { mode: 'cors', credentials: 'omit' })
      .then(function (r) { if (!r.ok) throw new Error('http ' + r.status); return r.blob(); })
      .then(function (b) {
        return new Promise(function (res, rej) {
          var fr = new FileReader();
          fr.onload = function () { res(String(fr.result || '')); };
          fr.onerror = function () { rej(new Error('read')); };
          fr.readAsDataURL(b);
        });
      });
  }

  function stripExternalUrls(root) {
    // 外链背景图会让 canvas 被「污染」，toDataURL 直接抛 SecurityError。
    // 统一换成 none —— 页面其余部分照常渲染，不会整张图导不出来。
    var bad = /url\(\s*['"]?(?:https?:)?\/\/[^)]*\)/gi;
    root.querySelectorAll('[style]').forEach(function (el) {
      var s = el.getAttribute('style') || '';
      bad.lastIndex = 0;
      if (bad.test(s)) {
        bad.lastIndex = 0;
        el.setAttribute('style', s.replace(bad, 'none'));
      }
    });
    root.querySelectorAll('style').forEach(function (st) {
      var css = st.textContent || '';
      bad.lastIndex = 0;
      if (bad.test(css)) {
        bad.lastIndex = 0;
        st.textContent = css.replace(bad, 'none');
      }
    });
  }

  async function snapshot(scale) {
    var doc = pv.contentDocument;
    if (!doc || !doc.documentElement) throw new Error('预览还没准备好');
    var W = S.width;
    var H = S.height;

    var clone = doc.documentElement.cloneNode(true);
    clone.querySelectorAll('script,noscript,link,meta[http-equiv]').forEach(function (n) { n.remove(); });

    /* 图片：按顺序对应，能内联就内联，拿不到就换占位 */
    var srcImgs = doc.querySelectorAll('img');
    var dstImgs = clone.querySelectorAll('img');
    for (var i = 0; i < dstImgs.length; i++) {
      var s = srcImgs[i];
      var d = dstImgs[i];
      if (!s || !d) continue;
      var src = s.getAttribute('src') || '';
      var alt = s.getAttribute('alt') || '';
      try {
        var data = await toDataUrl(s.src || src);
        if (data) d.setAttribute('src', data);
        else d.setAttribute('src', placeholderImg(alt || '图片'));
      } catch (e) {
        d.setAttribute('src', placeholderImg(alt || '图片'));
      }
      d.removeAttribute('srcset');
      d.removeAttribute('loading');
    }
    stripExternalUrls(clone);

    /* 固定渲染尺寸：否则 foreignObject 里 <html> 没有宽度概念，会挤成一条 */
    clone.setAttribute('style', (clone.getAttribute('style') || '') +
      ';width:' + W + 'px;height:' + H + 'px;margin:0;padding:0;overflow:hidden');
    var body = clone.querySelector('body');
    if (body) {
      body.setAttribute('style', (body.getAttribute('style') || '') +
        ';width:' + W + 'px;min-height:' + H + 'px;margin:0');
    }

    var xml = new XMLSerializer().serializeToString(clone);
    if (xml.indexOf('xmlns="http://www.w3.org/1999/xhtml"') < 0) {
      xml = xml.replace(/^<html/, '<html xmlns="http://www.w3.org/1999/xhtml"');
    }
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" width="' + W + '" height="' + H + '" ' +
      'viewBox="0 0 ' + W + ' ' + H + '">' +
      '<foreignObject x="0" y="0" width="' + W + '" height="' + H + '">' +
      xml + '</foreignObject></svg>';

    var img = new Image();
    await new Promise(function (res, rej) {
      img.onload = res;
      img.onerror = function () { rej(new Error('网页渲染成图片失败')); };
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    });

    var cv = document.createElement('canvas');
    cv.width = Math.round(W * scale);
    cv.height = Math.round(H * scale);
    var ctx = cv.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.drawImage(img, 0, 0, cv.width, cv.height);
    return cv.toDataURL('image/png');
  }

  function busy(text) {
    var box = $('#busyBox');
    if (!text) { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="ds-busy"><div class="box"><div class="ring"></div>' +
      '<p>' + esc(text) + '</p></div></div>';
  }

  /* ---------------- 提交 ---------------- */
  async function submitWork() {
    if (S.submitting) return;
    if (S.demo) { toast('演示模式不能提交，请从学生端进入', 'err'); return; }
    if (S.role !== 'student') { toast('只有学生能提交作品', 'err'); return; }
    if (!S.cfg.enabled) { toast('老师还没有开启网页设计', 'err'); return; }
    if (!S.cfg.allow_submit) { toast('老师暂未开放提交，先保存草稿留着自己看', 'err'); return; }
    if (!edHtml.value.trim()) { toast('HTML 区还是空的，先写点内容吧', 'err'); return; }

    S.submitting = true;
    busy('正在生成预览图…');
    try {
      await renderPreview();
      await new Promise(function (r) { setTimeout(r, 120); });   // 等一帧，图片解码完
      var png = await snapshot(2);
      // 太大就降到 1 倍重截一次（长页面 / 图片多的情况）
      var est = Math.floor(png.length * 3 / 4);
      if (est > MAX_PNG) {
        busy('页面较长，正在压缩…');
        png = await snapshot(1);
        est = Math.floor(png.length * 3 / 4);
      }
      if (est > MAX_PNG) throw new Error('作品图片超过 8MB，请精简页面（少放几张图 / 缩短页面）');

      busy('正在提交并归档…');
      var j = await api('/api/v2/design/submit', {
        method: 'POST',
        body: JSON.stringify({ html: edHtml.value, css: edCss.value, png: png })
      });
      if (!j.ok) throw new Error(j.msg || '提交失败');
      S.last = { at: j.at, url: j.url };
      S.lastSavedAt = Math.floor(Date.now() / 1000);
      setDirty(false);
      busy('');
      receipt(j, png);
      toast('作品已提交', 'ok');
    } catch (e) {
      busy('');
      toast((e && e.message) || '提交失败，请重试', 'err');
    } finally {
      S.submitting = false;
    }
  }

  function receipt(j, png) {
    var box = $('#busyBox');
    box.innerHTML = '<div class="ds-receipt"><div class="box">' +
      '<div class="big">🎉</div>' +
      '<h3>作品已提交</h3>' +
      '<p>' + esc((S.me && S.me.name) || '') + ' · ' + esc(j.at || '') + '<br>' +
      '已保存为图片并归档到「' + esc(j.course || S.cfg.course) + '」' +
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

  /* ---------------- 启动 ---------------- */
  function applyCfg(cfg) {
    if (!cfg) return;
    S.cfg = Object.assign(S.cfg, cfg);
    var intro = $('#intro');
    if (intro) intro.textContent = S.cfg.intro || '';
  }

  function paintStateBar() {
    var t = $('#stTitle');
    if (!t) return;
    var who = (S.me && S.me.name) ? (S.me.name + (S.me.student_no ? '　' + S.me.student_no : '')) : '';
    var parts = [];
    if (who) parts.push(who);
    if (S.last && S.last.at) parts.push('上次提交 ' + S.last.at.slice(5, 16));
    else parts.push('还没提交过');
    t.textContent = parts.join(' · ');
  }

  function boot() {
    syncTheme();
    watchTheme();
    fillTemplates();
    bindEditors();
    bindKb();
    kbRender();
    $('#btnKb').classList.add('ghost');

    $('#btnSave').onclick = function () { saveDraft(true); };
    $('#btnSubmit').onclick = function () { submitWork(); };

    window.addEventListener('resize', fit);
    /* 不做 beforeunload 拦截：每次刷新预览都会换掉 iframe 文档，
       个别浏览器会因此弹「是否离开」——学生每打几个字就被问一次，很烦。
       改为「每 90 秒静默存一次」，真丢了也只丢一小段。 */
    /* 每 90 秒静默存一次：机房电脑说关就关，别让一节课白写 */
    S.autoTimer = setInterval(function () {
      if (S.dirty && !S.demo) saveDraft(false);
    }, 90000);

    api('/api/v2/design/me').then(function (j) {
      if (!j.ok) throw new Error(j.msg || '读取失败');
      applyCfg(j.cfg);
      S.role = j.role || null;
      if (j.role === 'student') {
        S.me = j.me || null;
        S.draft = j.draft || null;
        S.last = j.last || null;
        var d = j.draft || {};
        if ((d.html || '').trim() || (d.css || '').trim()) {
          edHtml.value = d.html || '';
          edCss.value = d.css || '';
        } else {
          edHtml.value = TPL.card.html;
          edCss.value = TPL.card.css;
        }
        if (!S.cfg.enabled) toast('老师还没有开启网页设计，可以先自己练练', 'err');
        else if (!S.cfg.allow_submit) toast('老师暂未开放提交，先写并保存草稿');
      } else {
        // 教师预览 / 未登录：给一份示例，能看能玩，不能提交
        S.demo = true;
        S.role = S.role || 'guest';
        edHtml.value = j.role === 'teacher' ? TPL.card.html : TPL.home.html;
        edCss.value = j.role === 'teacher' ? TPL.card.css : TPL.home.css;
        $('#btnSubmit').disabled = true;
        $('#btnSave').disabled = true;
        setTimeout(function () {
          toast(j.role === 'teacher' ? '这是学生端的样子（教师预览，不保存不提交）'
                                     : '请从学生端登录后使用');
        }, 400);
      }
      paintStateBar();
      setDirty(false);
      paintCount();
      renderPreview();
    }).catch(function () {
      S.demo = true;
      S.role = 'guest';
      edHtml.value = TPL.home.html;
      edCss.value = TPL.home.css;
      $('#btnSubmit').disabled = true;
      $('#btnSave').disabled = true;
      paintStateBar();
      renderPreview();
      toast('连不上服务器，当前是本地练习模式', 'err');
    });

    // 预览区第一次量尺寸（iframe 还没内容时也要有个底）
    measure();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
