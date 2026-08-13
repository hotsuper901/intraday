/* Static-frontend logic for the Vercel layout.
   API base is /api/*; ticker symbol comes from ?symbol= in the URL. */
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // --- device auto-detection: phone / tablet / desktop --------------------
  // Sets data-device on <html>; CSS transforms the layout per device and the
  // flag is also readable anywhere via window.__device.
  (function detectDevice() {
    const mqPhone = window.matchMedia("(max-width: 640px)");
    const mqTablet = window.matchMedia("(min-width: 641px) and (max-width: 1024px)");
    function set() {
      const touch = ("ontouchstart" in window) || navigator.maxTouchPoints > 0;
      let d = "desktop";
      if (mqPhone.matches || (touch && window.innerWidth <= 700)) d = "phone";
      else if (mqTablet.matches || touch) d = "tablet";
      document.documentElement.setAttribute("data-device", d);
      window.__device = d;
    }
    if (mqPhone.addEventListener) {
      mqPhone.addEventListener("change", set);
      mqTablet.addEventListener("change", set);
    } else {
      mqPhone.addListener(set);
      mqTablet.addListener(set);
    }
    window.addEventListener("resize", set);
    window.addEventListener("orientationchange", set);
    set();
  })();

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
  // Adaptive decimals: sub-1 FX pairs (EURGBP 0.85495) need 5 places, a
  // $600 stock only 2. When no explicit precision is given, scale by size.
  const smartDec = (x) => {
    const a = Math.abs(x);
    if (!isFinite(a) || a >= 1000) return 2;
    if (a >= 100) return 3;
    if (a >= 1) return 4;
    if (a >= 0.01) return 5;
    return 8;
  };
  const fmtNum = (x, d) => {
    if (x == null) return "—";
    const n = Number(x);
    if (!isFinite(n)) return "—";
    const s = n.toFixed(d == null ? smartDec(n) : d);
    return d == null ? String(parseFloat(s)) : s;
  };
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

  // --- live ticker tape + breadth strip (whole watchlist, unfiltered) ----
  function tapeItem(r) {
    return `<span class="tape-item"><span class="sym">${esc(r.ticker)}</span>` +
      `<span class="${cls(r.change_pct)}">${fmtNum(r.price)} ${signed(r.change_pct)}%</span></span>`;
  }

  function renderBreadth(rows) {
    const el = $("#breadth");
    if (!el) return;
    const up = rows.filter((r) => (r.change_pct || 0) > 0).length;
    const down = rows.filter((r) => (r.change_pct || 0) < 0).length;
    const hot = [...rows].sort((a, b) => Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0))[0];
    el.innerHTML =
      `<span class="chip pos">▲ ${up} advancers</span>` +
      `<span class="chip neg">▼ ${down} decliners</span>` +
      (hot ? `<span class="chip">hottest: <b>${esc(hot.ticker)}</b> ${signed(hot.change_pct)}%</span>` : "");
  }

  async function loadTape() {
    try {
      const res = await fetch("/api/screener" + (bust ? `?_=${bust}` : ""));
      const data = await res.json();
      const track = $("#tape-track");
      if (track && data.rows && data.rows.length) {
        const items = data.rows.map(tapeItem).join("");
        track.innerHTML = items + items; // duplicated for a seamless loop
      }
      renderBreadth(data.rows || []);
    } catch (e) {}
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
        const res = await fetch("/api/screener?" + qs);
        if (!res.ok) throw new Error("http " + res.status);
        data = await res.json();
      } catch (e) {
        const body = $("#rows");
        if (body) body.innerHTML = `<tr><td colspan="11" class="empty">data feed unreachable (${esc(e.message)}) — retrying in 5s…</td></tr>`;
        setTimeout(loadScreener, 5000);
        return;
      }
      const body = $("#rows");
      if (!data.rows.length) {
        const msg = data.fetch_errors > 0
          ? "data source unavailable right now (rate-limited or down) — hit Refresh, or set DATA_MODE=demo"
          : "no tickers match — loosen the filters";
        body.innerHTML = `<tr><td colspan="11" class="empty">${esc(msg)}</td></tr>`;
      } else {
        body.innerHTML = data.rows.map((r, i) => `
          <tr style="--i:${Math.min(i, 20)}">
            <td data-label="Symbol"><a class="ticker-link" href="/ticker/${esc(r.ticker)}">${esc(r.ticker)}</a>${r.asset === "fx" ? '<span class="tag">FX</span>' : r.asset === "crypto" ? '<span class="tag">Crypto</span>' : ""}${r.source === "demo" && data.data_mode === "live" ? ' <span title="live fetch failed — showing demo data" class="muted">*</span>' : ""}</td>
            <td class="muted" data-label="Name">${esc(r.name)}</td>
            <td class="num" data-label="Price">${fmtNum(r.price)}</td>
            <td class="num ${cls(r.change_pct)}" data-label="Chg %">${signed(r.change_pct)}%</td>
            <td class="num ${cls(r.from_open_pct)}" data-label="Open %">${signed(r.from_open_pct)}%</td>
            <td class="num ${cls(r.rel_vol - 1)}" data-label="RelVol">${r.rel_vol == null ? "—" : fmtNum(r.rel_vol) + "x"}</td>
            <td class="num" data-label="ATR %">${r.atr_pct == null ? "—" : fmtNum(r.atr_pct) + "%"}</td>
            <td class="num ${r.rsi > 70 ? "pos" : r.rsi < 30 ? "neg" : ""}" data-label="RSI">${fmtNum(r.rsi, 1)}</td>
            <td class="num ${cls(r.vwap_dist_pct)}" data-label="VWAP Δ">${signed(r.vwap_dist_pct)}%</td>
            <td class="num" data-label="Day Vol">${fmtVol(r.day_volume)}</td>
            <td><a class="btn small" href="/ticker/${esc(r.ticker)}">Risk →</a></td>
          </tr>`).join("");
      }
      const note = $("#refresh-note");
      const stamp = data.refreshed_at
        ? "fetched " + new Date(data.refreshed_at * 1000).toLocaleTimeString()
        : "waiting for first fetch";
      const degraded = data.fetch_errors > 0 ? ` · ⚠ ${data.fetch_errors} symbols unreachable` : "";
      if (note) note.textContent = stamp + degraded + " · " + (data.market_state || "").toUpperCase();
      const rc = $("#row-count");
      if (rc) rc.textContent = data.rows.length + " symbols";
      setPills(data);
    }

    window.__loadPage = loadScreener;
    // filter drawer toggle (phones)
    const ft = $("#filter-toggle");
    const fp = $("#filters-panel");
    if (ft && fp) {
      ft.addEventListener("click", () => {
        fp.classList.toggle("open");
        ft.textContent = fp.classList.contains("open") ? "Filters ▴" : "Filters ▾";
      });
      if (window.__device === "phone") fp.classList.remove("open");
    }
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
    loadTape();
    setInterval(loadScreener, 12000);
    setInterval(loadTape, 15000);
  }

  // ======================================================================
  // Ticker detail page
  // ======================================================================
  if ($("#ticker-panel")) {
    // Clean URLs: /ticker/BTC-USD (also accepts legacy ?symbol=BTC-USD)
    const ticker = (
      (new URLSearchParams(location.search).get("symbol") || "") ||
      ((location.pathname.match(/\/ticker\/([^/]+)/) || [])[1] || "")
    ).toUpperCase().trim();
    $("#crumb-ticker").textContent = ticker;
    $("#h-ticker").childNodes[0].textContent = ticker + " ";
    let tickerData = null;

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
        ["VWAP (Δ)", `${fmtNum(m.vwap)} (${signed(m.vwap_dist_pct)}%)`],
        ["Day vol", fmtVol(m.day_volume)],
      ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    };

    const renderBarsTable = (bars) => {
      const body = $("#bars-body");
      const rows = bars.slice(-20).reverse();
      body.innerHTML = rows.map((b) => `
        <tr>
          <td class="muted" data-label="Time">${fmtTs(b.ts)}</td>
          <td class="num" data-label="Open">${fmtNum(b.open)}</td>
          <td class="num" data-label="High">${fmtNum(b.high)}</td>
          <td class="num" data-label="Low">${fmtNum(b.low)}</td>
          <td class="num ${cls(b.close - b.open)}" data-label="Close">${fmtNum(b.close)}</td>
          <td class="num" data-label="Vol">${fmtVol(b.volume)}</td>
        </tr>`).join("");
    };

    const readoutCb = (txt) => { $("#chart-readout").textContent = txt || "tap a candle to inspect"; };

    const renderChart = () => {
      if (!tickerData) return;
      drawCandles($("#chart"), tickerData.bars.slice(-78), tickerData.metrics.vwap, readoutCb);
    };

    // --- live forming candle: tick the last bar between real fetches -------
    let liveTimer = null;
    function stopLiveSim() {
      if (liveTimer) { clearInterval(liveTimer); liveTimer = null; }
    }
    function startLiveSim() {
      stopLiveSim();
      liveTimer = setInterval(() => {
        if (!tickerData || !tickerData.bars.length) return;
        const bars = tickerData.bars;
        const last = bars[bars.length - 1];
        // Nudge the forming candle's close inside its own high/low range.
        const fake = {
          ts: last.ts, open: last.open, high: last.high, low: last.low,
          volume: last.volume,
          close: Math.min(last.high, Math.max(last.low,
            last.close + (last.high - last.low) * 0.14 * (Math.random() - 0.5))),
        };
        drawCandles($("#chart"), bars.slice(-78, -1).concat([fake]), tickerData.metrics.vwap, readoutCb);
      }, 900);
    }

    async function loadTicker() {
      try {
        const url = `/api/ticker?symbol=${encodeURIComponent(ticker)}&bars_limit=160` + (bust ? `&_=${bust}` : "");
        const res = await fetch(url);
        if (!res.ok) {
          stopLiveSim();
          $("#t-name").textContent = "— no data. Add this symbol to WATCHLIST.";
          setTimeout(loadTicker, 5000);
          return;
        }
        tickerData = await res.json();
        renderStats(tickerData.metrics);
        if (tickerData.metrics.source === "demo" && tickerData.data_mode === "live") {
          $("#t-name").textContent = "— live fetch failed, showing demo data";
        }
        renderBarsTable(tickerData.bars);
        renderChart();
        startLiveSim();
        setPills(tickerData);
        loadSignal();
      } catch (e) {
        stopLiveSim();
        $("#t-name").textContent = "— feed unreachable, retrying…";
        setTimeout(loadTicker, 5000);
      }
    }

    // --- trading signal panel (multi-timeframe buy/sell prediction) -------
    const VERDICT_CLASS = {
      "STRONG BUY": "strong-buy", "BUY": "buy", "NEUTRAL": "neutral",
      "SELL": "sell", "STRONG SELL": "strong-sell",
    };
    let signalBusy = false;

    async function loadSignal() {
      if (signalBusy) return;
      signalBusy = true;
      try {
        const res = await fetch("/api/signal", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker }),
        });
        if (!res.ok) throw new Error("http " + res.status);
        renderSignal(await res.json());
      } catch (e) {
        const v = $("#signal-verdict");
        if (v) v.innerHTML = `<span class="muted">signal unavailable (${esc(e.message)}) — retrying…</span>`;
      } finally {
        signalBusy = false;
      }
    }

    function renderSignal(s) {
      const v = $("#signal-verdict");
      if (v) {
        v.className = "signal-verdict " + (VERDICT_CLASS[s.verdict] || "neutral");
        v.innerHTML = `<span class="sv-label">${esc(s.verdict)}</span>` +
          `<span class="sv-sub">${s.direction} · score ${s.score > 0 ? "+" : ""}${s.score}</span>`;
      }
      const cb = $("#conf-bar");
      if (cb) cb.style.width = (s.confidence || 0) + "%";
      const cl = $("#conf-label");
      if (cl) cl.textContent = `${s.confidence}% confidence${s.confluence ? " · 1m+5m confluence" : ""}`;
      const tfs = $("#sig-tfs");
      if (tfs) {
        tfs.innerHTML = ["1m", "5m"].map((tf) => {
          const a = s.timeframes[tf] || {};
          const c = VERDICT_CLASS[a.verdict] || "neutral";
          const ind = a.indicators || {};
          return `<div class="sig-tf">
            <span class="sig-tf-label">${tf}</span>
            <span class="sig-tf-badge ${c}">${esc(a.verdict || "—")}</span>
            <span class="sig-tf-score muted">${a.score > 0 ? "+" : ""}${a.score}</span>
            <span class="sig-tf-detail muted">EMA ${ind.ema9 != null && ind.ema21 != null ? (ind.ema9 >= ind.ema21 ? "bullish" : "bearish") : "—"} · RSI ${ind.rsi != null ? ind.rsi : "—"} · Stoch ${ind.stoch_k != null ? ind.stoch_k : "—"}${ind.pattern ? " · " + ind.pattern : ""}</span>
          </div>`;
        }).join("");
      }
      const p = s.prediction || {};
      const pred = $("#sig-prediction");
      if (pred) {
        pred.innerHTML = p.entry == null ? "" : `
          <div class="pred-item"><span class="k">Entry</span><span class="v">${fmtNum(p.entry)}</span></div>
          <div class="pred-item ${p.target != null && p.target > p.entry ? "pos" : p.target != null ? "neg" : ""}"><span class="k">Target</span><span class="v">${p.target != null ? fmtNum(p.target) : "—"}</span></div>
          <div class="pred-item neg"><span class="k">Stop</span><span class="v">${p.stop != null ? fmtNum(p.stop) : "—"}</span></div>
          <div class="pred-item"><span class="k">R:R</span><span class="v">${p.rr != null ? "1 : " + p.rr : "—"}</span></div>`;
      }
      const rs = $("#signal-reasons");
      if (rs) rs.innerHTML = (s.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");
      const chips = $("#sig-chips");
      if (chips) {
        const i5 = (s.timeframes["5m"] || {}).indicators || {};
        const items = [];
        if (i5.rsi != null) items.push(`RSI ${i5.rsi}`);
        if (i5.macd_hist != null) items.push(`MACD ${i5.macd_hist > 0 ? "+" : i5.macd_hist < 0 ? "−" : "0"}`);
        if (i5.stoch_k != null) items.push(`Stoch ${i5.stoch_k}/${i5.stoch_d != null ? i5.stoch_d : ""}`);
        if (i5.ema9 != null && i5.ema21 != null) items.push(i5.ema9 >= i5.ema21 ? "EMA9 > EMA21" : "EMA9 < EMA21");
        if (i5.vwap != null) items.push("VWAP " + fmtNum(i5.vwap));
        if (i5.bb_upper != null) items.push(`BB ${fmtNum(i5.bb_lower)}–${fmtNum(i5.bb_upper)}`);
        if (i5.pattern) items.push(i5.pattern);
        if (s.degraded) items.push("5m-only analysis");
        chips.innerHTML = items.map((x) => `<span class="chip">${esc(x)}</span>`).join("");
      }
    }

    window.__loadPage = loadTicker;
    window.addEventListener("resize", renderChart);
    $("#s-refresh").addEventListener("click", loadSignal);

    loadTicker();
    loadTape();
    setInterval(loadTicker, 10000);
    setInterval(loadTape, 20000);
    setInterval(loadSignal, 30000);
  }
})();
