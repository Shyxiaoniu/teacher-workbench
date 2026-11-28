/* 备案号按访问域名动态下发（2026-11-28）。
   离线包里没有备案号，只有数据卷 beian.json 配了域名白名单、且 Host 命中时才显示。
   教师端 index.html / 学生端 app.js 都会重建正文，所以用 MutationObserver 兜底。 */
(function () {
  var html = '';
  window.WB_BEIAN_HTML = '';

  function render() {
    var nodes = document.querySelectorAll('.beian-foot');
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.innerHTML !== html) el.innerHTML = html;
      el.style.display = html ? '' : 'none';
    }
  }

  fetch('/api/v2/beian', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      html = (d && d.show && d.html) ? d.html : '';
      window.WB_BEIAN_HTML = html;
      render();
      try {
        window.dispatchEvent(new CustomEvent('wb-beian-ready', { detail: d }));
      } catch (e) { /* 老浏览器没有 CustomEvent 构造器，忽略 */ }
    })
    .catch(function () { /* 接口不可用就保持隐藏，不显示任何备案号 */ });

  if (window.MutationObserver) {
    new MutationObserver(render).observe(document.documentElement,
      { childList: true, subtree: true });
  }
  document.addEventListener('DOMContentLoaded', render);
  render();
})();
