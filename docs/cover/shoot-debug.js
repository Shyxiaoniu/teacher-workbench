const fs = require('fs');
const { chromium } = require('C:/Users/WIT_User/.workbuddy/skills/playwright-browser-use/node_modules/playwright-core');
const LOG = 'E:/Documents/班级工作台2.0/docs/cover/shoot.log';
const say = (s) => fs.appendFileSync(LOG, s + '\r\n', 'utf8');

(async () => {
  const b = await chromium.launch({
    executablePath: 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  });
  const p = await b.newPage({ viewport: { width: 1080, height: 460 } });
  p.on('console', (m) => say('console[' + m.type() + '] ' + m.text()));
  p.on('pageerror', (e) => say('pageerror ' + e.message));
  await p.goto('file:///E:/Documents/%E7%8F%AD%E7%BA%A7%E5%B7%A5%E4%BD%9C%E5%8F%B02.0/docs/cover/cover.html?v=f', { waitUntil: 'networkidle' });
  await p.waitForTimeout(800);
  const st = await p.evaluate(() => {
    const fit = document.getElementById('fit');
    const img = document.getElementById('fitImg');
    const r = fit.getBoundingClientRect();
    return {
      bodyClass: document.body.className,
      fitDisplay: getComputedStyle(fit).display,
      transform: fit.style.transform.slice(0, 80),
      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
      imgLoaded: img.complete && img.naturalWidth > 0,
      natural: [img.naturalWidth, img.naturalHeight],
    };
  });
  say('DEBUG ' + JSON.stringify(st));
  await b.close();
  say('debug done');
})().catch((e) => { say('ERR ' + (e && e.stack || e)); process.exit(1); });
