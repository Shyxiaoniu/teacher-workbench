/* =========================================================================
   提交增强 · 老师端（挂在「活动」页 → ④ 提交设置）
   -------------------------------------------------------------------------
   设计约束：
   1. 不改工作台原有函数：只往 #sePanel 里画东西，靠观察 DOM 自动挂载/刷新。
   2. 所有数据来自 /api/enh/*；模板 / 素材 / 状态都存服务端，换电脑不丢。
   3. 学生链接优先用局域网地址（复用工作台的 qfStudentBase），避免发给学生打不开。
   ========================================================================= */
(function () {
  'use strict';

  var API = '/api/enh';
  var SE = {
    key: '', clsId: '', clsName: '',
    task: null, tpl: null, mats: [],
    classOptions: [],  // 系统导入的班级列表（供班级匹配选项展示）
    sel: -1,           // 模板编辑器选中的字段
    mtab: 'mount',     // 素材来源页签
    picks: [],         // 素材勾选
    modal: ''
  };

  /* ---------------- 基础 ---------------- */
  function E(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function $(id) { return document.getElementById(id); }
  var tT = null;
  function toast(msg) {
    var el = $('seToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'seToast'; el.className = 'se-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg; el.classList.add('on');
    clearTimeout(tT); tT = setTimeout(function () { el.classList.remove('on'); }, 2200);
  }
  function api(path, opt) {
    return fetch(API + path, Object.assign({ cache: 'no-store' }, opt || {}))
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          return { ok: r.ok, status: r.status, data: j || {} };
        });
      })
      .catch(function () { return { ok: false, status: 0, data: { msg: '连接不上提交服务，检查工作台是否还在运行' } }; });
  }
  function post(path, body) {
    return api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }
  function fmt(n) {
    n = Number(n) || 0;
    if (n > 1048576) return (n / 1048576).toFixed(n > 10485760 ? 0 : 1) + ' MB';
    if (n > 1024) return (n / 1024).toFixed(0) + ' KB';
    return n + ' B';
  }
  function base() {
    try { if (typeof qfStudentBase === 'function') return qfStudentBase(); } catch (e) {}
    return location.origin;
  }

  /* ---------------- 取当前活动 / 班级 ---------------- */
  function taskKey() {
    var k = '';
    try { if (typeof qfActiveId !== 'undefined' && qfActiveId) k = qfActiveId; } catch (e) {}
    if (!k) { var s = $('qfTaskSel'); if (s && s.value) k = s.value; }
    return k || '';
  }
  function classId() {
    try { if (typeof actCurClass === 'function') { var v = actCurClass(); return v == null ? '' : String(v); } } catch (e) {}
    return '';
  }
  function classMeta() {
    var id = classId();
    if (!id) return { id: '', name: '' };
    try {
      var rows = dbT('classes') || [];
      for (var i = 0; i < rows.length; i++) {
        if (String(rows[i].id) === String(id)) return { id: id, name: rows[i].name || '' };
      }
    } catch (e) {}
    return { id: id, name: '' };
  }
  function taskInfo() {
    var key = taskKey(), title = '', ap = '';
    try {
      if (typeof qfTaskList !== 'undefined' && qfTaskList) {
        for (var i = 0; i < qfTaskList.length; i++) {
          if (qfTaskList[i].task_id === key) { title = qfTaskList[i].title || ''; ap = qfTaskList[i].api || ''; }
        }
      }
    } catch (e) {}
    if (!title) {
      var s = $('qfTaskSel');
      if (s && s.selectedIndex >= 0 && s.options[s.selectedIndex]) title = s.options[s.selectedIndex].text || '';
    }
    if (!ap) {
      try { if (typeof qfApiBase === 'function') ap = qfApiBase().replace(/\/+$/, '') + '/api/' + key; } catch (e) {}
    }
    return { key: key, title: title || key, api: ap };
  }

  /* ---------------- 挂载 / 刷新 ---------------- */
  var mounting = false;
  function hook() {
    var host = $('sePanel');
    if (!host) return;
    var sig = taskKey() + '|' + classId();
    if (host.getAttribute('data-sig') === sig) return;
    host.setAttribute('data-sig', sig);
    load();
  }

  function load() {
    var host = $('sePanel');
    if (!host || mounting) return;
    var key = taskKey(), cm = classMeta();
    SE.key = key; SE.clsId = cm.id; SE.clsName = cm.name;
    if (!key) {
      host.innerHTML = card('先在上面选一个活动，这里才能配模板和素材。');
      return;
    }
    mounting = true;
    host.innerHTML = '<div class="se-card"><div class="se-h"><span class="n">④</span>提交设置' +
      '<span class="r"><span class="se-pill">读取中…</span></span></div></div>';
    var info = taskInfo();
    api('/task?key=' + encodeURIComponent(key)).then(function (r) {
      var exists = r.ok && r.data && r.data.task;
      var sync = post('/task/save', {
        task_key: key, title: info.title, api: info.api,
        class_names: cm.name ? [cm.name] : [], class_ids: cm.id ? [cm.id] : []
      });
      return sync.then(function (s) {
        if (!s.ok && !exists) throw new Error((s.data && s.data.msg) || '创建任务失败');
        return api('/task?key=' + encodeURIComponent(key));
      });
    }).then(function (r2) {
      if (!r2.ok || !r2.data.task) throw new Error((r2.data && r2.data.msg) || '任务读取失败');
      SE.task = r2.data.task;
      SE.tpl = r2.data.task.template || { version: 0, sections: [] };
      SE.mats = r2.data.task.materials || [];
      SE.classOptions = r2.data.class_options || [];
      render();
    }).catch(function (e) {
      host.innerHTML = card('读取失败：' + (e.message || e) + '。<br>检查「启动工作台」是否还在运行。');
    }).then(function () { mounting = false; });
  }

  function card(html) {
    return '<div class="se-card"><div class="se-h"><span class="n">④</span>提交设置</div>' +
      '<div class="se-muted">' + html + '</div></div>';
  }

  /* ---------------- 主卡片 ---------------- */
  function render() {
    var host = $('sePanel');
    if (!host) return;
    var t = SE.task, tpl = SE.tpl || {};
    var nf = 0;
    (tpl.sections || []).forEach(function (s) { nf += (s.fields || []).length; });
    var bad = (SE.mats || []).filter(function (m) { return !m.available; }).length;
    var portal = base() + '/s/?task=' + encodeURIComponent(SE.key) +
      (SE.clsName ? '&class=' + encodeURIComponent(SE.clsName) : '');
    var st = t.status || 'open';
    var stTxt = { draft: '草稿（学生看不到）', open: '进行中', closed: '已截止', archived: '已归档' }[st] || st;
    var pol = t.policy || {};
    var chk = pol.check || {};
    var sub = pol.submit || {};

    host.innerHTML =
      '<div class="se-card">' +
        '<div class="se-h"><span class="n">④</span>提交设置' +
          '<span class="r">' +
            '<span class="se-pill ' + (st === 'open' ? 'ok' : (st === 'closed' ? 'warn' : '')) + '">' + E(stTxt) + '</span>' +
            (SE.clsName ? '<span class="se-pill">' + E(SE.clsName) + '</span>' : '<span class="se-pill warn">未绑定班级</span>') +
          '</span>' +
        '</div>' +

        row('模板',
          (tpl.version
            ? E(tpl.name || '未命名') + ' · <b>' + nf + ' 个字段</b> · v' + tpl.version
            : '<b>还没配模板</b>（学生填的是经典表单：学号 / 姓名 / 标题 / 简介 / 文件）') +
          '<div class="se-muted">决定学生要填什么；改模板不影响已交的数据。</div>',
          '<button class="btn btn-ghost btn-sm" onclick="seOpenTpl()">编辑模板</button>' +
          '<button class="btn btn-ghost btn-sm" onclick="sePreset()">套预设</button>' +
          '<button class="btn btn-ghost btn-sm" onclick="sePreview()">学生视角</button>') +

        row('核对',
          '必填 <b>' + (chk.block_on_fail === false ? '✗' : '✓') + '</b> · 文件名提醒 <b>' +
          (chk.warn_confirm === false ? '✗' : '✓') + '</b> · 内容级 <b>' +
          (chk.content_level ? '✓' : '✗') + '</b>' +
          '<div class="se-muted">阻止级直接拦；提醒级学生可「仍然提交」，结果只给你看，不进排名。</div>',
          '<button class="btn btn-ghost btn-sm" onclick="seOpenCheck()">设置</button>') +

        row('素材',
          (SE.mats.length
            ? '已下发 <b>' + SE.mats.length + '</b> 个' + (bad ? ' · <b style="color:#E24B4A">' + bad + ' 个失效</b>' : '')
            : '还没下发素材') +
          '<div class="se-muted">学生页顶部会出现「📎 老师下发的素材」，可逐个下载。</div>',
          '<button class="btn btn-ghost btn-sm" onclick="seOpenMat()">管理</button>' +
          '<button class="btn btn-ghost btn-sm" onclick="seCheckMat()">体检</button>') +

        row('班级匹配',
          (classMatch() === 'none'
            ? '<b>不匹配</b>'
            : '<b>自动匹配系统导入的班级列表</b>' +
              (SE.classOptions && SE.classOptions.length
                ? '（系统里 ' + SE.classOptions.length + ' 个班：' + E(SE.classOptions.join('、')) + '）'
                : '（系统里还没有导入班级名单）')) +
          '<div class="se-muted">决定学生提交时，班级是自动从名单里找，还是原样保留。在「编辑模板」里改。</div>',
          '<button class="btn btn-ghost btn-sm" onclick="seOpenTpl()">去设置</button>') +

        row('门户',
          '<code>' + E('/s/?task=' + SE.key) + '</code>' +
          '<div class="se-muted">一条链接：看任务 / 下素材 / 交作品 / 看自己交过什么。' +
          (sub.allow_late ? ' 已允许截止后补交。' : '') + '</div>',
          '<button class="btn btn-ghost btn-sm" onclick="seCopyPortal()">复制</button>' +
          '<button class="btn btn-primary btn-sm" onclick="seOpenPortal()">打开学生页</button>') +

        row('状态',
          (t.deadline ? ('截止 ' + E(t.deadline)) : '不限时间') +
          '<div class="se-muted">截止后学生只能看历史；需要补交就重新开放。</div>',
          '<button class="btn btn-ghost btn-sm" onclick="seOpenStatus()">改状态</button>' +
          '<button class="btn btn-ghost btn-sm" onclick="seExport()">导出核对报告</button>') +
      '</div>';

    // 暂停后把 portal 完整链接缓存起来，复制时给全的
    SE.portal = portal;
  }

  function row(k, v, acts) {
    return '<div class="se-row"><div class="k">' + k + '</div><div class="v">' + v + '</div>' +
      '<div class="acts">' + acts + '</div></div>';
  }

  /* ---------------- 弹层框架 ---------------- */
  function openModal(html, small) {
    closeModal();
    var mask = document.createElement('div');
    mask.className = 'se-mask on'; mask.id = 'seMask';
    mask.onclick = closeModal;
    var box = document.createElement('div');
    box.className = 'se-modal on' + (small ? ' sm' : '');
    box.id = 'seModal';
    box.innerHTML = html;
    document.body.appendChild(mask);
    document.body.appendChild(box);
    SE.modal = 'open';
  }
  function closeModal() {
    var m = $('seMask'), b = $('seModal');
    if (m) m.remove();
    if (b) b.remove();
    SE.modal = '';
  }
  function mh(title, right) {
    return '<div class="se-mh"><b>' + title + '</b><span style="flex:1"></span>' + (right || '') +
      '<button class="x" onclick="seCloseModal()">✕</button></div>';
  }

  /* =======================================================================
     模板编辑器（F1.1）
     ======================================================================= */
  var TYPE_NAME = { text: '文本', textarea: '长文本', number: '数字', select: '下拉', radio: '单选', checkbox: '多选', date: '日期', file: '文件' };

  function openTpl() {
    var tpl = SE.tpl || {};
    if (!tpl.sections || !tpl.sections.length) {
      tpl = { version: 0, name: '作品提交', sections: [{ title: '一、作品信息', fields: [] }] };
    }
    SE.tpl = JSON.parse(JSON.stringify(tpl));
    if (!SE.tpl.sections || !SE.tpl.sections.length) SE.tpl.sections = [{ title: '一、作品信息', fields: [] }];
    SE.sel = -1;
    openModal(
      mh('模板编辑器 · ' + E(SE.tpl.name || '作品提交'),
        '<span class="se-pill" id="seTplVer">' + (SE.tpl.version ? 'v' + SE.tpl.version : '未保存') + '</span>') +
      '<div class="se-mb">' +
      classMatchHtml() +
      '<div class="se-ed">' +
        '<div class="se-pane"><div class="se-ph">字段库</div><div class="se-lib">' +
          ['text', 'textarea', 'number', 'select', 'radio', 'checkbox', 'date', 'file'].map(function (t) {
            return '<button onclick="seAddField(\'' + t + '\')">' + TYPE_NAME[t] + '</button>';
          }).join('') +
        '</div><div class="se-libnote">系统字段（学号 / 姓名 / 班级）已锁定，不能删改。单模板最多 15 个字段。</div></div>' +
        '<div class="se-pane"><div class="se-ph">表单画布</div><div class="se-list" id="seList">' + listHtml() + '</div></div>' +
        '<div class="se-pane"><div class="se-ph">属性</div><div class="se-props" id="seProps">' + propsHtml() + '</div></div>' +
      '</div></div>' +
      '<div class="se-mf"><span class="se-muted" id="seTplMsg"></span><span style="flex:1"></span>' +
        '<button class="btn btn-ghost btn-sm" onclick="seCloseModal()">取消</button>' +
        '<button class="btn btn-primary btn-sm" onclick="seSaveTpl()">保存模板</button></div>',
      false);
  }

  /* ---------------- 班级匹配选项 ----------------
     两个可选项：
       auto（默认）自动匹配系统导入的班级列表 —— 学生信息按学号到花名册里找对应班级
       none             不匹配 —— 不做班级匹配处理，学生自己填什么就是什么
     存在任务配置的 policy.class_match 里，随模板一起保存。            */
  var CM_LABEL = {
    auto: '自动匹配系统导入的班级列表',
    none: '不匹配'
  };
  function classMatch() {
    var pol = (SE.task && SE.task.policy) || {};
    var m = pol.class_match;
    if (m !== 'auto' && m !== 'none') m = 'auto';   // 缺省即自动匹配
    return m;
  }
  function classMatchHtml() {
    var cur = classMatch();
    var opts = (SE.classOptions || []).map(function (c) { return E(c); });
    var hint = cur === 'auto'
      ? (opts.length
          ? '系统里已导入 ' + opts.length + ' 个班级：' + opts.join('、')
          : '系统里还没有导入班级名单，学生填的班级将无法匹配到系统班级')
      : '不做班级匹配处理：学生填的班级原样保存，不与系统班级列表核对';
    return '<div class="se-cm">' +
      '<div class="se-cm-h">班级匹配</div>' +
      '<label class="se-cm-o"><input type="radio" name="seCm" value="auto"' +
        (cur === 'auto' ? ' checked' : '') + ' onchange="seUpdClassMatch(\'auto\')">' +
        '<span class="se-cm-t">' + CM_LABEL.auto +
        '<span class="se-cm-d">按学号在系统导入的班级列表里自动找到对应班级，学生不用手填</span></span></label>' +
      '<label class="se-cm-o"><input type="radio" name="seCm" value="none"' +
        (cur === 'none' ? ' checked' : '') + ' onchange="seUpdClassMatch(\'none\')">' +
        '<span class="se-cm-t">' + CM_LABEL.none +
        '<span class="se-cm-d">不与系统班级列表核对，学生填的班级原样保存</span></span></label>' +
      '<div class="se-cm-hint" id="seCmHint">' + E(hint) + '</div>' +
    '</div>';
  }
  function updClassMatch(mode) {
    // 先在本地更新，保存模板时一起提交
    SE.task = SE.task || {};
    SE.task.policy = SE.task.policy || {};
    SE.task.policy.class_match = (mode === 'none') ? 'none' : 'auto';
    var el = $('seCmHint');
    if (el) {
      var opts = (SE.classOptions || []).map(function (c) { return E(c); });
      el.textContent = mode === 'auto'
        ? (opts.length
            ? '系统里已导入 ' + opts.length + ' 个班级：' + opts.join('、')
            : '系统里还没有导入班级名单，学生填的班级将无法匹配到系统班级')
        : '不做班级匹配处理：学生填的班级原样保存，不与系统班级列表核对';
    }
  }

  function fields() { return SE.tpl.sections[0].fields; }

  function listHtml() {
    var fs = fields();
    var sys = ['学号', '姓名', '班级'].map(function (n) {
      return '<div class="se-f sys"><span class="lb">' + n + '<span class="rq">*</span></span><span class="tp">系统</span></div>';
    }).join('');
    return sys + fs.map(function (f, i) {
      return '<div class="se-f' + (SE.sel === i ? ' on' : '') + '" id="seF' + i + '" onclick="seSelField(' + i + ')">' +
        '<span class="lb">' + E(f.label || '（未命名）') + (f.required ? '<span class="rq">*</span>' : '') + '</span>' +
        '<span class="tp">' + (TYPE_NAME[f.type] || f.type) + '</span>' +
        '<span class="op" onclick="event.stopPropagation()">' +
          '<button onclick="seMoveField(' + i + ',-1)">↑</button>' +
          '<button onclick="seMoveField(' + i + ',1)">↓</button>' +
          '<button onclick="seDelField(' + i + ')">✕</button>' +
        '</span></div>';
    }).join('');
  }

  function fillList() { var el = $('seList'); if (el) el.innerHTML = listHtml(); }

  function propsHtml() {
    var f = fields()[SE.sel];
    if (!f) return '<div class="se-empty">点中间的字段卡<br>在这里改它</div>';
    var h = '';
    h += '<label>字段标签</label><input type="text" value="' + E(f.label) + '" oninput="seUpd(\'label\',this.value)">';
    h += '<label>填写提示</label><input type="text" value="' + E(f.hint || '') + '" oninput="seUpd(\'hint\',this.value)">';
    if (f.type === 'text' || f.type === 'textarea') {
      h += '<div class="two"><div><label>最少字数</label><input type="number" value="' + (f.min || 0) + '" oninput="seUpd(\'min\',+this.value)"></div>' +
        '<div><label>最多字数</label><input type="number" value="' + (f.max || (f.type === 'textarea' ? 600 : 200)) + '" oninput="seUpd(\'max\',+this.value)"></div></div>';
    }
    if (f.type === 'select' || f.type === 'radio' || f.type === 'checkbox') {
      h += '<label>选项（每行一个）</label><textarea rows="4" oninput="seUpdOptions(this.value)">' + E((f.options || []).join('\n')) + '</textarea>';
    }
    if (f.type === 'file') {
      h += '<div class="two"><div><label>最多文件数</label><input type="number" value="' + (f.maxCount || 3) + '" oninput="seUpd(\'maxCount\',+this.value)"></div>' +
        '<div><label>单个上限(MB)</label><input type="number" value="' + (f.maxMB || 100) + '" oninput="seUpd(\'maxMB\',+this.value)"></div></div>';
      h += '<label>允许的格式（逗号分隔）</label><input type="text" value="' + E((f.accept || []).join(',')) + '" oninput="seUpdAccept(this.value)">';
    }
    h += '<label class="se-chk"><input type="checkbox" ' + (f.required ? 'checked' : '') +
      ' onchange="seUpd(\'required\',this.checked)"> 必填（不填不让交）</label>';
    return h;
  }

  function addField(type) {
    var fs = fields();
    if (fs.length >= 15) return toast('字段太多了（15 个上限），学生填不完');
    var f = {
      key: 'f' + Date.now().toString(36).slice(-5),
      label: '新' + TYPE_NAME[type], type: type, required: false, hint: ''
    };
    if (type === 'text') { f.max = 60; }
    if (type === 'textarea') { f.min = 0; f.max = 600; }
    if (type === 'select') { f.options = ['选项一', '选项二']; }
    if (type === 'radio') { f.options = ['选项一', '选项二']; }
    if (type === 'checkbox') { f.options = ['选项一', '选项二']; }
    if (type === 'file') { f.maxCount = 3; f.maxMB = 100; f.accept = ['jpg', 'png', 'pdf', 'docx', 'pptx', 'mp4']; }
    fs.push(f);
    SE.sel = fs.length - 1;
    fillList(); $('seProps').innerHTML = propsHtml();
    toast('已添加「' + f.label + '」，改完记得保存');
  }

  function selField(i) {
    SE.sel = (SE.sel === i ? -1 : i);
    fillList(); $('seProps').innerHTML = propsHtml();
  }

  function moveField(i, d) {
    var fs = fields(), j = i + d;
    if (j < 0 || j >= fs.length) return;
    var t = fs[i]; fs[i] = fs[j]; fs[j] = t;
    if (SE.sel === i) SE.sel = j; else if (SE.sel === j) SE.sel = i;
    fillList(); $('seProps').innerHTML = propsHtml();
  }

  function delField(i) {
    fields().splice(i, 1);
    SE.sel = -1;
    fillList(); $('seProps').innerHTML = propsHtml();
    toast('字段已删除（历史提交里的数据仍保留）');
  }

  function upd(k, v) {
    var f = fields()[SE.sel];
    if (!f) return;
    f[k] = v;
    if (k === 'label' || k === 'required') {
      var row = $('seF' + SE.sel);
      if (row) {
        var lb = row.querySelector('.lb');
        if (lb) lb.innerHTML = E(f.label || '（未命名）') + (f.required ? '<span class="rq">*</span>' : '');
      }
    }
  }
  function updOptions(v) {
    var f = fields()[SE.sel];
    if (f) f.options = String(v).split('\n').map(function (x) { return x.trim(); }).filter(Boolean);
  }
  function updAccept(v) {
    var f = fields()[SE.sel];
    if (f) f.accept = String(v).split(',').map(function (x) { return x.trim().replace(/^\./, ''); }).filter(Boolean);
  }

  function saveTpl() {
    var fs = fields();
    if (!fs.length) return toast('空模板没法用，至少加一个字段');
    var body = { task: SE.key, name: SE.tpl.name || '作品提交', sections: SE.tpl.sections,
                 class_match: classMatch() };
    post('/form/save', body).then(function (r) {
      if (!r.ok || !r.data.ok) {
        var msg = (r.data && (r.data.msg || r.data.errors && r.data.errors.join('；'))) || '保存失败';
        var m = $('seTplMsg'); if (m) m.textContent = msg;
        return toast(msg);
      }
      SE.tpl = r.data.template;
      closeModal();
      load();
      toast('已保存，版本升到 v' + SE.tpl.version + ' · 学生端立刻生效');
    });
  }

  function preset() {
    openModal(
      mh('从预设开始') +
      '<div class="se-mb">' +
        ['A', 'B', 'C'].map(function (k) {
          var n = { A: '作品提交（通用）', B: '课堂任务（轻量）', C: '图文记录（过程性）' }[k];
          var d = {
            A: '作品标题 / 创作说明（≥50 字）/ 选题 / 作品文件（1-5 个）',
            B: '任务说明 / 成果文件（1-3 个）/ 完成度',
            C: '活动名称 / 过程照片（2-3 张）/ 一句话感受（≥20 字）'
          }[k];
          return '<div class="se-f" onclick="seApplyPreset(\'' + k + '\')"><span class="lb"><b>' + n + '</b><br>' +
            '<span class="se-muted">' + d + '</span></span><span class="tp">套用</span></div>';
        }).join('') +
        '<div class="se-note">套预设会替换当前模板的字段（版本号 +1）。已有提交的数据不受影响。</div>' +
      '</div>' +
      '<div class="se-mf"><span style="flex:1"></span><button class="btn btn-ghost btn-sm" onclick="seCloseModal()">取消</button></div>',
      true);
  }

  function applyPreset(k) {
    post('/form/save', { task: SE.key, preset: k }).then(function (r) {
      if (!r.ok || !r.data.ok) return toast((r.data && r.data.msg) || '套用失败');
      SE.tpl = r.data.template;
      closeModal(); load();
      toast('已套用预设，v' + SE.tpl.version);
    });
  }

  function preview() {
    var url = base() + '/s/?task=' + encodeURIComponent(SE.key) +
      (SE.clsName ? '&class=' + encodeURIComponent(SE.clsName) : '');
    window.open(url, '_blank');
  }

  /* =======================================================================
     素材（F3）
     ======================================================================= */
  function openMat() {
    openModal(
      mh('素材 · 学生页上能点的下载',
        '<button class="btn btn-ghost btn-sm" onclick="seMatAddStart()">＋ 从课件添加</button>' +
        '<button class="btn btn-ghost btn-sm" onclick="seCheckMat()">体检</button>') +
      '<div class="se-mb" id="seMatBody">' + matBody() + '</div>' +
      '<div class="se-mf"><span class="se-muted">引用：不占空间，源文件动了学生就拿新的；副本：复制一份，换机 / 拔盘也不失效。</span>' +
      '<span style="flex:1"></span><button class="btn btn-ghost btn-sm" onclick="seCloseModal()">关闭</button></div>',
      true);
  }

  function matBody() {
    if (!SE.mats.length) return '<div class="se-empty">还没下发素材。<br>点右上「＋ 从课件添加」，从资料夹或课件库里挑。</div>';
    return SE.mats.map(function (m, i) {
      return '<div class="se-mat' + (m.available ? '' : ' dead') + '">' +
        '<div class="ic">' + E((m.ext || 'FILE').slice(0, 4)) + '</div>' +
        '<div class="in"><div class="n">' + E(m.name) + '</div>' +
        '<div class="m">' + fmt(m.size) + ' · ' + (m.kind === 'copy' ? '副本' : '引用') +
        (m.note ? ' · ' + E(m.note) : '') + (m.available ? '' : ' · <span style="color:#E24B4A">原路径已不存在</span>') + '</div></div>' +
        '<div class="ops">' +
          '<button class="btn btn-ghost btn-sm" onclick="seMatOrder(' + i + ',-1)"' + (i === 0 ? ' disabled' : '') + '>↑</button>' +
          '<button class="btn btn-ghost btn-sm" onclick="seMatOrder(' + i + ',1)"' +
            (i === SE.mats.length - 1 ? ' disabled' : '') + '>↓</button>' +
          (m.available
            ? '<button class="btn btn-ghost btn-sm" onclick="seMatAct(\'' + m.id + '\',\'visible\',' + (m.visible === false) + ')">' + (m.visible === false ? '显示' : '隐藏') + '</button>'
            : '<button class="btn btn-ghost btn-sm" onclick="seMatAct(\'' + m.id + '\',\'to_copy\')">改为副本</button>') +
          '<button class="btn btn-ghost btn-sm" onclick="seMatAct(\'' + m.id + '\',\'remove\')">移除</button>' +
        '</div></div>';
    }).join('');
  }

  function refreshMat() {
    api('/material/list?task=' + encodeURIComponent(SE.key) + '&admin=1').then(function (r) {
      if (r.ok && r.data.items) {
        SE.mats = r.data.items;
        var b = $('seMatBody'); if (b) b.innerHTML = matBody();
        render();
      }
    });
  }

  function matAct(id, action, visible) {
    var body = { id: id, action: action, visible: visible };
    post('/material/update', body).then(function (r) {
      if (!r.ok || !r.data.ok) return toast((r.data && r.data.msg) || '操作失败');
      refreshMat();
      if (action === 'remove') toast('已移除（文件本身不动）');
      if (action === 'to_copy') toast('已改为副本，换机也不会失效');
    });
  }

  function matOrder(i, d) {
    var j = i + d;
    if (j < 0 || j >= SE.mats.length) return;
    var arr = SE.mats.slice();
    var t = arr[i]; arr[i] = arr[j]; arr[j] = t;
    post('/material/update', { id: arr[0].id, action: 'move', order: arr.map(function (m) { return m.id; }) })
      .then(function () { refreshMat(); });
    SE.mats = arr;
    var b = $('seMatBody'); if (b) b.innerHTML = matBody();
  }

  function checkMat() {
    post('/material/check', { task: SE.key }).then(function (r) {
      if (!r.ok || !r.data.ok) return toast((r.data && r.data.msg) || '体检失败');
      toast('体检完成：' + r.data.msg);
      refreshMat();
    });
  }

  /* ---- 从课件选素材 ---- */
  function matAddStart() {
    SE.picks = [];
    SE.mtab = 'mount';
    openModal(
      mh('添加素材') +
      '<div class="se-mb">' +
        '<div class="se-tabs">' +
          '<button id="seTabMount" class="on" onclick="seMatTab(\'mount\')">资料夹</button>' +
          '<button id="seTabCourse" onclick="seMatTab(\'course\')">课件库</button>' +
        '</div>' +
        '<div id="sePickArea"><div class="se-empty">读取中…</div></div>' +
        '<div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">' +
          '<span class="se-muted">下发方式</span>' +
          '<select id="seMatMode" class="se-sel" style="padding:6px 10px;font-size:12.5px;width:auto">' +
            '<option value="ref">引用（省空间，可能失效）</option>' +
            '<option value="copy">副本（复制一份，稳）</option>' +
          '</select>' +
          '<input id="seMatNote" placeholder="备注（可选，40 字内）" style="flex:1;min-width:160px;padding:6px 10px;' +
            'border:1px solid var(--border);border-radius:8px;font-size:12.5px;font-family:inherit">' +
        '</div>' +
        '<div class="se-note">学生页会按这里的顺序展示。素材不会暴露老师整个资料夹，只下发你勾的这几个。</div>' +
      '</div>' +
      '<div class="se-mf"><span class="se-muted" id="sePickCount">已选 0 个</span><span style="flex:1"></span>' +
        '<button class="btn btn-ghost btn-sm" onclick="seCloseModal()">取消</button>' +
        '<button class="btn btn-primary btn-sm" onclick="seDoAddMat()">下发</button></div>',
      true);
    matTab('mount');
  }

  function matTab(which) {
    SE.mtab = which;
    var a = $('seTabMount'), b = $('seTabCourse');
    if (a) a.className = which === 'mount' ? 'on' : '';
    if (b) b.className = which === 'course' ? 'on' : '';
    var box = $('sePickArea');
    if (!box) return;
    if (which === 'mount') {
      api('/cw/mounts').then(function (r) {
        if (!r.ok) {
          box.innerHTML = '<div class="se-note warn">资料夹接口没响应：' +
            E((r.data && r.data.msg) || '可能没连上服务，或当前不是本机地址') + '</div>';
          return;
        }
        var ms = (r.data.mounts || []);
        if (!ms.length) {
          box.innerHTML = '<div class="se-empty">还没有添加资料夹。<br>去「教学 → 课件 → 资料夹」添加一个，再回来下发。</div>';
          return;
        }
        box.innerHTML = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:9px">' +
          ms.map(function (m, i) {
            return '<button class="btn btn-ghost btn-sm" onclick="seScanMount(\'' + m.id + '\')">' +
              E(m.name || m.path) + '</button>';
          }).join('') + '</div><div id="seMountList" class="se-pick"><div class="se-empty">选一个资料夹，挑要下发的文件</div></div>';
      });
    } else {
      api('/cw/config').then(function (r) {
        if (!r.ok) {
          box.innerHTML = '<div class="se-note warn">课件库打不开：' + E((r.data && r.data.msg) || '可能当前不是本机地址') + '</div>';
          return;
        }
        var cs = (r.data.courses || []);
        box.innerHTML = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:9px">' +
          cs.map(function (c) {
            return '<button class="btn btn-ghost btn-sm" onclick="seScanCourse(\'' + E(c.name) + '\')">' +
              E(c.name) + '（' + (c.count || 0) + '）</button>';
          }).join('') + '</div><div id="seMountList" class="se-pick"><div class="se-empty">选一个课程，挑要下发的文件</div></div>';
      });
    }
  }

  function pickRow(id, name, size, extra) {
    return '<label><input type="checkbox" data-id="' + E(id) + '" data-name="' + E(name) + '" ' +
      (extra ? extra : '') + ' onchange="seCountPicks()">' +
      '<span class="nm">' + E(name) + '</span><span class="sz">' + fmt(size) + '</span></label>';
  }

  function scanMount(id) {
    var box = $('seMountList');
    if (box) box.innerHTML = '<div class="se-empty">扫描中…</div>';
    api('/cw/mount/scan?id=' + encodeURIComponent(id)).then(function (r) {
      if (!r.ok || !r.data.ok) {
        if (box) box.innerHTML = '<div class="se-note warn">' + E((r.data && r.data.msg) || '扫描失败') + '</div>';
        return;
      }
      var fs = (r.data.files || []).slice(0, 500);
      if (!fs.length) { box.innerHTML = '<div class="se-empty">这个资料夹里没有文件</div>'; return; }
      box.innerHTML = fs.map(function (f) {
        return pickRow(id + '\u0001' + f.rel, f.name || f.rel, f.size || 0);
      }).join('');
      seCountPicks();
    });
  }

  function scanCourse(name) {
    var box = $('seMountList');
    if (box) box.innerHTML = '<div class="se-empty">读取中…</div>';
    api('/cw/list?course=' + encodeURIComponent(name)).then(function (r) {
      if (!r.ok || !r.data.ok) {
        if (box) box.innerHTML = '<div class="se-note warn">' + E((r.data && r.data.msg) || '读取失败') + '</div>';
        return;
      }
      var fs = (r.data.files || []).slice(0, 500);
      if (!fs.length) { box.innerHTML = '<div class="se-empty">这个课程里没有文件</div>'; return; }
      box.innerHTML = fs.map(function (f) {
        return pickRow('course\u0001' + f.id, f.name || f.rel || f.id, f.size || 0, 'data-c="1"');
      }).join('');
      seCountPicks();
    });
  }

  function countPicks() {
    var el = $('sePickCount');
    if (!el) return;
    var n = document.querySelectorAll('#sePickArea input[type=checkbox]:checked').length;
    el.textContent = '已选 ' + n + ' 个';
  }

  function doAddMat() {
    var boxes = document.querySelectorAll('#sePickArea input[type=checkbox]:checked');
    if (!boxes.length) return toast('先勾要下发的文件');
    var items = [];
    Array.prototype.forEach.call(boxes, function (b) {
      var v = b.getAttribute('data-id') || '';
      var isCourse = b.getAttribute('data-c') === '1';
      if (isCourse) {
        items.push({ from: 'course', id: v.split('\u0001')[1], name: b.getAttribute('data-name') });
      } else {
        var parts = v.split('\u0001');
        items.push({ from: 'mount', id: parts[0], rel: parts.slice(1).join('\u0001') });
      }
    });
    var mode = ($('seMatMode') || {}).value || 'ref';
    var note = ($('seMatNote') || {}).value || '';
    post('/material/add', { task: SE.key, items: items, mode: mode, note: note }).then(function (r) {
      if (!r.ok || !r.data.ok) return toast((r.data && r.data.msg) || '下发失败');
      var skip = r.data.skipped || [];
      toast('已下发 ' + (r.data.added || []).length + ' 个' + (skip.length ? ('，' + skip.length + ' 个跳过') : ''));
      closeModal();
      load();
      setTimeout(openMat, 400);
    });
  }

  /* =======================================================================
     核对设置 / 状态 / 导出
     ======================================================================= */
  function openCheck() {
    var pol = (SE.task && SE.task.policy) || {};
    var c = pol.check || {};
    var s = pol.submit || {};
    openModal(
      mh('核对与提交规则') +
      '<div class="se-mb">' +
        '<label class="se-chk"><input type="checkbox" id="ckBlock" ' + (c.block_on_fail === false ? '' : 'checked') + '> ' +
          '阻止级校验（必填 / 字数 / 文件类型与大小不合格时，不让提交）</label>' +
        '<label class="se-chk"><input type="checkbox" id="ckWarn" ' + (c.warn_confirm === false ? '' : 'checked') + '> ' +
          '提醒级校验（文件名没有学号、重复提交等，只提醒，学生可「仍然提交」）</label>' +
        '<label class="se-chk"><input type="checkbox" id="ckRe" ' + (s.allow_resubmit === false ? '' : 'checked') + '> ' +
          '允许重新提交（会变成第 2 版，旧版保留）</label>' +
        '<label class="se-chk"><input type="checkbox" id="ckWd" ' + (s.allow_withdraw === false ? '' : 'checked') + '> ' +
          '允许学生撤回（撤回后这条不算交）</label>' +
        '<label class="se-chk"><input type="checkbox" id="ckLate" ' + (s.allow_late ? 'checked' : '') + '> ' +
          '截止后仍允许补交（会标注「补交」）</label>' +
        '<div class="se-note">核对结果只给老师看，不进排名、不影响「已交」统计。内容级核对（解析文档正文）默认关闭：机房无外网，误判伤积极性。</div>' +
      '</div>' +
      '<div class="se-mf"><span style="flex:1"></span><button class="btn btn-ghost btn-sm" onclick="seCloseModal()">取消</button>' +
        '<button class="btn btn-primary btn-sm" onclick="seSaveCheck()">保存</button></div>',
      true);
  }

  function saveCheck() {
    var pol = JSON.parse(JSON.stringify((SE.task && SE.task.policy) || {}));
    pol.check = pol.check || {}; pol.submit = pol.submit || {};
    pol.check.block_on_fail = $('ckBlock').checked;
    pol.check.warn_confirm = $('ckWarn').checked;
    pol.submit.allow_resubmit = $('ckRe').checked;
    pol.submit.allow_withdraw = $('ckWd').checked;
    pol.submit.allow_late = $('ckLate').checked;
    post('/task/save', { task_key: SE.key, policy: pol }).then(function (r) {
      if (!r.ok || !r.data.ok) return toast((r.data && r.data.msg) || '保存失败');
      closeModal(); load(); toast('规则已保存');
    });
  }

  function openStatus() {
    var t = SE.task || {};
    openModal(
      mh('任务状态') +
      '<div class="se-mb">' +
        '<label>状态</label>' +
        '<select id="stSel" style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:8px;font-family:inherit;font-size:13px">' +
          ['open:进行中（学生可交）', 'closed:已截止（只读）', 'draft:草稿（学生看不到）', 'archived:归档（不参与统计）']
            .map(function (x) { var p = x.split(':'); return '<option value="' + p[0] + '"' + ((t.status || 'open') === p[0] ? ' selected' : '') + '>' + p[1] + '</option>'; }).join('') +
        '</select>' +
        '<label style="display:block;margin-top:12px">截止时间（留空＝不限）</label>' +
        '<input id="stDdl" type="datetime-local" value="' + E((t.deadline || '').replace(' ', 'T').slice(0, 16)) + '" ' +
          'style="width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:8px;font-family:inherit;font-size:13px">' +
        '<div class="se-note">到期后系统自动按「已截止」处理（学生只读）。要让学生补交，把状态改回「进行中」或勾选允许补交。</div>' +
      '</div>' +
      '<div class="se-mf"><span style="flex:1"></span><button class="btn btn-ghost btn-sm" onclick="seCloseModal()">取消</button>' +
        '<button class="btn btn-primary btn-sm" onclick="seSaveStatus()">保存</button></div>',
      true);
  }

  function saveStatus() {
    var st = ($('stSel') || {}).value || 'open';
    var ddl = ($('stDdl') || {}).value || '';
    ddl = ddl ? ddl.replace('T', ' ').slice(0, 16) : '';
    post('/task/save', { task_key: SE.key, status: st, deadline: ddl }).then(function (r) {
      if (!r.ok || !r.data.ok) return toast((r.data && r.data.msg) || '保存失败');
      closeModal(); load(); toast('已更新：' + st);
    });
  }

  function exportReport() {
    var cls = SE.clsName || '';
    var url = API + '/export?task=' + encodeURIComponent(SE.key) + '&cls=' + encodeURIComponent(cls);
    window.open(url, '_blank');
  }

  function copyPortal() {
    var txt = SE.portal || (base() + '/s/?task=' + encodeURIComponent(SE.key));
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt);
      } else {
        var ta = document.createElement('textarea');
        ta.value = txt; document.body.appendChild(ta); ta.select();
        document.execCommand('copy'); ta.remove();
      }
      toast('已复制学生链接：' + txt);
    } catch (e) {
      toast('复制失败，手动复制：' + txt);
    }
  }

  function openPortal() {
    window.open(SE.portal || (base() + '/s/?task=' + encodeURIComponent(SE.key)), '_blank');
  }

  /* ---------------- 挂载 ---------------- */
  var pending = false;
  function tick() {
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      try { hook(); } catch (e) {}
    });
  }
  setInterval(tick, 1200);
  try {
    new MutationObserver(tick).observe(document.body, { childList: true, subtree: true });
  } catch (e) {}
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tick);
  } else {
    setTimeout(tick, 300);
  }

  /* ---------------- 对外 ---------------- */
  window.SE = SE;
  window.seOpenTpl = openTpl;
  window.seCloseTpl = closeModal;
  window.sePreset = preset;
  window.seApplyPreset = applyPreset;
  window.seSaveTpl = saveTpl;
  window.seAddField = addField;
  window.seSelField = selField;
  window.seMoveField = moveField;
  window.seDelField = delField;
  window.seUpd = upd;
  window.seUpdClassMatch = updClassMatch;
  window.seUpdOptions = updOptions;
  window.seUpdAccept = updAccept;
  window.sePreview = preview;
  window.seOpenMat = openMat;
  window.seMatAddStart = matAddStart;
  window.seMatTab = matTab;
  window.seScanMount = scanMount;
  window.seScanCourse = scanCourse;
  window.seCountPicks = countPicks;
  window.seDoAddMat = doAddMat;
  window.seMatAct = matAct;
  window.seMatOrder = matOrder;
  window.seCheckMat = checkMat;
  window.seOpenCheck = openCheck;
  window.seSaveCheck = saveCheck;
  window.seOpenStatus = openStatus;
  window.seSaveStatus = saveStatus;
  window.seExport = exportReport;
  window.seCopyPortal = copyPortal;
  window.seOpenPortal = openPortal;
  window.seCloseModal = closeModal;
})();
