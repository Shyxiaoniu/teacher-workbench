/* ============================================================
   本机数据备份模块（唯一实现）  window.BackupIO
   ------------------------------------------------------------
   背景（2026-10-31 去重）：
   「系统设置 → 数据与备份」和「工具 → 💾 数据备份与迁移」原本是同一件事，
   但两处各写各的入口：工具页只有导出、要导入还得跳回设置页，改一处忘另一处。
   现在所有格式解析、校验、加解密、落盘、回滚、下载、密码框都收在这个文件里，
   两个入口只负责调用 openPanel()/export()/importFile()，不再各自持有实现。

   设计要点（沿用 2026-10-29 自检结论，行为与旧实现逐字节兼容）：
   1. 密码不跟着数据走：wb-lock-hash / wb-lock-qa 属于「这台设备」，既不进备份、
      也不接受覆盖 —— 换设备导入后仍用新设备的密码。
   2. 导入统一重建密文：先把备份里的值解成明文，再用**本机当前密钥**重新加密落盘。
   3. 明文备份不该要密码：认得出 app/ls 就直接导入，只有密文才弹密码框。
   4. 可校验：带条目数与 SHA-256 指纹，截断 / 损坏 / 被改过的一律中止，本机数据不动。
   5. 只认自己的键：只接收 wb-* 字符串值；写盘前清掉本机旧的表，避免残留表串进来。
   6. 与宿主解耦：加密实现、密钥、localStorage 键名、提示与落盘节流都由宿主注入
      （见 index.html 的 __backupEnv），所以这个文件不依赖任何页面全局变量。
   ============================================================ */
(function (root) {
  'use strict';

  var APP = 'class-workbench';
  var ENV = 'cw-backup';
  var VER = 3;                 /* 3 = 带 sum/count；2 = 2026-10 版；1 = 更早 */
  var LOCAL_ONLY = ['wb-lock-hash', 'wb-lock-qa'];   /* 设备凭据：不进备份、不被覆盖 */

  var provider = null;         /* () => env，由宿主在启动时注入 */

  /** 宿主环境（缓存一次，避免每个函数都判空） */
  function E() {
    if (!provider) throw new Error('BackupIO 未初始化：请先调用 BackupIO.init()');
    return provider();
  }

  /** 该键是否需要加密落盘（与 index.html 的 dbPersist / todoPersist 完全一致） */
  function encKey(k, env) {
    if (k === env.todoKey) return true;
    if (k.indexOf(env.dbPrefix) !== 0) return false;
    return env.dbKeys.indexOf(k.slice(env.dbPrefix.length)) >= 0;
  }

  /** 内容指纹：键名排序后逐个拼接，SHA-256 取前 24 个 base64 字符 */
  function sumOf(ls, env) {
    var s = Object.keys(ls).sort().map(function (k) { return k + '\u0001' + ls[k]; }).join('\u0002');
    try {
      return env.cipher.bytesToB64(env.cipher.sha256(new TextEncoder().encode(s))).slice(0, 24);
    } catch (e) { return ''; }
  }

  /** 文件名时间戳：同一天导出多次也不会互相覆盖 */
  function stamp(env) {
    var d = new Date();
    var p = function (n) { return String(n).padStart(2, '0'); };
    return env.curDate() + '-' + p(d.getHours()) + p(d.getMinutes());
  }

  /** 把 localStorage 里所有 wb-* 键（除设备凭据）打成一个对象 */
  function collect() {
    var env = E(), ls = {};
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (!k || k.indexOf('wb-') !== 0) continue;
        if (LOCAL_ONLY.indexOf(k) >= 0) continue;
        var v = localStorage.getItem(k);
        if (typeof v === 'string') ls[k] = v;
      }
    } catch (e) {}
    return ls;
  }

  /** 触发浏览器下载（Blob + a[download]，不依赖任何第三方库） */
  function download(text, type, name) {
    var blob = new Blob([text], { type: type });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 4000);
  }

  /** 导出：设了密码 → 整包加密的 .cwbackup；没设密码 → 明文 .json */
  function exportData() {
    var env = E();
    /* 锁定状态下（数据是密文、本机密钥还没装载）导出的是「假明文」：文件看着像备份，
       里面装的是解不开的密文。导出按钮本来就在解锁后的界面里，这里只兜住异常时序。 */
    if (!env.keySet() && env.lockActive()) {
      env.toast('请先解锁再导出备份（当前处于锁定状态，数据还是密文）');
      return false;
    }
    var ls = collect();
    var n = Object.keys(ls).length;
    if (!n) { env.toast('本机还没有可导出的数据'); return false; }
    var dump = {
      app: APP, version: VER,
      at: new Date().toISOString().slice(0, 16).replace('T', ' '),
      encrypted: !!env.keySet(),
      count: n, sum: sumOf(ls, env),
      term: env.termCfg(), appDate: env.appDate() || '',
      ls: ls
    };
    if (env.keySet()) {
      /* 外壳带 salt（2026-10-23 牛哥：换新设备导入失败）。盐值是设备相关的，
         旧版备份没带它，换设备后即使输对密码也因盐值不同而解不开。 */
      var payload = JSON.stringify({ app: ENV, v: VER, enc: true,
        salt: env.lockSalt(), count: n, payload: env.enc(JSON.stringify(dump)) });
      download(payload, 'application/octet-stream', '班级工作台备份-' + stamp(env) + '.cwbackup');
      env.toast('已导出加密备份（' + n + ' 项，换设备还原时需旧密码）');
    } else {
      download(JSON.stringify(dump, null, 2), 'application/json',
        '班级工作台备份-' + stamp(env) + '.json');
      env.toast('已导出备份（' + n + ' 项，明文）');
    }
    return true;
  }

  /** 密钥派生（不落本机密钥，专供「用指定密码解别人的备份」） */
  function deriveKey(pwd, salt, env) {
    return env.cipher.pbkdf2(new TextEncoder().encode(pwd),
      new TextEncoder().encode(salt || ''), env.iters, 32);
  }

  /** 用「指定密钥」加密一段文本（全量备份用：备份密码独立于本机锁密码，
      所以不能走 env.enc——那把锁绑定的是本机设备密钥）。
      输出格式与 index.html 的 encStr 完全一致：v1:c:<iv>:<mac>:<ct>，
      这样 env.dec(text, key) 能直接解开。 */
  function encWithKey(plain, key, env) {
    var iv = new Uint8Array(16);
    if (root.crypto && root.crypto.getRandomValues) root.crypto.getRandomValues(iv);
    else for (var i = 0; i < 16; i++) iv[i] = Math.floor(Math.random() * 256);
    var ct = env.cipher.aesCbcEncrypt(key, iv, new TextEncoder().encode(plain));
    var mac = env.cipher.hmacSha256(key, ct);
    return env.encPrefix + ':c:' + env.cipher.bytesToB64(iv) + ':'
      + env.cipher.bytesToB64(mac) + ':' + env.cipher.bytesToB64(ct);
  }

  /** 随机盐（base64 字符串，进备份外壳；换机解密靠它派生同一把密钥） */
  function randomSalt(env) {
    var b = new Uint8Array(12);
    if (root.crypto && root.crypto.getRandomValues) root.crypto.getRandomValues(b);
    else for (var i = 0; i < 12; i++) b[i] = Math.floor(Math.random() * 256);
    return env.cipher.bytesToB64(b);
  }

  /** 自定义密码输入弹窗（type=password 掩码；原生 prompt 不掩码会明文） */
  function askPassword(title, sub, placeholder) {
    var env = E();
    return new Promise(function (resolve) {
      var ov = document.createElement('div');
      ov.className = 'modal-ov';
      ov.innerHTML = ''
        + '<div class="modal" role="dialog" aria-modal="true">'
        + '  <div class="modal-head"><div>'
        + '    <div class="modal-title">' + env.esc(title || '请输入密码') + '</div>'
        + (sub ? '<div class="modal-sub">' + env.esc(sub) + '</div>' : '')
        + '  </div></div>'
        + '  <div class="modal-sec">'
        + '    <div class="modal-lb">密码</div>'
        + '    <input id="askPwdInput" class="mo-input" type="password"'
        + '           placeholder="' + env.esc(placeholder || '请输入密码') + '"'
        + '           autocomplete="current-password"'
        + '           style="font-size:14px;padding:11px 12px">'
        + '  </div>'
        + '  <div class="modal-acts">'
        + '    <button class="btn" style="width:auto;padding:7px 16px;font-size:12.5px"'
        + '      onclick="__askPwdCancel()">取消</button>'
        + '    <button class="btn btn-primary" style="width:auto;padding:7px 22px;font-size:12.5px"'
        + '      onclick="__askPwdOk()">确定</button>'
        + '  </div>'
        + '</div>';
      var done = false;
      var inp = function () { return ov.querySelector('#askPwdInput'); };
      var onKey = function (e) {
        if (e.key === 'Escape') finish(null);
        else if (e.key === 'Enter') finish(inp().value);
      };
      var close = function () {
        if (ov.parentNode) ov.parentNode.removeChild(ov);
        document.removeEventListener('keydown', onKey);
      };
      var finish = function (v) { if (done) return; done = true; close(); resolve(v); };
      root.__askPwdOk = function () { finish(inp().value); };
      root.__askPwdCancel = function () { finish(null); };
      ov.onclick = function (e) { if (e.target === ov) finish(null); };
      document.addEventListener('keydown', onKey);
      document.body.appendChild(ov);
      setTimeout(function () { var i = inp(); if (i) i.focus(); }, 30);
    });
  }

  /**
   * 解析备份文件文本 → { dump, key, mode }
   * · key = 解开「值级别密文」用的密钥（明文备份为 null）
   * · 返回 null = 用户取消了密码输入
   * · 解不开 / 不是本应用的文件 → throw（调用方提示，且不改动本机数据）
   */
  async function decode(text) {
    var env = E();
    var raw = String(text || '').replace(/^\uFEFF/, '');      /* 去掉可能的 BOM */
    if (!raw.trim()) throw new Error('文件是空的');
    var parsed = null;
    try { parsed = JSON.parse(raw); } catch (e) { parsed = null; }

    /* ① 明文备份：认得出就直接用，不问密码（新设备尚无密码时尤其重要） */
    if (parsed && parsed.app === APP && parsed.ls && typeof parsed.ls === 'object') {
      /* 少数历史文件是「锁定状态下导出」的：外壳明文、值却是密文。判断只看数据表 / 待办，
         先拿本机密钥试一条，不行再按文件里的盐要密码。 */
      var cipherVals = Object.keys(parsed.ls)
        .filter(function (k) {
          return encKey(k, env) && typeof parsed.ls[k] === 'string'
            && parsed.ls[k].indexOf(env.encPrefix + ':') === 0;
        });
      if (!cipherVals.length) return { dump: parsed, key: null, mode: 'plain' };
      if (env.keySet()) {
        try {
          env.dec(parsed.ls[cipherVals[0]], env.key());
          return { dump: parsed, key: env.key(), mode: 'plain-locked' };
        } catch (e) { /* 不是本机密钥 → 继续要密码 */ }
      }
      var s0 = String(parsed.ls['wb-lock-hash'] || '').split(':')[0];
      if (!s0) {
        throw new Error('这份备份是在锁定状态下导出的，文件里没有盐值信息，无法解密。请在原设备解锁后重新导出。');
      }
      var pwd0 = await askPassword('此备份已加密',
        '这份备份是加密状态下导出的，请输入「导出该备份时所用的密码」。', '导出备份时的密码');
      if (!pwd0) return null;
      var k0 = deriveKey(pwd0, s0, env);
      try { env.dec(parsed.ls[cipherVals[0]], k0); }
      catch (e) { throw new Error('密码不对，或备份文件已损坏'); }
      return { dump: parsed, key: k0, mode: 'plain-locked' };
    }

    /* ② 新版加密外壳 */
    if (parsed && parsed.app === ENV) {
      if (!parsed.payload) throw new Error('备份文件不完整（缺少 payload，可能没下载完）');
      if (env.keySet()) {
        try { return { dump: JSON.parse(env.dec(parsed.payload, env.key())), key: env.key(), mode: 'enc' }; }
        catch (e) { /* 本机密钥解不开 → 走密码框 */ }
      }
      var pwd = await askPassword('此备份已加密',
        '请输入「导出该备份时所用的密码」（换设备导入请用旧设备的密码）。', '导出备份时的密码');
      if (!pwd) return null;
      var k = deriveKey(pwd, parsed.salt || '', env);
      var plain;
      try { plain = env.dec(parsed.payload, k); }
      catch (e) { throw new Error('密码不对，或备份文件已损坏'); }
      var d = null;
      try { d = JSON.parse(plain); } catch (e) { throw new Error('备份内容损坏（解密后不是有效数据）'); }
      return { dump: d, key: k, mode: 'enc' };
    }

    /* ③ 旧版格式：整串密文，没有外壳（盐值没写进文件，只能按本机盐试） */
    if (raw.indexOf(env.encPrefix + ':') === 0) {
      if (env.keySet()) {
        try { return { dump: JSON.parse(env.dec(raw, env.key())), key: env.key(), mode: 'enc' }; }
        catch (e) { /* 继续走密码框 */ }
      }
      if (!env.lockSalt()) {
        throw new Error('这是旧版备份（文件里没有设备盐值），无法在新设备上还原。请在原设备上用最新版重新导出备份，再导入新设备。');
      }
      var pwd2 = await askPassword('此备份已加密',
        '请输入「导出该备份时所用的密码」。注意：旧版备份不含设备盐值，换设备可能无法还原。', '导出备份时的密码');
      if (!pwd2) return null;
      var k2 = deriveKey(pwd2, env.lockSalt(), env);
      var plain2;
      try { plain2 = env.dec(raw, k2); }
      catch (e) { throw new Error('密码不对，或这是别的设备的旧版备份（不含盐值，无法跨设备还原）'); }
      var d2 = null;
      try { d2 = JSON.parse(plain2); } catch (e) { throw new Error('备份内容损坏'); }
      return { dump: d2, key: k2, mode: 'enc-legacy' };
    }

    throw new Error('不是本应用导出的备份文件');
  }

  /** 校验 + 落盘。任何异常都在动 localStorage 之前抛出；写盘失败整体回滚。 */
  function apply(dump, key) {
    var env = E();
    if (!dump || dump.app !== APP || !dump.ls || typeof dump.ls !== 'object' || Array.isArray(dump.ls)) {
      throw new Error('备份内容不是本应用的数据');
    }
    var ver = Number(dump.version || 1);
    if (ver > VER) {
      throw new Error('这份备份来自更新版本的工作台（v' + ver + '），请先把本机升级到同一版本再导入');
    }
    var src = dump.ls;
    var keys = Object.keys(src);

    /* 校验和：有就比对，没有（v2 及更早）则按条目数粗查 */
    if (dump.sum) {
      if (sumOf(src, env) !== dump.sum) {
        throw new Error('备份文件校验不通过（可能没下载完或已损坏），已中止导入，本机数据没有改动');
      }
    } else if (typeof dump.count === 'number' && keys.length < dump.count) {
      throw new Error('备份文件不完整（条目数不足），已中止导入，本机数据没有改动');
    }

    /* 先整体解密成明文，全部成功后才动 localStorage——半路失败不会留下半套数据 */
    var plain = {};
    var dropped = 0;
    keys.forEach(function (k) {
      if (typeof k !== 'string' || k.indexOf('wb-') !== 0) return;
      if (LOCAL_ONLY.indexOf(k) >= 0) return;              /* 本机密码凭据不被覆盖 */
      var v = src[k];
      if (typeof v !== 'string') return;
      if (v.indexOf(env.encPrefix + ':') === 0) {
        if (key) {
          try { v = env.dec(v, key); }
          catch (e) {
            if (encKey(k, env)) throw new Error('备份里的数据解不开（密钥不匹配），已中止导入');
            return;                                        /* 配置类键解不开就丢掉 */
          }
        } else if (encKey(k, env)) {
          throw new Error('备份里存的是密文数据，但当前无法解密（请在原设备解锁后重新导出）');
        } else {
          dropped++;                                       /* 缓存 / 旧配置残留 */
          return;
        }
      }
      plain[k] = v;
    });
    if (!Object.keys(plain).length) throw new Error('备份里没有可导入的数据');
    if (dropped) console.warn('[backup] 跳过无法解密的配置项', dropped);

    /* 写盘：先记旧值，异常可整体回滚 */
    var backupOld = {};
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var bk = localStorage.key(i);
        if (bk && bk.indexOf(env.dbPrefix) === 0) backupOld[bk] = localStorage.getItem(bk);
      }
    } catch (e) {}
    env.freeze(true);          /* 冻结节流落盘，避免刚写进去就被覆盖 */
    /* 直接写 localStorage 而不是宿主封装：封装会吞异常，
       浏览器配额满时会「看起来导入成功、其实没写进去」。 */
    var put = function (k, v) {
      try { localStorage.setItem(k, v); }
      catch (e) {
        var quota = e && (e.name === 'QuotaExceededError' || e.code === 22 || e.code === 1014);
        throw new Error(quota
          ? '本机浏览器存储空间不足，无法写入完整备份。可先在原设备删掉一些大附件 / 自定义大屏后重新导出。'
          : '写入本机存储失败：' + ((e && e.message) || e));
      }
    };
    try {
      /* 清掉本机旧的表数据（含备份里没有的、新版本才有的表），避免残留串进来 */
      Object.keys(backupOld).forEach(function (k) { try { localStorage.removeItem(k); } catch (e) {} });
      Object.keys(plain).forEach(function (k) {
        put(k, (encKey(k, env) && env.keySet()) ? env.enc(plain[k]) : plain[k]);
      });
    } catch (e) {
      try {
        Object.keys(plain).forEach(function (k) {
          if (!(k in backupOld)) { try { localStorage.removeItem(k); } catch (e2) {} }
        });
        Object.keys(backupOld).forEach(function (k) {
          try { localStorage.setItem(k, backupOld[k]); } catch (e2) {}
        });
      } catch (e2) {}
      env.freeze(false);
      throw e;
    }
    return Object.keys(plain).length;
  }

  /** 选文件 → 解析 → 二次确认 → 落盘 → 重载（与旧 importAllData 行为一致） */
  function importFile() {
    var env = E();
    var f = document.createElement('input');
    f.type = 'file';
    f.accept = '.cwbackup,.json,application/json';
    f.onchange = function () {
      var file = f.files && f.files[0];
      if (!file) return;
      var r = new FileReader();
      r.onerror = function () { env.toast('读不到这个文件，可能已被移动或没有权限'); };
      r.onload = async function () {
        var parsed;
        try { parsed = await decode(String(r.result || '')); }
        catch (e) { env.toast('导入失败：' + ((e && e.message) || e)); return; }
        if (!parsed) { env.toast('已取消导入'); return; }
        var dump = parsed.dump, key = parsed.key;
        var n = Object.keys(dump.ls || {}).length;
        var when = dump.at ? ('（导出于 ' + dump.at + '）') : '';
        var tbl = env.dbKeys.filter(function (k) { return dump.ls['wb-db-' + k]; }).length;
        if (!(await (window.uiConfirm
          ? window.uiConfirm('即将用这份备份覆盖本机数据' + when + '。\n\n'
          + '· 备份共 ' + n + ' 项，含 ' + tbl + ' 张数据表\n'
          + '· 本机现有的班级 / 学生 / 成绩等会被替换\n'
          + (env.lockActive() ? '· 本机密码保持不变（备份里的旧密码不会覆盖它）\n' : '')
          + '\n确定继续吗？', { title: '导入备份', okText: '导入' })
          : Promise.resolve(confirm('即将用这份备份覆盖本机数据' + when + '。\n\n'
          + '· 备份共 ' + n + ' 项，含 ' + tbl + ' 张数据表\n'
          + '· 本机现有的班级 / 学生 / 成绩等会被替换\n'
          + (env.lockActive() ? '· 本机密码保持不变（备份里的旧密码不会覆盖它）\n' : '')
          + '\n确定继续吗？'))))) return;
        var cnt;
        try { cnt = apply(dump, key); }
        catch (e) { env.toast('导入失败：' + ((e && e.message) || e)); return; }
        env.toast('已导入 ' + cnt + ' 项，正在重新载入…');
        setTimeout(function () { env.reload(); }, 900);
      };
      r.readAsText(file);
    };
    f.click();
  }

  /**
   * 全量加密导出（v2.0.4 · 系统设置「数据与备份」唯一入口）
   * 服务端整库（含教师账号与学生口令哈希）+ 本机浏览器数据，一个文件带走。
   * 加密密钥由「备份密码」+ 随机盐派生（PBKDF2），与设备锁密码互相独立：
   * 换新机器只要记得备份密码就能解开，不需要旧设备的任何状态。
   */
  async function exportFull() {
    var env = E();
    if (!env.keySet() && env.lockActive()) {
      env.toast('请先解锁再导出备份（当前处于锁定状态，数据还是密文）');
      return false;
    }
    /* ① 取服务端全量（教师账号 / 学生口令哈希在 ?full=1 里） */
    var server = null;
    try {
      var r = await fetch('/api/v2/teacher/export?full=1',
        { credentials: 'same-origin', cache: 'no-store' });
      if (r.ok) {
        var j = await r.json();
        if (j && j.tables) server = j;
      }
    } catch (e) { server = null; }
    if (!server) {
      env.toast('拿不到服务端数据（登录已过期或服务未启动），本次不导出');
      return false;
    }
    /* ② 备份密码（独立于设备锁，输两遍防手滑） */
    var pwd = await askPassword('设置备份密码',
      '整个备份用这个密码加密。换电脑恢复时必须输入它，请务必记牢。', '至少 6 位');
    if (pwd === null) return false;
    if (!pwd || pwd.length < 6) { env.toast('备份密码至少 6 位，已取消导出'); return false; }
    var pwd2 = await askPassword('再输一遍备份密码', '', '重复输入');
    if (pwd2 === null) return false;
    if (pwd2 !== pwd) { env.toast('两次输入的密码不一致，已取消导出'); return false; }

    /* ③ 打包：本机 localStorage + 服务端表 / 配置 / 学生口令哈希 */
    var ls = collect();
    var n = Object.keys(ls).length;
    var tblN = Object.keys(server.tables || {})
      .filter(function (t) { return Array.isArray(server.tables[t]) && server.tables[t].length; }).length;
    var dump = {
      app: APP, version: VER, kind: 'full',
      at: new Date().toISOString().slice(0, 16).replace('T', ' '),
      encrypted: true, count: n, sum: sumOf(ls, env),
      term: env.termCfg(), appDate: env.appDate() || '',
      ls: ls,
      server: {
        app_version: server.version, exported_at: server.exported_at,
        tables: server.tables, config: server.config, creds: server.creds || [],
        sub: server.sub || null          /* 提交系统：题目/花名册/提交记录 */
      }
    };
    var salt = randomSalt(env);
    var k = deriveKey(pwd, salt, env);
    var shell = JSON.stringify({ app: ENV, v: VER, enc: true, kind: 'full',
      salt: salt, count: n, payload: encWithKey(JSON.stringify(dump), k, env) });
    download(shell, 'application/octet-stream',
      '班级工作台全量备份-' + stamp(env) + '.cwbackup');
    env.toast('已导出全量加密备份（服务端 ' + tblN + ' 张表 + 本机 ' + n + ' 项）');
    return true;
  }

  /**
   * 从全量备份一键恢复：选文件 → 输备份密码 → 确认 →
   * ① 本机数据整体替换（apply）→ ② 服务端整库替换（tables+config+creds）→ 刷新。
   * 旧版纯本机备份（没有 server 段）自动回落到普通导入流程。
   */
  function importFull() {
    var env = E();
    var f = document.createElement('input');
    f.type = 'file';
    f.accept = '.cwbackup,.json,application/json';
    f.onchange = function () {
      var file = f.files && f.files[0];
      if (!file) return;
      var r = new FileReader();
      r.onerror = function () { env.toast('读不到这个文件，可能已被移动或没有权限'); };
      r.onload = async function () {
        var parsed;
        try { parsed = await decode(String(r.result || '')); }
        catch (e) { env.toast('导入失败：' + ((e && e.message) || e)); return; }
        if (!parsed) { env.toast('已取消导入'); return; }
        var dump = parsed.dump, key = parsed.key;
        var n = Object.keys(dump.ls || {}).length;

        if (dump.kind === 'full' && dump.server) {
          var sv = dump.server;
          var tblN = Object.keys(sv.tables || {})
            .filter(function (t) { return Array.isArray(sv.tables[t]) && sv.tables[t].length; }).length;
          var credsN = (sv.creds || []).length;
          if (!(await (window.uiConfirm
            ? window.uiConfirm('全量恢复会整体替换两边的数据' +
            (dump.at ? '（导出于 ' + dump.at + '）' : '') + '：\n\n'
            + '· 服务端：' + tblN + ' 张表 + 配置' + (credsN ? ' + ' + credsN + ' 条学生密码' : '') + '\n'
            + '· 本机：' + n + ' 项界面数据\n'
            + '· 教师账号 / 学生密码 / 模块开关会回到备份时的状态\n'
            + (env.lockActive() ? '· 本机解锁密码保持不变\n' : '')
            + '\n确定继续吗？', { title: '全量恢复', okText: '恢复' })
            : Promise.resolve(confirm('全量恢复会整体替换两边的数据' +
            (dump.at ? '（导出于 ' + dump.at + '）' : '') + '：\n\n'
            + '· 服务端：' + tblN + ' 张表 + 配置' + (credsN ? ' + ' + credsN + ' 条学生密码' : '') + '\n'
            + '· 本机：' + n + ' 项界面数据\n'
            + '· 教师账号 / 学生密码 / 模块开关会回到备份时的状态\n'
            + (env.lockActive() ? '· 本机解锁密码保持不变\n' : '')
            + '\n确定继续吗？'))))) return;
          var cnt;
          try { cnt = apply(dump, key); }        /* 先落本机（失败不动服务端） */
          catch (e) { env.toast('本机数据导入失败：' + ((e && e.message) || e)); return; }
          var ok2 = false, msg2 = '';
          try {
            var resp = await fetch('/api/v2/admin/import', {
              method: 'POST', credentials: 'same-origin',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ tables: sv.tables, config: sv.config || {},
                                     creds: sv.creds || [], sub: sv.sub || {} })
            });
            var j = await resp.json().catch(function () { return {}; });
            ok2 = !!j.ok;
            if (!ok2) msg2 = (j && j.msg) || ('HTTP ' + resp.status);
          } catch (e) { msg2 = (e && e.message) || e; }
          if (!ok2) {
            env.toast('本机数据已还原，但服务端恢复失败：' + msg2 + '。刷新后可重试服务端部分。');
          } else {
            env.toast('已恢复：服务端 ' + tblN + ' 张表 + 本机 ' + cnt + ' 项，正在重新载入…');
          }
          setTimeout(function () { env.reload(); }, 1200);
          return;
        }

        /* 旧版纯本机备份：走原恢复流程 */
        var when = dump.at ? ('（导出于 ' + dump.at + '）') : '';
        var tbl = env.dbKeys.filter(function (k) { return dump.ls['wb-db-' + k]; }).length;
        if (!(await (window.uiConfirm
          ? window.uiConfirm('即将用这份备份覆盖本机数据' + when + '。\n\n'
          + '· 备份共 ' + n + ' 项，含 ' + tbl + ' 张数据表（纯本机备份，不含服务端数据）\n'
          + '· 本机现有的班级 / 学生 / 成绩等会被替换\n'
          + (env.lockActive() ? '· 本机密码保持不变（备份里的旧密码不会覆盖它）\n' : '')
          + '\n确定继续吗？', { title: '导入备份', okText: '导入' })
          : Promise.resolve(confirm('即将用这份备份覆盖本机数据' + when + '。\n\n'
          + '· 备份共 ' + n + ' 项，含 ' + tbl + ' 张数据表（纯本机备份，不含服务端数据）\n'
          + '· 本机现有的班级 / 学生 / 成绩等会被替换\n'
          + (env.lockActive() ? '· 本机密码保持不变（备份里的旧密码不会覆盖它）\n' : '')
          + '\n确定继续吗？'))))) return;
        var cnt2;
        try { cnt2 = apply(dump, key); }
        catch (e) { env.toast('导入失败：' + ((e && e.message) || e)); return; }
        env.toast('已导入 ' + cnt2 + ' 项，正在重新载入…');
        setTimeout(function () { env.reload(); }, 900);
      };
      r.readAsText(file);
    };
    f.click();
  }

  /**
   * 两个入口共用的面板：设置页与工具页都调它，避免再各自长出一套按钮/文案。
   * 面板里的按钮直接调本模块，所以不存在「两个实现」。
   */
  function openPanel(opts) {
    var env = E();
    var o = opts || {};
    var ov = document.createElement('div');
    ov.className = 'modal-ov';
    ov.innerHTML = ''
      + '<div class="modal" role="dialog" aria-modal="true" style="max-width:520px">'
      + '  <div class="modal-head"><div>'
      + '    <div class="modal-title">数据备份与迁移</div>'
      + '    <div class="modal-sub">导出 / 恢复用的是同一套实现：' +
             (env.keySet() ? '当前已设密码，导出为 .cwbackup 加密文件' : '当前未设密码，导出为 .json 明文') + '</div>'
      + '  </div></div>'
      + '  <div class="modal-sec">'
      + '    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">'
      + '      <button class="btn btn-primary" style="width:auto;padding:8px 16px;font-size:13px"'
      + '        onclick="BackupIO.export()">导出备份</button>'
      + '      <button class="btn" style="width:auto;padding:8px 16px;font-size:13px"'
      + '        onclick="BackupIO.importFile()">从备份恢复</button>'
      + '    </div>'
      + '    <div class="term-hint" style="margin:0">'
      + '      导出会把本机全部数据（班级、学生、考勤、作业、成绩、座位、设置…）打包成一个文件；'
      + '      换电脑 / 重装浏览器前务必导出一份，在新设备上点「从备份恢复」就能整体搬过来。<br>'
      + '      导入只替换数据、<b>不替换本机密码</b>；文件带条目数与校验和，不完整会直接中止。<br>'
      + '      需要「清空本机数据」等更细的操作，去 <a href="#settings" onclick="__bkClosePanel()">⚙ 系统设置 → 数据与备份</a>。'
      + '    </div>'
      + '  </div>'
      + '  <div class="modal-acts">'
      + '    <button class="btn" style="width:auto;padding:7px 16px;font-size:12.5px"'
      + '      onclick="__bkClosePanel()">关闭</button>'
      + '  </div>'
      + '</div>';
    root.__bkClosePanel = function () {
      if (ov.parentNode) ov.parentNode.removeChild(ov);
      document.removeEventListener('keydown', onKey);
    };
    var onKey = function (e) { if (e.key === 'Escape') root.__bkClosePanel(); };
    ov.onclick = function (e) { if (e.target === ov) root.__bkClosePanel(); };
    document.addEventListener('keydown', onKey);
    document.body.appendChild(ov);
    return o;
  }

  var api = {
    init: function (fn) { provider = fn; return api; },
    /* 常量对外暴露，方便设置页文案 / 测试脚本对齐 */
    APP: APP, ENV: ENV, VER: VER, LOCAL_ONLY: LOCAL_ONLY,
    collect: collect, sumOf: function (ls) { return sumOf(ls, E()); },
    export: exportData, decode: decode, apply: apply,
    importFile: importFile, askPassword: askPassword, openPanel: openPanel,
    exportFull: exportFull, importFull: importFull
  };
  root.BackupIO = api;
})(typeof window !== 'undefined' ? window : this);
