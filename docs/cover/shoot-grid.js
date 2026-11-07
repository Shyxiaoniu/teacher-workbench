const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/WIT_User/.workbuddy/skills/playwright-browser-use/node_modules/playwright-core');

const DIR = 'E:/Documents/班级工作台2.0/docs/cover/';
const LOG = DIR + 'shoot.log';
const say = (s) => fs.appendFileSync(LOG, s + '\r\n', 'utf8');

(async () => {
  fs.writeFileSync(LOG, 'grid start\r\n', 'utf8');
  const b = await chromium.launch({
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  });
  const p = await b.newPage({ viewport: { width: 1536, height: 1024 }, deviceScaleFactor: 1 });
  await p.goto('file:///E:/Documents/%E7%8F%AD%E7%BA%A7%E5%B7%A5%E4%BD%9C%E5%8F%B02.0/docs/cover/grid.html', { waitUntil: 'networkidle' });
  await p.waitForTimeout(500);
  await p.screenshot({ path: DIR + 'grid.png' });
  await b.close();
  say('grid done');
})().catch((e) => { say('ERR ' + e); process.exit(1); });
