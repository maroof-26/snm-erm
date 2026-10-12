/* POS till: catalog, cart, payment, hold / recall and checkout. No framework; state is the `cart` array. */
(function () {
  'use strict';
  const PRODUCTS = JSON.parse(document.getElementById('pos-products').textContent);
  const CUSTOMERS = JSON.parse(document.getElementById('pos-customers').textContent);
  const CUR = POS.currency;
  const $ = id => document.getElementById(id);
  const num = v => parseFloat(String(v == null ? '' : v).replace(/,/g, '')) || 0;
  const r2 = n => Math.round(n * 100) / 100;
  const money = n => (Number(n) || 0).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  const qfmt = n => (Number(n) || 0).toLocaleString('en-US', {maximumFractionDigits: 3});
  const esc = s => String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const csrf = () => (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';
  const HOLD_KEY = 'pos_hold_' + POS.tenant;

  let cart = [], method = 'cash', lastReceipt = null;
  const byId = {};
  PRODUCTS.forEach(p => { byId[p.id] = p; });

  /* ------------------------------------------------------------------ customers */
  CUSTOMERS.forEach(c => {
    const o = document.createElement('option');
    o.value = c.id; o.textContent = c.n + (c.ph ? ' · ' + c.ph : '');
    $('customer').appendChild(o);
  });

  /* ------------------------------------------------------------------ catalog */
  function tile(p) {
    const out = p.q != null && p.q <= 0;
    const info = p.q != null ? `${qfmt(p.q)} ${esc(p.u)} in stock` : esc(p.u);
    return `<button type="button" class="tile${out ? ' out' : ''}" data-id="${p.id}"><div class="nm">${esc(p.n)}</div><div class="st">${info}</div><div class="pr">${CUR} ${money(p.p)}</div></button>`;
  }
  function renderGrid() {
    const q = $('q').value.trim().toLowerCase();
    const list = q ? PRODUCTS.filter(p => (p.n + ' ' + p.s + ' ' + p.b).toLowerCase().includes(q)) : PRODUCTS;
    const html = list.slice(0, 150).map(tile).join('');
    $('grid').innerHTML = html || '<div class="no-items">No items found.</div>';
  }
  $('grid').addEventListener('click', e => {
    const t = e.target.closest('.tile');
    if (t) add(byId[t.dataset.id]);
  });
  $('q').addEventListener('input', renderGrid);
  $('q').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); scan(); }
    else if (e.key === 'Escape') { $('q').value = ''; renderGrid(); }
  });
  function scan() {
    const code = $('q').value.trim();
    if (!code) return;
    const lc = code.toLowerCase();
    let hit = PRODUCTS.find(p => p.b && p.b === code) || PRODUCTS.find(p => p.s && p.s.toLowerCase() === lc);
    if (!hit) {
      const m = PRODUCTS.filter(p => (p.n + ' ' + p.s + ' ' + p.b).toLowerCase().includes(lc));
      if (m.length === 1) hit = m[0];
    }
    if (hit) { add(hit); $('q').value = ''; renderGrid(); }
    else showErr('No item matches "' + code + '".');
  }

  /* ------------------------------------------------------------------ cart */
  function add(p) {
    if (!p) return;
    const line = cart.find(l => l.id === p.id);
    if (line) line.qty += 1;
    else cart.push({id: p.id, n: p.n, u: p.u, qty: 1, rate: p.p, q: p.q});
    hideErr();
    renderCart();
    const box = $('lines'); box.scrollTop = box.scrollHeight;
  }
  function stockWarn(l) {
    return l.q != null && l.qty > l.q ? `<div class="warn"><i class="bi bi-exclamation-triangle-fill"></i> Only ${qfmt(l.q)} ${esc(l.u)} in stock${POS.allowNegative ? '' : ' — sale will be blocked'}</div>` : '';
  }
  function renderCart() {
    $('lines').innerHTML = cart.length ? cart.map((l, i) => `<div class="line" data-i="${i}">
      <div class="top"><span class="nm">${esc(l.n)}</span><button type="button" class="rm" data-act="rm" aria-label="Remove">&times;</button></div>
      <div class="ctl"><span class="step"><button type="button" data-act="minus">&minus;</button><input class="qty" data-comma="1" data-decimals="3" inputmode="decimal" value="${qfmt(l.qty)}"><button type="button" data-act="plus">+</button></span>
        <span class="x">${esc(l.u)} &times;</span><input class="rate" data-comma="1" data-decimals="2" inputmode="decimal" value="${money(l.rate)}"><span class="amt">${money(l.qty * l.rate)}</span></div>
      <div class="warn-box">${stockWarn(l)}</div></div>`).join('')
      : '<div class="empty-cart"><i class="bi bi-cart3"></i>Cart is empty.<br>Scan a barcode or tap an item.</div>';
    updateTotals();
  }
  $('lines').addEventListener('click', e => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const i = Number(btn.closest('.line').dataset.i), l = cart[i];
    if (btn.dataset.act === 'rm') cart.splice(i, 1);
    else if (btn.dataset.act === 'plus') l.qty += 1;
    else if (btn.dataset.act === 'minus') { l.qty -= 1; if (l.qty <= 0) cart.splice(i, 1); }
    renderCart();
  });
  $('lines').addEventListener('input', e => {
    const row = e.target.closest('.line');
    if (!row) return;
    const l = cart[Number(row.dataset.i)];
    if (e.target.classList.contains('qty')) l.qty = num(e.target.value);
    else if (e.target.classList.contains('rate')) l.rate = num(e.target.value);
    else return;
    row.querySelector('.amt').textContent = money(l.qty * l.rate);
    row.querySelector('.warn-box').innerHTML = stockWarn(l);
    updateTotals();
  });

  /* ------------------------------------------------------------------ totals & payment */
  function calc() {
    const sub = r2(cart.reduce((s, l) => s + r2(l.qty * l.rate), 0));
    const raw = $('discount').value.trim();
    let disc = !raw ? 0 : (raw.endsWith('%') ? sub * num(raw) / 100 : num(raw));
    disc = r2(Math.min(Math.max(disc, 0), sub));
    const total = r2(sub - disc);
    const rawRecv = $('received').value.trim();
    return {sub, disc, total, recv: rawRecv === '' ? total : num(rawRecv)};
  }
  const walkIn = () => $('customer').value === '';
  function updateTotals() {
    const c = calc(), n = cart.length;
    $('subtotal').textContent = money(c.sub);
    $('total').textContent = CUR + ' ' + money(c.total);
    $('fabCount').textContent = n; $('fabTotal').textContent = CUR + ' ' + money(c.total);
    $('cartOpen').classList.toggle('empty', !n);
    const box = $('changeBox');
    if (!n) { box.textContent = ''; box.className = 'change-box'; }
    else if (c.recv >= c.total) { box.textContent = c.recv > c.total ? 'Change: ' + CUR + ' ' + money(c.recv - c.total) : ''; box.className = 'change-box ok'; }
    else if (walkIn()) { box.textContent = 'Short by ' + CUR + ' ' + money(c.total - c.recv); box.className = 'change-box bad'; }
    else { box.textContent = 'On credit: ' + CUR + ' ' + money(c.total - c.recv); box.className = 'change-box'; }
    $('pay').disabled = !n || (walkIn() && c.recv < c.total);
    renderQuick(c.total);
  }
  function renderQuick(total) {
    if (method !== 'cash' || total <= 0) { $('quick').innerHTML = ''; return; }
    const vals = [];
    [50, 100, 500, 1000, 5000].forEach(step => {
      const v = Math.ceil(total / step) * step;
      if (v > total && !vals.includes(v)) vals.push(v);
    });
    $('quick').innerHTML = '<button type="button" data-v="">Exact</button>' + vals.slice(0, 3).map(v => `<button type="button" data-v="${v}">${money(v)}</button>`).join('');
  }
  $('quick').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    $('received').value = b.dataset.v ? money(b.dataset.v) : '';
    updateTotals();
  });
  $('received').addEventListener('input', updateTotals);
  $('discount').addEventListener('input', updateTotals);
  $('customer').addEventListener('change', updateTotals);
  $('methods').addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b) return;
    method = b.dataset.m;
    document.querySelectorAll('#methods button').forEach(x => x.classList.toggle('on', x === b));
    updateTotals();
  });

  function showErr(msg) { const el = $('err'); el.textContent = msg; el.classList.remove('d-none'); }
  function hideErr() { $('err').classList.add('d-none'); }

  /* ------------------------------------------------------------------ checkout */
  async function pay() {
    if ($('pay').disabled) return;
    hideErr();
    const c = calc();
    $('pay').disabled = true;
    let data;
    try {
      const r = await fetch(POS.checkout, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf()},
        body: JSON.stringify({customer: $('customer').value ? Number($('customer').value) : null, discount: c.disc,
          lines: cart.map(l => ({id: l.id, qty: l.qty, rate: l.rate})), payments: [{method: method, amount: c.recv}]})
      });
      data = await r.json();
    } catch (err) { data = {ok: false, error: 'Could not reach the server. Check your connection and try again.'}; }
    if (!data.ok) { showErr(data.error || 'Could not complete the sale.'); updateTotals(); return; }
    cart.forEach(l => { const p = byId[l.id]; if (p && p.q != null) p.q -= l.qty; });
    lastReceipt = data;
    $('doneCode').textContent = data.code;
    $('doneTotal').textContent = CUR + ' ' + money(data.total);
    $('doneChange').textContent = num(data.change) > 0 ? 'Change: ' + CUR + ' ' + money(data.change) : '';
    $('autoPrint').checked = localStorage.getItem('pos_autoprint') === '1';
    $('done').classList.remove('d-none');
    if ($('autoPrint').checked) printReceipt();
    $('newSale').focus();
  }
  function printReceipt() {
    if (!lastReceipt) return;
    const f = document.createElement('iframe');
    f.style.cssText = 'position:fixed;width:0;height:0;border:0;visibility:hidden';
    f.src = lastReceipt.receipt + '?print=1&change=' + encodeURIComponent(lastReceipt.change);
    document.body.appendChild(f);
    setTimeout(() => f.remove(), 30000);
  }
  function resetSale() {
    cart = []; $('discount').value = ''; $('received').value = ''; $('customer').value = ''; hideErr();
    method = 'cash'; document.querySelectorAll('#methods button').forEach(x => x.classList.toggle('on', x.dataset.m === 'cash'));
    renderCart(); renderGrid();
    $('cart').classList.remove('open');
    $('q').focus();
  }
  $('pay').addEventListener('click', pay);
  $('clear').addEventListener('click', () => { if (!cart.length || confirm('Clear the whole cart?')) resetSale(); });
  $('newSale').addEventListener('click', () => { $('done').classList.add('d-none'); resetSale(); });
  $('printBtn').addEventListener('click', printReceipt);
  $('autoPrint').addEventListener('change', e => localStorage.setItem('pos_autoprint', e.target.checked ? '1' : '0'));

  /* ------------------------------------------------------------------ hold / recall */
  const held = () => { try { return JSON.parse(localStorage.getItem(HOLD_KEY) || '[]'); } catch (e) { return []; } };
  const saveHeld = list => { localStorage.setItem(HOLD_KEY, JSON.stringify(list)); heldBadge(); };
  function heldBadge() { const n = held().length; $('heldCount').textContent = n; $('heldCount').classList.toggle('d-none', !n); }
  function hold() {
    if (!cart.length) return;
    const list = held();
    list.push({id: Date.now(), at: new Date().toISOString(), cart: cart, customer: $('customer').value, discount: $('discount').value});
    saveHeld(list); resetSale();
  }
  function showHeld() {
    const list = held();
    $('heldList').innerHTML = list.length ? list.map(h => {
      const total = h.cart.reduce((s, l) => s + l.qty * l.rate, 0);
      return `<div class="held-row" data-id="${h.id}"><div class="info"><b>${h.cart.length} item${h.cart.length > 1 ? 's' : ''} · ${CUR} ${money(total)}</b><small>${new Date(h.at).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}</small></div>
        <button class="btn btn-sm btn-primary" data-act="resume">Resume</button><button class="btn btn-sm btn-outline-danger" data-act="drop">&times;</button></div>`;
    }).join('') : '<div class="text-muted py-3">No held sales.</div>';
    $('heldModal').classList.remove('d-none');
  }
  $('heldList').addEventListener('click', e => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const id = Number(btn.closest('.held-row').dataset.id);
    const list = held(), h = list.find(x => x.id === id);
    if (!h) return;
    if (btn.dataset.act === 'resume') {
      if (cart.length && !confirm('Replace the current cart with this held sale?')) return;
      cart = h.cart; $('customer').value = h.customer || ''; $('discount').value = h.discount || '';
      saveHeld(list.filter(x => x.id !== id)); $('heldModal').classList.add('d-none'); renderCart();
    } else { saveHeld(list.filter(x => x.id !== id)); showHeld(); }
  });
  $('hold').addEventListener('click', hold);
  $('recall').addEventListener('click', showHeld);
  $('heldClose').addEventListener('click', () => $('heldModal').classList.add('d-none'));

  /* ------------------------------------------------------------------ misc */
  $('cartOpen').addEventListener('click', () => $('cart').classList.add('open'));
  $('cartClose').addEventListener('click', () => $('cart').classList.remove('open'));
  document.addEventListener('keydown', e => {
    if (e.key === 'F2') { e.preventDefault(); $('cart').classList.remove('open'); $('q').focus(); $('q').select(); }
    else if (e.key === 'F4') { e.preventDefault(); pay(); }
    else if (e.key === 'F8') { e.preventDefault(); hold(); }
  });
  const tick = () => { $('clock').textContent = new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}); };
  tick(); setInterval(tick, 30000);

  renderGrid(); renderCart(); heldBadge();
})();
