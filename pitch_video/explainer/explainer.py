#!/usr/bin/env python3
"""Records the full step-by-step Urdu explainer video.

Each scene = several "beats". A beat is one narrated Urdu sentence plus the on-screen actions that happen while
it is spoken. Every scene is recorded as its own clip, then stretched to real time (markers flash at the start
and end of each clip), mixed with the narration and joined.

    python3 explainer.py --reset            # wipe the demo client's data, record everything, build final video
    python3 explainer.py --only products    # re-record only some scenes (needs the data from earlier scenes)
    python3 explainer.py --compose-only     # just re-join the existing clips
Needs: the dev server on :8010, the `pitch` login, edge-tts, ffmpeg, pdftoppm, playwright + Chrome.
"""
import argparse, base64, hashlib, json, os, re, shutil, subprocess, sys, time
from playwright.sync_api import sync_playwright

BASE = 'http://localhost:8010'
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.environ.get('EXPLAINER_WORK', os.path.join(HERE, 'build'))
PROJECT = os.path.abspath(os.path.join(HERE, '..', '..'))
VOICE, RATE = 'ur-PK-AsadNeural', '-4%'
USER, PASSWORD = 'pitch', 'Pitch@12345'
GAP = 0.3          # silence after each sentence
CHROME = '/usr/bin/google-chrome'
for d in ('audio', 'raw', 'clips', 'img'):
    os.makedirs(f'{WORK}/{d}', exist_ok=True)

# ----------------------------------------------------------------------------------------------- narration
def tts(text):
    path = f"{WORK}/audio/{hashlib.md5((VOICE + RATE + text).encode()).hexdigest()}.mp3"
    if not os.path.exists(path):
        for attempt in range(4):
            r = subprocess.run(['edge-tts', '--voice', VOICE, f'--rate={RATE}', '--text', text, '--write-media', path], capture_output=True)
            if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 500:
                break
            time.sleep(1.5)
        else:
            raise RuntimeError('TTS failed for: ' + text)
    return path

def duration(path):
    return float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path]).decode())

# ----------------------------------------------------------------------------------------------- in-page overlay
OVERLAY = r"""
(() => {
  if (window.top !== window) return;
  const css = `
  @import url('https://fonts.googleapis.com/css2?family=Noto+Nastaliq+Urdu:wght@500;700&display=swap');
  #cap{pointer-events:none;position:fixed;left:0;right:0;bottom:0;z-index:99999;background:linear-gradient(transparent,rgba(12,20,40,.93) 38%);padding:50px 60px 22px;text-align:center;direction:rtl;
       font-family:'Noto Nastaliq Urdu',serif;font-weight:700;font-size:36px;line-height:2;color:#fff;transition:opacity .25s}
  #cur{position:fixed;z-index:100000;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:rgba(255,196,0,.55);border:3px solid #ffc400;pointer-events:none;transition:transform .12s}
  #cur.click{transform:scale(1.7)}
  .ring{outline:4px solid #ffc400 !important;outline-offset:3px;border-radius:10px;transition:outline .2s}
  .pulse{animation:pulse 0.9s infinite;position:relative;z-index:5}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(37,211,102,.9)}100%{box-shadow:0 0 0 22px rgba(37,211,102,0)}}
  #doc{position:fixed;right:30px;top:70px;z-index:99998;width:380px;border-radius:10px;overflow:hidden;background:#fff;box-shadow:0 20px 60px rgba(0,0,0,.5);
       transform:translateX(120%);transition:transform .6s cubic-bezier(.2,.8,.2,1);border:1px solid #cfd5df}
  #doc.in{transform:none} #doc img{width:100%;display:block}
  #back{position:fixed;inset:0;z-index:99997;background:rgba(10,16,30,.72);opacity:0;pointer-events:none;transition:opacity .4s}
  #back.in{opacity:1}
  #phone{position:fixed;left:50%;top:14px;z-index:99998;width:250px;margin:0 0 0 -125px;border-radius:34px;border:8px solid #0c1428;background:#0c1428;overflow:hidden;
         box-shadow:0 25px 70px rgba(0,0,0,.6);transform:translateY(130%);transition:transform .7s cubic-bezier(.2,.8,.2,1)}
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

CARD = """<!doctype html><html lang="ur"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>@import url('https://fonts.googleapis.com/css2?family=Noto+Nastaliq+Urdu:wght@700&display=swap');
html,body{margin:0;height:100%;background:radial-gradient(circle at 30% 20%,#243a6b,#0c1428 70%);color:#fff;font-family:system-ui,sans-serif}
.w{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding-bottom:90px}
h1{font-family:'Noto Nastaliq Urdu',serif;font-size:84px;line-height:1.7;margin:0;direction:rtl} p{font-size:26px;letter-spacing:.14em;text-transform:uppercase;color:#aebbe0;margin:18px 0 0}
.bar{width:90px;height:5px;background:#ffc400;border-radius:4px;margin-top:28px}</style></head>
<body><div class="w"><h1>%(title)s</h1><div class="bar"></div><p>%(sub)s</p></div></body></html>"""

# ----------------------------------------------------------------------------------------------- actor
class Actor:
    def __init__(self, page, ctx):
        self.page, self.ctx, self.t0 = page, ctx, time.time()

    def now(self): return time.time() - self.t0
    def wait_until(self, t):
        d = t - self.now()
        if d > 0: time.sleep(d)
    def hold(self, s): time.sleep(s)

    def caption(self, text):
        self.page.evaluate("t => { sessionStorage.cap = t; const c = document.getElementById('cap'); if (c) { c.textContent = t; c.style.opacity = 1; } }", text)

    def flash(self):
        self.page.evaluate("() => { const m = document.createElement('div'); m.style.cssText = 'position:fixed;left:0;top:0;width:6px;height:6px;background:#f0f;z-index:2000000'; document.body.appendChild(m); setTimeout(() => m.remove(), 320); }")

    def loc(self, sel): return sel if not isinstance(sel, str) else self.page.locator(sel).first

    def glide(self, sel, ms=550, dx=0, dy=0):
        loc = self.loc(sel)
        loc.scroll_into_view_if_needed(timeout=5000)
        time.sleep(0.12)
        bb = loc.bounding_box()
        if bb['y'] + bb['height'] > 545:       # would sit under the caption bar
            loc.evaluate("e => e.scrollIntoView({block: 'center', behavior: 'smooth'})"); time.sleep(0.55)
            bb = loc.bounding_box()
        x, y = bb['x'] + bb['width'] / 2 + dx, bb['y'] + bb['height'] / 2 + dy
        cx, cy = self.page.evaluate("JSON.parse(sessionStorage.cur || '[640,360]')")
        st, d = time.time(), ms / 1000
        while True:
            el = time.time() - st
            f = min(1, el / d); f = f * f * (3 - 2 * f)
            self.page.mouse.move(cx + (x - cx) * f, cy + (y - cy) * f)
            if el >= d: break

    def click(self, sel, ms=500, go=None):
        self.glide(sel, ms)
        self.page.mouse.down(); time.sleep(0.09); self.page.mouse.up()
        if go is not None:
            self.page.wait_for_url(go, timeout=15000); self.page.wait_for_load_state('load'); time.sleep(0.35)

    def type(self, sel, text, delay=75, clear=False):
        self.glide(sel, 450)
        self.page.mouse.down(); time.sleep(0.06); self.page.mouse.up()
        if clear: self.page.keyboard.press('Control+a')
        self.page.keyboard.type(text, delay=delay)

    def select(self, sel, label):
        loc = self.loc(sel); self.glide(loc, 450); self.ring(loc, 0.5)
        loc.select_option(label=label)

    def ring(self, sel, sec=0.9):
        loc = self.loc(sel); loc.evaluate("e => e.classList.add('ring')"); time.sleep(sec); loc.evaluate("e => e.classList.remove('ring')")

    def scroll(self, y, ms=600):
        self.page.evaluate("y => window.scrollTo({top: y, behavior: 'smooth'})", y); time.sleep(ms / 1000)

    def show_doc(self, b64):
        self.page.evaluate("""b64 => { let d = document.getElementById('doc'); if (!d) { d = document.createElement('div'); d.id = 'doc'; document.body.appendChild(d); }
            d.innerHTML = '<img src="data:image/png;base64,' + b64 + '">'; setTimeout(() => d.classList.add('in'), 60); }""", b64)
    def hide_doc(self): self.page.evaluate("() => { const d = document.getElementById('doc'); if (d) d.classList.remove('in'); }")

    def show_phone(self, b64):
        self.page.evaluate("""b64 => { let b = document.getElementById('back'); if (!b) { b = document.createElement('div'); b.id = 'back'; document.body.appendChild(b); }
            let d = document.getElementById('phone'); if (!d) { d = document.createElement('div'); d.id = 'phone'; document.body.appendChild(d); }
            d.innerHTML = '<img src="data:image/png;base64,' + b64 + '">'; setTimeout(() => { b.classList.add('in'); d.classList.add('in'); }, 60); }""", b64)
    def set_phone(self, b64): self.page.evaluate("b64 => { const i = document.querySelector('#phone img'); if (i) i.src = 'data:image/png;base64,' + b64; }", b64)
    def hide_phone(self): self.page.evaluate("() => { for (const id of ['phone','back']) { const e = document.getElementById(id); if (e) e.classList.remove('in'); } }")

# ----------------------------------------------------------------------------------------------- helpers for scenes
def side(A, href, go):
    A.click(f'.sidebar a[href="{href}"]', go=go)

def pdf_preview(A, url, name, dpi=80):
    """Fetch a PDF through the logged-in session and show its first page as an overlay."""
    data = A.ctx.request.get(BASE + url).body()
    pdf, png = f'{WORK}/img/{name}.pdf', f'{WORK}/img/{name}'
    open(pdf, 'wb').write(data)
    subprocess.run(['pdftoppm', '-r', str(dpi), '-png', '-singlefile', '-f', '1', '-l', '1', pdf, png], check=True)
    return base64.b64encode(open(png + '.png', 'rb').read()).decode()

def phone_shots(browser):
    """Mobile screenshots of the live app for the 'works on phones' scene."""
    out = []
    ctx = browser.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2, is_mobile=True, storage_state=f'{WORK}/state.json')
    p = ctx.new_page()
    p.goto(BASE + '/?range=all'); p.wait_for_load_state('load'); p.wait_for_timeout(1200)
    out.append(base64.b64encode(p.screenshot()).decode())
    p.goto(BASE + '/sales/'); p.wait_for_load_state('load'); p.wait_for_timeout(500)
    first = p.locator('tbody tr a').first.get_attribute('href')
    out.append(base64.b64encode(p.screenshot()).decode())
    p.goto(BASE + '/sales/new/'); p.wait_for_load_state('load'); p.wait_for_timeout(500)
    out.append(base64.b64encode(p.screenshot()).decode())
    p.goto(BASE + '/ledger/'); p.wait_for_load_state('load'); p.wait_for_timeout(500)
    href = p.locator('tbody tr a').first.get_attribute('href'); p.goto(BASE + href); p.wait_for_load_state('load'); p.wait_for_timeout(500)
    out.append(base64.b64encode(p.screenshot()).decode())
    ctx.close()
    return out

# ----------------------------------------------------------------------------------------------- the story
HOME = re.compile(r'^http://localhost:8010/(\?.*)?$')
def U(path): return re.compile(re.escape(BASE + path) + r'(\?.*)?$')
def UR(rx): return re.compile(rx)

def build_scenes():
    S = []

    # 1 ─ intro card
    S.append(dict(name='intro', card=('آسان حساب کتاب', 'Step-by-step guide'), beats=[
        ('السلام علیکم! آج ہم سیکھیں گے کہ اپنا کاروباری حساب کتاب کیسے سنبھالنا ہے۔', None),
        ('اس ویڈیو میں شروع سے آخر تک ہر قدم دکھایا گیا ہے۔', None),
    ]))

    # 2 ─ login
    def login_user(A): A.type('[name=username]', USER)
    def login_pass(A): A.type('[name=password]', PASSWORD, delay=95)
    def login_go(A): A.click('.btn-signin', go=HOME)
    S.append(dict(name='login', start='/accounts/login/', auth=False, beats=[
        ('سب سے پہلے اپنے یوزر نیم اور پاس ورڈ سے لاگ ان کریں۔', login_user),
        ('پاس ورڈ لکھیں، اور لاگ ان کا بٹن دبائیں۔', lambda A: (login_pass(A), login_go(A))),
    ]))

    # 3 ─ dashboard tour
    S.append(dict(name='dashboard', start='/', beats=[
        ('یہ آپ کا ڈیش بورڈ ہے۔ یہاں پورے کاروبار کا خلاصہ نظر آتا ہے۔', lambda A: A.glide('.stat', 900)),
        ('نئے صارف کے لیے تین آسان قدم یہاں لکھے ہوئے ہیں۔', lambda A: (A.glide('.card.border-primary', 700), A.ring('.card.border-primary', 1.2))),
        ('یہاں سیل، خریداری، وصولی اور ادائیگی کی رقم دکھائی جاتی ہے۔', lambda A: (A.glide('.stat >> nth=0', 600), A.glide('.stat >> nth=2', 800))),
        ('لینے اور دینے کی رقم بھی یہیں نظر آتی ہے۔', lambda A: (A.glide('.stat:has-text("To receive")', 700), A.glide('.stat:has-text("To pay")', 700))),
        ('اور اوپر کیلنڈر سے آپ کوئی بھی مدت چن سکتے ہیں۔', lambda A: (A.click('.range-btn', 600), A.hold(1.6), A.page.keyboard.press('Escape'))),
    ]))

    # 4 ─ products
    def add_product(A, name, unit, price, first=True):
        A.click('a[href="/products/new/"]', go=U('/products/new/'))
        A.type('#id_name', name)
        A.select('#id_unit', unit)
        A.type('#id_price', price)
        A.click('form button:has-text("Save")', go=U('/products/'))
    S.append(dict(name='products', start='/', beats=[
        ('پہلا قدم: اپنی چیزیں شامل کریں۔ لیفٹ مینو سے پروڈکٹس کھولیں۔', lambda A: side(A, '/products/', U('/products/'))),
        ('ایڈ کا بٹن دبائیں۔', lambda A: A.click('a[href="/products/new/"]', go=U('/products/new/'))),
        ('چیز کا نام لکھیں۔', lambda A: A.type('#id_name', 'Cement (50 kg bag)')),
        ('یونٹ چنیں، جیسے کلو، بیگ یا لیٹر۔', lambda A: A.select('#id_unit', 'Bag (bag)')),
        ('اور ریٹ لکھیں۔ لکھتے ہوئے رقم میں کوما خود لگ جاتے ہیں۔', lambda A: A.type('#id_price', '1320', delay=140)),
        ('سیو دبائیں۔', lambda A: A.click('form button:has-text("Save")', go=U('/products/'))),
        ('اسی طرح باقی چیزیں بھی شامل کر لیں۔', lambda A: add_product(A, 'Sariya (Steel bar)', 'Kilogram (kg)', '285')),
    ]))

    # 5 ─ customers & vendors
    def add_vendor(A):
        side(A, '/parties/?kind=vendor', UR(r'/parties/\?kind=vendor'))
        A.click('a:has-text("Add vendor")', go=UR(r'/parties/new/'))
        A.type('#id_name', 'Maple Leaf Cement Dealer'); A.type('#id_phone', '0300-5550101')
        A.click('form button:has-text("Save")', go=UR(r'/parties/(\?.*)?$'))
    S.append(dict(name='customers', start='/', beats=[
        ('دوسرا قدم: گاہک شامل کریں۔ کسٹمرز کھولیں اور ایڈ کسٹمر دبائیں۔',
         lambda A: (side(A, '/parties/?kind=customer', UR(r'/parties/\?kind=customer')), A.click('a:has-text("Add customer")', go=UR(r'/parties/new/')))),
        ('نام اور فون نمبر لکھیں۔ فون نمبر سے واٹس ایپ پر بل بھیجنا آسان ہو جاتا ہے۔',
         lambda A: (A.type('#id_name', 'Bilal Builders'), A.type('#id_phone', '0321-7654321'))),
        ('اگر پہلے سے کوئی رقم لینی ہے تو اوپننگ بیلنس میں لکھ دیں۔', lambda A: A.type('#id_opening_balance', '15000', delay=140)),
        ('سیو کریں۔', lambda A: A.click('form button:has-text("Save")', go=UR(r'/parties/(\?.*)?$'))),
        ('وینڈر یعنی سپلائر بھی بالکل اسی طرح شامل ہوتے ہیں۔', add_vendor),
    ]))

    # 6 ─ first sale
    def second_item(A):
        A.click('#add', 450)
        A.select('#id_items-1-product', 'Sariya (Steel bar)')
        A.type('#id_items-1-quantity', '300')
    S.append(dict(name='sale', start='/', beats=[
        ('تیسرا قدم: پہلی سیل بنائیں۔ سیلز میں جا کر نیو سیل دبائیں۔',
         lambda A: (side(A, '/sales/', U('/sales/')), A.click('a[href="/sales/new/"]', go=U('/sales/new/')))),
        ('گاہک چنیں۔', lambda A: A.select('#id_party', 'Bilal Builders')),
        ('آئٹم چنیں۔ ریٹ اور یونٹ خود آ جاتے ہیں۔', lambda A: (A.select('#id_items-0-product', 'Cement (50 kg bag)'), A.ring('#id_items-0-rate', 0.8))),
        ('مقدار لکھیں۔ ٹوٹل نیچے ساتھ ساتھ بنتا جاتا ہے۔', lambda A: (A.type('#id_items-0-quantity', '150', delay=120), A.ring('#total', 0.9))),
        ('اگر ایک سے زیادہ چیزیں ہوں تو ایک اور آئٹم شامل کر لیں۔', second_item),
        ('اگر کچھ رقم ابھی مل گئی ہے تو وہ یہاں لکھ دیں، یا فل ایمونٹ دبائیں۔', lambda A: A.type('#id_paid_now', '100000', delay=110)),
        ('اب سیو سیل دبائیں۔', lambda A: A.click('button:has-text("Save sale")', go=UR(r'/sales/\d+/$'))),
    ]))

    # 7 ─ the invoice: pdf + whatsapp
    def open_first_sale(A):
        side(A, '/sales/', U('/sales/')); A.click('tbody tr a', go=UR(r'/sales/\d+/$'))
    def show_pdf(A):
        href = A.page.locator('a:has-text("PDF")').first.get_attribute('href')
        A.glide('a:has-text("PDF")', 600)
        A.show_doc(pdf_preview(A, href, 'invoice'))
        A.hold(2.4)
    def whatsapp(A):
        A.hide_doc(); A.glide('a[href*="wa.me"]', 700)
        A.page.evaluate("document.querySelector('a[href*=\"wa.me\"]').classList.add('pulse')"); A.hold(1.8)
    S.append(dict(name='invoice', start='/', beats=[
        ('سیو کرتے ہی بل تیار ہے۔ اس پر بقایا رقم اور حالت صاف لکھی ہوتی ہے۔',
         lambda A: (open_first_sale(A), A.glide('tfoot', 800), A.ring('.badge', 1.0))),
        ('پرنٹ یا پی ڈی ایف سے بل نکالیں۔', show_pdf),
        ('یا واٹس ایپ کا بٹن دبا کر گاہک کو بل کی تفصیل بھیج دیں۔', whatsapp),
    ]))

    # 8 ─ receive the remaining money
    def pay_from_invoice(A):
        open_first_sale(A)
        A.click('a:has-text("Receive money")', go=UR(r'/payments/new/'))
    def show_paid(A):
        side(A, '/sales/', U('/sales/')); A.glide('tbody tr td:last-child .badge', 800); A.ring('tbody tr td:last-child .badge', 1.2)
    S.append(dict(name='payment', start='/', beats=[
        ('باقی رقم جب ملے تو بل کھول کر ریسیو منی دبائیں۔', pay_from_invoice),
        ('رقم پہلے سے لکھی ہوتی ہے۔ ادائیگی کا طریقہ چنیں، اور سیو کریں۔',
         lambda A: (A.glide('#id_amount', 600), A.ring('#id_amount', 0.8), A.select('#id_method', 'Bank transfer'), A.click('form button:has-text("Save")', go=U('/payments/')))),
        ('بل خود بخود ادا شدہ ہو جاتا ہے۔', show_paid),
    ]))

    # 9 ─ ledger
    def open_party_ledger(A):
        side(A, '/ledger/', U('/ledger/')); A.glide('tbody tr >> nth=0', 700)
        A.click('tbody tr a', go=UR(r'/ledger/\d+/'))
    S.append(dict(name='ledger', start='/', beats=[
        ('اب دیکھیں لیجر یعنی کھاتہ۔ یہاں ہر گاہک اور وینڈر کا حساب ہے۔',
         lambda A: (side(A, '/ledger/', U('/ledger/')), A.glide('tbody tr >> nth=0', 800))),
        ('جن سے رقم لینی ہے ان کے آگے لینے ہیں لکھا ہے، اور جنہیں دینی ہے ان کے آگے دینے ہیں۔', lambda A: (A.glide('.ledger-totals, tbody tr >> nth=0', 600), A.hold(1.2))),
        ('کسی کا نام دبائیں تو اس کا پورا کھاتہ کھل جاتا ہے۔', lambda A: A.click('tbody tr a', go=UR(r'/ledger/\d+/'))),
        ('ہر لائن میں ڈیبٹ، کریڈٹ اور بیلنس ہے۔ ڈیٹیلز دبائیں تو پوری تفصیل کھل جاتی ہے۔',
         lambda A: (A.click('.ledger-view >> nth=1', 700), A.hold(2.4), A.click('#ledgerModal .btn-close', 500))),
        ('اور نیچے کل رقم ہر وقت نظر آتی ہے، چاہے لائنیں کتنی ہی ہوں۔', lambda A: (A.glide('.ledger-totals', 700), A.ring('.ledger-totals', 1.2))),
        ('ریمائنڈ کا بٹن دبا کر بقایا رقم کی یاد دہانی واٹس ایپ پر بھیجیں۔',
         lambda A: (A.glide('a:has-text("Remind")', 700), A.page.evaluate("document.querySelector('a[href*=\"wa.me\"]') && document.querySelector('a[href*=\"wa.me\"]').classList.add('pulse')"), A.hold(1.5))),
    ]))

    # 10 ─ manual adjustment
    S.append(dict(name='adjust', start='/', beats=[
        ('اگر کوئی رقم ہاتھ سے لکھنی ہو تو ایڈجسٹمنٹ دبائیں۔',
         lambda A: (open_party_ledger(A), A.click('a:has-text("Adjustment")', go=UR(r'/ledger/entry/new/')))),
        ('میں نے دیا یا میں نے لیا، ان میں سے ایک چنیں۔', lambda A: (A.glide('label[for="id_direction_0"]', 600), A.click('label[for="id_direction_0"]', 400), A.hold(0.6))),
        ('رقم لکھیں اور سیو کریں۔ نوٹ لکھنا ضروری نہیں۔',
         lambda A: (A.type('#id_amount', '2500', delay=120), A.click('form button:has-text("Save")', go=UR(r'/ledger/\d+/')))),
    ]))

    # 11 ─ purchases
    def new_purchase(A):
        side(A, '/purchases/', U('/purchases/')); A.click('a[href="/purchases/new/"]', go=U('/purchases/new/'))
    def purchase_fill(A):
        A.select('#id_party', 'Maple Leaf Cement Dealer'); A.select('#id_items-0-product', 'Cement (50 kg bag)')
        A.type('#id_items-0-quantity', '500', delay=120); A.type('#id_items-0-rate', '1250', delay=110, clear=True)
    def pay_vendor(A):
        A.click('a[href*="/payments/new/?invoice="]', go=UR(r'/payments/new/'))
        A.glide('#id_amount', 600); A.ring('#id_amount', 0.8)
        A.click('form button:has-text("Save")', go=U('/payments/'))
    S.append(dict(name='purchase', start='/', beats=[
        ('خریداری بھی اسی طرح درج ہوتی ہے۔ پرچیزز میں نئی خریداری بنائیں۔', new_purchase),
        ('وینڈر اور آئٹم چنیں، مقدار اور ریٹ لکھیں، اور سیو کریں۔',
         lambda A: (purchase_fill(A), A.click('form button:has-text("Save")', go=UR(r'/purchases/\d+/$')))),
        ('وینڈر کو رقم دینی ہو تو پے کا بٹن دبائیں۔', pay_vendor),
    ]))

    # 12 ─ date range + filters
    S.append(dict(name='filters', start='/', beats=[
        ('ہر فہرست میں کیلنڈر کے بٹن سے مدت بدل سکتے ہیں۔',
         lambda A: (side(A, '/sales/', U('/sales/')), A.click('.range-btn', 600), A.hold(0.8), A.click('.range-menu a:has-text("This year")', 500, go=UR(r'range=year')))),
        ('اور سرچ یا فلٹر سے مطلوبہ بل جلدی ڈھونڈیں۔',
         lambda A: (A.type('input[name=q]', 'Bilal', delay=130), A.page.keyboard.press('Enter'), A.page.wait_for_load_state('load'), A.hold(1.2),
                    A.click('label[for=st-unpaid]', 600), A.hold(1.6))),
    ]))

    # 13 ─ reports
    def report_page(A):
        side(A, '/reports/', U('/reports/')); A.glide('a[href="/reports/sales/"]', 600)
        A.click('a[href="/reports/sales/"]', go=U('/reports/sales/'))
    def report_pdf(A):
        A.glide('a:has-text("PDF")', 600)
        A.show_doc(pdf_preview(A, '/reports/sales/?range=all&format=pdf', 'report'))
        A.hold(2.4); A.hide_doc(); A.glide('a:has-text("CSV")', 700)
    S.append(dict(name='reports', start='/', beats=[
        ('رپورٹس میں سیل، خریداری، وصولی اور ادائیگی کی رپورٹ ملتی ہے۔', report_page),
        ('تاریخ چنیں، اور پی ڈی ایف یا سی ایس وی میں ڈاؤن لوڈ کریں۔', report_pdf),
    ]))

    # 14 ─ mobile
    def phone_in(A):
        A.show_phone(A.shots[0]); A.hold(1.6)
    def phone_cycle(A):
        for img in A.shots[1:]:
            A.set_phone(img); A.hold(1.35)
    S.append(dict(name='mobile', start='/', prep='phone', beats=[
        ('یہ پورا نظام موبائل پر بھی اسی طرح چلتا ہے۔', phone_in),
        ('نیچے والے مینو سے ہوم، سیلز، پیمنٹس اور لیجر میں آسانی سے جائیں۔', phone_cycle),
    ]))

    # 15 ─ outro card
    S.append(dict(name='outro', card=('شکریہ!', 'Products · Customers · Sales · Payments · Ledger'), beats=[
        ('تو بس یہی ہیں آسان قدم: چیزیں، گاہک، سیل، وصولی اور کھاتہ۔', None),
        ('شکریہ! آج ہی اپنا حساب کتاب آسان بنائیں۔', None),
    ]))
    return S

# ----------------------------------------------------------------------------------------------- recording
def record_scene(browser, scene, shots=None):
    name = scene['name']
    print(f'\n== recording {name}', flush=True)
    audio = [(text, tts(text)) for text, _ in scene['beats']]
    durs = [duration(p) for _, p in audio]
    vdir = f'{WORK}/raw/{name}'; shutil.rmtree(vdir, ignore_errors=True)
    kw = dict(viewport={'width': 1280, 'height': 720}, record_video_dir=vdir, record_video_size={'width': 1280, 'height': 720})
    if scene.get('auth', True): kw['storage_state'] = f'{WORK}/state.json'
    ctx = browser.new_context(**kw)
    ctx.add_init_script(OVERLAY)
    page = ctx.new_page()
    page.on('pageerror', lambda e: print('   [page error]', e, flush=True))
    A = Actor(page, ctx); A.shots = shots
    if 'card' in scene:
        title, sub = scene['card']
        page.route('**/__card__', lambda r: r.fulfill(body=CARD.replace('%(title)s', title).replace('%(sub)s', sub), content_type='text/html; charset=utf-8'))
        page.goto(BASE + '/__card__')
    else:
        page.goto(BASE + scene['start'])
    page.wait_for_load_state('load'); page.evaluate('document.fonts.ready'); time.sleep(1.0)
    A.t0 = time.time(); A.flash(); t_start = time.time()
    starts = []
    for i, ((text, fn), d) in enumerate(zip(scene['beats'], durs)):
        t_beat = A.now(); starts.append(t_beat)
        A.caption(text)
        if fn:
            try: fn(A)
            except Exception as e:
                print(f'   [beat {i} action failed] {type(e).__name__}: {str(e)[:300]}', flush=True)
                page.screenshot(path=f'{WORK}/img/fail_{name}_{i}.png')
        A.wait_until(t_beat + d + GAP)
    time.sleep(0.4)
    A.flash(); t_end = time.time()
    time.sleep(0.5)
    vpath = page.video.path()
    if scene['name'] == 'login':   # keep a fresh session for all later scenes
        pass
    ctx.close()
    meta = dict(name=name, video=vpath, wall=t_end - t_start, beats=[dict(start=s, audio=p, dur=d) for s, (_, p), d in zip(starts, audio, durs)])
    json.dump(meta, open(f'{WORK}/raw/{name}.json', 'w'))
    print(f'   done: {meta["wall"]:.1f}s of video, {len(audio)} beats', flush=True)
    return meta

def ensure_session(browser):
    ctx = browser.new_context(viewport={'width': 1280, 'height': 720})
    p = ctx.new_page(); p.goto(BASE + '/accounts/login/')
    p.fill('[name=username]', USER); p.fill('[name=password]', PASSWORD); p.click('.btn-signin'); p.wait_for_url(HOME)
    ctx.storage_state(path=f'{WORK}/state.json'); ctx.close()

# ----------------------------------------------------------------------------------------------- composing
def find_flashes(video):
    out = subprocess.run(['ffmpeg', '-i', video, '-vf', 'fps=50,crop=6:6:0:0,signalstats,metadata=print:key=lavfi.signalstats.VAVG', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    vs = [float(x) for x in re.findall(r'VAVG=([\d.]+)', out)]
    hits = [i / 50 for i, v in enumerate(vs) if v > 190]
    groups = []
    for h in hits:
        if not groups or h - groups[-1][-1] > 0.45: groups.append([h])
        else: groups[-1].append(h)
    return [g[0] for g in groups]

def compose_scene(name):
    m = json.load(open(f'{WORK}/raw/{name}.json'))
    fl = find_flashes(m['video'])
    if len(fl) < 2: raise RuntimeError(f'{name}: found {len(fl)} flash markers (need 2)')
    vs, ve = fl[0], fl[-1]
    k = m['wall'] / (ve - vs)
    wall = m['wall']
    # input 1 = silent track as long as the clip, so the audio never ends before the video
    ins = ['-f', 'lavfi', '-t', f'{wall:.3f}', '-i', 'anullsrc=r=44100:cl=stereo']
    parts = [f"[0:v]trim=start={vs:.3f}:end={ve:.3f},setpts=(PTS-STARTPTS)*{k:.5f},fps=30,crop=1280:712:0:8,scale=1280:720,setsar=1,format=yuv420p[v]"]
    for i, b in enumerate(m['beats']):
        ins += ['-i', b['audio']]; ms = int(b['start'] * 1000)
        parts.append(f'[{i+2}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms}[a{i}]')
    n = len(m['beats'])
    parts.append('[1:a]' + ''.join(f'[a{i}]' for i in range(n)) + f'amix=inputs={n+1}:duration=longest,volume={n+1}[aout]')
    out = f'{WORK}/clips/{name}.mp4'
    r = subprocess.run(['ffmpeg', '-y', '-i', m['video']] + ins + ['-filter_complex', ';'.join(parts), '-map', '[v]', '-map', '[aout]', '-t', f'{wall:.3f}',
                        '-c:v', 'libx264', '-preset', 'medium', '-crf', '20', '-r', '30', '-c:a', 'aac', '-b:a', '160k', '-ar', '44100', '-ac', '2', out], capture_output=True, text=True)
    if r.returncode: raise RuntimeError(r.stderr[-800:])
    print(f'   composed {name}: stretch x{1/k:.2f} (markers {vs:.2f}s..{ve:.2f}s)', flush=True)
    return out

def build_final(order, final):
    clips = [f'{WORK}/clips/{n}.mp4' for n in order if os.path.exists(f'{WORK}/clips/{n}.mp4')]
    lst = f'{WORK}/clips/list.txt'
    open(lst, 'w').write(''.join(f"file '{c}'\n" for c in clips))
    r = subprocess.run(['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', final], capture_output=True, text=True)
    if r.returncode: raise RuntimeError(r.stderr[-800:])
    total = duration(final)
    print(f'\nFINAL: {final}  ({total // 60:.0f}m {total % 60:.0f}s, {len(clips)} scenes)', flush=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='comma-separated scene names'); ap.add_argument('--reset', action='store_true'); ap.add_argument('--compose-only', action='store_true')
    ap.add_argument('--out', default=os.path.join(PROJECT, 'pitch_video', 'urdu_full_walkthrough.mp4'))
    a = ap.parse_args()
    scenes = build_scenes(); order = [s['name'] for s in scenes]
    if not a.compose_only:
        if a.reset:
            subprocess.run([f'{PROJECT}/venv/bin/python', 'manage.py', 'shell', '-c', RESET], cwd=PROJECT, check=True, capture_output=True)
            print('demo client data wiped', flush=True)
        todo = [s for s in scenes if not a.only or s['name'] in a.only.split(',')]
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=CHROME, args=['--no-sandbox'])
            ensure_session(b)
            for s in todo:
                shots = phone_shots(b) if s.get('prep') == 'phone' else None
                record_scene(b, s, shots)
                compose_scene(s['name'])
            b.close()
    else:
        for n in order:
            if os.path.exists(f'{WORK}/raw/{n}.json'): compose_scene(n)
    build_final(order, a.out)

RESET = """
from tenants.models import Tenant
from erp.models import *
t = Tenant.objects.get(slug='al-noor-traders')
Payment.objects.filter(tenant=t).delete(); LedgerEntry.objects.filter(tenant=t).delete(); Invoice.objects.filter(tenant=t).delete()
Product.objects.filter(tenant=t).delete(); Party.objects.filter(tenant=t).delete()
"""

if __name__ == '__main__':
    main()
