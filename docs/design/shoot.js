const fs = require('fs');
const path = require('path');
const { chromium } = require('C:/Users/WIT_User/.workbuddy/skills/playwright-browser-use/node_modules/playwright-core');

const HTML = 'file:///E:/Documents/%E7%8F%AD%E7%BA%A7%E5%B7%A5%E4%BD%9C%E5%8F%B02.0/docs/design/workbench-dark.html';
const DIR = 'E:/Documents/班级工作台2.0/docs/design/';
const LOG = DIR + 'shoot.log';

const say = (s) => fs.appendFileSync(LOG, s + '\r\n', 'utf8');

(async () => {
  fs.writeFileSync(LOG, 'start ' + new Date().toISOString() + '\r\n', 'utf8');
  const b = await chromium.launch({
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    args: ['--force-color-profile=srgb'],
  });
  for (const [scale, name] of [[1, 'workbench-dark-1600x1000.png'], [2, 'workbench-dark-3200x2000.png']]) {
    const p = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: scale });
    await p.goto(HTML, { waitUntil: 'networkidle' });
    await p.waitForTimeout(700);
    await p.screenshot({ path: DIR + name });
    await p.close();
    say('shot ' + name);
  }
  await b.close();
  say('done');
})().catch((e) => {
  say('ERR ' + (e && e.stack ? e.stack : e));
  process.exit(1);
});
