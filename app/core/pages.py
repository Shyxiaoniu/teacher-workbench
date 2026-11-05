#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""服务端渲染的几个小页面：登录、初始化、学生端控制台、加入说明。

这些页面刻意不依赖任何前端框架 —— 教室机器可能是五年前的 Edge，
而且这些页面必须在“主程序还没加载”的时刻就能用。
"""

BRAND = "#0F6E56"
BRAND_2 = "#1D9E75"

BASE_CSS = """
*{box-sizing:border-box}
body{margin:0;background:#F7FAF9;color:#16231F;
  font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 64px}
.card{background:#fff;border:1px solid rgba(15,110,86,.16);border-radius:16px;padding:26px 28px;
  box-shadow:0 1px 3px rgba(15,110,86,.06)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:18px}
.logo{width:34px;height:34px;border-radius:9px;background:#0F6E56;color:#fff;display:grid;
  place-items:center;font-size:17px;font-weight:700}
h1{font-size:22px;margin:0 0 6px;letter-spacing:-.2px}
h2{font-size:15.5px;margin:0 0 12px}
.sub{color:#5F6E68;font-size:13.6px;margin:0 0 18px}
label{display:block;font-size:12.6px;color:#55655F;margin:14px 0 6px;font-weight:600}
input,select{width:100%;padding:11px 13px;border:1px solid #D6E3DE;border-radius:10px;font-size:15px;
  background:#FBFDFC;color:#16231F;outline:none;font-family:inherit}
input:focus,select:focus{border-color:#1D9E75;box-shadow:0 0 0 3px rgba(29,158,117,.12)}
button{padding:11px 18px;border:0;border-radius:10px;background:#0F6E56;color:#fff;font-size:14.5px;
  font-weight:600;cursor:pointer;font-family:inherit}
button:hover{background:#1D9E75}
button.ghost{background:#EAF4F0;color:#0F6E56}
button.danger{background:#B3261E}
.row{display:flex;gap:10px;align-items:center}
.err{color:#B3261E;font-size:13px;min-height:20px;margin-top:10px}
.tip{color:#849690;font-size:12.4px;line-height:1.8;margin-top:14px}
/* 「？」说明钮（2026-11-05）：提示精简成一两行，完整说明收进弹窗 */
.qbtn{width:22px;height:22px;border-radius:50%;border:1px solid #CDE2DA;background:#F1F7F4;
  color:#0F6E56;font-size:13px;font-weight:700;cursor:pointer;line-height:1;padding:0;flex:none}
.qbtn:hover{background:#0F6E56;color:#fff;border-color:#0F6E56}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
.kpi{background:#F1F7F4;border-radius:12px;padding:14px 16px}
.kpi b{display:block;font-size:24px;color:#0F6E56;line-height:1.2}
.kpi span{font-size:12.4px;color:#5F6E68}
table{width:100%;border-collapse:collapse;font-size:13.2px}
th{text-align:left;color:#24564A;background:#F1F7F4;padding:9px 10px;font-weight:650}
td{padding:9px 10px;border-bottom:1px solid rgba(15,110,86,.08);color:#55655F}
.switch{display:flex;align-items:center;justify-content:space-between;padding:11px 0;
  border-bottom:1px solid rgba(15,110,86,.08)}
.switch:last-child{border-bottom:0}
.switch .nm{font-size:14px}
.switch .ds{font-size:12.2px;color:#849690}
.toggle{width:46px;height:26px;border-radius:99px;background:#D6E3DE;position:relative;cursor:pointer;
  transition:.18s;flex:none}
.toggle::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;
  background:#fff;transition:.18s;box-shadow:0 1px 2px rgba(0,0,0,.2)}
.toggle.on{background:#1D9E75}
.toggle.on::after{left:23px}
#qr{display:flex;justify-content:center;padding:12px 0}
#qr img,#qr canvas{border:8px solid #fff;border-radius:10px}
.url{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:13px;background:#F2F6F4;
  border:1px solid rgba(15,110,86,.12);padding:8px 11px;border-radius:8px;word-break:break-all}
.note{background:#F4FAF7;border-left:3px solid #1D9E75;border-radius:0 10px 10px 0;padding:10px 14px;
  font-size:13px;color:#4A6A60;margin:14px 0}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.6px;background:#F0F5F3;
  padding:1px 5px;border-radius:5px;font-variant-ligatures:none}
/* 禁用连字：部分等宽字体把 -- 连成 — ，命令行参数会被新手照抄错 */
@media(max-width:760px){.grid{grid-template-columns:1fr}}
"""

LOGO = '<div class="logo">班</div>'


def _page(title, body, extra_head=""):
    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title><style>%s</style>%s</head>
<body><div class="wrap">%s</div></body></html>""" % (title, BASE_CSS, extra_head, body)


LOGIN_BODY = """
<div class="card" style="max-width:440px;margin:7vh auto 0">
  <div class="brand">__LOGO__<div><div style="font-weight:750;font-size:15px">班级工作台</div>
  <div style="font-size:12px;color:#849690">服务端部署 · 多教师账号</div></div></div>
  <div style="display:flex;gap:8px;margin:4px 0 14px">
    <button type="button" id="tabAdmin" style="flex:1;padding:9px 10px;border-radius:10px;border:1.5px solid #0F6E56;background:#0F6E56;color:#fff;font-weight:700;font-size:13.5px;cursor:pointer">管理员登录</button>
    <button type="button" id="tabTeacher" style="flex:1;padding:9px 10px;border-radius:10px;border:1.5px solid #D9E5DF;background:#fff;color:#5F6E68;font-weight:700;font-size:13.5px;cursor:pointer">教师登录</button>
  </div>
  <h1 id="lgTitle">管理员登录</h1>
  <p class="sub" id="lgSub">默认账号 <b>teacher</b>、默认密码 <b>t12345678</b>；部署时可用环境变量修改。管理员只负责教师账号的注册与管理。</p>
  <form id="f">
    <label>账号</label>
    <input id="user" type="text" autocomplete="username" placeholder="账号" value="teacher" autofocus>
    <label>密码</label>
    <input id="pwd" type="password" autocomplete="current-password" placeholder="密码">
    <div class="err" id="err">__MSG__</div>
    <button style="width:100%;margin-top:6px" id="btn">进入管理控制台</button>
  </form>
  <div id="forgotWrap" style="margin-top:14px;font-size:13px;text-align:center">
    <a href="/teacher/forgot" style="color:#0F6E56;text-decoration:none">忘记密码？用密保问题重设 →</a>
  </div>
  <div style="margin-top:8px;font-size:13px;text-align:center">
    <a href="javascript:void 0" id="impLink" style="color:#5F6E68;text-decoration:none">📥 数据导入（换设备 / 重装后一键恢复全部数据）</a>
  </div>
  <div class="tip" style="display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap">
    <span>学生入口 <code>/s/</code> · 教师账号由管理员统一创建</span>
    <button id="helpBtn" type="button" class="qbtn" aria-label="查看登录页说明">？</button>
  </div>
</div>
<script>
document.getElementById('impLink').onclick=function(){LoginIO.open();};
document.getElementById('helpBtn').onclick=function(){LoginIO.help('login');};
var lgRole='admin';
function lgSet(r){
  lgRole=r;
  var a=document.getElementById('tabAdmin'), t=document.getElementById('tabTeacher');
  var on='1.5px solid #0F6E56', off='1.5px solid #D9E5DF';
  a.style.background=r==='admin'?'#0F6E56':'#fff'; a.style.color=r==='admin'?'#fff':'#5F6E68'; a.style.border=r==='admin'?on:off;
  t.style.background=r==='teacher'?'#0F6E56':'#fff'; t.style.color=r==='teacher'?'#fff':'#5F6E68'; t.style.border=r==='teacher'?on:off;
  document.getElementById('lgTitle').textContent=r==='admin'?'管理员登录':'教师登录';
  document.getElementById('lgSub').innerHTML=r==='admin'
    ? '默认账号 <b>teacher</b>、默认密码 <b>t12345678</b>；部署时可用环境变量修改。管理员只负责教师账号的注册与管理。'
    : '教师账号由管理员在管理控制台创建；每位教师的数据、资料与文件彼此独立。';
  document.getElementById('btn').textContent=r==='admin'?'进入管理控制台':'进入教师工作台';
  document.getElementById('forgotWrap').style.display=r==='teacher'?'':'none';
  document.getElementById('err').textContent='';
}
document.getElementById('tabAdmin').onclick=function(){lgSet('admin');};
document.getElementById('tabTeacher').onclick=function(){lgSet('teacher');};
</script>
<script>
document.getElementById('f').onsubmit = async function(e){
  e.preventDefault();
  var err=document.getElementById('err'), btn=document.getElementById('btn');
  var label=btn.textContent;
  err.textContent=''; btn.disabled=true; btn.textContent='验证中…';
  var r=await fetch('/api/v2/auth/teacher',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:document.getElementById('user').value.trim(),
                         password:document.getElementById('pwd').value, role:lgRole})});
  var j=await r.json().catch(function(){return {};});
  if(j.ok){location.href=j.landing||(lgRole==='admin'?'/admin/':'/teacher/');return;}
  err.textContent=j.msg||'账号或密码不正确'; btn.disabled=false; btn.textContent=label;
};
</script>"""

# 2.0.13 起账号注册已取消：下面的 SETUP_BODY 是历史模板，仅作备份兼容保留，
# 没有任何路由或函数再渲染它（login_page_setup 也改为直接返回登录页）。
# 教师账号一律由管理员在 /admin/ 控制台创建。
SETUP_BODY = """
<div class="card" style="max-width:480px;margin:5vh auto 0">
  <div class="brand">__LOGO__<div><div style="font-weight:750;font-size:15px">班级工作台</div>
  <div style="font-size:12px;color:#849690">首次启动 · 只需设置一次</div></div></div>
  <div id="setupTitle">
    <h1>创建管理员账号</h1>
    <p class="sub">这个账号保护教师端和全部学生数据，只保存在服务端，不上传第三方、不联外网。</p>
  </div>
  <div id="loginTitle" style="display:none">
    <h1>教师登录</h1>
    <p class="sub">用已有的管理员账号进入工作台。</p>
  </div>
  <form id="f">
    <label>账号</label>
    <input id="user" type="text" autocomplete="username" value="teacher" autofocus>
    <label>密码（至少 8 位，别用 123456 这类）</label>
    <input id="p1" type="password" autocomplete="new-password" placeholder="设置密码">
    <label>再输一遍</label>
    <input id="p2" type="password" autocomplete="new-password" placeholder="确认密码">
    <div style="margin:18px 0 4px;padding-top:14px;border-top:1px solid rgba(15,110,86,.12)">
      <div style="font-size:13px;font-weight:700;color:#0F6E56">密保问题（用于忘记密码时自助改密）</div>
    </div>
    <label>问题</label>
    <input id="q" type="text" maxlength="60" placeholder="例如：我第一只宠物的名字？">
    <label>答案</label>
    <input id="a" type="text" placeholder="答案（不区分大小写，忽略空格）">
    <div class="err" id="err">__MSG__</div>
    <button style="width:100%;margin-top:8px" id="btn">创建并进入</button>
  </form>
  <form id="lg" style="display:none">
    <label>账号</label>
    <input id="luser" type="text" autocomplete="username" value="teacher" autofocus>
    <label>密码</label>
    <input id="lpwd" type="password" autocomplete="current-password" placeholder="密码">
    <div class="err" id="lerr"></div>
    <button style="width:100%;margin-top:8px" id="lbtn">进入工作台</button>
  </form>
  <div style="margin-top:14px;font-size:13px;text-align:center">
    <a href="javascript:void 0" id="swLogin" style="color:#0F6E56;text-decoration:none">已有账号？直接登录 →</a>
    <a href="javascript:void 0" id="swSetup" style="display:none;color:#0F6E56;text-decoration:none">← 还没有账号？创建一个</a>
  </div>
  <div style="margin-top:8px;font-size:13px;text-align:center">
    <a href="javascript:void 0" id="impLink" style="color:#5F6E68;text-decoration:none">📥 数据导入（旧设备有备份？直接恢复，账号也一起回来）</a>
  </div>
  <div class="tip" style="display:flex;align-items:center;justify-content:center;gap:8px;flex-wrap:wrap">
    <span>密保答案只存加密哈希 · 学生入口 <code>/s/</code> · 详细说明点</span>
    <button id="helpBtn" type="button" class="qbtn" aria-label="查看首次设置说明">？</button>
  </div>
</div>
<script>
document.getElementById('f').onsubmit = async function(e){
  e.preventDefault();
  var p1=document.getElementById('p1').value, p2=document.getElementById('p2').value;
  var user=document.getElementById('user').value.trim();
  var q=document.getElementById('q').value.trim(), a=document.getElementById('a').value;
  var err=document.getElementById('err');
  if(user.length<2){err.textContent='账号至少 2 个字符';return;}
  if(p1.length<8){err.textContent='密码至少 8 位';return;}
  if(p1!==p2){err.textContent='两次输入不一致';return;}
  if(!q||!a.trim()){err.textContent='请设置密保问题和答案（忘了密码时靠它找回）';return;}
  if(q.length<4){err.textContent='密保问题至少 4 个字';return;}
  var btn=document.getElementById('btn'); btn.disabled=true; btn.textContent='创建中…';
  var r=await fetch('/api/v2/auth/setup',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:user,password:p1,question:q,answer:a})});
  var j=await r.json().catch(function(){return {};});
  if(j.ok){location.href='/teacher/';return;}
  err.textContent=j.msg||'创建失败'; btn.disabled=false; btn.textContent='创建并进入';
};
/* 已有账号直接登录（2026-11-04）：首次设置页可切换到登录表单。
   典型场景：数据/配置已经在服务器上、账号是之前设好的，不需要再建一个。 */
(function(){
  var show=function(login){
    document.getElementById('f').style.display=login?'none':'block';
    document.getElementById('setupTitle').style.display=login?'none':'block';
    document.getElementById('lg').style.display=login?'block':'none';
    document.getElementById('loginTitle').style.display=login?'block':'none';
    document.getElementById('swLogin').style.display=login?'none':'inline';
    document.getElementById('swSetup').style.display=login?'inline':'none';
    document.getElementById('lerr').textContent='';
    document.getElementById('err').textContent='';
    (login?document.getElementById('luser'):document.getElementById('user')).focus();
  };
  document.getElementById('swLogin').onclick=function(){show(true);};
  document.getElementById('swSetup').onclick=function(){show(false);};
  document.getElementById('lg').onsubmit = async function(e){
    e.preventDefault();
    var err=document.getElementById('lerr'), btn=document.getElementById('lbtn');
    err.textContent=''; btn.disabled=true; btn.textContent='验证中…';
    var r=await fetch('/api/v2/auth/teacher',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:document.getElementById('luser').value.trim(),
                           password:document.getElementById('lpwd').value})});
    var j=await r.json().catch(function(){return {};});
    if(j.ok){location.href='/teacher/';return;}
    err.textContent=j.msg||'账号或密码不正确'; btn.disabled=false; btn.textContent='进入工作台';
  };
})();
document.getElementById('impLink').onclick=function(){LoginIO.open();};
document.getElementById('helpBtn').onclick=function(){LoginIO.help('setup');};
</script>"""


# 登录 / 首次设置页共用的两份公开脚本：
#   · cipher.js        AES/SHA256 加密核心（与教师端 index.html 同一份文件）
#   · login-import.js  「数据导入」对话框 + 「？」说明弹窗（window.LoginIO）
LOGIN_SCRIPTS = ('<script src="/core/cipher.js"></script>'
                 '<script src="/login-import.js"></script>')


def login_page(msg=""):
    body = LOGIN_BODY.replace("__LOGO__", LOGO).replace("__MSG__", msg)
    return _page("登录 · 班级工作台", body, LOGIN_SCRIPTS)


def login_page_setup(msg=""):
    # 2.0.13：账号注册已取消（教师账号由管理员统一创建）。
    # 保留函数只是为了让旧调用 / 旧书签落到登录页，不再出现创建账号表单。
    return login_page(msg)


# ---------------------------------------------------------------------------
# 管理员控制台（2.0.13）：只管教师账号，不承接任何业务设置
# 2026-11-05：改名 / 重置密码 / 删除原来是浏览器原生 prompt() / confirm()，
# 外观和整个工作台完全脱节。统一换成页内弹窗（dlg）：
# 同一张卡片语言（白卡 + 品牌绿描边 + 16px 圆角），危险操作用红色主按钮。
# ---------------------------------------------------------------------------
ADMIN_DLG_CSS = """
.dlg-ov{position:fixed;inset:0;background:rgba(22,35,31,.42);display:flex;
  align-items:center;justify-content:center;z-index:99;padding:20px}
.dlg{background:#fff;border:1px solid rgba(15,110,86,.16);border-radius:16px;
  box-shadow:0 12px 40px rgba(15,110,86,.18);width:100%;max-width:360px;
  padding:22px 24px;animation:dlgIn .16s ease}
@keyframes dlgIn{from{transform:translateY(10px) scale(.97);opacity:0}
  to{transform:none;opacity:1}}
.dlg h3{margin:0 0 6px;font-size:16px;color:#16231F}
.dlg .d{font-size:12.8px;color:#5F6E68;line-height:1.7;margin:0 0 12px}
.dlg input{margin:2px 0 0}
.dlg .btns{display:flex;gap:10px;justify-content:flex-end;margin-top:8px}
.dlg .btns button{padding:9px 16px;font-size:13.5px}
.dlg .btns .cancel{background:#EAF4F0;color:#0F6E56}
.dlg .btns .cancel:hover{background:#DCEEE6}
.dlg .btns .dlg-ok{background:#0F6E56}
.dlg .btns .dlg-ok:hover{background:#1D9E75}
.dlg .btns .dlg-ok.danger{background:#B3261E}
.dlg .btns .dlg-ok.danger:hover{background:#C6352C}
"""

ADMIN_BODY = """
<style>__DLG_CSS__</style>
<div class="card" style="max-width:760px;margin:5vh auto 0">
  <div class="brand">__LOGO__<div><div style="font-weight:750;font-size:15px">班级工作台 · 管理员</div>
  <div style="font-size:12px;color:#849690">教师账号管理</div></div></div>
  <h1>教师账号</h1>
  <p class="sub">管理员只负责教师账号的注册与管理。班级、学生、资料、课程与各项设置仍由教师端配置；
  每位教师的数据、资料与文件各自独立，互不重叠。</p>

  <div style="border:1px solid #E4EDE8;border-radius:12px;padding:14px;margin:16px 0">
    <b style="font-size:14px">新增教师账号</b>
    <form id="addForm" style="margin-top:10px">
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <input id="nu" placeholder="登录账号" style="flex:1;min-width:130px" autocomplete="off">
        <input id="nn" placeholder="显示姓名（选填）" style="flex:1;min-width:130px" autocomplete="off">
        <input id="np" type="password" placeholder="初始密码（至少 8 位）" style="flex:1;min-width:160px" autocomplete="new-password">
        <button type="submit" id="addBtn">创建账号</button>
      </div>
      <div class="err" id="addErr"></div>
    </form>
  </div>

  <div id="list" style="margin-top:12px"><span style="color:#849690">加载中…</span></div>

  <div style="border-top:1px solid #E4EDE8;margin-top:20px;padding-top:14px">
    <b style="font-size:14px">修改管理员密码</b>
    <form id="pwdForm" style="margin-top:10px">
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <input id="oldP" type="password" placeholder="当前密码" style="flex:1;min-width:130px">
        <input id="newP" type="password" placeholder="新密码（至少 8 位）" style="flex:1;min-width:160px">
        <button type="submit">保存</button>
      </div>
      <div class="err" id="pwdErr"></div>
    </form>
  </div>

  <div style="margin-top:18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;font-size:13px">
    <a href="javascript:void 0" id="logout" style="color:#0F6E56;text-decoration:none">退出登录</a>
    <span style="color:#849690">账号信息保存在服务端 accounts.json；删除账号不会自动清空其数据目录。</span>
  </div>
</div>
<script>
function A(u,b){return fetch(u,{method:'POST',credentials:'same-origin',
  headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})})
  .then(function(r){return r.json().catch(function(){return {};});});}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function toast(el,msg){el.textContent=msg;if(msg)setTimeout(function(){el.textContent='';},4000);}

/* 工作台风格弹窗（替代浏览器原生 prompt / confirm）。
   cfg: {title, desc, input, type, value, placeholder, ok, danger, empty, validate}
   返回 Promise：确定 → 输入框的值（无输入框时 true），取消 / 点遮罩 / Esc → null。 */
function dlg(cfg){
  return new Promise(function(res){
    var ov=document.createElement('div');ov.className='dlg-ov';
    ov.innerHTML='<div class="dlg" role="dialog" aria-modal="true">'+
      '<h3>'+esc(cfg.title||'')+'</h3>'+
      (cfg.desc?'<p class="d">'+esc(cfg.desc)+'</p>':'')+
      (cfg.input?'<input id="dlgIn" type="'+(cfg.type||'text')+'" value="'+
        esc(cfg.value||'')+'" placeholder="'+esc(cfg.placeholder||'')+'" autocomplete="off">':'')+
      '<div class="err" style="margin-top:8px" id="dlgErr"></div>'+
      '<div class="btns"><button type="button" class="cancel">取消</button>'+
      '<button type="button" class="dlg-ok'+(cfg.danger?' danger':'')+'">'+
      esc(cfg.ok||'确定')+'</button></div></div>';
    document.body.appendChild(ov);
    var inp=ov.querySelector('#dlgIn'),err=ov.querySelector('#dlgErr');
    function close(v){document.removeEventListener('keydown',onKey);ov.remove();res(v);}
    function ok(){
      if(!cfg.input)return close(true);
      var v=(inp.value||'').trim();
      if(!v){err.textContent=cfg.empty||'请填写内容';inp.focus();return;}
      if(cfg.validate){var m=cfg.validate(v);if(m){err.textContent=m;inp.focus();return;}}
      close(v);
    }
    function onKey(e){
      if(e.key==='Escape')close(null);
      else if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();ok();}
    }
    ov.querySelector('.cancel').onclick=function(){close(null);};
    ov.querySelector('.dlg-ok').onclick=ok;
    ov.addEventListener('mousedown',function(e){if(e.target===ov)close(null);});
    document.addEventListener('keydown',onKey);
    if(inp){inp.focus();inp.select();}else{ov.querySelector('.dlg-ok').focus();}
  });
}

async function load(){
  var r=await fetch('/api/v2/admin/teachers',{credentials:'same-origin'});
  if(r.status===401){location.href='/teacher/login';return;}
  var j=await r.json().catch(function(){return {};});
  var box=document.getElementById('list');
  var rows=(j.teachers||[]);
  box.innerHTML='<table style="width:100%;border-collapse:collapse;font-size:13px">'+
    '<thead><tr style="text-align:left;color:#5F6E68"><th style="padding:7px 4px">账号</th>'+
    '<th style="padding:7px 4px">姓名</th><th style="padding:7px 4px">状态</th>'+
    '<th style="padding:7px 4px">操作</th></tr></thead><tbody>'+
    rows.map(function(t){
      return '<tr style="border-top:1px solid #EEF3F0"><td style="padding:8px 4px"><b>'+esc(t.username)+'</b>'+
        '<div style="font-size:11px;color:#849690">'+esc(t.id)+'</div></td>'+
        '<td style="padding:8px 4px">'+esc(t.name||'—')+'</td>'+
        '<td style="padding:8px 4px">'+(t.has_password?'已设密码':'<span style="color:#B4692E">未设密码</span>')+
        (t.builtin?' · 默认账号':'')+'</td>'+
        '<td style="padding:8px 4px;white-space:nowrap">'+
        '<button type="button" data-act="name" data-id="'+esc(t.id)+'" data-u="'+esc(t.username)+'">改名</button> '+
        '<button type="button" data-act="pwd" data-id="'+esc(t.id)+'">重置密码</button> '+
        (t.builtin?'':'<button type="button" data-act="del" data-id="'+esc(t.id)+'">删除</button>')+
        '</td></tr>';
    }).join('')+'</tbody></table>';
  box.querySelectorAll('button[data-act]').forEach(function(b){
    b.onclick=async function(){
      var id=b.getAttribute('data-id'), act=b.getAttribute('data-act');
      if(act==='name'){
        var u=await dlg({title:'修改登录账号',
          desc:'教师用这个账号登录教师端与登录页，改完请及时告知本人。',
          input:true,value:b.getAttribute('data-u')||'',
          placeholder:'新的登录账号名',ok:'保存',empty:'请填写账号名',
          validate:function(v){return v.length>=2?'':'账号名至少 2 个字符';}});
        if(!u)return; var r=await A('/api/v2/admin/teacher/update',{id:id,username:u});
        toast(document.getElementById('addErr'),r.ok?'已改名':(r.msg||'失败')); load();
      }else if(act==='pwd'){
        var p=await dlg({title:'重置初始密码',
          desc:'重置后请把新密码告知这位老师，TA 登录后可在设置里自行修改。',
          input:true,type:'password',placeholder:'新密码（至少 8 位）',ok:'重置',
          empty:'请填写新密码',
          validate:function(v){return v.length>=8?'':'密码至少 8 位';}});
        if(!p)return; var r=await A('/api/v2/admin/teacher/update',{id:id,password:p});
        toast(document.getElementById('addErr'),r.ok?'密码已重置':(r.msg||'失败'));
      }else{
        var sure=await dlg({title:'删除教师账号',
          desc:'删除后这位教师将无法登录；其数据目录会保留，不会自动清空。',
          ok:'删除',danger:true});
        if(!sure)return;
        var r=await A('/api/v2/admin/teacher/delete',{id:id});
        toast(document.getElementById('addErr'),r.ok?'已删除':(r.msg||'失败')); load();
      }
    };
  });
}
document.getElementById('addForm').onsubmit=async function(e){
  e.preventDefault();
  var err=document.getElementById('addErr'); err.textContent='';
  var r=await A('/api/v2/admin/teacher/add',{
    username:document.getElementById('nu').value.trim(),
    name:document.getElementById('nn').value.trim(),
    password:document.getElementById('np').value});
  if(r.ok){document.getElementById('nu').value='';document.getElementById('nn').value='';
    document.getElementById('np').value='';toast(err,'已创建教师账号');load();}
  else toast(err,r.msg||'创建失败');
};
document.getElementById('pwdForm').onsubmit=async function(e){
  e.preventDefault();var err=document.getElementById('pwdErr');
  var r=await A('/api/v2/admin/password',{
    old:document.getElementById('oldP').value,new:document.getElementById('newP').value});
  if(r.ok){document.getElementById('oldP').value='';document.getElementById('newP').value='';toast(err,'已修改');}
  else toast(err,r.msg||'修改失败');
};
document.getElementById('logout').onclick=async function(){
  await A('/api/v2/auth/logout',{}); location.href='/teacher/login';
};
load();
</script>"""


def admin_page():
    return _page("管理员控制台 · 班级工作台",
                 ADMIN_BODY.replace("__LOGO__", LOGO)
                           .replace("__DLG_CSS__", ADMIN_DLG_CSS))


FORGOT_BODY = """
<div class="card" style="max-width:440px;margin:7vh auto 0">
  <div class="brand">__LOGO__<div><div style="font-weight:750;font-size:15px">班级工作台</div>
  <div style="font-size:12px;color:#849690">教师端 · 找回密码</div></div></div>
  <h1>用密保问题重设密码</h1>
  <p class="sub">两步：回答密保问题 → 设置新密码。不需要旧密码，数据不受影响。</p>

  <div id="s1">
    <label>密保问题</label>
    <div class="url" id="qbox" style="font-size:14px">正在读取…</div>
    <label>答案</label>
    <input id="ans" type="text" autocomplete="off" placeholder="不区分大小写，空格会被忽略">
    <div class="err" id="e1"></div>
    <button style="width:100%;margin-top:6px" id="b1">验证答案</button>
  </div>

  <div id="s2" style="display:none">
    <div class="note">✓ 密保验证通过。设置一个新密码（至少 4 位）。</div>
    <label>新密码</label>
    <input id="n1" type="password" autocomplete="new-password" placeholder="新密码">
    <label>再输一遍</label>
    <input id="n2" type="password" autocomplete="new-password" placeholder="重复新密码">
    <div class="err" id="e2"></div>
    <button style="width:100%;margin-top:6px" id="b2">保存并进入工作台</button>
  </div>

  <div class="tip" id="fallback" style="display:none">
    这个账号还没设置密保问题，无法自助找回。请在服务器上执行
    <code>docker compose exec workbench python launch.py --reset-password</code>
    清除口令后重新设置（数据不会丢）。
  </div>
  <div style="margin-top:16px;font-size:13px;text-align:center">
    <a href="/teacher/login" style="color:#5F6E68">← 返回登录</a>
  </div>
</div>
<script>
var ticket='';
function el(id){return document.getElementById(id);}
fetch('/api/v2/auth/qa/question',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
 .then(function(r){return r.json();}).then(function(j){
   if(!j.ok){el('qbox').textContent='读取失败，请刷新重试';return;}
   if(!j.has_qa){
     el('s1').style.display='none'; el('fallback').style.display='block';
     el('qbox').textContent='未设置密保问题'; return;
   }
   el('qbox').textContent=j.question;
 }).catch(function(){ el('qbox').textContent='网络异常，请刷新重试'; });

el('b1').onclick=function(){
  var a=el('ans').value, e=el('e1'), b=el('b1');
  if(!a.trim()){e.textContent='请填写答案';return;}
  e.textContent=''; b.disabled=true; b.textContent='验证中…';
  fetch('/api/v2/auth/qa/verify',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({answer:a})}).then(function(r){return r.json();}).then(function(j){
    b.disabled=false; b.textContent='验证答案';
    if(!j.ok){e.textContent=j.msg||'答案不正确';return;}
    ticket=j.ticket;
    el('s1').style.display='none'; el('s2').style.display='block';
    el('n1').focus();
  }).catch(function(){b.disabled=false;b.textContent='验证答案';e.textContent='网络异常';});
};

el('b2').onclick=function(){
  var p1=el('n1').value,p2=el('n2').value,e=el('e2'),b=el('b2');
  if(p1.length<4){e.textContent='新密码至少 4 位';return;}
  if(p1!==p2){e.textContent='两次输入不一致';return;}
  e.textContent=''; b.disabled=true; b.textContent='保存中…';
  fetch('/api/v2/auth/qa/reset',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({ticket:ticket,password:p1})}).then(function(r){return r.json();}).then(function(j){
    if(j.ok){location.href='/teacher/';return;}
    b.disabled=false; b.textContent='保存并进入工作台';
    e.textContent=j.msg||'保存失败';
    if(/过期/.test(j.msg||'')) setTimeout(function(){location.reload();},1200);
  }).catch(function(){b.disabled=false;b.textContent='保存并进入工作台';e.textContent='网络异常';});
};
document.addEventListener('keydown',function(e){
  if(e.key!=='Enter')return;
  if(el('s2').style.display!=='none') el('b2').click(); else el('b1').click();
});
</script>"""


def forgot_page():
    return _page("找回密码 · 班级工作台", FORGOT_BODY.replace("__LOGO__", LOGO))


# 「/join 扫码进入班级」整页已删除（2026-11-01 牛哥：不要扫码功能）。
# JOIN_BODY / join_page / 二维码脚本一并移除；旧地址 /join 在 server.py
# 里 302 到 /s/，旧的二维码和收藏不会落到 404。


CONSOLE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>学生端控制台 · 班级工作台</title>
<style>__CSS__
.top{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.top .l{display:flex;align-items:center;gap:10px}
.links a{color:#0F6E56;text-decoration:none;font-size:13.4px;margin-left:16px}
.sec{margin-top:18px}
.sec h2{font-size:15px;margin:0 0 12px;display:flex;align-items:center;gap:8px}
.sec h2 .dot{width:7px;height:7px;border-radius:50%;background:#1D9E75}
.mini{font-size:12.2px;color:#849690}
.badge{display:inline-block;font-size:11.5px;padding:2px 8px;border-radius:99px;
  background:#E7F5EE;color:#0B6B3A;font-weight:600}
.badge.warn{background:#FDF1E4;color:#B4570F}
.seg{display:flex;background:#EEF4F1;border-radius:10px;padding:3px;gap:3px;margin:10px 0}
.seg button{flex:1;background:none;color:#55655F;font-size:13px;padding:8px 6px;border-radius:8px}
.seg button.on{background:#fff;color:#0F6E56;box-shadow:0 1px 2px rgba(15,110,86,.12)}
.inp-row{display:flex;gap:8px;margin-top:8px}
.inp-row input{flex:1}
.inp-row button{flex:none;padding:10px 14px;font-size:13.5px}
@keyframes cIn{from{opacity:0;transform:translateY(10px) scale(.97)}to{opacity:1;transform:none}}
</style></head>
<body><div class="wrap">
  <div class="top">
    <div class="l"><div class="logo">班</div>
      <div><div style="font-weight:750;font-size:16px">学生端控制台</div>
      <div class="mini" id="sub">加载中…</div></div></div>
    <div class="links">
      <a href="/teacher/">返回工作台</a>
      <a href="javascript:void 0" id="logout">退出</a>
    </div>
  </div>

  <div class="grid">
    <div class="card">
      <h2><span class="dot"></span>学生入口</h2>
      <div class="url" id="entryUrl">—</div>
      <div style="display:flex;align-items:center;gap:10px;margin-top:10px;padding:9px 11px;
        background:#F4FAF7;border:1px solid rgba(15,110,86,.14);border-radius:10px">
        <span style="font-size:12.2px;color:#5F6E68;flex:none">班级码</span>
        <b id="clsCode" style="font-family:ui-monospace,Menlo,Consolas,monospace;font-size:19px;
          font-weight:750;letter-spacing:.12em;color:#0F6E56">—</b>
        <button id="copyCode" type="button" style="margin-left:auto;flex:none;padding:5px 10px;
          font-size:12px;font-weight:600;border-radius:7px;background:#fff;color:#0F6E56;
          border:1px solid rgba(15,110,86,.24);cursor:pointer">复制</button>
      </div>
      <div class="tip">学生在浏览器输入上面的网址进入：选班级 → 选学号 → 输密码，
        姓名会自动带出来，不用手打。校内网不用填班级码，从外网访问才需要。</div>
    </div>

    <div class="card">
      <h2><span class="dot"></span>今天的数据</h2>
      <div class="grid" style="grid-template-columns:repeat(2,1fr);gap:10px">
        <div class="kpi"><b id="kOnline">0</b><span>在线设备</span></div>
        <div class="kpi"><b id="kStudents">0</b><span>学生</span></div>
        <div class="kpi"><b id="kHw">0</b><span>待交作业</span></div>
        <div class="kpi"><b id="kLeave">0</b><span>待批请假</span></div>
      </div>
      <div class="tip">教师端的改动会自动推送到学生端，不需要手动同步。</div>
    </div>
  </div>

  <div class="card sec">
    <h2><span class="dot"></span>学生登录</h2>
    <div class="seg" id="modeSeg">
      <button data-mode="uniform">全班统一初始密码</button>
      <button data-mode="per">每人独立密码</button>
    </div>
    <div class="mini" id="modeHint"></div>
    <div class="inp-row">
      <input id="initPwd" placeholder="初始密码（至少 6 位）">
      <button id="savePwd">保存</button>
    </div>
    <div class="switch" style="margin-top:12px">
      <div><div class="nm">首次登录必须改密码</div>
        <div class="ds">学生用初始密码进来后，先设一个新密码才能看数据</div></div>
      <div class="toggle" id="forceToggle"></div>
    </div>
    <div class="row" style="justify-content:space-between;margin-top:12px">
      <div class="mini">已单独设过密码：<b id="credCount">0</b> 人</div>
      <button class="ghost" id="resetAll">全部重置为初始密码</button>
    </div>
    <div class="tip">学生名单直接来自教师端的「学生」页，改了名册这里立刻生效，不用二次维护。</div>
  </div>

  <div class="grid sec">
    <div class="card">
      <h2><span class="dot"></span>学生能用的功能</h2>
      <div id="mods"></div>
      <div class="tip">关掉后，学生端入口消失，接口也会直接拒绝（不是只把按钮藏起来）。</div>
    </div>
    <div class="card">
      <h2><span class="dot"></span>可见字段</h2>
      <div id="vis"></div>
      <div class="tip">默认全部关闭。手机号、家长信息学生端永远拿不到。</div>
    </div>
  </div>

  <div class="card sec">
    <h2><span class="dot"></span>教师账号</h2>
    <div class="inp-row">
      <input id="tUser" placeholder="账号" disabled>
      <button id="saveUser" disabled title="账号名由管理员在管理控制台统一修改">管理员管理</button>
    </div>
    <div class="tip" style="margin:0 0 8px">教师账号的注册、改名与删除由管理员负责；这里只能改自己的密码与密保。</div>
    <div class="inp-row">
      <input id="tOld" type="password" placeholder="当前密码">
      <input id="tNew" type="password" placeholder="新密码（至少 4 位）">
      <button id="saveTpwd">改密码</button>
    </div>
    <div class="tip">改完立即生效，已经登录的其它设备会被退出（当前这台保持登录）。忘了密码就在服务器上执行
      <code>docker compose exec workbench python launch.py --reset-password</code>。</div>

    <div style="height:1px;background:rgba(15,110,86,.1);margin:16px 0 4px"></div>
    <h2 style="font-size:14px"><span class="dot"></span>密保问题 <span class="badge" id="qaBadge">未设置</span></h2>
    <div class="mini">忘记密码时，回答密保问题就能自助改密（登录页「忘记密码」）。答案只存加密哈希。</div>
    <div class="inp-row">
      <input id="qaQ" placeholder="密保问题，如：我小学班主任姓什么？">
    </div>
    <div class="inp-row">
      <input id="qaA" placeholder="密保答案（不区分大小写）" style="flex:2">
      <input id="qaPwd" type="password" placeholder="当前密码" style="flex:1">
      <button id="saveQa">保存</button>
    </div>
  </div>

  <div class="card sec">
    <h2><span class="dot"></span>请假申请 <span class="badge" id="leaveBadge">0 待批</span></h2>
    <div id="leaves"><div class="mini">暂无申请</div></div>
  </div>

  <div class="card sec">
    <h2><span class="dot"></span>最近操作</h2>
    <div id="audit"><div class="mini">暂无记录</div></div>
  </div>
</div>
<script>
const MOD_META = {
  homework:['作业','布置、提交、批改'],
  scores:['成绩','已发布考试的本人分数'],
  courseware:['课件','教师标记可下发的课件'],
  morality:['德育','本人得分（不含扣分原因）'],
  duty:['值日','本人值日安排'],
  leave:['请假','提交请假申请']
};
const VIS_META = {
  contact:['家长联系方式','手机号、家长姓名（强烈建议保持关闭）'],
  rank:['班级排名','显示本人位次'],
  morality_reason:['德育扣分原因','显示具体事由'],
  leave_reason:['请假事由','教师可见学生填写的原因']
};
let S = null;

async function api(url, opt){
  const r = await fetch(url, Object.assign({headers:{'Content-Type':'application/json'}}, opt||{}));
  if(r.status===401){ location.href='/teacher/login'; return {}; }
  return r.json().catch(()=>({}));
}

function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

/* 统一弹窗：替代原生 alert/confirm —— 原生弹窗顶着浏览器标题缩在角落，
   和控制台风格脱节。返回 Promise；msg 可含 \n，容器 pre-wrap。 */
function uiDialog(o){
  o = o || {};
  return new Promise(res=>{
    const ov = document.createElement('div');
    ov.style.cssText = 'position:fixed;inset:0;z-index:900;background:rgba(20,30,26,.34);display:flex;align-items:center;justify-content:center;padding:20px';
    ov.innerHTML = '<div style="background:#fff;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.22);max-width:380px;width:100%;overflow:hidden;animation:cIn .16s ease">'
      + '<div style="padding:15px 18px 0;font-weight:600;font-size:14.5px;color:#16231F">' + esc(o.title||'提示') + '</div>'
      + '<div style="padding:10px 18px 4px;font-size:13px;line-height:1.8;white-space:pre-wrap;word-break:break-word;color:#2C3A35">' + (o.html||'') + '</div>'
      + '<div style="display:flex;gap:8px;justify-content:flex-end;padding:12px 18px 15px">'
      + (o.cancelText ? '<button class="ghost ui-c" style="padding:8px 16px;font-size:13px">' + esc(o.cancelText) + '</button>' : '')
      + '<button class="' + (o.danger?'danger':'') + ' ui-o" style="padding:8px 16px;font-size:13px">' + esc(o.okText||'确定') + '</button>'
      + '</div></div>';
    document.body.appendChild(ov);
    const close = v => { ov.remove(); document.removeEventListener('keydown', onKey, true); res(v); };
    ov.querySelector('.ui-o').onclick = () => close(true);
    const c = ov.querySelector('.ui-c');
    if (c) c.onclick = () => close(false);
    ov.addEventListener('pointerdown', e => { if (e.target === ov) close(false); });
    function onKey(e){
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(false); }
      else if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); close(true); }
    }
    document.addEventListener('keydown', onKey, true);
    ov.querySelector('.ui-o').focus();
  });
}
function uiConfirm(msg, o){ return uiDialog(Object.assign({ html:msg, cancelText:'取消', danger:true }, o||{})); }
function uiAlert(msg, o){ return uiDialog(Object.assign({ html:msg, okText:'知道了' }, o||{})); }

function toggleRow(name, desc, on, key, kind){
  return '<div class="switch"><div><div class="nm">'+esc(name)+'</div>' +
    '<div class="ds">'+esc(desc)+'</div></div>' +
    '<div class="toggle '+(on?'on':'')+'" data-kind="'+kind+'" data-key="'+key+'"></div></div>';
}

function paint(){
  if(!S) return;
  document.getElementById('sub').textContent =
    (S.school||'班级工作台') + ' · 服务运行中 · 数据版本 ' + S.rev;
  document.getElementById('entryUrl').textContent = S.entry_url;
  document.getElementById('clsCode').textContent = (S.class_code || '—');
  document.getElementById('kOnline').textContent = S.online;
  document.getElementById('kStudents').textContent = S.students;
  document.getElementById('kHw').textContent = S.open_homework;
  document.getElementById('kLeave').textContent = S.pending_leaves;
  document.getElementById('leaveBadge').textContent = S.pending_leaves + ' 待批';

  document.getElementById('mods').innerHTML = Object.keys(MOD_META)
    .map(k=>toggleRow(MOD_META[k][0], MOD_META[k][1], S.modules[k], k, 'modules')).join('');
  document.getElementById('vis').innerHTML = Object.keys(VIS_META)
    .map(k=>toggleRow(VIS_META[k][0], VIS_META[k][1], S.visibility[k], k, 'visibility')).join('');
  bindToggles();

  const a = S.student_auth || {};
  document.querySelectorAll('#modeSeg button').forEach(b=>{
    b.classList.toggle('on', b.dataset.mode === (a.mode||'uniform'));
  });
  document.getElementById('initPwd').value = a.initial_password || '123456';
  document.getElementById('credCount').textContent = a.custom_count || 0;
  document.getElementById('forceToggle').classList.toggle('on', !!a.force_change);
  document.getElementById('modeHint').textContent = (a.mode==='per')
    ? '每人独立密码：没设过密码的学生无法登录，需要你逐个设置或先切回统一密码。'
    : '全班统一初始密码：学生第一次用这个密码登录，之后可以自己改。';
  document.getElementById('tUser').value = S.teacher_username || 'teacher';
  const qb = document.getElementById('qaBadge');
  if (qb) {
    qb.textContent = S.teacher_has_qa ? '已设置' : '未设置';
    qb.className = 'badge' + (S.teacher_has_qa ? '' : ' warn');
  }
  const qi = document.getElementById('qaQ');
  if (qi && !qi.value && S.teacher_question) qi.value = S.teacher_question;

  paintLeaves();
  paintAudit();
}

function bindToggles(){
  document.querySelectorAll('.toggle[data-key]').forEach(el=>{
    el.onclick = async ()=>{
      const kind = el.dataset.kind, key = el.dataset.key;
      el.classList.toggle('on');
      const body = {}; body[key] = el.classList.contains('on');
      await api('/api/v2/teacher/'+kind, {method:'POST', body:JSON.stringify(body)});
      S[kind][key] = body[key];
    };
  });
  const ft = document.getElementById('forceToggle');
  ft.onclick = async ()=>{
    const on = !ft.classList.contains('on');
    ft.classList.toggle('on', on);
    await api('/api/v2/teacher/student-auth',{method:'POST',
      body:JSON.stringify({force_change:on})});
    S.student_auth.force_change = on;
  };
}

document.querySelectorAll('#modeSeg button').forEach(b=>{
  b.onclick = async ()=>{
    const mode = b.dataset.mode;
    const j = await api('/api/v2/teacher/student-auth',{method:'POST',
      body:JSON.stringify({password_mode:mode})});
    if(j.ok){ S.student_auth.mode = mode; paint(); }
  };
});

document.getElementById('savePwd').onclick = async ()=>{
  const v = document.getElementById('initPwd').value.trim();
  const j = await api('/api/v2/teacher/student-auth',{method:'POST',
    body:JSON.stringify({initial_password:v})});
  await uiAlert(j.ok ? '已保存。学生下次登录用这个密码。' : (j.msg||'保存失败'));
};

document.getElementById('resetAll').onclick = async ()=>{
  if(!(await uiConfirm('把所有学生改过的密码重置为初始密码？',{title:'重置密码',okText:'重置'}))) return;
  await api('/api/v2/teacher/reset-cred',{method:'POST',body:JSON.stringify({all:true})});
  await load();
};

document.getElementById('saveUser').onclick = async ()=>{
  const u = document.getElementById('tUser').value.trim();
  const j = await api('/api/v2/teacher/account',{method:'POST',body:JSON.stringify({username:u})});
  await uiAlert(j.ok ? '账号已改为 '+j.username : (j.msg||'保存失败'));
  await load();
};

document.getElementById('saveTpwd').onclick = async ()=>{
  const old = document.getElementById('tOld').value, nw = document.getElementById('tNew').value;
  const j = await api('/api/v2/teacher/password',{method:'POST',
    body:JSON.stringify({old:old,new:nw})});
  await uiAlert(j.ok ? '密码已更新。其它设备上的登录已失效。' : (j.msg||'修改失败'));
  if(j.ok){ document.getElementById('tOld').value=''; document.getElementById('tNew').value=''; }
};

document.getElementById('saveQa').onclick = async ()=>{
  const q = (document.getElementById('qaQ').value||'').trim();
  const a = document.getElementById('qaA').value;
  const pwd = document.getElementById('qaPwd').value;
  if(!q || !a.trim()){ await uiAlert('密保问题和答案都要填'); return; }
  const j = await api('/api/v2/teacher/security',{method:'POST',
    body:JSON.stringify({question:q,answer:a,password:pwd})});
  await uiAlert(j.ok ? '密保问题已保存，忘记密码时可用它自助改密' : (j.msg||'保存失败'));
  if(j.ok){ document.getElementById('qaA').value=''; document.getElementById('qaPwd').value=''; await load(); }
};

function paintLeaves(){
  const box = document.getElementById('leaves');
  if(!S.leaves || !S.leaves.length){ box.innerHTML = '<div class="mini">暂无申请</div>'; return; }
  const head = '<table><thead><tr><th>学生</th><th>日期</th><th>事由</th>' +
    '<th>状态</th><th></th></tr></thead><tbody>';
  box.innerHTML = head + S.leaves.map(l=>
    '<tr><td>'+esc(l.name||'')+'</td><td>'+esc(l.date||'')+'</td>' +
    '<td>'+esc((l.reason||'').slice(0,40))+'</td><td>' +
    (l.status==='pending'?'<span class="badge warn">待批</span>':'<span class="badge">已处理</span>') +
    '</td><td>' + (l.status==='pending'
      ? '<button class="ghost" style="padding:5px 10px;font-size:12px" onclick="decide('+jsAttr(l.id)+',\'approve\')">同意</button> ' +
        '<button class="ghost" style="padding:5px 10px;font-size:12px" onclick="decide('+jsAttr(l.id)+',\'reject\')">驳回</button>'
      : '') + '</td></tr>').join('') + '</tbody></table>';
}

/* JS 字面量塞进 HTML 双引号属性时，引号必须转义成 &quot;，
   否则 onclick="decide("lv1001…",'approve')" 会被截断成 decide( */
function jsAttr(v){
  return JSON.stringify(String(v==null?'':v))
    .replace(/&/g,'&amp;').replace(/"/g,'&quot;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function decide(id, action){
  await api('/api/v2/teacher/leave',{method:'POST',body:JSON.stringify({id:id,action:action})});
  await load();
}

function paintAudit(){
  const box = document.getElementById('audit');
  if(!S.audit || !S.audit.length){ box.innerHTML = '<div class="mini">暂无记录</div>'; return; }
  const head = '<table><thead><tr><th style="width:150px">时间</th>' +
    '<th style="width:110px">角色</th><th>操作</th></tr></thead><tbody>';
  box.innerHTML = head + S.audit.slice(0,12).map(a=>
    '<tr><td class="mini">'+esc(a.ts)+'</td><td>'+esc(a.actor||'')+'</td>' +
    '<td>'+esc(a.action||'')+' <span class="mini">'+esc((a.detail||'').slice(0,60))+'</span></td></tr>'
  ).join('') + '</tbody></table>';
}

async function load(){
  S = await api('/api/v2/teacher/console');
  paint();
}

document.getElementById('logout').onclick = async ()=>{
  await api('/api/v2/auth/logout',{method:'POST',body:'{}'});
  location.href='/teacher/login';
};
document.getElementById('copyCode').onclick = ()=>{
  const t = (S && S.class_code) || '';
  if(!t) return;
  const btn = document.getElementById('copyCode');
  const done = ()=>{ btn.textContent='已复制'; setTimeout(()=>{btn.textContent='复制';},1600); };
  const fallback = ()=>{
    const ta = document.createElement('textarea');
    ta.value = t; ta.setAttribute('readonly','');
    ta.style.position='fixed'; ta.style.top='-1000px'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select();
    let ok=false; try{ ok=document.execCommand('copy'); }catch(e){}
    document.body.removeChild(ta);
    if(ok){ done(); } else { btn.textContent='请手动选中'; }
  };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(t).then(done).catch(fallback);
  } else { fallback(); }
};
load();
setInterval(load, 6000);
</script></body></html>
"""


def console_page():
    return CONSOLE_HTML.replace("__CSS__", BASE_CSS)
