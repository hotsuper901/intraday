/* Static-frontend logic for the Vercel layout.
   API base is /api/*; ticker symbol comes from ?symbol= in the URL. */
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // --- ET clock ----------------------------------------------------------
  const etFmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  function tickClock() {
    const el = $("#et-clock");
    if (el) el.textContent = etFmt.format(new Date()) + " ET";
  }
  tickClock();
  setInterval(tickClock, 1000);

  const fmtVol = (v) => v >= 1e9 ? (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(0) + "k" : String(v);
  const fmtNum = (x, d = 2) => (x == null ? "—" : Number(x).toFixed(d));
  const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "muted");
  const signed = (x, d = 2) => (x == null ? "—" : (x > 0 ? "+" : "") + Number(x).toFixed(d));

  const etMinFmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
  });
  const fmtTs = (ts) => ts ? etMinFmt.format(new Date(ts * 1000)) : "—";

  function setPills(data) {
    const mp = $("#mode-pill");
    if (mp && data.data_mode) {
      mp.textContent = data.data_mode.toUpperCase();
      mp.className = "pill " + (data.data_mode === "live" ? "live" : "");
    }
    const sp = $("#state-pill");
    if (sp && data.market_state) {
      sp.textContent = data.market_state.toUpperCase();
      sp.className = "pill " + data.market_state;
    }
  }

  // --- refresh button: cache-busting re-fetch (no background worker here) --
  let bust = 0;
  const rb = $("#refresh-btn");
  if (rb) {
    rb.addEventListener("click", () => {
      rb.textContent = "⟳ …";
      bust = Date.now();
      if (window.__loadPage) window.__loadPage();
      setTimeout(() => { rb.textContent = "⟳ Refresh"; }, 1200);
    });
  }

  // ======================================================================
  // Screener page
  // ======================================================================
  if ($("#screener-table")) {
    let sort = "change_desc";
    let timer = null;

    const readFilters = () => ({
      min_change: $("#f-min-change").value,
      min_relvol: $("#f-min-relvol").value,
      min_price: $("#f-min-price").value,
      max_price: $("#f-max-price").value,
      direction: $("#f-direction").value,
      sort,
    });

    async function loadScreener() {
      const f = readFilters();
      const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== "" && v != null));
      if (bust) qs.set("_", bust);
      let data;
      try {
        data = await (await fetch("/api/screener?" + qs)).json();
      } catch (e) { return; }
      const body = $("#rows");
      if (!data.rows.length) {
        const msg = data.fetch_errors > 0
          ? "data source unavailable right now (rate-limited or down) — hit Refresh, or set DATA_MODE=demo"
          : "no tickers match — loosen the filters";
        body.innerHTML = `<tr><td colspan="11" class="empty">${esc(msg)}</td></tr>`;
      } else {
        body.innerHTML = data.rows.map((r) => `
          <tr>
            <td><a class="ticker-link" href="/ticker.html?symbol=${esc(r.ticker)}">${esc(r.ticker)}</a>${r.asset === "fx" ? '<span class="tag">FX</span>' : r.asset === "crypto" ? '<span class="tag">Crypto</span>' : ""}${r.source === "demo" && data.data_mode === "live" ? ' <span title="live fetch failed — showing demo data" class="muted">*</span>' : ""}</td>
            <td class="muted">${esc(r.name)}</td>
            <td class="num">${fmtNum(r.price)}</td>
            <td class="num ${cls(r.change_pct)}">${signed(r.change_pct)}%</td>
            <td class="num ${cls(r.from_open_pct)}">${signed(r.from_open_pct)}%</td>
            <td class="num ${cls(r.rel_vol - 1)}">${fmtNum(r.rel_vol)}x</td>
            <td class="num">${fmtNum(r.atr_pct)}%</td>
            <td class="num ${r.rsi > 70 ? "pos" : r.rsi < 30 ? "neg" : ""}">${fmtNum(r.rsi, 1)}</td>
            <td class="num ${cls(r.vwap_dist_pct)}">${signed(r.vwap_dist_pct)}%</td>
            <td class="num">${fmtVol(r.day_volume)}</td>
            <td><a class="btn small" href="/ticker.html?symbol=${esc(r.ticker)}">Risk →</a></td>
          </tr>`).join("");
      }
      const note = $("#refresh-note");
      const stamp = data.refreshed_at
        ? "fetched " + new Date(data.refreshed_at * 1000).toLocaleTimeString()
        : "waiting for first fetch";
      if (note) note.textContent = stamp + " · " + (data.market_state || "").toUpperCase();
      const rc = $("#row-count");
      if (rc) rc.textContent = data.rows.length + " symbols";
      setPills(data);
    }

    window.__loadPage = loadScreener;
    for (const inp of ["#f-min-change", "#f-min-relvol", "#f-min-price", "#f-max-price", "#f-direction"]) {
      $(inp).addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(loadScreener, 300); });
    }
    $("#preset-movers").addEventListener("click", () => {
      $("#f-min-change").value = "1.5"; $("#f-min-relvol").value = "1.5";
      $("#f-min-price").value = ""; $("#f-max-price").value = "";
      loadScreener();
    });
    $("#preset-all").addEventListener("click", () => {
      $("#f-min-change").value = ""; $("#f-min-relvol").value = "";
      $("#f-min-price").value = ""; $("#f-max-price").value = "";
      loadScreener();
    });
    document.querySelectorAll("th[data-sort]").forEach((th) =>
      th.addEventListener("click", () => { sort = th.dataset.sort; loadScreener(); }));

    loadScreener();
    setInterval(loadScreener, 20000);
  }

  // ======================================================================
  // Ticker detail page
  // ======================================================================
  if ($("#ticker-panel")) {
    const ticker = (new URLSearchParams(location.search).get("symbol") || "").toUpperCase().trim();
    $("#crumb-ticker").textContent = ticker;
    $("#h-ticker").childNodes[0].textContent = ticker + " ";
    let tickerData = null;
    let riskTimer = null;

    const renderStats = (m) => {
      $("#t-name").textContent = m.name && m.name !== m.ticker ? "— " + m.name : "";
      $("#t-price").textContent = fmtNum(m.price);
      const chg = $("#t-change");
      chg.textContent = `${signed(m.change_pct)}% today`;
      chg.className = "pill " + (m.change_pct > 0 ? "pos" : m.change_pct < 0 ? "neg" : "muted");
      $("#stat-cards").innerHTML = [
        ["Class", m.asset === "fx" ? "FX" : m.asset === "crypto" ? "Crypto" : "Equity"],
        ["Range", `${fmtNum(m.day_low)} – ${fmtNum(m.day_high)}`],
        ["From open", `${signed(m.from_open_pct)}%`],
        ["RelVol", `${fmtNum(m.rel_vol)}x`],
        ["ATR", `${fmtNum(m.atr_pct)}%`],
        ["RSI", `${fmtNum(m.rsi, 1)}`],
        ["VWAP", `${fmtNum(m.vwap)}`],
        ["VWAP Δ", `${signed(m.vwap_dist_pct)}%`],
        ["Day vol", fmtVol(m.day_volume)],
      ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    };

    const renderBarsTable = (bars) => {
      const body = $("#bars-body");
      const rows = bars.slice(-20).reverse();
      body.innerHTML = rows.map((b) => `
        <tr>
          <td class="muted">${fmtTs(b.ts)}</td>
          <td class="num">${fmtNum(b.open)}</td>
          <td class="num">${fmtNum(b.high)}</td>
          <td class="num">${fmtNum(b.low)}</td>
          <td class="num ${cls(b.close - b.open)}">${fmtNum(b.close)}</td>
          <td class="num">${fmtVol(b.volume)}</td>
        </tr>`).join("");
    };

    const renderChart = () => {
      if (!tickerData) return;
      drawCandles(
        $("#chart"), tickerData.bars.slice(-78),
        tickerData.metrics.vwap,
        (txt) => { $("#chart-readout").textContent = txt; }
      );
    };

    async function loadTicker() {
      try {
        const url = `/api/ticker?symbol=${encodeURIComponent(ticker)}&bars_limit=160` + (bust ? `&_=${bust}` : "");
        const res = await fetch(url);
        if (!res.ok) {
          $("#t-name").textContent = "— no data. Add this symbol to WATCHLIST.";
          return;
        }
        tickerData = await res.json();
        renderStats(tickerData.metrics);
        if (tickerData.metrics.source === "demo" && tickerData.data_mode === "live") {
          $("#t-name").textContent = "— live fetch failed, showing demo data";
        }
        renderBarsTable(tickerData.bars);
        renderChart();
        setPills(tickerData);
        suggestStop();
        runRisk();
      } catch (e) {}
    }

    function suggestStop() {
      if (!tickerData) return;
      const m = tickerData.metrics;
      const entry = $("#r-entry");
      if (!entry.value) entry.value = m.price.toFixed(2);
      const atrMove = (m.atr_pct || 1.5) / 100 * (parseFloat(entry.value) || m.price);
      $("#r-stop").value = ((parseFloat(entry.value) || m.price) - 1.5 * atrMove).toFixed(2);
      runRisk();
    }

    async function runRisk() {
      clearTimeout(riskTimer);
      const entry = parseFloat($("#r-entry").value);
      const stop = parseFloat($("#r-stop").value);
      if (!(entry > 0) || !(stop > 0)) return;
      riskTimer = setTimeout(async () => {
        let data;
        try {
          const res = await fetch("/api/risk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              ticker,
              entry,
              stop,
              account: parseFloat($("#r-account").value) || 25000,
              risk_pct: parseFloat($("#r-risk").value) || 1,
            }),
          });
          data = await res.json();
        } catch (e) { return; }
        const box = $("#verdict-box");
        box.textContent = data.verdict === "GO" ? "✓ GO" : data.verdict;
        box.className = "verdict " + data.verdict;
        $("#reason-list").innerHTML = data.reasons.map((r) => `<li>${esc(r)}</li>`).join("");
        $("#sizing").innerHTML = `
          <span>units <b>${Number.isInteger(data.shares) ? data.shares : data.shares.toFixed(4)}</b></span>
          <span>$ risk <b>$${fmtNum(data.dollar_risk)}</b></span>
          <span>stop distance <b>${fmtNum(data.stop_dist_pct)}%</b></span>
          ${data.capped ? `<span class="neg">capped at max position</span>` : ""}
        `;
      }, 250);
    }

    window.__loadPage = loadTicker;
    $("#r-suggest").addEventListener("click", suggestStop);
    for (const id of ["#r-account", "#r-risk", "#r-entry", "#r-stop"]) {
      $(id).addEventListener("input", runRisk);
    }

    loadTicker();
    setInterval(loadTicker, 30000);
  }
})();
