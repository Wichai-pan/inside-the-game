// Records each scene of the demo video from the public site with headless Chrome (screenshots at ~30 fps).
// Chrome's own video recorder crops the bottom of the viewport on this Mac, so frames are captured as JPEGs
// and written as an ffconcat list per scene; compose.py muxes them with the voice-over.
import { chromium } from 'playwright';
import fs from 'node:fs';

const BASE = 'https://wichai-pan.github.io/inside-the-game/';
const W = 1280, H = 800;
const dur = JSON.parse(fs.readFileSync('vo/durations.json', 'utf8'));
const out = 'clips';
fs.mkdirSync(out, { recursive: true });

const FONT = "Inter,-apple-system,Helvetica,Arial,sans-serif";
const page0 = (body) => `<html><body style="margin:0;height:100vh;display:flex;flex-direction:column;justify-content:center;align-items:center;background:radial-gradient(circle at 30% 20%,#123222,#07090c 70%);color:#eef3ef;font-family:${FONT};text-align:center">${body}</body></html>`;
const CARD = (title, sub, small = '') => page0(`
<div style="letter-spacing:.22em;color:#4ade80;font-size:18px;font-weight:700">INSIDE THE GAME</div>
<div style="font:800 76px/1 'Barlow Condensed',${FONT};text-transform:uppercase;margin:20px 40px">${title}</div>
<div style="font-size:26px;color:#b9c8bd;max-width:1000px;line-height:1.4">${sub}</div>
<div style="font-size:18px;color:#8aa093;margin-top:36px;max-width:1050px;line-height:1.7">${small}</div>`);

// Real output of `python -m insidegame.ask` on 25 Oct 2026 (qwen3.5:9b via Ollama); checked against the bundle.
const TERMINAL = page0(`
<div style="width:1100px;text-align:left;background:#0d1117;border:1px solid #2b3440;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.6);overflow:hidden">
 <div style="background:#161b22;padding:10px 14px;color:#8b949e;font:13px ${FONT}">● ● ●&nbsp;&nbsp; MCP server · 7 tools · Agent Framework agent</div>
 <pre id="t" style="margin:0;padding:22px 26px;font:17px/1.6 'JetBrains Mono',Menlo,monospace;color:#e6edf3;white-space:pre-wrap;min-height:330px"></pre>
</div>
<div style="margin-top:22px;color:#8aa093;font-size:16px">tools: list_matches · get_match_state · get_key_moments · get_player_stats · get_team_stats · get_commentary · get_studio_analysis</div>
<script>
const cmd = '$ python -m insidegame.ask "Who scored the goals, and how good were the chances?"';
const outp = '\\n\\n<span style="color:#4ade80">[ollama:qwen3.5:9b] tools called: get_key_moments</span>\\n\\nLuca Brennan scored for Ridgeway United with a shot from 12 m that had an xG of 0.27, making it a moderately good chance. Yusuf Arslan converted a penalty with an xG of 0.77, which is a very high-quality chance as penalties are highly likely to be scored. Ade Bankole scored for Harbor City from 21 m with an xG of only 0.04, indicating it was a poor quality chance that defied the odds.';
const t = document.getElementById('t'); let i = 0;
const tick = () => { if (i <= cmd.length) { t.textContent = cmd.slice(0, i++); setTimeout(tick, 28); } else setTimeout(() => { t.innerHTML = t.textContent + outp; }, 700); };
setTimeout(tick, 300);
</script>`);

async function overlay(page, html, top = false) {
  await page.evaluate(([html, top]) => {
    let o = document.getElementById('__vo');
    if (!o) {
      o = document.createElement('div'); o.id = '__vo';
      o.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);z-index:99999;background:rgba(7,10,9,.92);color:#fff;font:700 22px Inter,-apple-system,Helvetica,sans-serif;padding:12px 22px;border-radius:12px;border:1px solid #4ade80;max-width:1120px;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.55)';
      document.body.appendChild(o);
    }
    o.innerHTML = html; o.style.bottom = top ? 'auto' : '26px'; o.style.top = top ? '72px' : 'auto';
  }, [html, top]);
}
async function seek(page, t) {
  await page.evaluate((t) => { const s = document.querySelector('#seek'); s.value = t; s.dispatchEvent(new Event('input', { bubbles: true })); s.dispatchEvent(new Event('change', { bubbles: true })); }, t);
}
async function smoothScroll(page, y, ms) {
  await page.evaluate(([y, ms]) => new Promise(r => { const y0 = scrollY, t0 = performance.now(); const f = (n) => { const k = Math.min(1, (n - t0) / ms); scrollTo(0, y0 + (y - y0) * (k < .5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2)); k < 1 ? requestAnimationFrame(f) : r(); }; requestAnimationFrame(f); }), [y, ms]);
}
const top = (page, sel, off = 70) => page.evaluate(([s, off]) => document.querySelector(s).getBoundingClientRect().top + scrollY - off, [sel, off]);
async function live(page, { lang = 'en', profile = 'casual', at = 0, speed = '2' } = {}) {
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForSelector('#seek');
  if (profile !== 'casual') await page.click(`#profile button[data-v=${profile}]`);
  if (lang !== 'en') await page.click(`#lang button[data-v=${lang}]`);
  await page.click(`#speed button[data-v="${speed}"]`);
  await seek(page, at);
  await page.evaluate(() => scrollTo(0, document.querySelector('#pitch').getBoundingClientRect().top + scrollY - 70));
}
const wait = (page, s) => page.waitForTimeout(Math.max(0, s) * 1000);
const play = (page) => page.click('#play');

const scenes = [
  // 1 problem
  async (page, d) => { await page.setContent(CARD('One match. Every fan.', 'One broadcast speaks to everyone the same way.<br>Inside the Game writes it again for each viewer.')); return () => wait(page, d); },
  // 2 match centre
  async (page, d) => { await page.goto(BASE, { waitUntil: 'networkidle' }); return async () => { await wait(page, 2); await overlay(page, 'Casual or analyst · English or 中文 · your club · your player'); await wait(page, 2.5); await smoothScroll(page, await top(page, '#pitch', 330), 1500); await wait(page, d - 6); }; },
  // 3 two voices, casual
  async (page, d) => { await live(page, { at: 240 }); await overlay(page, 'Two voices: instant play-by-play + agent analysis'); return async () => { await play(page); await wait(page, d); }; },
  // 4 analyst
  async (page, d) => { await live(page, { at: 57 }); await overlay(page, 'Casual: the story'); return async () => { await play(page); await wait(page, 2); await page.click('#profile button[data-v=analyst]'); await page.evaluate(() => scrollTo(0, document.querySelector('#pitch').getBoundingClientRect().top + scrollY - 70)); await overlay(page, 'Analyst: metrics, reasons and evidence with event IDs'); await wait(page, d - 2); }; },
  // 5 Chinese
  async (page, d) => { await live(page, { at: 377, lang: 'zh' }); await overlay(page, 'Simplified Chinese, written natively by the same workflow'); return async () => { await play(page); await wait(page, d); }; },
  // 6 player focus by clicking a dot
  async (page, d) => { await live(page, { at: 232 }); await overlay(page, 'Click a player to follow them'); return async () => {
    await play(page); await wait(page, 1);
    const dot = page.locator('#dots g.pl', { has: page.locator('title', { hasText: 'Yusuf Arslan' }) });
    try { await dot.click({ timeout: 1500, force: true }); } catch { await page.selectOption('#player', 'b9'); }
    await overlay(page, 'Player focus: #10 Yusuf Arslan (fictional)'); await wait(page, d - 2.5); }; },
  // 7 workflow (about page)
  async (page, d) => { await page.goto(BASE + 'about.html', { waitUntil: 'networkidle' }); return async () => {
    await wait(page, 0.6); const y = await page.evaluate(() => { const h = [...document.querySelectorAll('h2')].find(e => /live workflow/i.test(e.textContent)); return h.getBoundingClientRect().top + scrollY - 110; });
    await smoothScroll(page, y, 1800); await overlay(page, 'Microsoft Agent Framework: typed executors, checks and a retry loop'); await wait(page, d - 2.4); }; },
  // 8 a rejected line and its rewrite (real reasons from the bundle)
  async (page, d) => { await live(page, { at: 226 }); await overlay(page, `<div style="font-size:15px;color:#9fb3a6;letter-spacing:.08em">3:39 · CASUAL · ENGLISH</div>
<div style="margin-top:6px;font-size:19px;text-align:left"><span style="color:#f87171">✕ Draft 1 rejected:</span> <span style="font-weight:500">“80% is Harbor City's possession in the last minute only: write 'in the last minute' next to it, or use the match share (56%)”</span></div>
<div style="margin-top:6px;font-size:19px;text-align:left"><span style="color:#4ade80">✓ Draft 2 passed:</span> <span style="font-weight:500">“Ashby wins the ball 25m from goal as Harbor City held 80% possession in the last minute.”</span></div>`);
    await page.evaluate(() => { const o = document.getElementById('__vo'); o.style.left = '24px'; o.style.transform = 'none'; o.style.maxWidth = '730px'; o.style.bottom = '18px'; });
    return async () => { await play(page); await wait(page, d); }; },
  // 9 measured latency
  async (page, d) => { await live(page, { at: 186, profile: 'analyst', speed: '4' }); await overlay(page, 'Measured, not simulated: median 12.3 s per line (local 9B model)'); return async () => {
    await play(page); await wait(page, 2.5); await smoothScroll(page, await top(page, '#transcript-card', 90), 1600); await wait(page, d - 4.1); }; },
  // 10 MCP + ask.py
  async (page, d) => { await page.setContent(TERMINAL); return () => wait(page, d); },
  // 11 studio
  async (page, d) => { await page.goto(BASE + 'studio.html?match=remix-42', { waitUntil: 'networkidle' }); await overlay(page, 'Post-match studio: six storylines from full-match statistics'); return async () => {
    await wait(page, 1.5); const y = await page.evaluate(() => document.body.scrollHeight - innerHeight); await smoothScroll(page, Math.min(y, 1900), (d - 2.5) * 1000); await wait(page, 1); }; },
  // 12 end card
  async (page, d) => { await page.setContent(CARD('Inside the Game', 'One match. Every fan.', 'Microsoft Agent Framework · MCP server · ready for Microsoft Foundry<br>Synthetic matches stitched from DFL open data (Bassek et al. 2025, CC BY 4.0) · fictional clubs and players<br>wichai-pan.github.io/inside-the-game · github.com/Wichai-pan/inside-the-game')); return () => wait(page, d); },
];

const only = process.argv[2] ? process.argv[2].split(',').map(Number) : null;
const browser = await chromium.launch({ channel: 'chrome' });
for (let i = 0; i < scenes.length; i++) {
  if (only && !only.includes(i + 1)) continue;
  const ctx = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const run = await scenes[i](page, dur[i]);
  const dir = `${out}/scene${i + 1}`; fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir);
  let going = true; const shots = []; const c0 = Date.now();
  const loop = (async () => { while (going) { const t = (Date.now() - c0) / 1000; const p = `${dir}/${String(shots.length).padStart(5, '0')}.jpg`; await page.screenshot({ path: p, type: 'jpeg', quality: 88 }); shots.push([p, t]); } })();
  await run(); going = false; await loop;
  const end = (Date.now() - c0) / 1000;
  let cc = 'ffconcat version 1.0\n';
  shots.forEach(([p, t], k) => { const next = k + 1 < shots.length ? shots[k + 1][1] : end; cc += `file '${p.split('/').pop()}'\nduration ${(next - t).toFixed(3)}\n`; });
  cc += `file '${shots.at(-1)[0].split('/').pop()}'\n`;
  fs.writeFileSync(`${dir}/frames.txt`, cc);
  console.log('scene', i + 1, 'frames', shots.length, 'over', end.toFixed(1), 's');
  await ctx.close();
}
await browser.close();
