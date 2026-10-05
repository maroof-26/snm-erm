import re, json, subprocess, time, os, glob, shutil
from playwright.sync_api import sync_playwright

D = os.path.dirname(os.path.abspath(__file__))
BASE = 'http://localhost:8010'
LINES = ['اپنے کاروبار کا سارا حساب، اب ایک ہی جگہ۔', 'چند سیکنڈ میں سیل بنائیں۔',
         'بل واٹس ایپ پر بھیجیں، اور پیسے وصول کریں۔', 'لینے اور دینے کا پورا کھاتہ، موبائل پر بھی۔']
GAP = 0.2
def dur(f):
    return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f]).decode())
durs = [dur(f'{D}/s{i+1}.mp3') for i in range(4)]
starts = [sum(durs[:i]) + GAP * i for i in range(4)]
TOTAL = starts[-1] + durs[-1] + 0.5

OVERLAY = """
(() => {
  if (window.top !== window) return;
  const css = `
  @import url('https://fonts.googleapis.com/css2?family=Noto+Nastaliq+Urdu:wght@500;700&display=swap');
  #cap{pointer-events:none;position:fixed;left:0;right:0;bottom:0;z-index:99999;background:linear-gradient(transparent,rgba(12,20,40,.92) 35%);padding:46px 40px 22px;text-align:center;direction:rtl;
       font-family:'Noto Nastaliq Urdu',serif;font-weight:700;font-size:38px;line-height:1.9;color:#fff;transition:opacity .25s}
  #cur{position:fixed;z-index:100000;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:rgba(255,196,0,.55);border:3px solid #ffc400;pointer-events:none;transition:transform .12s}
  #cur.click{transform:scale(1.7)}
  .pulse{animation:pulse 0.9s infinite;position:relative;z-index:5}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(37,211,102,.9)}100%{box-shadow:0 0 0 22px rgba(37,211,102,0)}}
  #phone{position:fixed;right:34px;bottom:120px;z-index:99998;width:236px;height:490px;border-radius:32px;border:8px solid #0c1428;background:#0c1428;overflow:hidden;
         box-shadow:0 20px 60px rgba(0,0,0,.45);transform:translateY(120%);transition:transform .6s cubic-bezier(.2,.8,.2,1)}
  #phone.in{transform:none} #phone img{width:100%;display:block}`;
  const build = () => {
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    const cap = document.createElement('div'); cap.id = 'cap'; document.body.appendChild(cap);
    const cur = document.createElement('div'); cur.id = 'cur'; document.body.appendChild(cur);
    cap.textContent = sessionStorage.cap || ''; cap.style.opacity = sessionStorage.cap ? 1 : 0;
    const p = JSON.parse(sessionStorage.cur || '[640,360]'); cur.style.left = p[0] + 'px'; cur.style.top = p[1] + 'px';
    document.addEventListener('mousemove', e => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; sessionStorage.cur = JSON.stringify([e.clientX, e.clientY]); }, true);
    document.addEventListener('mousedown', () => cur.classList.add('click'), true);
    document.addEventListener('mouseup', () => cur.classList.remove('click'), true);
  };
  document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', build) : build();
})();
"""

def main():
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path='/usr/bin/google-chrome', args=['--no-sandbox'])
        a = b.new_context(viewport={'width': 1280, 'height': 720})
        pg = a.new_page(); pg.goto(BASE + '/accounts/login/')
        pg.fill('[name=username]', 'pitch'); pg.fill('[name=password]', 'Pitch@12345'); pg.click('button')
        pg.wait_for_url(BASE + '/'); a.storage_state(path=f'{D}/state.json'); a.close()

        m = b.new_context(viewport={'width': 390, 'height': 800}, device_scale_factor=2, is_mobile=True, storage_state=f'{D}/state.json')
        mp = m.new_page(); mp.goto(BASE + '/ledger/'); mp.wait_for_load_state('load'); mp.wait_for_timeout(500)
        import base64; phone_b64 = base64.b64encode(mp.screenshot()).decode(); m.close()
        vdir = f'{D}/raw'; shutil.rmtree(vdir, ignore_errors=True)
        ctx = b.new_context(viewport={'width': 1280, 'height': 720}, storage_state=f'{D}/state.json',
                            record_video_dir=vdir, record_video_size={'width': 1280, 'height': 720})
        ctx.add_init_script(OVERLAY)
        page = ctx.new_page(); t_page = time.time()
        page.goto(BASE + '/?range=all'); page.wait_for_load_state('networkidle'); page.wait_for_timeout(900)
        t0 = time.time(); offset = t0 - t_page; marks = []

        def now(): return time.time() - t0
        def wait_until(t):
            d = t - now()
            if d > 0: time.sleep(d)
        def caption(i):
            page.evaluate("t => { sessionStorage.cap = t; const c = document.getElementById('cap'); if (c) { c.textContent = t; c.style.opacity = 1; } const m = document.createElement('div'); m.style.cssText = 'position:fixed;left:0;top:0;width:6px;height:6px;background:#f0f;z-index:200000'; document.body.appendChild(m); setTimeout(() => m.remove(), 300); }", LINES[i])
            marks.append((f'caption{i}', round(now(), 2)))
        def glide(loc, ms=500, dx=0, dy=0):
            loc.scroll_into_view_if_needed(timeout=3000)
            bb = loc.bounding_box(); x, y = bb['x'] + bb['width'] / 2 + dx, bb['y'] + bb['height'] / 2 + dy
            cx, cy = page.evaluate("JSON.parse(sessionStorage.cur || '[640,360]')")
            st = time.time(); d = ms / 1000
            while True:
                el = time.time() - st
                f = min(1, el / d); f = f * f * (3 - 2 * f)
                page.mouse.move(cx + (x - cx) * f, cy + (y - cy) * f)
                if el >= d: break
        def click(loc, ms=450):
            glide(loc, ms); page.mouse.down(); time.sleep(0.08); page.mouse.up()

        # scene 1: dashboard
        caption(0)
        glide(page.locator('.stat').first, 650); glide(page.locator('canvas'), 800, dy=-40)
        wait_until(starts[0] + durs[0] - 0.75)
        click(page.locator('a:has-text("New Sale")'), 550)
        # scene 2: new sale form
        page.wait_for_url('**/sales/new/'); page.wait_for_load_state('load'); wait_until(starts[1]); caption(1)
        marks.append(('scene2 start', round(now(), 2)))
        glide(page.locator('#id_party'), 250); page.select_option('#id_party', label='Bilal Builders')
        glide(page.locator('#id_items-0-product'), 250); page.select_option('#id_items-0-product', label='Cement (50 kg bag)')
        glide(page.locator('#id_items-0-quantity'), 200); page.click('#id_items-0-quantity'); page.keyboard.type('150', delay=70)
        page.fill('#id_paid_now', '100000'); glide(page.locator('#id_paid_now'), 250)
        marks.append(('scene2 actions done', round(now(), 2)))
        # scene 3: save -> invoice, whatsapp
        click(page.locator('button:has-text("Save sale")'), 350)
        page.wait_for_url(re.compile(r'/sales/\d+/$')); page.wait_for_load_state('load'); wait_until(starts[2]); caption(2)
        wa = page.locator('a:has-text("WhatsApp")'); glide(wa, 600)
        page.evaluate("document.querySelector('a[href*=\"wa.me\"]').classList.add('pulse')")
        wait_until(starts[2] + durs[2] * 0.6)
        glide(page.locator('a:has-text("Receive money")'), 550)
        page.evaluate("document.querySelector('a[href*=\"wa.me\"]').classList.remove('pulse')")
        # scene 4: ledger + phone
        wait_until(starts[3] - 1.3)
        click(page.locator('.sidebar a[href="/ledger/"]'), 400)
        page.wait_for_url('**/ledger/'); page.wait_for_load_state('load'); wait_until(starts[3]); caption(3)
        page.evaluate("""b64 => { const d = document.createElement('div'); d.id = 'phone'; d.innerHTML = '<img src="data:image/png;base64,' + b64 + '">'; document.body.appendChild(d); setTimeout(() => d.classList.add('in'), 60); }""", phone_b64)
        glide(page.locator('tbody tr').nth(1), 700, dx=-120)
        wait_until(TOTAL)
        print(marks)
        vpath = page.video.path(); ctx.close(); b.close()
    json.dump({'video': vpath, 'offset': offset, 'total': TOTAL, 'starts': starts}, open(f'{D}/meta.json', 'w'))
    print(json.dumps({'offset': round(offset, 2), 'total': round(TOTAL, 2), 'starts': [round(s, 2) for s in starts]}))

main()
