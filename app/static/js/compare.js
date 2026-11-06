/* Comparison page: price chart, summary, correlation, fundamentals by industry template.
 *
 * Also used, read-only, by a reopened saved discussion: when the page contains
 * <script id="saved-discussion">, everything is drawn from that saved snapshot and
 * no market-data request is made.
 */
(() => {
  const COLORS = ['#1f6feb', '#e8590c', '#2b8a3e', '#ae3ec9', '#c92a2a'];
  const $ = (id) => document.getElementById(id);
  const form = $('compare-form');
  const savedEl = $('saved-discussion');
  const SAVED = savedEl ? JSON.parse(savedEl.textContent) : null;
  const state = { data: null, mode: 'rebased', charts: [], fundamentalsLoaded: false,
                  run: 0, shown: { compare: null, briefing: null } };

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
  const companyLabel = (c) => c.name || c.ticker;

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

  // The exact response text and the server's signature, so Save can send back what was shown.
  async function getSigned(url) {
    const r = await fetch(url, { headers: { Accept: 'application/json' } });
    const text = await r.text();
    let d = {};
    try { d = JSON.parse(text); } catch (_) { /* not JSON */ }
    if (!r.ok) throw new Error(d.error || `Request failed (${r.status})`);
    return { data: d, text, sig: r.headers.get('X-Nimbus-Signature') };
  }

  const newSubmissionId = () => (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 14)}`;

  function destroyCharts() { state.charts.forEach((c) => c.destroy()); state.charts = []; }

  // ---------- company-name autocomplete ----------
  const tickerInput = $('tickers');
  const suggestionBox = $('company-suggestions');
  const companyDirectory = (window.NIMBUS && window.NIMBUS.companyDirectory) || [];
  let activeSuggestion = -1;

  function currentCompanyTerm() {
    return tickerInput.value.split(',').pop().trim().toLowerCase();
  }

  function closeSuggestions() {
    if (!suggestionBox) return;
    suggestionBox.hidden = true;
    suggestionBox.innerHTML = '';
    activeSuggestion = -1;
    tickerInput.setAttribute('aria-expanded', 'false');
  }

  function chooseCompany(ticker) {
    const parts = tickerInput.value.split(',');
    parts[parts.length - 1] = ` ${ticker}`;
    tickerInput.value = parts.map((p) => p.trim()).filter(Boolean).join(', ');
    closeSuggestions();
    tickerInput.focus();
  }

  function setActiveSuggestion(index) {
    const buttons = [...suggestionBox.querySelectorAll('.company-suggestion')];
    if (!buttons.length) return;
    activeSuggestion = (index + buttons.length) % buttons.length;
    buttons.forEach((button, i) => button.classList.toggle('active', i === activeSuggestion));
  }

  function renderCompanySuggestions() {
    const query = currentCompanyTerm();
    if (!query) { closeSuggestions(); return; }
    const matches = companyDirectory
      .map((company) => ({
        ...company,
        rank: company.ticker.toLowerCase().startsWith(query) || company.name.toLowerCase().startsWith(query) ? 0 : 1,
      }))
      .filter((company) => company.ticker.toLowerCase().includes(query) || company.name.toLowerCase().includes(query))
      .sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name))
      .slice(0, 6);
    if (!matches.length) { closeSuggestions(); return; }

    suggestionBox.innerHTML = '';
    matches.forEach((company) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'company-suggestion';
      button.setAttribute('role', 'option');
      const name = document.createElement('span');
      name.className = 'company-suggestion-name';
      name.textContent = company.name;
      const ticker = document.createElement('span');
      ticker.className = 'company-suggestion-ticker';
      ticker.textContent = company.ticker;
      button.append(name, ticker);
      button.addEventListener('mousedown', (event) => event.preventDefault());
      button.addEventListener('click', () => chooseCompany(company.ticker));
      suggestionBox.appendChild(button);
    });
    suggestionBox.hidden = false;
    tickerInput.setAttribute('aria-expanded', 'true');
    activeSuggestion = -1;
  }

  if (tickerInput) tickerInput.addEventListener('input', renderCompanySuggestions);
  if (tickerInput) tickerInput.addEventListener('focus', renderCompanySuggestions);
  if (tickerInput) tickerInput.addEventListener('blur', () => setTimeout(closeSuggestions, 120));
  if (tickerInput) tickerInput.addEventListener('keydown', (event) => {
    if (suggestionBox.hidden) return;
    const buttons = suggestionBox.querySelectorAll('.company-suggestion');
    if (event.key === 'ArrowDown') { event.preventDefault(); setActiveSuggestion(activeSuggestion + 1); }
    if (event.key === 'ArrowUp') { event.preventDefault(); setActiveSuggestion(activeSuggestion - 1); }
    if (event.key === 'Enter' && activeSuggestion >= 0) {
      event.preventDefault();
      buttons[activeSuggestion].click();
    }
    if (event.key === 'Escape') closeSuggestions();
  });

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

  // ---------- 1. at-a-glance cards ----------
  function renderOverview() {
    const overview = $('overview-cards');
    overview.className = `overview-grid overview-grid-${state.data.companies.length}`;
    overview.innerHTML = state.data.companies.map((company, i) => {
      const stats = company.stats;
      const totalReturn = stats.total_return;
      const returnClass = totalReturn >= 0 ? 'up' : 'down';
      return `<article class="overview-card" style="--company-color:${COLORS[i]}">
        <div class="overview-head">
          <div class="overview-company" title="${esc(companyLabel(company))}">${esc(companyLabel(company))}</div>
          <span class="ticker">${esc(company.ticker)}</span>
        </div>
        <div class="muted small overview-sector">${esc(company.sector || 'Sector unavailable')}</div>
        <div class="overview-return ${returnClass}">${fmt.pct(totalReturn, true)}</div>
        <div class="muted small">Return over ${esc(state.data.period)}</div>
        <div class="overview-metrics">
          <div class="overview-metric"><span>Last price</span><b>${esc(company.currency)} ${fmt.price(company.last_price)}</b></div>
          <div class="overview-metric"><span>Market cap</span><b>${fmt.money(company.market_cap)}</b></div>
          <div class="overview-metric"><span>Volatility</span><b>${fmt.pct(stats.annual_vol)}</b></div>
          <div class="overview-metric"><span>Max drawdown</span><b>${fmt.pct(stats.max_drawdown)}</b></div>
        </div>
      </article>`;
    }).join('');
  }

  // ---------- 2. price chart ----------
  let priceChart = null;
  function renderPriceChart() {
    const { companies } = state.data;
    const key = state.mode;
    const dates = [...new Set(companies.flatMap((c) => c[key].map((p) => p[0])))].sort();
    const datasets = companies.map((c, i) => {
      const m = new Map(c[key]);
      return {
        label: companyLabel(c), data: dates.map((d) => m.get(d) ?? null), spanGaps: true,
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

  // ---------- 3. summary ----------
  function renderSummary() {
    const rows = state.data.companies.map((c, i) => {
      const s = c.stats;
      const tr = s.total_return;
      return `<tr>
        <td><span class="dot" style="background:${COLORS[i]}"></span><b>${esc(companyLabel(c))}</b><div class="muted small">${esc(c.ticker)}</div></td>
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

  // ---------- 3b. financials, ratings and news (Feature 1) ----------
  const NO_SUMMARY_TEXT = 'No summary provided by the source';
  const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u) ? u : null);
  const fmtDate = (iso) => {
    if (!iso) return 'Date not given';
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  };

  function companyHeader(companies) {
    return `<thead><tr><th></th>${companies.map((c, i) =>
      `<th class="num"><span class="dot" style="background:${COLORS[i]}"></span>${esc(companyLabel(c))}<div class="muted small">${esc(c.ticker)}</div></th>`).join('')}</tr></thead>`;
  }

  function renderFinancials(companies) {
    const rows = [
      ['Fiscal year', (f) => f.year ?? '—'],
      ['Revenue', (f) => fmt.money(f.revenue)],
      ['Net income', (f) => fmt.money(f.net_income)],
      ['Total debt', (f) => fmt.money(f.total_debt)],
      ["Shareholders' equity", (f) => fmt.money(f.equity)],
      ['Revenue growth (YoY)', (f) => fmt.pct(f.revenue_growth, true), 'core'],
      ['Net profit margin', (f) => fmt.pct(f.net_margin), 'core'],
      ['Debt-to-equity', (f, c) => fmt.x(f.debt_to_equity)
        + (c.is_bank ? '<div class="muted small">not meaningful for banks</div>' : ''), 'core'],
    ];
    const body = rows.map(([label, cell, cls]) => `<tr class="${cls || ''}"><td>${esc(label)}</td>${companies.map((c) => {
      const f = c.financials || {};
      return f.error ? `<td class="num muted small">${esc(f.error)}</td>` : `<td class="num">${cell(f, c)}</td>`;
    }).join('')}</tr>`).join('');
    $('financials-table').innerHTML = companyHeader(companies) + `<tbody>${body}</tbody>`;
  }

  // Upside uses the last price shown in the Summary table (same figures as a saved record),
  // so the two can never disagree; the server's own estimate is only a fallback.
  function upsideFromShownPrice(r, c) {
    const shown = state.data && state.data.companies.find((x) => x.ticker === c.ticker);
    const target = r.view?.target_mean;
    if (target != null && shown && shown.last_price > 0) return target / shown.last_price - 1;
    return r.view?.upside ?? null;
  }

  function renderRatings(companies) {
    const count = (k) => (r) => (r.counts && r.counts[k] != null ? String(r.counts[k]) : '—');
    const rows = [
      ['Consensus', (r) => esc(r.view?.recommendation ? r.view.recommendation.replace(/\b\w/g, (m) => m.toUpperCase()) : '—')],
      ['Number of analysts', (r) => (r.view?.analysts ?? '—')],
      ['Strong buy', count('strongBuy')], ['Buy', count('buy')], ['Hold', count('hold')],
      ['Sell', count('sell')], ['Strong sell', count('strongSell')],
      ['Mean price target', (r, c) => (r.view?.target_mean != null ? `${esc(c.currency || '')} ${fmt.price(r.view.target_mean)}` : '—')],
      ['Upside to mean target', (r, c) => fmt.pct(upsideFromShownPrice(r, c), true)],
    ];
    const body = rows.map(([label, cell]) => `<tr><td>${esc(label)}</td>${companies.map((c) => {
      const r = c.ratings || {};
      if (!r.counts && !r.view) return `<td class="num muted small">${esc(r.message || 'No analyst ratings available.')}</td>`;
      return `<td class="num">${cell(r, c)}</td>`;
    }).join('')}</tr>`).join('');
    $('ratings-table').innerHTML = companyHeader(companies) + `<tbody>${body}</tbody>`;
  }

  function renderNewsItem(item) {
    const url = safeUrl(item.url);
    const headline = url
      ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(item.headline)}</a>`
      : `${esc(item.headline)} <span class="muted small">(no link provided)</span>`;
    const source = item.publisher ? esc(item.publisher) : `${esc(item.via || 'Yahoo Finance')} <span class="muted">(publisher not given)</span>`;
    const summary = item.summary
      ? `<p class="news-summary">${esc(item.summary)}</p>${item.summary_source ? `<div class="muted small">Summary from ${esc(item.summary_source)}</div>` : ''}`
      : `<p class="news-summary muted"><i>${NO_SUMMARY_TEXT}</i></p>`;
    return `<li class="news-item"><div class="news-headline">${headline}</div>
      <div class="muted small">Source: ${source} · ${esc(fmtDate(item.published_at))}</div>${summary}</li>`;
  }

  function renderNews(companies) {
    $('news-list').innerHTML = companies.map((c, i) => {
      const n = c.news || {};
      const items = n.items || [];
      const list = items.length
        ? `<ul class="news-items">${items.map(renderNewsItem).join('')}</ul>`
        : `<p class="muted small">${esc(n.message || 'No news found.')}</p>`;
      const note = items.length && n.route ? `<div class="muted small">Via ${esc(n.route)}${n.message ? ` — ${esc(n.message)}` : ''}</div>` : '';
      return `<div class="news-company" style="--company-color:${COLORS[i]}">
        <div class="news-company-head"><b>${esc(companyLabel(c))}</b> <span class="ticker">${esc(c.ticker)}</span></div>
        ${note}${list}</div>`;
    }).join('');
  }

  async function loadBriefing(tickers) {
    const status = $('briefing-status');
    status.hidden = false;
    status.textContent = 'Loading financials, analyst ratings and news…';
    ['financials-table', 'ratings-table', 'news-list'].forEach((id) => { $(id).innerHTML = ''; });
    try {
      let d;
      if (SAVED) {
        d = SAVED.briefing;
      } else {
        const run = state.run;
        const res = await getSigned(`/api/briefing?tickers=${encodeURIComponent(tickers)}`);
        if (run !== state.run) return;  // a newer comparison has started; ignore this answer
        state.shown.briefing = res;
        d = res.data;
      }
      if (d.demo_data) $('demo-banner').hidden = false;
      renderFinancials(d.companies);
      renderRatings(d.companies);
      renderNews(d.companies);
      status.hidden = true;
    } catch (e) {
      status.textContent = e.message;
    }
  }

  // ---------- 4. correlation ----------
  function renderCorrelation() {
    const { tickers, matrix } = state.data.correlation;
    if (tickers.length < 2) {
      $('corr-table').innerHTML = '<tbody><tr><td class="muted small">Correlation needs at least 2 companies.</td></tr></tbody>';
      return;
    }
    const companies = new Map(state.data.companies.map((c) => [c.ticker, c]));
    const label = (ticker) => companyLabel(companies.get(ticker) || { ticker });
    const head = `<thead><tr><th></th>${tickers.map((t) => `<th class="num">${esc(label(t))}</th>`).join('')}</tr></thead>`;
    const body = tickers.map((t, i) => `<tr><th>${esc(label(t))}</th>${matrix[i].map((v, j) => {
      if (v == null) return '<td class="num">—</td>';
      const alpha = i === j ? 0.08 : Math.min(Math.abs(v), 1) * 0.55;
      const rgb = v >= 0 ? '31,111,235' : '232,89,12';
      return `<td class="num mono" style="background:rgba(${rgb},${alpha.toFixed(2)})">${v.toFixed(2)}</td>`;
    }).join('')}</tr>`).join('');
    $('corr-table').innerHTML = head + `<tbody>${body}</tbody>`;
  }

  // ---------- 5. fundamentals ----------
  async function loadFundamentals() {
    const tickers = state.data.companies.map((c) => c.ticker).join(',');
    const tpl = $('template-select').value;
    $('template-desc').textContent = 'Loading financial statements…';
    destroyCharts();
    $('trend-charts').innerHTML = '';
    $('valuation-table').innerHTML = '';
    try {
      const d = SAVED ? savedFundamentals(tpl) : await getJSON(`/api/fundamentals?tickers=${encodeURIComponent(tickers)}&template=${tpl}`);
      if (d.demo_data) $('demo-banner').hidden = false;
      $('template-desc').textContent = d.template.description;
      renderValuation(d);
      renderTrends(d);
      state.fundamentalsLoaded = true;
    } catch (e) {
      $('template-desc').textContent = e.message;
    }
  }

  // Saved discussion: rebuild the fundamentals view from the saved yearly rows and
  // the saved quote figures, for any template, without asking the server.
  function savedFundamentals(key) {
    const template = (window.NIMBUS.templates || []).find((t) => t.key === key) || window.NIMBUS.templates[0];
    const companies = SAVED.compare.companies.map((c) => ({
      ticker: c.ticker, name: c.name, currency: c.currency,
      snapshot: Object.fromEntries(template.metrics.filter((m) => m.source === 'snapshot').map((m) => [m.key, c[m.key] ?? null])),
      trend: (SAVED.trends || {})[c.ticker] || [],
    }));
    return { template, companies, demo_data: SAVED.data_source === 'demo' };
  }

  function renderValuation(d) {
    const metrics = d.template.metrics.filter((m) => m.source === 'snapshot');
    if (!metrics.length) return;
    const head = `<thead><tr><th>Current valuation</th>${d.companies.map((c) => `<th class="num">${esc(companyLabel(c))}</th>`).join('')}</tr></thead>`;
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
        return { label: companyLabel(c), data: years.map((y) => byYear.get(y) ?? null), borderColor: COLORS[i], backgroundColor: COLORS[i],
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

  const fundamentalsPanel = $('fundamentals-panel');
  fundamentalsPanel.addEventListener('toggle', () => {
    if (fundamentalsPanel.open && state.data && !state.fundamentalsLoaded) loadFundamentals();
  });
  $('template-select').addEventListener('change', () => {
    state.fundamentalsLoaded = false;
    if (state.data && fundamentalsPanel.open) loadFundamentals();
  });

  // ---------- 5. save buttons ----------
  function renderSaveButtons() {
    if (!$('save-buttons')) return;
    $('save-buttons').innerHTML = state.data.companies.map((c) => {
      // No price in the link: the server fetches the live price when the RM saves.
      const url = `${window.NIMBUS.newSnapshotUrl}?ticker=${encodeURIComponent(c.ticker)}`;
      return `<a class="chip accent" href="${url}">Save ${esc(companyLabel(c))}</a>`;
    }).join('');
  }

  // ---------- main ----------
  async function run() {
    const tickers = $('tickers').value;
    const period = $('period').value;
    closeSuggestions();
    setStatus('Loading market data…');
    $('demo-banner').hidden = true;
    $('results').hidden = true;
    form.querySelector('button[type="submit"], button:not([type])').disabled = true;
    try {
      state.run += 1;
      state.shown = { compare: null, briefing: null };
      const res = await getSigned(`/api/compare?tickers=${encodeURIComponent(tickers)}&period=${period}`);
      const d = res.data;
      state.shown.compare = res;
      state.data = d;
      $('save-submission-id').value = newSubmissionId();  // one ID per comparison shown
      state.fundamentalsLoaded = false;
      destroyCharts();
      $('trend-charts').innerHTML = '';
      $('valuation-table').innerHTML = '';
      $('template-desc').textContent = 'Open this section to load financial statements.';
      history.replaceState(null, '', `?tickers=${encodeURIComponent(d.companies.map((c) => c.ticker).join(','))}&period=${encodeURIComponent(period)}`);
      // The Save form saves exactly the companies and period on screen.
      $('save-tickers').value = d.companies.map((c) => c.ticker).join(',');
      $('save-period').value = period;
      $('results').hidden = false;
      $('demo-banner').hidden = !d.demo_data;
      $('template-select').value = d.suggested_template;
      // Started first so financials, ratings and news still load if a chart fails to draw.
      loadBriefing(d.companies.map((c) => c.ticker).join(','));
      renderOverview();
      renderPriceChart();
      renderSummary();
      renderCorrelation();
      renderSaveButtons();
      setStatus('');
      if (fundamentalsPanel.open) loadFundamentals();
    } catch (e) {
      setStatus(e.message, true);
    } finally {
      form.querySelector('button[type="submit"], button:not([type])').disabled = false;
    }
  }

  // ---------- saved discussion: draw everything from the snapshot ----------
  function showSaved() {
    state.data = SAVED.compare;
    $('demo-banner').hidden = SAVED.data_source !== 'demo';
    $('template-select').value = SAVED.compare.suggested_template || 'general';
    const tickers = SAVED.compare.companies.map((c) => c.ticker).join(',');
    loadBriefing(tickers);
    renderOverview();
    renderSummary();
    renderCorrelation();
    try { renderPriceChart(); } catch (e) { setStatus('The price chart could not be drawn: ' + e.message, true); }
  }

  if (SAVED) { showSaved(); return; }

  // ---------- Save form ----------
  const clientSelect = $('client-select');
  if (clientSelect) {
    const syncClient = () => { $('new-client').hidden = clientSelect.value !== '__new__'; };
    clientSelect.addEventListener('change', syncClient);
    syncClient();
    // Coming Back to this page restores it from the browser cache with the button still
    // disabled; re-enable it. Saving again then sends the same one-time ID, so the server
    // shows the record already saved instead of creating another.
    window.addEventListener('pageshow', () => {
      $('save-button').disabled = false;
      $('save-button').textContent = 'Save discussion';
    });
    // Save sends the exact figures on screen (signed by the server) and a one-time ID,
    // so the record equals the screen and one click is stored once.
    $('save-form').addEventListener('submit', (e) => {
      const { compare, briefing } = state.shown;
      if (!compare || !briefing) {
        e.preventDefault();
        setStatus('Wait for "Financials, ratings & news" to finish loading, then save.', true);
        return;
      }
      $('save-compare-json').value = compare.text;
      $('save-compare-sig').value = compare.sig || '';
      $('save-briefing-json').value = briefing.text;
      $('save-briefing-sig').value = briefing.sig || '';
      $('save-button').disabled = true;
      $('save-button').textContent = 'Saving…';
    });
  }

  form.addEventListener('submit', (e) => { e.preventDefault(); run(); });
  document.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', () => {
    $('tickers').value = b.dataset.preset; closeSuggestions(); run();
  }));
  if ($('tickers').value.trim()) run();
})();
