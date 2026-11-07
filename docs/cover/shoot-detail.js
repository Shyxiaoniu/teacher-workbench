const fs = require('fs');
const { chromium } = require('C:/Users/WIT_User/.workbuddy/skills/playwright-browser-use/node_modules/playwright-core');
const LOG = 'E:/Documents/班级工作台2.0/docs/cover/shoot.log';
const DIR = 'E:/Documents/班级工作台2.0/docs/cover/';
const say = (s) => fs.appendFileSync(LOG, s + '\r\n', 'utf8');

(async () => {
  const b = await chromium.launch({
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  });
  const p = await b.newPage({ viewport: { width: 1080, height: 460 }, deviceScaleFactor: 3 });
  await p.goto('file:///E:/Documents/%E7%8F%AD%E7%BA%A7%E5%B7%A5%E4%BD%9C%E5%8F%B02.0/docs/cover/cover.html?v=f', { waitUntil: 'networkidle' });
  await p.waitForTimeout(800);
  await p.screenshot({ path: DIR + 'detail.png', clip: { x: 560, y: 60, width: 360, height: 400 } });
  await b.close();
  say('detail done');
})().catch((e) => { say('ERR ' + (e && e.stack || e)); process.exit(1); });
