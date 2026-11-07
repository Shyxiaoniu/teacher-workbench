const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/WIT_User/.workbuddy/skills/playwright-browser-use/node_modules/playwright-core');

const HTML = 'file:///E:/Documents/%E7%8F%AD%E7%BA%A7%E5%B7%A5%E4%BD%9C%E5%8F%B02.0/docs/cover/cover.html';
const DIR = 'E:/Documents/班级工作台2.0/docs/cover/';
const SHOTS = 'E:/Documents/班级工作台2.0/docs/shots/s38/';
const LOG = DIR + 'shoot.log';

const say = (s) => fs.appendFileSync(LOG, s + '\r\n', 'utf8');

// 渲染前先校验：所有要用的截图必须存在，并打印最后修改时间，避免用旧图出片
const NEED = [
  's38-11-教师端-大屏.png',
  's38-04-学生端-座位表.png',
  's38-02-学生端-今日.png',
];

(async () => {
  fs.writeFileSync(LOG, 'start ' + new Date().toISOString() + '\r\n', 'utf8');

  let newestSrc = 0;
  for (const n of NEED) {
    const p = path.join(SHOTS, n);
    if (!fs.existsSync(p)) { say('MISSING ' + n); throw new Error('缺截图 ' + n); }
    const st = fs.statSync(p);
    newestSrc = Math.max(newestSrc, st.mtimeMs);
    say('src ' + n + '  ' + Math.round(st.size / 1024) + 'KB  mtime=' + st.mtime.toISOString());
  }

  // 过时判定：项目版本文件比截图还新，说明代码已改、图还没重拍 —— 直接报警
  const verFile = 'E:/Documents/班级工作台2.0/version.json';
  if (fs.existsSync(verFile)) {
    const vm = fs.statSync(verFile).mtimeMs;
    if (vm > newestSrc) {
      say('WARN 截图比 version.json 旧 ' +
          ((vm - newestSrc) / 3600000).toFixed(1) + ' 小时，画面可能已过时');
    } else {
      say('OK  截图不早于 version.json');
    }
  }

  const b = await chromium.launch({
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    args: ['--force-color-profile=srgb'],
  });

  const jobs = [];
  for (const v of ['f', 'c', 'd', 'e']) {
    jobs.push([v, 1, 'cover-' + v.toUpperCase() + '-1080x460.png']);
    jobs.push([v, 2, 'cover-' + v.toUpperCase() + '-2160x920.png']);
  }

  for (const [v, scale, name] of jobs) {
    const p = await b.newPage({ viewport: { width: 1080, height: 460 }, deviceScaleFactor: scale });
    await p.goto(HTML + '?v=' + v, { waitUntil: 'networkidle' });
    await p.waitForTimeout(600);
    await p.screenshot({ path: DIR + name });
    await p.close();
    say('shot ' + name);
  }

  await b.close();

  // 出片复核：成品必须比所有源截图新，否则说明这一轮拿的是旧图
  for (const [, , name] of jobs) {
    const out = path.join(DIR, name);
    if (!fs.existsSync(out)) { say('STALE ' + name); throw new Error('没生成 ' + name); }
    if (fs.statSync(out).mtimeMs < newestSrc) {
      say('STALE ' + name + '（成品比源图旧）');
      throw new Error('成品比源图旧：' + name);
    }
  }
  say('fresh ok');
  say('done');
})().catch((e) => {
  say('ERR ' + (e && e.stack ? e.stack : e));
  process.exit(1);
});
