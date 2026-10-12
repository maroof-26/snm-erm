(function () {
  const SIG = /[0-9.\-]/;
  function fmt(text, dec) {
    const neg = text.trim().startsWith('-');
    text = text.replace(/[^0-9.]/g, '');
    const i = text.indexOf('.');
    let ip = i < 0 ? text : text.slice(0, i);
    let dp = i < 0 ? null : text.slice(i + 1).replace(/\./g, '').slice(0, dec);
    ip = ip.replace(/^0+(?=\d)/, '').replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return (neg ? '-' : '') + ip + (dp !== null ? '.' + dp : '');
  }
  document.addEventListener('input', function (e) {
    const el = e.target;
    if (!el.matches || !el.matches('input[data-comma]')) return;
    const dec = parseInt(el.dataset.decimals || '2', 10);
    const caret = el.selectionStart;
    const sig = (el.value.slice(0, caret).match(/[0-9.\-]/g) || []).length;   // real characters before the caret
    const v = fmt(el.value, dec);
    el.value = v;
    let n = 0, idx = 0;
    while (idx < v.length && n < sig) { if (SIG.test(v[idx])) n++; idx++; }
    el.setSelectionRange(idx, idx);
  });
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('input[data-comma]').forEach(function (el) {
      let v = el.value; if (!v) return;
      const dec = parseInt(el.dataset.decimals || '2', 10);
      if (dec > 2) v = v.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');   // 150.000 -> 150
      el.value = fmt(v, dec);
    });
  });
})();
