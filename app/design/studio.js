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
    tab: 'html', width: 900, height: 900, scale: 1, contentHeight: 900,
    split: 0.5,   /* 左右分栏比例（代码区占多少），可拖分隔条调整 */
    /* mode（2.0.40）：代码区显示方式，hl=关键词高亮 / plain=普通文本 */
    mode: 'plain', practice: false,
    dirty: false, saving: false, submitting: false, demo: false,
    lastSavedAt: 0,
    /* 图片素材（2.0.41）：老师从「资料」下发的图片，列表缓存 + 分类过滤 */
    assets: null, assetCat: 'all', assetQ: '', assetView: 'grid',
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
    /* 默认模板（2.0.41）：内容用「测试内容」—— 一眼能看出是示例，
       学生照着改文字就行，不会被当成某个真人已经写好的作业。
       第一行注释在预览里不显示，只在代码区提示「这是示例」。 */
    card: {
      name: '我的名片（示例）',
      html: '<!-- 这是示例内容，把姓名 / 班级 / 爱好改成你自己的 -->\n' +
        '<div class="wrap">\n' +
        '  <div class="card">\n' +
        '    <div class="avatar">🎨</div>\n' +
        '    <h1>张三</h1>\n' +
        '    <p class="sub">示例中学 · 七年级（1）班</p>\n' +
        '    <p class="desc">这里写一句自我介绍：爱好、特长、正在学什么。<br>把这句话换成你自己的话。</p>\n' +
        '    <div class="tags">\n' +
        '      <span>标签一</span><span>标签二</span><span>标签三</span>\n' +
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
      name: '个人主页（示例）',
      html: '<!-- 这是示例内容，改成你自己的名字和介绍 -->\n' +
        '<header class="top">\n' +
        '  <b>🌟 张三的网页</b>\n' +
        '  <nav><a href="#">首页</a><a href="#">作品</a><a href="#">关于</a></nav>\n' +
        '</header>\n' +
        '<section class="hero">\n' +
        '  <h1>你好，我是张三</h1>\n' +
        '  <p>一句话介绍自己：喜欢什么、正在学什么。</p>\n' +
        '</section>\n' +
        '<main class="grid">\n' +
        '  <div class="box"><div class="ic">🎨</div><h3>我的爱好</h3><p>写一项爱好。</p></div>\n' +
        '  <div class="box"><div class="ic">🤖</div><h3>我的社团</h3><p>写一项社团活动。</p></div>\n' +
        '  <div class="box"><div class="ic">🏀</div><h3>我的运动</h3><p>写一项运动。</p></div>\n' +
        '</main>\n' +
        '<footer>© 2026 示例中学 · 信息科技</footer>',
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

    /* 空白页（2.0.41）：真的什么都不给 —— HTML 与 CSS 两个框都是空的，
       预览是一片白。raw:true 让 applyTemplate 跳过「补文档骨架」，
       否则学生会看到一个只有 <!DOCTYPE> 的壳，反而不知道从哪下手。 */
    blank: {
      name: '空白页',
      raw: true,
      html: '',
      css: ''
    }
  };

  /* ---------------- 预览 ---------------- */
  function buildDoc(html, css) {
    return '<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="UTF-8">' +
      '<style>' + BASE_CSS + '</style>\n<style>' + (css || '') + '</style>\n' +
      '</head><body>\n' + (html || '') + '\n</body></html>';
  }

  var renderSeq = 0;
  /** CSS 页签顶部附一段「平台已经加上的基础样式」清单（写成注释）。
      学生因此能看到这个页面实际用到的全部标签规则，又不会跟自己的样式打架。
      重复调用不会叠加：已经带过就原样返回。 */
  function cssWithHint(css) {
    css = String(css || '');
    if (css.indexOf('平台已自动给这个页面') >= 0) return css;
    var lines = BASE_CSS.replace(/\s+$/, '').split('\n').map(function (l) { return '   ' + l; });
    return '/* 平台已自动给这个页面加上下面这些基础样式（不用重复写）：\n'
      + lines.join('\n')
      + '\n   下面是这个页面自己的规则。 */\n' + css;
  }
  /** 把「片段」补成完整网页文档；本来就是完整文档的原样返回（2.0.41）。
      CSS 直接写进 <style> 段 —— 不再有单独的 CSS 窗口，学生在一个框里看到整页。 */
  function docOf(html, css) {
    var h = String(html || '');
    if (/<!DOCTYPE|<html[\s>]/i.test(h)) return h;
    var style = (BASE_CSS + '\n' + String(css || '')).replace(/\s+$/, '');
    return '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
      + '<meta charset="UTF-8">\n'
      + '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
      + '<title>我的网页</title>\n'
      + '<style>\n' + style + '\n</style>\n'
      + '</head>\n<body>\n'
      + h + '\n</body>\n</html>';
  }
  /** 预览用的最终文档（2.0.41）：一个代码区、一份文档，直接用 edHtml；
      样式就在它自己的 <style> 段里，不再从别处拼。只有老草稿是纯 body 片段时，
      才退回 buildDoc 把片段补成整页。 */
  function composeDoc() {
    var h = String(edHtml.value || '');
    if (/<!DOCTYPE|<html[\s>]/i.test(h)) return h;
    return buildDoc(h, edCss.value);
  }
  function renderPreview() {
    var seq = ++renderSeq;
    var doc = composeDoc();
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
    var content = Math.max(d.documentElement.scrollHeight || 0,
                           d.body ? d.body.scrollHeight : 0);
    content = Math.max(360, Math.min(MAX_H, content + 2));
    /* 内容真实高度：导出 PNG 时按这个裁，不会把「撑满用的白底」也导进去 */
    S.contentHeight = content;

    /* 预览画布至少撑满内容区：短页面（比如一张名片）下面不再是大片点阵，
       白色画布与左边代码区一样高，两个面板看起来才是「一样大」。 */
    var availW = Math.max(240, stage.clientWidth - 32);
    var k = Math.min(1, availW / S.width);
    var availH = Math.max(240, stage.clientHeight - 32);
    var h = content;
    if (k > 0) h = Math.max(h, Math.round(availH / k));
    h = Math.min(MAX_H, h);
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
    if (sz) {
      /* 显示「导出尺寸」，不是撑满后的画布高度：短页面撑满只是为了视觉对齐，
         真正交上去 / 导出的图还是内容那一段（900 × 501 这种）。 */
      sz.textContent = S.width + ' × ' + (S.contentHeight || S.height) +
        (k < 0.999 ? '（' + Math.round(k * 100) + '%）' : '');
    }
  }

  /* ---------------- 代码显示模式（2.0.40） ----------------
     两种模式，切换即时生效、随草稿一起保存：
       hl    = 关键词高亮：<pre> 层负责上色，<textarea> 透明叠在上面负责输入；
       plain = 普通文本：直接显示 textarea 原生文字，复制粘贴最省心。
     切换只改 class，不重建 textarea —— 光标、滚动位置、撤销栈都保住。 */
  var HL = { html: $('#hlHtml'), css: $('#hlCss') };

  function escHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /** 一个标签 token → 带色 HTML（标签名绿、属性名橙、属性值蓝、尖括号灰） */
  function hlTagToken(tok) {
    var m = /^<(\/?)([a-zA-Z][\w:-]*)([\s\S]*?)(\/?)>$/.exec(tok);
    if (!m) return escHtml(tok);
    var out = '<span class="tk-pun">&lt;' + m[1] + '</span>' +
      '<span class="tk-tag">' + escHtml(m[2]) + '</span>';
    var attrs = m[3] || '';
    var are = /([^\s=\/>]+)(\s*=\s*)("[^"]*"|'[^']*'|[^\s>]+)?/g;
    var a, idx = 0;
    while ((a = are.exec(attrs))) {
      if (a.index > idx) out += escHtml(attrs.slice(idx, a.index));
      out += '<span class="tk-attr">' + escHtml(a[1]) + '</span>';
      if (a[2]) out += escHtml(a[2]);
      if (a[3]) out += '<span class="tk-val">' + escHtml(a[3]) + '</span>';
      idx = a.index + a[0].length;
    }
    if (idx < attrs.length) out += escHtml(attrs.slice(idx));
    out += '<span class="tk-pun">' + (m[4] ? '/' : '') + '&gt;</span>';
    return out;
  }

  function hlHtmlSrc(src) {
    src = String(src || '');
    var out = '', i = 0;
    var re = /<!--[\s\S]*?-->|<\/?[a-zA-Z][^>]*>/g;
    var m;
    while ((m = re.exec(src))) {
      if (m.index > i) out += escHtml(src.slice(i, m.index));
      if (m[0].slice(0, 4) === '<!--') out += '<span class="tk-com">' + escHtml(m[0]) + '</span>';
      else out += hlTagToken(m[0]);
      i = m.index + m[0].length;
    }
    out += escHtml(src.slice(i));
    return out;
  }

  /** CSS：注释 / @规则 / 选择器 / 属性名 / 属性值 分开上色（够初中用，不追完整语法） */
  function hlCssSrc(src) {
    src = String(src || '');
    var out = '', i = 0, depth = 0, guard = 0;
    function pun(s) { return '<span class="tk-pun">' + s + '</span>'; }
    while (i < src.length) {
      /* 兜底闸：任何未预见的边界都不该让整页卡死（写错一个字符就白屏，代价太大） */
      if (++guard > 200000) break;
      if (src.substr(i, 2) === '/*') {
        var e = src.indexOf('*/', i + 2);
        var end = e < 0 ? src.length : e + 2;
        out += '<span class="tk-com">' + escHtml(src.slice(i, end)) + '</span>';
        i = end;
        continue;
      }
      var ch = src[i];
      if (ch === '{') { depth++; out += pun('{'); i++; continue; }
      if (ch === '}') { depth = Math.max(0, depth - 1); out += pun('}'); i++; continue; }
      var j = i;
      if (depth === 0) {
        while (j < src.length && src[j] !== '{' && src[j] !== '}' && src[j] !== ';') {
          if (src.substr(j, 2) === '/*') break;
          j++;
        }
        var seg = src.slice(i, j);
        if (/^\s*@/.test(seg)) out += '<span class="tk-at">' + escHtml(seg) + '</span>';
        else if (seg.trim()) out += '<span class="tk-sel">' + escHtml(seg) + '</span>';
        else out += escHtml(seg);
      } else {
        while (j < src.length && src[j] !== ';' && src[j] !== '}'
               && src.substr(j, 2) !== '/*') j++;
        var dec = src.slice(i, j);
        var dm = /^(\s*)([-\w]+)(\s*:\s*)([\s\S]*)$/.exec(dec);
        if (dm) {
          out += escHtml(dm[1]) +
            '<span class="tk-attr">' + escHtml(dm[2]) + '</span>' +
            escHtml(dm[3]) +
            '<span class="tk-val">' + escHtml(dm[4]) + '</span>';
        } else {
          out += escHtml(dec);
        }
      }
      i = j;
      /* 分号必须在这里吃掉：否则 j 停在原地、i 不前进，高亮会卡成死循环
         （2.0.40 实测：设计器一打开整页无响应，就是因为这一行）。 */
      if (i < src.length && src[i] === ';') { out += pun(';'); i++; }
    }
    return out;
  }

  function paintHL() {
    /* CSS 页签的对照区：HTML 用 HTML 语法上色，放在 CSS 上方做对照（2.0.41）。
       这一段放在模式判断之前 —— 普通文本模式下也要有内容，否则对照区是空白。 */
    var cmp = $('#cmpHtml');
    if (cmp) cmp.innerHTML = hlHtmlSrc(edHtml.value);
    if (S.mode !== 'hl') return;
    var panes = [[edHtml, HL.html], [edCss, HL.css]];
    panes.forEach(function (p) {
      var ta = p[0], pre = p[1];
      if (!ta || !pre) return;
      var top = ta.scrollTop, left = ta.scrollLeft;
      pre.innerHTML = (ta === edHtml) ? hlHtmlSrc(ta.value) : hlCssSrc(ta.value);
      pre.scrollTop = top;
      pre.scrollLeft = left;
    });
  }

  function applyMode(mode, markDirty) {
    S.mode = (String(mode) === 'plain') ? 'plain' : 'hl';
    var code = document.querySelector('.ds-code');
    if (code) code.classList.toggle('plain', S.mode === 'plain');
    var btn = $('#btnMode');
    if (btn) {
      btn.setAttribute('aria-pressed', S.mode === 'hl' ? 'true' : 'false');
      btn.title = S.mode === 'hl'
        ? '当前：关键词高亮（点一下切成普通文本）'
        : '当前：普通文本（点一下切成关键词高亮）';
    }
    var tx = $('#modeTxt');
    if (tx) tx.textContent = S.mode === 'hl' ? '关键词高亮' : '普通文本';
    paintHL();
    if (markDirty) setDirty(true);
  }

  function bindScrollSync(ta, pre) {
    if (!ta || !pre) return;
    ta.addEventListener('scroll', function () {
      pre.scrollTop = ta.scrollTop;
      pre.scrollLeft = ta.scrollLeft;
    });
  }

  /* ---------------- 左右分栏（2.0.40） ----------------
     拖动 .ds-split 实时分配代码区 / 预览区宽度：
       · 用 flex-grow 比例（--gl / --gr），不是写死 px —— 分隔条那 9px 自动扣掉，
         窗口 resize 后比例保持不变（拖动结果不会因为换窗口尺寸而跑掉）；
       · 每侧最小 280px，容器太窄时锁各半、不允许拖；拖到极限就停住；
       · 拖动中 rAF 节流调 measure()，预览缩放和画布高度实时跟着新宽度走；
       · 松手写 localStorage，下次打开还是这个比例；双击恢复各半，方向键微调。 */
  var SPLIT_KEY = 'wb-design-split';
  var SPLIT_MIN = 280;                    // 每侧最小可用宽度
  var SP = { on: false, w: 0, x0: 0, r0: 0.5, raf: 0 };

  function splitBody() { return document.querySelector('.ds-body'); }

  /** 把比例夹在「两边都不小于 SPLIT_MIN」的范围内；容器太窄就锁 0.5。
      注意要扣掉分隔条自己那 9px，否则两栏实际宽度会比 280 少 3~4px。 */
  function clampRatio(r, w) {
    if (!w) return 0.5;
    var inner = Math.max(1, w - 9);
    var minR = SPLIT_MIN / inner;
    if (minR > 0.5) return 0.5;
    return Math.max(minR, Math.min(1 - minR, r));
  }

  function setSplitRatio(r, live) {
    var body = splitBody();
    if (!body) return;
    r = Math.max(0.15, Math.min(0.85, r));
    S.split = r;
    body.style.setProperty('--gl', String(r));
    body.style.setProperty('--gr', String(1 - r));
    if (!live) return;
    if (SP.raf) return;
    SP.raf = requestAnimationFrame(function () {
      SP.raf = 0;
      measure();          // 预览缩放 / 画布高度跟着新宽度重算
    });
  }

  function saveSplit() {
    try { localStorage.setItem(SPLIT_KEY, String(Math.round(S.split * 1000) / 1000)); } catch (e) {}
  }

  function loadSplit() {
    var r = 0.5;
    try {
      var v = parseFloat(localStorage.getItem(SPLIT_KEY) || '');
      if (isFinite(v) && v >= 0.15 && v <= 0.85) r = v;
    } catch (e) {}
    setSplitRatio(r, false);
  }

  function bindSplit() {
    var sp = $('#split');
    var body = splitBody();
    if (!sp || !body) return;

    sp.addEventListener('pointerdown', function (e) {
      if (window.innerWidth <= 760) return;               // 窄屏是上下排，不拖
      var rect = body.getBoundingClientRect();
      if (rect.width < SPLIT_MIN * 2 + 9) return;         // 放不下两侧最小宽度
      SP.on = true;
      SP.w = rect.width;
      SP.x0 = e.clientX;
      SP.r0 = S.split;
      try { sp.setPointerCapture(e.pointerId); } catch (err) {}
      sp.classList.add('dragging');
      document.body.classList.add('ds-resizing');
      e.preventDefault();
    });

    sp.addEventListener('pointermove', function (e) {
      if (!SP.on) return;
      var r = SP.r0 + (e.clientX - SP.x0) / SP.w;
      setSplitRatio(clampRatio(r, SP.w), true);
    });

    function endDrag(e) {
      if (!SP.on) return;
      SP.on = false;
      sp.classList.remove('dragging');
      document.body.classList.remove('ds-resizing');
      try { sp.releasePointerCapture(e.pointerId); } catch (err) {}
      saveSplit();
      measure();
    }
    sp.addEventListener('pointerup', endDrag);
    sp.addEventListener('pointercancel', endDrag);

    sp.addEventListener('dblclick', function () {
      var rect = body.getBoundingClientRect();
      setSplitRatio(clampRatio(0.5, rect.width), true);
      saveSplit();
    });

    /* 键盘也能调：左右方向键 2%，Home 回到各半（拖拽条可聚焦） */
    sp.addEventListener('keydown', function (e) {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight' && e.key !== 'Home') return;
      e.preventDefault();
      var rect = body.getBoundingClientRect();
      var r = S.split;
      if (e.key === 'ArrowLeft') r -= 0.02;
      else if (e.key === 'ArrowRight') r += 0.02;
      else r = 0.5;
      setSplitRatio(clampRatio(r, rect.width), true);
      saveSplit();
    });
  }

  /* ---------------- 滚动同步（2.0.40） ----------------
     代码区 ↔ 预览区双向跟随。两个窗口的滚动来源必须分开，否则 A→B→A 会打架：

       · 用户触发 —— 滚轮 / 拖滚动条 / 键盘翻页，产生真实 scroll 事件；
       · 程序触发 —— syncCodeToPreview 给 stage 赋 scrollTop、
                     syncPreviewToCode 给 textarea 赋 scrollTop，
                     以及高亮层 pre 跟随 textarea（bindScrollSync）。
     区分办法：赋值前记下「时间 + 目标值」；事件到达时若「刚刚（150ms 内）
     且当前位置正是当时设的值」，就判定是自己的回声，跳过。

     位置映射：不按像素一一对应 —— 一行 HTML 可能渲染出几百像素高，
     硬对必然错位。这里把两边各自的「可滚动范围」归一化到 0~1 再换算，
     保证滚到顶 / 滚到底严格对齐、中间平滑跟随，也天然消化了两端
     行高（17.5px 一行的代码）与渲染高度（页面上百像素的区块）的差异。 */
  var SS = {
    /* 程序最后一次给某个容器设的目标值：{v, at}。
       用户一有真实操作（滚轮 / 按滚动条 / 键盘）就立刻清空，
       所以「是不是自己的回声」不靠猜时间窗，而是「值还是不是我刚设的那个」。 */
    prog: { code: { v: 0, at: 0 }, prev: { v: 0, at: 0 } },
    raf: { code: 0, prev: 0 }
  };
  var SS_ECHO_MS = 1500;         // 兜底：程序赋值超过这个时间就不再当回声

  function scrollMax(el) {
    return Math.max(0, (el.scrollHeight || 0) - (el.clientHeight || 0));
  }
  function ratioTo(srcTop, srcMax, dstMax) {
    if (srcMax <= 0 || dstMax <= 0) return 0;
    var r = Math.max(0, Math.min(1, srcTop / srcMax));
    return Math.round(r * dstMax);
  }
  function activeTa() { return S.tab === 'css' ? edCss : edHtml; }

  /* 程序触发：赋值并记下目标值（赋完读一次，拿到浏览器 clamp 后的真实值） */
  function markProg(key, el, v) {
    el.scrollTop = v;
    SS.prog[key] = { v: el.scrollTop, at: Date.now() };
  }
  /* 用户操作：清掉回声标记，之后的 scroll 一定是真实交互 */
  function clearProg(key) {
    SS.prog[key] = { v: 0, at: 0 };
  }
  function isEcho(key, cur) {
    var p = SS.prog[key];
    return !!p.at && (Date.now() - p.at) < SS_ECHO_MS && Math.abs(cur - p.v) <= 2;
  }

  /* 程序触发：代码 → 预览 */
  function syncCodeToPreview() {
    var ta = activeTa();
    var a = scrollMax(ta), b = scrollMax(stage);
    if (!a || !b) return;
    var y = ratioTo(ta.scrollTop, a, b);
    if (Math.abs(stage.scrollTop - y) < 1) return;
    markProg('prev', stage, y);
  }

  /* 程序触发：预览 → 代码（高亮层随后由 bindScrollSync 自己跟上） */
  function syncPreviewToCode() {
    var ta = activeTa();
    var a = scrollMax(ta), b = scrollMax(stage);
    if (!a || !b) return;
    var y = ratioTo(stage.scrollTop, b, a);
    if (Math.abs(ta.scrollTop - y) < 1) return;
    markProg('code', ta, y);
  }

  function bindPreviewSync() {
    [edHtml, edCss].forEach(function (ta) {
      /* 用户操作：滚轮 / 拖滚动条 / 键盘翻页 —— 先清标记，避免被当成回声 */
      ta.addEventListener('wheel', function () { clearProg('code'); }, { passive: true });
      ta.addEventListener('pointerdown', function () { clearProg('code'); }, { passive: true });
      ta.addEventListener('keydown', function () { clearProg('code'); }, { passive: true });
      ta.addEventListener('scroll', function () {
        if (SS.raf.code) return;
        SS.raf.code = requestAnimationFrame(function () {
          SS.raf.code = 0;
          if (ta !== activeTa()) return;                 // 隐藏的那个页签不参与
          if (isEcho('code', ta.scrollTop)) return;      // 程序触发的回声
          syncCodeToPreview();                            // 用户触发：带动预览
        });
      });
    });

    stage.addEventListener('wheel', function () { clearProg('prev'); }, { passive: true });
    stage.addEventListener('pointerdown', function () { clearProg('prev'); }, { passive: true });
    stage.addEventListener('keydown', function () { clearProg('prev'); }, { passive: true });
    stage.addEventListener('scroll', function () {
      if (SS.raf.prev) return;
      SS.raf.prev = requestAnimationFrame(function () {
        SS.raf.prev = 0;
        if (isEcho('prev', stage.scrollTop)) return;
        syncPreviewToCode();                              // 用户触发：带动代码
      });
    });
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
        paintHL();
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
          setDirty(true); paintCount(); paintHL(); schedulePreview();
        }
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
          e.preventDefault(); saveDraft(true);
        }
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
          e.preventDefault();
          (S.practice ? exportPng() : submitWork());
        }
      });
    });
    bindScrollSync(edHtml, HL.html);
    bindScrollSync(edCss, HL.css);

    $$('.ds-tabs button[data-tab]').forEach(function (b) {
      b.onclick = function () {
        S.tab = b.getAttribute('data-tab');
        $$('.ds-tabs button[data-tab]').forEach(function (x) {
          x.classList.toggle('on', x === b);
        });
        /* HTML / CSS 仍是两个页签，切换时切 pane 显隐（原布局不变） */
        $$('.code-pane').forEach(function (p) {
          p.hidden = p.getAttribute('data-pane') !== S.tab;
        });
        var HINT = { html: '整个网页的代码（样式只有占位，规则写在 CSS 页签）',
                     css: '这里只写样式规则，页面用到的标签见顶部注释' };
        $('#tabHint').textContent = HINT[S.tab] || '';
        (S.tab === 'html' ? edHtml : edCss).focus();
        paintHL();
        /* 换页签后预览也跟到这一份代码的阅读位置（用户点页签 = 用户触发） */
        syncCodeToPreview();
      };
    });

    var mb = $('#btnMode');
    if (mb) {
      mb.onclick = function () {
        applyMode(S.mode === 'hl' ? 'plain' : 'hl', true);
        toast(S.mode === 'hl' ? '已切换：关键词高亮' : '已切换：普通文本显示');
      };
    }

    $$('#devSeg button').forEach(function (b) {
      b.onclick = function () {
        S.width = Number(b.getAttribute('data-w')) || 900;
        paintDevSeg();
        measure();
        setDirty(true);
      };
    });

    $('#btnRefresh').onclick = function () { renderPreview().then(function () { toast('已刷新预览'); }); };
  }

  /** 设备宽度按钮的高亮状态：从草稿恢复宽度时也要对得上 */
  function paintDevSeg() {
    $$('#devSeg button').forEach(function (x) {
      x.classList.toggle('on', Number(x.getAttribute('data-w')) === S.width);
    });
  }

  /* ---------------- 模板 ---------------- */
  function applyTemplate(k) {
    if (!TPL[k]) return;
    edHtml.value = TPL[k].raw ? '' : docOf(TPL[k].html, TPL[k].css);
    edCss.value = '';   // 样式已经写进 HTML 的 <style> 里，隐藏框不参与
    edCss.value = TPL[k].css;
    edHtml.scrollTop = 0; edCss.scrollTop = 0;
    setDirty(true);
    paintCount();
    /* 2.0.41 修复：高亮模式下可见的是 <pre> 层（textarea 文字透明），
       填完值不重绘高亮层，学生看到的就是「模板选了但左边没变」。
       这一步以前漏了，现在补上。 */
    paintHL();
    renderPreview();
    toast('已载入模板：' + TPL[k].name, 'ok');
  }
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
      if (!has) { applyTemplate(k); return; }
      /* 会覆盖现有代码 → 走工作台统一样式的确认弹窗（不再用浏览器原生 confirm） */
      var msg = '换成「' + TPL[k].name + '」会覆盖左边现在的代码。\n建议先点「保存草稿」，再换模板。';
      if (window.WBUI && WBUI.confirm) {
        WBUI.confirm({ title: '换模板会覆盖当前代码', message: msg, tone: 'warn',
                       confirmText: '换模板', cancelText: '再想想' })
          .then(function (ok) { if (ok) applyTemplate(k); });
      } else if (window.confirm(msg)) {
        applyTemplate(k);
      }
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
    $('#btnKb').onclick = function () {
      open(!panel.classList.contains('open'));
      if (panel.classList.contains('open')) closeImgPanel();   // 两个抽屉互斥
    };
    $('#kbClose').onclick = function () { open(false); };
    $('#kbSearch').addEventListener('input', function () {
      kbState.q = this.value || '';
      paintKbList();
    });
  }

  /* ---------------- 图片素材（2.0.41） ----------------
     链路：教师端「资料」上传图片 → 下发给本班 → 学生端「资料」页看到
     → 点「加到网页」记进 localStorage → 这里列出并一键插进代码。
     地址统一 /cw/<文件id> 闸门（同源 + 学生会话），导出 PNG 时能内联成 dataURL，
     所以不依赖外网，机房断网也照样出图。 */
  var ASSET_KEY = 'wb-design-assets';
  /* 移除列表（2.0.41）：删素材只影响「我的素材库」，不动物理文件，
     所以用这个键记住「哪些不想再看到」。两端同一套规则。 */
  var ASSET_HIDE = 'wb-design-assets-off';

  function assetHidden() {
    try {
      var m = JSON.parse(localStorage.getItem(ASSET_HIDE) || '{}');
      return (m && typeof m === 'object') ? m : {};
    } catch (e) { return {}; }
  }
  /** 移除一张素材：先从「我加的」删掉，再记进隐藏表。
      返回 true 表示已生效；写不进去（隐私模式）返回 false 让上层提示。 */
  function removeAsset(a) {
    if (!a) return false;
    try {
      var list = assetPicked().filter(function (x) { return String(x.id) !== String(a.id); });
      localStorage.setItem(ASSET_KEY, JSON.stringify(list));
      var m = assetHidden();
      m[String(a.id)] = 1;
      localStorage.setItem(ASSET_HIDE, JSON.stringify(m));
      return true;
    } catch (e) {
      return false;
    }
  }
  /** 删除前确认：走工作台统一弹窗，两端文案一致 */
  function askRemoveAsset(a) {
    var name = (a && a.title) || '图片';
    var msg = '把「' + name + '」从图片素材里移除？\n\n'
      + '只从这个素材列表移除，不会删除老师资料里的原文件，随时可以从「＋ 从资料中添加文件」再加回来。';
    var doIt = function () {
      if (!removeAsset(a)) {
        toast('移除失败：浏览器不允许写本地存储（可能是隐私模式）', 'err');
        return;
      }
      S.assets = null;
      loadAssets(true).then(function () {
        paintAssetCats();
        paintAssets();
      });
      toast('已移除：' + name, 'ok');
    };
    if (window.WBUI && WBUI.confirm) {
      WBUI.confirm({ title: '移除图片素材', message: msg, tone: 'warn',
                     confirmText: '移除', cancelText: '取消' })
        .then(function (ok) { if (ok) doIt(); });
    } else if (window.confirm(msg)) {
      doIt();
    }
  }

  /** 学生在「资料」页点过「加到网页」的图片（只用于分组，不影响能否插入） */
  function assetPicked() {
    try {
      var a = JSON.parse(localStorage.getItem(ASSET_KEY) || '[]');
      return Array.isArray(a) ? a : [];
    } catch (e) { return []; }
  }
  window.wbAssetPick = function (id, title, url) {
    var list = assetPicked();
    if (!list.some(function (x) { return String(x.id) === String(id); })) {
      list.unshift({ id: String(id), title: title || '', url: url || '', at: Date.now() });
      try {
        localStorage.setItem(ASSET_KEY, JSON.stringify(list.slice(0, 80)));
        /* 之前移除过的，重新添加时要把「隐藏」标记摘掉，否则加了也看不见 */
        var off = assetHidden();
        if (off[String(id)]) {
          delete off[String(id)];
          localStorage.setItem(ASSET_HIDE, JSON.stringify(off));
        }
      } catch (e) {}
    }
    S.assets = null;
    return true;
  };

  function loadAssets(force) {
    if (S.assets && !force) return Promise.resolve(S.assets);
    return api('/api/v2/design/assets').then(function (j) {
      if (j && j.ok) { S.assets = j.assets || []; return S.assets; }
      S.assets = [];
      return [];
    }).catch(function () { S.assets = []; return []; });
  }

  function assetCats() {
    var rows = S.assets || [];
    var picked = assetPicked();
    var cats = [{ k: 'all', n: '全部 ' + rows.length }];
    if (picked.length) cats.push({ k: 'mine', n: '我加的 ' + picked.length });
    var seen = {};
    rows.forEach(function (a) {
      var c = String(a.course || '').trim();
      if (!c || seen[c]) return;
      seen[c] = 1;
      cats.push({ k: 'c:' + c, n: c });
    });
    return cats;
  }

  function paintAssetCats() {
    var box = $('#imgCats');
    if (!box) return;
    box.innerHTML = assetCats().map(function (c) {
      return '<button type="button" data-acat="' + esc(c.k) + '"' +
        (S.assetCat === c.k ? ' class="on"' : '') + '>' + esc(c.n) + '</button>';
    }).join('');
  }

  function paintAssets() {
    var box = $('#imgList');
    if (!box) return;
    /* 多视图（2.0.41）：网格 / 列表共用同一份 DOM，只切一个 class */
    box.classList.toggle('list', S.assetView === 'list');
    var rows = S.assets;
    if (!rows) {
      box.innerHTML = '<div class="kb-empty" style="grid-column:1/-1">正在读取图片…</div>';
      return;
    }
    var picked = assetPicked();
    /* 兜底（2.0.41）：服务端一条都没给（老师还没下发，或接口在老版本上不存在）时，
       把学生自己在资料页点过「加到网页」的那几张列出来 —— 加过却看不到最让人困惑。 */
    if (!rows.length && picked.length) {
      rows = picked.map(function (x) {
        return { id: x.id, title: x.title || '图片', url: x.url || '', course: '' };
      });
    }
    var pickedIds = {};
    picked.forEach(function (x) { pickedIds[String(x.id)] = 1; });
    var q = String(S.assetQ || '').trim().toLowerCase();
    var list = rows.filter(function (a) {
      if (S.assetCat === 'mine' && !pickedIds[String(a.id)]) return false;
      if (S.assetCat.indexOf('c:') === 0 &&
          String(a.course || '').trim() !== S.assetCat.slice(2)) return false;
      if (q && String(a.title || '').toLowerCase().indexOf(q) < 0) return false;
      return true;
    });
    /* 「我加的」排前面：学生刚在资料页点过的，一眼就能找到 */
    list.sort(function (a, b) {
      var pa = pickedIds[String(a.id)] ? 0 : 1, pb = pickedIds[String(b.id)] ? 0 : 1;
      if (pa !== pb) return pa - pb;
      return String(a.title || '').localeCompare(String(b.title || ''));
    });
    /* 移除过的先剔掉（2.0.41）：删除只影响这个列表，原文件还在资料里 */
    var off = assetHidden();
    list = list.filter(function (a) { return !off[String(a.id)]; });
    if (!list.length) {
      /* 教师练习看的是「课件库全部图片」，学生看的是「老师下发的图片」——
         两种情况空态说法不一样，别都写「老师还没下发」（教师端会说错话）。 */
      var isTeacher = !!(S.practice || S.role === 'teacher');
      box.innerHTML = '<div class="kb-empty" style="grid-column:1/-1">' +
        (q ? '没找到这个名字的图片'
           : (rows.length ? '这个分类下还没有图片'
                          : (isTeacher
                             ? '课件库里还没有图片。<br>先去「资料」上传几张，这里就能直接选用。'
                             : (picked.length
                                ? '这里显示的是你加到网页的 ' + picked.length + ' 张图；老师下发新图片后会一起出现在「全部」里。'
                                : '老师还没下发图片。<br>等老师在「资料」里推送后，这里就有了')))) +
        '</div>';
      return;
    }
    box.innerHTML = list.map(function (a, i) {
      var isNew = pickedIds[String(a.id)];
      return '<div class="img-card" data-aidx="' + i + '" title="' + esc(a.title || '') + '">' +
        '<div class="bd">' +
          '<img class="th" src="' + esc(a.url) + '" alt="' + esc(a.title || '') + '" loading="lazy"' +
          ' onerror="this.classList.add(\'bad\');this.removeAttribute(\'src\')">' +
          (isNew ? '<span class="tag-new">已加</span>' : '') +
          '<button type="button" class="img-del" data-del="' + i + '"' +
          ' title="从图片素材里移除（不删原文件）">×</button>' +
          '<span class="pick">插入</span>' +
        '</div>' +
        '<span class="nm">' + esc(a.title || '图片') +
          (a.course ? '<i class="cs">' + esc(a.course) + '</i>' : '') + '</span>' +
        '</div>';
    }).join('');
    box.onclick = function (e) {
      /* 先看是不是点了删除：删除按钮不能顺带把图插进代码里 */
      var del = e.target.closest ? e.target.closest('[data-del]') : null;
      if (del) {
        e.stopPropagation();
        var host = del.closest('[data-aidx]');
        var one = host ? list[Number(host.getAttribute('data-aidx'))] : null;
        if (one) askRemoveAsset(one);
        return;
      }
      var card = e.target.closest('[data-aidx]');
      if (!card) return;
      var a = list[Number(card.getAttribute('data-aidx'))];
      if (a) insertAsset(a);
    };
  }

  /** 点一张图 → 按当前页签插代码：HTML 区插 <img>，CSS 区插背景图 */
  function insertAsset(a) {
    if (!a || !a.url) return;
    var css = (S.tab === 'css');
    var alt = String(a.title || '图片').replace(/"/g, '');
    var snippet = css
      ? 'background-image: url("' + a.url + '");\nbackground-size: cover;\nbackground-position: center;'
      : '<img src="' + a.url + '" alt="' + alt + '" width="320">';
    var ta = css ? edCss : edHtml;
    var st = ta.selectionStart, en = ta.selectionEnd;
    var v = ta.value;
    var nl = (st > 0 && v[st - 1] !== '\n') ? '\n' : '';
    var nl2 = (en < v.length && v[en] !== '\n') ? '\n' : '';
    ta.value = v.slice(0, st) + nl + snippet + nl2 + v.slice(en);
    ta.selectionStart = ta.selectionEnd = st + nl.length + snippet.length;
    setDirty(true); paintCount(); paintHL(); renderPreview();
    toast(css ? '已插入背景图样式：' + (a.title || '图片')
              : '已插入图片：' + (a.title || '图片'), 'ok');
  }

  function closeImgPanel() {
    var p = $('#imgPanel');
    if (!p) return;
    p.classList.remove('open');
    p.setAttribute('aria-hidden', 'true');
  }

  /* ---------------- 完整代码视图（2.0.41） ----------------
     HTML 页签里现在放的就是完整网页代码，这里保留一个取值入口给状态栏用。 */
  function paintFullCode() {}
  function bindImg() {
    var panel = $('#imgPanel');
    if (!panel) return;
    /* 视图切换（2.0.41）：网格看大图挑图，列表看名字与归属；选择记在本机 */
    var vbar = $('#imgView');
    try { S.assetView = localStorage.getItem('wb-design-imgview') === 'list' ? 'list' : 'grid'; } catch (e) {}
    var paintView = function () {
      $$('#imgView button').forEach(function (b) {
        b.classList.toggle('on', b.getAttribute('data-view') === S.assetView);
      });
    };
    if (vbar) {
      paintView();
      vbar.onclick = function (e) {
        var b = e.target.closest ? e.target.closest('button[data-view]') : null;
        if (!b) return;
        S.assetView = b.getAttribute('data-view') === 'list' ? 'list' : 'grid';
        try { localStorage.setItem('wb-design-imgview', S.assetView); } catch (err) {}
        paintView();
        paintAssets();
      };
    }
    $('#btnImg').onclick = function () {
      var open = !panel.classList.contains('open');
      panel.classList.toggle('open', open);
      panel.setAttribute('aria-hidden', open ? 'false' : 'true');
      if (open) {
        var kb = $('#kbPanel');
        if (kb && kb.classList.contains('open')) {
          kb.classList.remove('open');
          kb.setAttribute('aria-hidden', 'true');
        }
        var foot = $('#imgFootTip');
        if (foot) {
          foot.textContent = (S.practice || S.role === 'teacher')
            ? '这里列出课件库里的全部图片；「从资料中添加」可把图片收进自己的素材。'
            : '老师下发到「资料」的图片会自动出现在这里；也可以手动添加。';
        }
        paintAssetCats();
        paintAssets();
        loadAssets(true).then(function () { paintAssetCats(); paintAssets(); });
      }
    };
    /* 从资料中添加文件（2.0.41）：把当前可用的资料文件收进「我加的」。
       学生看到的是老师下发的、教师看到的是课件库全部 —— 服务端已按角色分流，
       所以两端这一个按钮的行为天然一致。 */
    var addBtn = $('#btnImgAdd');
    if (addBtn) addBtn.onclick = function () {
      var rows = S.assets || [];
      if (!rows.length) {
        toast((S.practice || S.role === 'teacher')
          ? '课件库里还没有文件，先去「资料」上传'
          : '老师还没有下发资料，等下发后再来添加', 'err');
        return;
      }
      var have = {};
      assetPicked().forEach(function (x) { have[String(x.id)] = 1; });
      var add = rows.filter(function (a) { return !have[String(a.id)]; });
      if (!add.length) { toast('资料里的文件都已经在素材里了'); return; }
      add.forEach(function (a) { wbAssetPick(a.id, a.title, a.url); });
      S.assets = null;
      loadAssets(true).then(function () { paintAssetCats(); paintAssets(); });
      toast('已从资料添加 ' + add.length + ' 个文件到图片素材', 'ok');
    };
    $('#imgClose').onclick = closeImgPanel;
    $('#imgSearch').addEventListener('input', function () {
      S.assetQ = this.value || '';
      paintAssets();
    });
    $('#imgCats').onclick = function (e) {
      var b = e.target.closest('button[data-acat]');
      if (!b) return;
      S.assetCat = b.getAttribute('data-acat');
      $$('#imgCats button').forEach(function (x) { x.classList.toggle('on', x === b); });
      paintAssets();
    };
  }

  /* ---------------- 保存草稿 ---------------- */
  function saveDraft(manual) {
    if (S.demo) { if (manual) toast('演示模式下不保存，登录学生端后再试'); return Promise.resolve(false); }
    if (S.saving) return Promise.resolve(false);
    S.saving = true;
    // 显示设置一起存：下次进来还是上次那种看法（高亮 / 普通、桌面 / 平板 / 手机）
    var payload = { html: edHtml.value, css: edCss.value,
                    mode: S.mode, width: S.width };
    return api('/api/v2/design/draft', { method: 'POST', body: JSON.stringify(payload) })
      .then(function (j) {
        S.saving = false;
        if (!j.ok) { if (manual) toast(j.msg || '保存失败', 'err'); return false; }
        S.lastSavedAt = j.updated || Math.floor(Date.now() / 1000);
        setDirty(false);
        if (manual) {
          toast(S.practice ? '练习进度已保存，下次进来接着写'
                           : '草稿已保存，换台电脑登录也能接着写', 'ok');
        }
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
     其它（外站没给 CORS 头）换成占位块，绝不能让它污染 canvas。
     2.0.41：老师下发的图片走 /cw/<id> 闸门，**必须带会话 cookie**，
     所以同源地址要用 same-origin 凭证；跨域仍是 omit（带过去也没用，还会被 CORS 拒）。 */
  function toDataUrl(src) {
    if (!src) return Promise.resolve('');
    if (/^data:/i.test(src)) return Promise.resolve(src);
    var abs = src;
    try { abs = new URL(src, location.href).href; } catch (e) {}
    var same = abs.indexOf(location.origin + '/') === 0;
    return fetch(abs, same
        ? { credentials: 'same-origin' }
        : { mode: 'cors', credentials: 'omit' })
      .then(function (r) { if (!r.ok) throw new Error('http ' + r.status); return r.blob(); })
      .then(function (b) {
        if (b && b.type && b.type.indexOf('image/') !== 0) throw new Error('not image');
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
    /* 导出用「内容真实高度」，不是撑满预览区的高度：短页面只导它自己那一段，
       不会带一大片白底；长页面照样整页导出。 */
    var H = S.contentHeight || S.height;

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
    if (!S.cfg.enabled) { toast('老师还没有开启网页', 'err'); return; }
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
        /* 交上去的是「合成后的完整文件」：占位的 <style> 已经换成 CSS 页签的真样式，
           老师下载回去双击就能看，不依赖平台。 */
        body: JSON.stringify({ html: composeDoc(), css: edCss.value, png: png })
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

  /* ---------------- 教师练习：导出图片 ----------------
     老师练习不往学生作品库里交，只把当前预览导成 PNG 存到本机下载夹，
     方便放进课件 / 打印 / 当示例发给学生。 */
  async function exportPng() {
    if (S.submitting) return;
    S.submitting = true;
    busy('正在生成图片…');
    try {
      await renderPreview();
      await new Promise(function (r) { setTimeout(r, 120); });
      var png = await snapshot(2);
      var est = Math.floor(png.length * 3 / 4);
      if (est > MAX_PNG) png = await snapshot(1);
      var d = new Date();
      var pad = function (n) { return ('0' + n).slice(-2); };
      var name = '网页设计练习-' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) +
        '-' + pad(d.getHours()) + pad(d.getMinutes()) + '.png';
      var a = document.createElement('a');
      a.href = png;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      busy('');
      toast('图片已保存到下载文件夹：' + name, 'ok');
    } catch (e) {
      busy('');
      toast((e && e.message) || '导出失败，请重试', 'err');
    } finally {
      S.submitting = false;
    }
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
    if (S.practice) {
      parts.push('教师练习');
      parts.push(S.lastSavedAt ? '上次保存 ' + fmtTime(S.lastSavedAt) : '还没保存过');
    } else {
      if (who) parts.push(who);
      if (S.last && S.last.at) parts.push('上次提交 ' + S.last.at.slice(5, 16));
      else parts.push('还没提交过');
    }
    t.textContent = parts.join(' · ');
  }

  function boot() {
    syncTheme();
    watchTheme();
    /* 教师练习模式（2.0.40）：/design/student.html?mode=teacher&practice=1
       顶栏多一个「返回设置」，提交按钮换成「导出图片」，进度存教师自己的草稿。
       进练习页时还要告诉外层把 iframe 恢复成视口高度（设置页会把它撑长）。 */
    S.practice = /[?&]practice=1/.test(location.search || '');
    if (S.practice) {
      var back = $('#btnBack');
      if (back) {
        back.hidden = false;
        back.onclick = function () { location.href = '/design/teacher.html'; };
      }
      var sub = $('#brandSub');
      if (sub) sub.textContent = '教师练习 · 进度自动保存';
      var bt = $('#btnSubmit');
      if (bt) {
        bt.textContent = '导出图片';
        bt.title = '把当前预览导成 PNG 存到下载文件夹';
      }
      try {
        if (window.parent && window.parent !== window) {
          window.parent.postMessage({ type: 'wb-design-fit' }, '*');
        }
      } catch (e) {}
    }
    fillTemplates();
    bindEditors();
    bindPreviewSync();
    loadSplit();
    bindSplit();
    bindKb();
    kbRender();
    bindImg();
    $('#btnKb').classList.add('ghost');

    $('#btnSave').onclick = function () { saveDraft(true); };
    $('#btnSubmit').onclick = function () { (S.practice ? exportPng() : submitWork()); };

    /* 窗口变化时两头都要重算：宽度决定缩放，高度决定「画布至少撑多高」。
       只调 fit() 不重算撑满高度，拖窗口后下沿会留着一条空白。 */
    window.addEventListener('resize', function () {
      var d = pv.contentDocument;
      if (d && d.body) measure();
      else fit();
    });
    /* 不做 beforeunload 拦截：每次刷新预览都会换掉 iframe 文档，
       个别浏览器会因此弹「是否离开」——学生每打几个字就被问一次，很烦。
       改为「每 90 秒静默存一次」，真丢了也只丢一小段。 */
    /* 每 90 秒静默存一次：机房电脑说关就关，别让一节课白写 */
    S.autoTimer = setInterval(function () {
      if (S.dirty && !S.demo) saveDraft(false);
    }, 90000);

    api('/api/v2/design/me' + (S.practice ? '?practice=1' : '')).then(function (j) {
      if (!j.ok) throw new Error(j.msg || '读取失败');
      applyCfg(j.cfg);
      S.role = j.role || null;
      /* 学生 = 自己的草稿；教师练习 = 教师自己的草稿。两条路共用同一段恢复逻辑 */
      if (j.role === 'student' || j.practice) {
        S.me = j.me || null;
        S.draft = j.draft || null;
        S.last = j.last || null;
        var d = j.draft || {};
        if ((d.html || '').trim() || (d.css || '').trim()) {
          edHtml.value = docOf(d.html, d.css);
          /* 老草稿的 css 已经被 docOf 并进 <style>，隐藏框清空，别让同一份样式存两遍 */
          edCss.value = /<!DOCTYPE|<html[\s>]/i.test(String(d.html || '')) ? '' : (d.css || '');
        } else {
          edHtml.value = docOf(TPL.card.html, TPL.card.css);
          edCss.value = '';
        }
        /* 显示设置：老师练习默认高亮（看得清结构），学生默认普通文本 */
        applyMode(d.mode || (S.practice ? 'hl' : 'plain'));
        if (d.width) { S.width = Number(d.width) || S.width; paintDevSeg(); }
        /* 草稿里带了保存时间：恢复后状态栏直接显示「已保存 HH:MM」，
           而不是「未保存」——学生/老师看到会有「我上次白写了」的错觉。 */
        if (d.updated) S.lastSavedAt = Number(d.updated) || 0;
        if (S.practice) {
          toast('练习模式：改完自动保存，下次进来接着写');
        } else if (!S.cfg.enabled) {
          toast('老师还没有开启网页，可以先自己练练', 'err');
        } else if (!S.cfg.allow_submit) {
          toast('老师暂未开放提交，先写并保存草稿');
        }
      } else {
        // 教师预览 / 未登录：给一份示例，能看能玩，不保存不提交
        S.demo = true;
        S.role = S.role || 'guest';
        edHtml.value = j.role === 'teacher'
          ? docOf(TPL.card.html, TPL.card.css)
          : docOf(TPL.home.html, TPL.home.css);
        edCss.value = '';   // 样式已在 docOf 里写进 <style>，这里不再重复
        applyMode(j.role === 'teacher' ? 'hl' : 'plain');
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
      edHtml.value = docOf(TPL.home.html, TPL.home.css);
      edCss.value = '';
      applyMode('plain');
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
