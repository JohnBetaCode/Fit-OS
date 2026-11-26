// Range chips for time charts (Week · Month · 3 months · Year · All), filtered in the browser.
// Markup: templates/_range.html `range_chips(target)`; the chart needs an "inside" dataZoom with
// filterMode "filter" so the y axis fits the visible range. The choice is remembered per chart.
window.fitosRange = function (chart, target) {
  const box = document.querySelector('[data-range-for="' + target + '"]');
  if (!chart || !box) return;
  const store = 'fitos-range-' + target;
  const buttons = Array.from(box.querySelectorAll('button[data-days]'));
  function pick(btn, remember) {
    const days = Number(btn.dataset.days);
    if (days > 0) {
      const end = Date.now();
      chart.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, startValue: end - days * 864e5, endValue: end });
    } else {
      chart.dispatchAction({ type: 'dataZoom', dataZoomIndex: 0, start: 0, end: 100 });
    }
    buttons.forEach(b => {
      const on = b === btn;
      b.classList.toggle('border-accent', on);
      b.classList.toggle('text-ink', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    if (remember) { try { localStorage.setItem(store, btn.dataset.key); } catch (e) { /* private mode */ } }
  }
  buttons.forEach(b => b.addEventListener('click', () => pick(b, true)));
  let saved = null;
  try { saved = localStorage.getItem(store); } catch (e) { saved = null; }
  const first = buttons.find(b => b.dataset.key === saved) || buttons.find(b => b.dataset.key === box.dataset.default) || buttons[buttons.length - 1];
  if (first) pick(first, false);
};
// The dataZoom every ranged chart carries (programmatic only: no wheel or drag hijacking).
window.fitosRangeZoom = [{ type: 'inside', xAxisIndex: 0, filterMode: 'filter', zoomOnMouseWheel: false,
                           moveOnMouseMove: false, moveOnMouseWheel: false, preventDefaultMouseMove: false }];
