/* Comparison page: price chart, summary, correlation, fundamentals by industry template. */
(() => {
  const COLORS = ['#1f6feb', '#e8590c', '#2b8a3e', '#ae3ec9', '#c92a2a'];
  const $ = (id) => document.getElementById(id);
  const form = $('compare-form');
  const state = { data: null, mode: 'rebased', charts: [] };

  // ---------- formatting ----------
  const fmt = {
    pct: (v, signed) => v == null ? '—' : `${signed && v > 0 ? '+' : ''}${(v * 100).toFixed(1)}%`,
    x: (v) => v == null ? '—' : `${v.toFixed(2)}×`,
    num: (v) => v == null ? '—' : v.toFixed(2),
    money: (v) => {
      if (v == null) return '—';
      const a = Math.abs(v);
      if (a >= 1e12) return (v / 1e12).toFixed(2) + 'T';
      if (a >= 1e9) return (v / 1e9).toFixed(1) + 'B';
      if (a >= 1e6) return (v / 1e6).toFixed(1) + 'M';
      return v.toLocaleString();
    },
    price: (v) => v == null ? '—' : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function setStatus(msg, isError) {
    const el = $('status');
    el.hidden = !msg;
    el.textContent = msg || '';
    el.className = 'status' + (isError ? ' error' : '');
  }

  async function getJSON(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
    return d;
  }

  function destroyCharts() { state.charts.forEach((c) => c.destroy()); state.charts = []; }

  function baseChartOptions(yFormat) {
    const grid = cssVar('--line') || '#e5e7eb';
    const text = cssVar('--muted') || '#6b7280';
    return {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { position: 'bottom', labels: { color: text, boxWidth: 12, usePointStyle: true } },
        tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${yFormat(ctx.parsed.y)}` } },
      },
      scales: {
        x: { ticks: { color: text, maxTicksLimit: 8, maxRotation: 0 }, grid: { display: false } },
        y: { ticks: { color: text, callback: yFormat }, grid: { color: grid } },
      },
    };
  }

  // ---------- 1. price chart ----------
  let priceChart = null;
  function renderPriceChart() {
    const { companies } = state.data;
    const key = state.mode;
    const dates = [...new Set(companies.flatMap((c) => c[key].map((p) => p[0])))].sort();
    const datasets = companies.map((c, i) => {
      const m = new Map(c[key]);
      return {
        label: c.ticker, data: dates.map((d) => m.get(d) ?? null), spanGaps: true,
        borderColor: COLORS[i], backgroundColor: COLORS[i], borderWidth: 2, pointRadius: 0, tension: 0.1,
      };
    });
    const yFmt = key === 'rebased' ? (v) => v == null ? '' : v.toFixed(0) : (v) => v == null ? '' : fmt.price(v);
    if (priceChart) priceChart.destroy();
    priceChart = new Chart($('price-chart'), { type: 'line', data: { labels: dates, datasets }, options: baseChartOptions(yFmt) });
    $('mode-help').textContent = key === 'rebased'
      ? 'All prices start at 100 so you can compare returns, whatever each share costs.'
      : 'Actual closing prices in each stock\'s own currency.';
  }

  document.querySelectorAll('.seg-btn').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.seg-btn').forEach((x) => x.classList.toggle('active', x === b));
    state.mode = b.dataset.mode;
    if (state.data) renderPriceChart();
  }));

  // ---------- 2. summary ----------
  function renderSummary() {
    const rows = state.data.companies.map((c, i) => {
      const s = c.stats;
      const tr = s.total_return;
      return `<tr>
        <td><span class="dot" style="background:${COLORS[i]}"></span><b>${esc(c.ticker)}</b><div class="muted small">${esc(c.name)}</div></td>
        <td>${esc(c.sector)}</td>
        <td class="num mono">${esc(c.currency)} ${fmt.price(c.last_price)}</td>
        <td class="num"><span class="chg ${tr >= 0 ? 'up' : 'down'}">${fmt.pct(tr, true)}</span></td>
        <td class="num">${fmt.pct(s.cagr, true)}</td>
        <td class="num">${fmt.pct(s.annual_vol)}</td>
        <td class="num">${fmt.pct(s.max_drawdown)}</td>
        <td class="num">${fmt.money(c.market_cap)}</td>
      </tr>`;
    }).join('');
    $('summary-table').innerHTML = `<thead><tr><th>Company</th><th>Sector</th><th class="num">Last price</th>
      <th class="num">Return (${esc(state.data.period)})</th><th class="num">Annualised return</th>
      <th class="num">Volatility</th><th class="num">Max drawdown</th><th class="num">Market cap</th></tr></thead><tbody>${rows}</tbody>`;
  }

  // ---------- 3. correlation ----------
  function renderCorrelation() {
    const { tickers, matrix } = state.data.correlation;
    const head = `<thead><tr><th></th>${tickers.map((t) => `<th class="num">${esc(t)}</th>`).join('')}</tr></thead>`;
    const body = tickers.map((t, i) => `<tr><th>${esc(t)}</th>${matrix[i].map((v, j) => {
      if (v == null) return '<td class="num">—</td>';
      const alpha = i === j ? 0.08 : Math.min(Math.abs(v), 1) * 0.55;
      const rgb = v >= 0 ? '31,111,235' : '232,89,12';
      return `<td class="num mono" style="background:rgba(${rgb},${alpha.toFixed(2)})">${v.toFixed(2)}</td>`;
    }).join('')}</tr>`).join('');
    $('corr-table').innerHTML = head + `<tbody>${body}</tbody>`;
  }

  // ---------- 4. fundamentals ----------
  async function loadFundamentals() {
    const tickers = state.data.companies.map((c) => c.ticker).join(',');
    const tpl = $('template-select').value;
    $('template-desc').textContent = 'Loading financial statements…';
    destroyCharts();
    $('trend-charts').innerHTML = '';
    $('valuation-table').innerHTML = '';
    try {
      const d = await getJSON(`/api/fundamentals?tickers=${encodeURIComponent(tickers)}&template=${tpl}`);
      if (d.demo_data) $('demo-banner').hidden = false;
      $('template-desc').textContent = d.template.description;
      renderValuation(d);
      renderTrends(d);
    } catch (e) {
      $('template-desc').textContent = e.message;
    }
  }

  function renderValuation(d) {
    const metrics = d.template.metrics.filter((m) => m.source === 'snapshot');
    if (!metrics.length) return;
    const head = `<thead><tr><th>Current valuation</th>${d.companies.map((c) => `<th class="num">${esc(c.ticker)}</th>`).join('')}</tr></thead>`;
    const body = metrics.map((m) => `<tr><td title="${esc(m.help)}">${esc(m.label)} <span class="help">?</span></td>${
      d.companies.map((c) => `<td class="num">${fmt[m.format](c.snapshot[m.key])}</td>`).join('')}</tr>`).join('');
    $('valuation-table').innerHTML = head + `<tbody>${body}</tbody>`;
  }

  function renderTrends(d) {
    const metrics = d.template.metrics.filter((m) => m.source === 'trend');
    const years = [...new Set(d.companies.flatMap((c) => c.trend.map((r) => r.year)))].sort();
    const wrap = $('trend-charts');
    metrics.forEach((m) => {
      const datasets = d.companies.map((c, i) => {
        const byYear = new Map(c.trend.map((r) => [r.year, r[m.key]]));
        return { label: c.ticker, data: years.map((y) => byYear.get(y) ?? null), borderColor: COLORS[i], backgroundColor: COLORS[i],
                 borderWidth: 2, pointRadius: 3, spanGaps: true };
      });
      if (datasets.every((ds) => ds.data.every((v) => v == null))) return;
      const card = document.createElement('div');
      card.className = 'trend-card';
      card.innerHTML = `<div class="trend-title">${esc(m.label)}</div><div class="muted small">${esc(m.help)}</div><div class="chart-wrap small"><canvas></canvas></div>`;
      wrap.appendChild(card);
      const yFmt = (v) => (v == null ? '' : fmt[m.format](v));
      state.charts.push(new Chart(card.querySelector('canvas'), { type: 'line', data: { labels: years, datasets }, options: baseChartOptions(yFmt) }));
    });
    if (!wrap.children.length) wrap.innerHTML = '<p class="muted">No statement data available for these companies.</p>';
  }

  $('template-select').addEventListener('change', () => state.data && loadFundamentals());

  // ---------- 5. save buttons ----------
  function renderSaveButtons() {
    $('save-buttons').innerHTML = state.data.companies.map((c) => {
      const url = `${window.NIMBUS.newSnapshotUrl}?ticker=${encodeURIComponent(c.ticker)}&price=${c.last_price ?? ''}`;
      return `<a class="chip accent" href="${url}">Save ${esc(c.ticker)} @ ${fmt.price(c.last_price)}</a>`;
    }).join('');
  }

  // ---------- main ----------
  async function run() {
    const tickers = $('tickers').value;
    const period = $('period').value;
    setStatus('Loading market data…');
    $('demo-banner').hidden = true;
    try {
      const d = await getJSON(`/api/compare?tickers=${encodeURIComponent(tickers)}&period=${period}`);
      state.data = d;
      history.replaceState(null, '', `?tickers=${encodeURIComponent(d.companies.map((c) => c.ticker).join(','))}`);
      $('results').hidden = false;
      $('demo-banner').hidden = !d.demo_data;
      $('template-select').value = d.suggested_template;
      renderPriceChart();
      renderSummary();
      renderCorrelation();
      renderSaveButtons();
      setStatus('');
      loadFundamentals();
    } catch (e) {
      setStatus(e.message, true);
    }
  }

  form.addEventListener('submit', (e) => { e.preventDefault(); run(); });
  document.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', () => {
    $('tickers').value = b.dataset.preset; run();
  }));
  if ($('tickers').value.trim()) run();
})();
