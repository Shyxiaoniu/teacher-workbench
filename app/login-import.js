/* ============================================================
   登录页「数据导入」 window.LoginIO（2026-11-05）
   ------------------------------------------------------------
   场景：换设备 / 系统重新部署后，浏览器打开 /teacher/login，
   还没登录（全新部署甚至还没有账号），就要把旧机的全部数据
   一键搬进来 —— 服务端 29 张表 + 配置 + 学生密码 + 打字成绩 +
   作品附件，都在备份的 server 段里。

   依赖：/core/cipher.js（JS_CIPHER）—— 与教师端备份导出端同一份
   加密实现，解密格式逐字节一致（v1:c:<iv>:<mac>:<ct>）。

   服务端入口：POST /api/v2/admin/import-preauth
     · 还没账号 → 首次设置窗口内（启动24h + 内网）放行；
     · 已有账号 → 必须先验证教师账号密码（强度等同登录）。
   冲突策略三种（服务端 import_snapshot mode 参数）：
     replace 整体替换 / merge 合并（同号以备份为准）/ skip 只补新的。

   旧版纯本机备份（没有 server 段）这里不接 —— 登录教师端后在
   「系统设置 → 数据与备份」导入。
   ============================================================ */
(function (root) {
  'use strict';
  var ENC_ITERS = 150000;
  var ENV_APP = 'cw-backup';
  var LOCAL_APP = 'class-workbench';
  var LOCAL_ONLY = { 'wb-lock-hash': 1, 'wb-lock-qa': 1 };   /* 设备凭据不迁移 */

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }
  function el(id) { return document.getElementById(id); }

  /* ---------------- 通用弹窗（登录页没有 uiDialog，自带一份） ---------------- */
  function dlg(html, o) {
    o = o || {};
    var ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;z-index:900;background:rgba(20,30,26,.38);'
      + 'display:flex;align-items:center;justify-content:center;padding:18px';
    ov.innerHTML = '<div style="background:#fff;border-radius:16px;box-shadow:0 22px 60px rgba(0,0,0,.24);'
      + 'width:100%;max-width:' + (o.width || '440px') + ';max-height:88vh;overflow:auto;padding:22px 24px">'
      + html + '</div>';
    document.body.appendChild(ov);
    var close = function () {
      if (ov.parentNode) ov.parentNode.removeChild(ov);
      document.removeEventListener('keydown', onKey);
    };
    var onKey = function (e) { if (e.key === 'Escape') { e.preventDefault(); close(); } };
    document.addEventListener('keydown', onKey);
    return { ov: ov, close: close };
  }
  function alertBox(title, msg, okText) {
    var d = dlg('<div style="font-weight:700;font-size:16px;margin-bottom:10px">' + esc(title) + '</div>'
      + '<div style="font-size:13.6px;line-height:1.8;white-space:pre-wrap">' + msg + '</div>'
      + '<div style="text-align:right;margin-top:16px"><button id="_dOk" style="padding:9px 22px">'
      + esc(okText || '知道了') + '</button></div>');
    d.ov.querySelector('#_dOk').onclick = d.close;
  }

  /* ---------------- 备份解密（与 backup.js decode 的全量分支对齐） ---------------- */
  function decPayload(payload, key) {
    /* 格式 v1:c:<iv>:<mac>:<ct>，先验 HMAC 再解密 —— 密码错 / 文件被改都会在这一步现形 */
    var parts = String(payload || '').split(':');
    if (parts.length !== 5 || parts[0] !== 'v1' || parts[1] !== 'c') {
      throw new Error('备份密文格式不对（文件可能没下载完整或已损坏）');
    }
    var C = root.JS_CIPHER;
    var iv = C.b64ToBytes(parts[2]), ct = C.b64ToBytes(parts[4]);
    if (C.bytesToB64(C.hmacSha256(key, ct)) !== parts[3]) {
      throw new Error('备份密码不对，或备份文件已被改动过');
    }
    var plain = C.aesCbcDecrypt(key, iv, ct);
    return JSON.parse(new TextDecoder().decode(plain));
  }

  /**
   * 解析备份文件文本 → { dump, key }
   * 校验顺序刻意是「先认文件、再要密码」：拿错文件 / 纯本机备份这类
   * 不需要密码就能判定的问题，不逼用户先填密码再报错。
   * 只认全量备份（kind=full 且带 server 段）；纯本机备份 / 别的文件 → throw。
   */
  function readBackup(text, pwd) {
    var raw = String(text || '').replace(/^\uFEFF/, '');
    if (!raw.trim()) throw new Error('文件是空的');
    var parsed = null;
    try { parsed = JSON.parse(raw); } catch (e) { parsed = null; }

    if (parsed && parsed.app === ENV_APP) {
      if (!parsed.payload) throw new Error('备份文件不完整（缺少加密数据段，可能没下载完）');
      if (!parsed.salt) throw new Error('这份备份太旧，里面没有盐值信息，无法解密。'
        + '请在原设备升级到最新版后重新导出。');
      /* 认得出是加密全量备份，才轮到密码说话 */
      if (!pwd) throw new Error('请先输入这份备份的密码');
      var C = root.JS_CIPHER;
      var key = C.pbkdf2(new TextEncoder().encode(pwd),
        new TextEncoder().encode(parsed.salt), ENC_ITERS, 32);
      var dump = decPayload(parsed.payload, key);
      if (dump.kind !== 'full' || !dump.server || !dump.server.tables) {
        throw new Error('这是早期版本的「纯本机备份」，不含服务端数据，无法在登录页导入。'
          + '请先登录教师端，在「系统设置 → 数据与备份」里导入这份文件。');
      }
      return { dump: dump, key: key };
    }
    if (parsed && parsed.app === LOCAL_APP) {
      throw new Error('这是「纯本机备份」（不含服务端数据），无法在登录页导入。'
        + '请先登录教师端，在「系统设置 → 数据与备份」里导入这份文件。');
    }
    throw new Error('不是班级工作台导出的备份文件（.cwbackup）。'
      + '请到旧设备的「系统设置 → 数据与备份」点「一键导出」生成备份。');
  }

  /* ---------------- 本机界面数据（ls 段） ---------------- */
  /* 全新设备上顺手把备份里的本机段也铺进去（值级密文用备份密码解开，落成明文）。
     本机已经有数据（wb-db-* 存在）时不动 —— 那多半是正在用的设备，铺旧数据只会倒退。 */
  function applyLocalLs(dump, key) {
    try {
      var hasData = false;
      for (var i = 0; i < localStorage.length; i++) {
        if (String(localStorage.key(i) || '').indexOf('wb-db-') === 0) { hasData = true; break; }
      }
      if (hasData) return { n: 0, skipped: true };
      var src = (dump && dump.ls) || {};
      var n = 0;
      Object.keys(src).forEach(function (k) {
        if (typeof k !== 'string' || k.indexOf('wb-') !== 0) return;
        if (LOCAL_ONLY[k]) return;
        var v = src[k];
        if (typeof v !== 'string') return;
        if (v.indexOf('v1:') === 0 && key) {
          try { v = decPayload(v, key); }
          catch (e) { return; }               /* 解不开的配置项丢掉，别中断整体 */
        }
        localStorage.setItem(k, v);
        n++;
      });
      return { n: n, skipped: false };
    } catch (e) { return { n: 0, err: String(e && e.message || e) }; }
  }

  /* ---------------- 导入对话框 ---------------- */
  var UI = null;                 /* 当前弹窗 */

  function closeUI() {
    if (UI) { UI.close(); UI = null; }
  }

  function open() {
    closeUI();
    var d = dlg('', { width: '480px' });
    UI = d;
    var hasPwd = true;           /* 已有账号 → 需要教师密码；拉不到状态时按需要展示 */
    fetch('/api/v2/state', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (j) { hasPwd = !!j.has_password; paintForm(); })
      .catch(function () { paintForm(); });

    function paintForm() {
      var h = ''
        + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
        + '  <div style="font-weight:700;font-size:16.5px">📥 数据导入</div>'
        + '  <span style="font-size:12px;color:#849690">换设备 / 重新部署后，一键恢复全部数据</span></div>'
        + '<div style="font-size:12.8px;color:#5F6E68;line-height:1.75;margin:8px 0 14px">'
        + '  选择旧设备「一键导出」生成的 <b>.cwbackup</b> 全量备份文件，'
        + '  输入当时设置的<b>备份密码</b>即可整体恢复服务端数据'
        + '  （班级、学生、成绩、教师账号、学生密码、打字成绩、作品附件都会回来）。</div>'
        + '<label style="display:block;font-size:12.6px;font-weight:600;color:#55655F;margin:10px 0 5px">备份文件</label>'
        + '<input id="impFile" type="file" accept=".cwbackup,.json" '
        + '  style="width:100%;padding:9px 11px;border:1px solid #D6E3DE;border-radius:10px;font-size:13.5px;background:#FBFDFC">'
        + '<label style="display:block;font-size:12.6px;font-weight:600;color:#55655F;margin:12px 0 5px">备份密码</label>'
        + '<input id="impPwd" type="password" autocomplete="off" placeholder="导出这份备份时设置的密码" '
        + '  style="width:100%;padding:10px 12px;border:1px solid #D6E3DE;border-radius:10px;font-size:14px;background:#FBFDFC">'
        + (hasPwd
          ? '<label style="display:block;font-size:12.6px;font-weight:600;color:#55655F;margin:12px 0 5px">教师账号密码</label>'
          + '<input id="impTpwd" type="password" autocomplete="off" placeholder="当前服务的教师密码" '
          + '  style="width:100%;padding:10px 12px;border:1px solid #D6E3DE;border-radius:10px;font-size:14px;background:#FBFDFC">'
          + '<div style="font-size:12px;color:#849690;margin-top:5px">'
          + '  这台服务已设有账号：导入数据前需先验证教师密码，防止学生把备份灌进服务端。</div>'
          : '')
        + '<label style="display:block;font-size:12.6px;font-weight:600;color:#55655F;margin:14px 0 6px">已存在同类数据时</label>'
        + '<div id="impModes" style="display:flex;flex-direction:column;gap:7px;font-size:13px">'
        /* 注意：.card 里的 input 都被 BASE_CSS 设了 width:100%，单选框必须显式改回 auto，
           否则它的盒子占满整行，文字被挤到下一行、看着像两栏错位。 */
        + '  <label style="display:flex;gap:9px;align-items:flex-start;cursor:pointer;font-weight:400">'
        + '    <input type="radio" name="impMode" value="replace" checked style="width:auto;flex:none;margin:2px 0 0">'
        + '    <span style="flex:1"><b>覆盖</b> —— 备份带的表整表替换（换新设备 / 全新部署选这个）</span></label>'
        + '  <label style="display:flex;gap:9px;align-items:flex-start;cursor:pointer;font-weight:400">'
        + '    <input type="radio" name="impMode" value="merge" style="width:auto;flex:none;margin:2px 0 0">'
        + '    <span style="flex:1"><b>合并</b> —— 保留本机已有数据，同号记录以备份为准更新</span></label>'
        + '  <label style="display:flex;gap:9px;align-items:flex-start;cursor:pointer;font-weight:400">'
        + '    <input type="radio" name="impMode" value="skip" style="width:auto;flex:none;margin:2px 0 0">'
        + '    <span style="flex:1"><b>跳过</b> —— 只补本机没有的记录，已有的一律不动</span></label>'
        + '</div>'
        + '<div id="impErr" style="min-height:18px;font-size:12.6px;color:#B3261E;margin-top:10px"></div>'
        + '<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:8px">'
        + '  <button id="impCancel" style="background:#EAF4F0;color:#0F6E56">关闭</button>'
        + '  <button id="impGo" style="padding:10px 24px">校验文件</button></div>';
      d.ov.firstChild.innerHTML = h;
      el('impCancel').onclick = closeUI;
      el('impGo').onclick = onVerify;
      if (el('impPwd')) el('impPwd').focus();
    }

    var curMode = 'replace';      /* 进入预览后表单被重绘，策略要记在变量里 */

    function mode() {
      var r = d.ov.querySelector('input[name=impMode]:checked');
      return r ? r.value : curMode;
    }

    function onVerify() {
      var err = el('impErr');
      err.textContent = '';
      var f = el('impFile').files && el('impFile').files[0];
      if (!f) { err.textContent = '请先选择备份文件'; return; }
      /* 密码不在这里预检：是不是备份文件、要不要密码，都由 readBackup 按顺序判定
         （拿错文件 → 直接报文件问题，不逼用户先填密码） */
      var tp = el('impTpwd');
      if (tp && !tp.value) { err.textContent = '请输入这台服务的教师密码'; return; }
      var go = el('impGo');
      go.disabled = true; go.textContent = '正在校验…';
      var reader = new FileReader();
      reader.onerror = function () {
        go.disabled = false; go.textContent = '校验文件';
        err.textContent = '读不到这个文件，可能已被移动或没有读取权限';
      };
      reader.onload = function () {
        var dump = null, key = null;
        try { var r = readBackup(String(reader.result || ''), el('impPwd').value); dump = r.dump; key = r.key; }
        catch (e) {
          go.disabled = false; go.textContent = '校验文件';
          err.textContent = (e && e.message) || '校验失败';
          return;
        }
        paintPreview(dump, key);
      };
      reader.readAsText(f);
    }

    function paintPreview(dump, key) {
      var sv = dump.server || {};
      var tables = sv.tables || {};
      var tblN = 0, rows = 0;
      Object.keys(tables).forEach(function (t) {
        if (Array.isArray(tables[t]) && tables[t].length) { tblN++; rows += tables[t].length; }
      });
      var credsN = (sv.creds || []).length;
      var typN = (sv.typing && sv.typing.students) ? Object.keys(sv.typing.students).length : 0;
      var fileN = sv.files ? Object.keys(sv.files).length : 0;
      var subN = sv.sub ? Object.keys(sv.sub).length : 0;
      var lines = [
        '· 服务端数据：' + tblN + ' 张表 / ' + rows + ' 行',
        credsN ? '· 学生密码：' + credsN + ' 条' : '',
        typN ? '· 打字成绩：' + typN + ' 名学生' : '',
        fileN ? '· 学生作品附件：' + fileN + ' 个' : '',
        subN ? '· 提交系统数据：' + subN + ' 项（题目 / 花名册 / 提交记录）' : '',
        '· 教师账号与设置：随备份恢复'
      ].filter(Boolean).join('<br>');
      var m = mode();
      curMode = m;                /* 后面 runImport 时单选框已不在 DOM，用变量传 */
      var mName = { replace: '覆盖', merge: '合并', skip: '跳过' }[m];
      var h = ''
        + '<div style="font-weight:700;font-size:16.5px;margin-bottom:2px">✓ 备份校验通过</div>'
        + '<div style="font-size:12.4px;color:#849690;margin-bottom:12px">'
        + '  导出于 ' + esc(dump.at || '未知时间') + ' · 冲突策略：<b>' + mName + '</b></div>'
        + '<div style="font-size:13.4px;line-height:1.9;background:#F4FAF7;border-left:3px solid #1D9E75;'
        + '  border-radius:0 10px 10px 0;padding:12px 14px;margin-bottom:6px">' + lines + '</div>'
        + (m === 'replace'
          ? '<div style="font-size:12.4px;color:#8A6A1C;line-height:1.7;margin-top:10px">'
          + '  「覆盖」会用备份内容替换服务端现有数据，导入后以备份为准。若这台服务里已有数据，'
          + '  请确认备份比它新。想保留双方数据可先返回改选「合并」。</div>' : '')
        + '<div id="impErr" style="min-height:18px;font-size:12.6px;color:#B3261E;margin-top:8px"></div>'
        + '<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:8px">'
        + '  <button id="impBack" style="background:#EAF4F0;color:#0F6E56">返回</button>'
        + '  <button id="impRun" style="padding:10px 24px">开始导入</button></div>';
      d.ov.firstChild.innerHTML = h;
      el('impBack').onclick = paintForm;
      el('impRun').onclick = function () { runImport(sv, dump, key); };
    }

    function runImport(sv, dump, key, force) {
      var btn = el('impRun');
      if (btn) { btn.disabled = true; btn.textContent = '正在写入服务端…'; }
      var payload = {
        tables: sv.tables || {},
        config: sv.config || {},
        mode: mode(), force: !!force
      };
      if ('creds' in sv) payload.creds = sv.creds || [];
      if ('sub' in sv) payload.sub = sv.sub || {};
      if ('typing' in sv) payload.typing = sv.typing || {};
      if ('files' in sv) payload.files = sv.files || {};
      if ('activity' in sv) payload.activity = sv.activity || {};
      var tp = el('impTpwd');
      if (tp) payload.teacher_password = tp.value;
      fetch('/api/v2/admin/import-preauth', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (j) {
          /* 数据量明显减少：服务端要求再确认一次 → 二次确认后带 force 重试 */
          if (!j.ok && j.code === 'needs_force') {
            var msg = (j.msg || '') + '\n\n继续将用备份内容覆盖服务端现有数据，且无法撤销。';
            var c = dlg('<div style="font-size:13.6px;line-height:1.8;white-space:pre-wrap">'
              + esc(msg) + '</div>'
              + '<div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px">'
              + '<button id="_cNo" style="background:#EAF4F0;color:#0F6E56">停止</button>'
              + '<button id="_cYes" style="background:#B3261E">仍然导入</button></div>');
            c.ov.querySelector('#_cNo').onclick = function () { c.close(); paintPreview(dump, key); };
            c.ov.querySelector('#_cYes').onclick = function () {
              c.close(); runImport(sv, dump, key, true);
            };
            return;
          }
          if (!j.ok) {
            if (btn) { btn.disabled = false; btn.textContent = '开始导入'; }
            var err = el('impErr');
            if (err) err.textContent = j.msg || ('导入失败（HTTP 状态未知）');
            return;
          }
          finish(j, dump, key);
        })
        .catch(function (e) {
          if (btn) { btn.disabled = false; btn.textContent = '开始导入'; }
          var err = el('impErr');
          if (err) err.textContent = '连不上服务端：' + ((e && e.message) || e)
            + '。请确认工作台服务正在运行。';
        });
    }

    function finish(j, dump, key) {
      /* 新设备顺手把备份里的本机段铺进来（只在本机还是空的时候） */
      var ls = applyLocalLs(dump, key);
      var c = j.counts || {};
      var extras = { student_creds: 1, typing_students: 1, submission_files: 1,
                     activity_tasks: 1, activity_submissions: 1, activity_files: 1,
                     submission_data: 1 };
      var tblN = Object.keys(c).filter(function (k) { return !extras[k]; }).length;
      var bits = ['服务端 ' + tblN + ' 张表 / ' + (j.rows || 0) + ' 行'];
      if (c.student_creds) bits.push('学生密码 ' + c.student_creds + ' 条');
      if (c.typing_students) bits.push('打字成绩 ' + c.typing_students + ' 人');
      if (c.submission_files) bits.push('作品附件 ' + c.submission_files + ' 个');
      if (c.activity_tasks) bits.push('课堂活动 ' + c.activity_tasks + ' 个');
      if (ls.n) bits.push('本机界面数据 ' + ls.n + ' 项');
      var warn = (j.warnings || []).slice();
      if (ls.skipped) warn.push('本机已有数据，界面数据保持原样未动。');
      if (ls.err) warn.push('本机界面数据没写入成功：' + ls.err + '（不影响服务端数据）');
      var h = ''
        + '<div style="font-weight:700;font-size:17px;color:#0F6E56;margin-bottom:10px">✓ 导入完成</div>'
        + '<div style="font-size:13.6px;line-height:1.9">已恢复：' + esc(bits.join('，')) + '。</div>'
        + (warn.length
          ? '<div style="font-size:12.6px;color:#8A6A1C;line-height:1.75;margin-top:10px;'
            + 'background:#FFF8E2;border-radius:10px;padding:10px 13px">'
            + warn.map(esc).join('<br>') + '</div>' : '')
        + '<div style="font-size:12.8px;color:#5F6E68;line-height:1.75;margin-top:12px">'
        + '  现在可以用<b>备份里的教师账号和密码</b>登录了（账号密码已随备份恢复）。</div>'
        + '<div style="text-align:right;margin-top:14px"><button id="impDone" style="padding:10px 28px">去登录</button></div>';
      d.ov.firstChild.innerHTML = h;
      el('impDone').onclick = function () {
        location.href = '/teacher/login';
      };
    }
  }

  /* ---------------- 「？」说明弹窗（登录页提示精简配套） ---------------- */
  var HELP = {
    login: ['登录页说明',
      '【学生怎么进】\n浏览器打开本服务地址后加 /s/（学生入口会自动带出），'
      + '选择班级和学号，输入老师设置的初始密码（默认 123456）。\n\n'
      + '【忘了教师密码】\n设过密保问题：点上方「忘记密码」自助重设；'
      + '没设过密保：需要在服务器上执行\n'
      + 'docker compose exec workbench python launch.py --reset-password\n'
      + '清除后重新设置，数据不会丢。\n\n'
      + '【换设备 / 重装】\n点「数据导入」，选择旧设备导出的 .cwbackup 全量备份，'
      + '输入备份密码即可整体恢复。'],
    setup: ['首次设置说明',
      '【这个账号】\n管理员账号保护教师端和全部学生数据，只存在服务端，不上传第三方。\n\n'
      + '【密保问题】\n答案只保存加密哈希，连老师自己也看不到原文；忘了密码时用它自助改密。\n\n'
      + '【学生登录】\n学生在 /s/ 选择班级、学号，输入初始密码（默认 123456）即可进入，'
      + '只能看到自己的数据。\n\n'
      + '【旧设备有数据？】\n点「数据导入」恢复备份后，教师账号也会一起回来，'
      + '就不用在这里新建账号了。']
  };
  function help(topic) {
    var t = HELP[topic] || HELP.login;
    alertBox(t[0], esc(t[1]).replace(/\n/g, '<br>'));
  }

  root.LoginIO = { open: open, help: help, alert: alertBox };
})(typeof window !== 'undefined' ? window : this);
