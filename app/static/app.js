/* Page logic: ET clock, refresh button, screener table, ticker detail + risk. */
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

  // --- refresh button -----------------------------------------------------
  const rb = $("#refresh-btn");
  if (rb) {
    rb.addEventListener("click", async () => {
      const label = rb.querySelector(".btn-text");
      if (label) label.textContent = "…";
      else rb.textContent = "⟳ …";
      try { await fetch("/api/refresh", { method: "POST" }); } catch (e) {}
      setTimeout(() => {
        loadPage();
        setTimeout(() => {
          const l2 = rb.querySelector(".btn-text");
          if (l2) l2.textContent = "Refresh";
          else rb.textContent = "⟳ Refresh";
        }, 1500);
      }, 1200);
    });
  }

  // ======================================================================
  // Shared pro features: symbol search + keyboard shortcuts
  // ======================================================================
  let SYMBOL_LIST = [];
  const searchInput = $("#sym-search");
  const searchDrop = $("#sym-drop");

  function loadSymbols() {
    fetch("/api/symbols").then((r) => r.json()).then((d) => {
      SYMBOL_LIST = d.symbols || [];
      if (window.__onSymbols) window.__onSymbols(SYMBOL_LIST);
    }).catch(() => {});
  }

  if (searchInput) {
    loadSymbols();
    searchInput.addEventListener("input", () => {
      const q = searchInput.value.trim().toUpperCase();
      const matches = SYMBOL_LIST
        .filter((s) => s.ticker.includes(q) || (s.name || "").toUpperCase().includes(q))
        .slice(0, 8);
      if (!searchDrop) return;
      if (!q || !matches.length) {
        searchDrop.innerHTML = "";
        searchDrop.classList.remove("open");
        return;
      }
      searchDrop.innerHTML = matches.map((s) => `
        <button type="button" class="sym-opt" role="option" data-t="${esc(s.ticker)}">
          <span class="so-t">${esc(s.ticker)}</span>
          <span class="so-n">${esc(s.name)}</span>
        </button>`).join("");
      searchDrop.classList.add("open");
    });
    const goSearch = (t) => {
      if (!t) return;
      searchDrop.classList.remove("open");
      searchInput.blur();
      if (t === (window.__currentTicker || "")) {
        if (window.__loadPage) window.__loadPage();
      } else {
        location.href = "/ticker/" + encodeURIComponent(t);
      }
    };
    searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const first = searchDrop && searchDrop.querySelector(".sym-opt");
        goSearch(first ? first.dataset.t : searchInput.value.trim().toUpperCase());
      } else if (e.key === "Escape") {
        searchDrop.classList.remove("open");
      }
    });
    searchDrop.addEventListener("click", (e) => {
      const btn = e.target.closest(".sym-opt");
      if (btn) goSearch(btn.dataset.t);
    });
    document.addEventListener("click", (e) => {
      if (searchDrop && !searchDrop.contains(e.target) && e.target !== searchInput) {
        searchDrop.classList.remove("open");
      }
    });
  }

  // keyboard shortcuts: / search, r refresh, ←/→ symbol pager
  document.addEventListener("keydown", (e) => {
    if (e.target && e.target.matches("input, select, textarea")) return;
    if (e.key === "/" && searchInput) {
      e.preventDefault();
      searchInput.focus();
      searchInput.select();
    } else if ((e.key === "r" || e.key === "R") && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const rb2 = $("#refresh-btn");
      if (rb2) rb2.click();
    } else if (e.key === "ArrowRight" && window.__pagerNav) {
      window.__pagerNav(1);
    } else if (e.key === "ArrowLeft" && window.__pagerNav) {
      window.__pagerNav(-1);
    }
  });

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
  const addCommas = (s) => {
    const dot = s.indexOf(".");
    const i = dot === -1 ? s : s.slice(0, dot);
    const f = dot === -1 ? "" : s.slice(dot);
    return i.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + f;
  };
  const fmtNum = (x, d) => {
    if (x == null) return "—";
    const n = Number(x);
    if (!isFinite(n)) return "—";
    const s = n.toFixed(d == null ? smartDec(n) : d);
    return addCommas(d == null ? String(parseFloat(s)) : s);
  };
  const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "muted");
  const signed = (x, d = 2) => (x == null ? "—" : (x > 0 ? "+" : "") + Number(x).toFixed(d));

  const etMinFmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
  });
  const fmtTs = (ts) => ts ? etMinFmt.format(new Date(ts * 1000)) : "—";

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

  // ======================================================================
  // Screener page
  // ======================================================================
  if ($("#screener-table")) {
    let sort = "change_desc";
    let lastRefresh = null;
    let noteSuffix = "";
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
            <td data-label="Symbol"><a class="ticker-link" href="/ticker/${esc(r.ticker)}">${esc(r.ticker)}</a>${r.asset === "fx" ? '<span class="tag">FX</span>' : r.asset === "crypto" ? '<span class="tag">Crypto</span>' : ""}</td>
            <td class="muted" data-label="Name">${esc(r.name)}</td>
            <td class="num" data-label="Price">${fmtNum(r.price)}</td>
            <td class="num bar-cell ${cls(r.change_pct)}" data-label="Chg %"><span class="chg-fill" style="width:${Math.min(Math.abs(r.change_pct || 0) * 18, 100)}%"></span><span class="chg-val">${signed(r.change_pct)}%</span></td>
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
        ? "updated 0s ago"
        : "waiting for first refresh";
      const degraded = data.fetch_errors > 0 ? ` · ⚠ ${data.fetch_errors} symbols unreachable` : "";
      if (note) {
        lastRefresh = data.refreshed_at || null;
        noteSuffix = degraded + " · " + (data.market_state || "").toUpperCase();
        note.textContent = stamp + noteSuffix;
      }
      const rc = $("#row-count");
      if (rc) rc.textContent = data.rows.length + " symbols";
      const sp = $("#state-pill");
      if (sp) {
        sp.textContent = (data.market_state || "?").toUpperCase();
        sp.className = "pill " + (data.market_state || "");
      }
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
    const updateSortTh = () => {
      document.querySelectorAll("th[data-sort]").forEach((th) => {
        const base = th.dataset.sort.replace(/_(asc|desc)$/, "");
        const active = sort.replace(/_(asc|desc)$/, "") === base;
        th.classList.toggle("sorted", active);
        th.classList.toggle("asc", active && !sort.endsWith("_desc"));
        th.classList.toggle("desc", active && sort.endsWith("_desc"));
        th.setAttribute("aria-sort", active
          ? (sort.endsWith("_desc") ? "descending" : "ascending") : "none");
      });
    };
    document.querySelectorAll("th[data-sort]").forEach((th) =>
      th.addEventListener("click", () => {
        const base = th.dataset.sort.replace(/_(asc|desc)$/, "");
        if (base === "change") {
          sort = sort === "change_desc" ? "change_asc" : "change_desc";
        } else {
          sort = base; // relvol | atr | rsi (desc)
        }
        updateSortTh();
        loadScreener();
      }));
    updateSortTh();

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

    loadScreener();
    loadTape();
    setInterval(loadScreener, 12000);
    setInterval(loadTape, 15000);
    setInterval(() => {
      const note = $("#refresh-note");
      if (note && lastRefresh) {
        const s = Math.max(0, Math.round(Date.now() / 1000 - lastRefresh));
        note.textContent = `updated ${s}s ago` + noteSuffix;
      }
    }, 1000);
  }

  // ======================================================================
  // Ticker detail page
  // ======================================================================
  if ($("#ticker-panel")) {
    const ticker = (window.location.pathname.split("/").pop() || "").toUpperCase().trim();
    window.__currentTicker = ticker;
    let tickerData = null;

    // --- timeframe toggle (1m / 5m / 15m, persisted) -----------------------
    const aggregate = (bars, n) => {
      const out = [];
      let cur = null, bucket = null;
      for (const b of bars) {
        const bk = Math.floor(b.ts / (n * 300));
        if (bk !== bucket) {
          cur = { ts: bk * n * 300, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume || 0 };
          out.push(cur);
          bucket = bk;
        } else {
          cur.high = Math.max(cur.high, b.high);
          cur.low = Math.min(cur.low, b.low);
          cur.close = b.close;
          cur.volume += b.volume || 0;
        }
      }
      return out;
    };
    let tf = parseInt(localStorage.getItem("radar-tf") || "5", 10) || 5;
    if (![1, 5, 15].includes(tf)) tf = 5;
    const tfBtns = document.querySelectorAll("#tf-toggle .tf-btn");
    const tfNote = $("#tf-note");
    const setTf = (v, quiet) => {
      tf = v;
      localStorage.setItem("radar-tf", String(v));
      tfBtns.forEach((b) => b.classList.toggle("active", parseInt(b.dataset.tf, 10) === v));
      if (tfNote) tfNote.textContent = "";
      if (!quiet) loadTicker();
    };
    tfBtns.forEach((b) => b.addEventListener("click", () => setTf(parseInt(b.dataset.tf, 10))));
    setTf(tf, true);

    // --- symbol pager (prev/next through the watchlist) ---------------------
    const goSymbol = (delta) => {
      const idx = SYMBOL_LIST.findIndex((s) => s.ticker === ticker);
      if (idx < 0) return;
      const next = SYMBOL_LIST[(idx + delta + SYMBOL_LIST.length) % SYMBOL_LIST.length];
      if (next) location.href = "/ticker/" + encodeURIComponent(next.ticker);
    };
    const pagerPrev = $("#sym-prev");
    const pagerNext = $("#sym-next");
    if (pagerPrev) pagerPrev.addEventListener("click", () => goSymbol(-1));
    if (pagerNext) pagerNext.addEventListener("click", () => goSymbol(1));
    window.__pagerNav = goSymbol;
    window.__onSymbols = (list) => {
      const i = list.findIndex((s) => s.ticker === ticker);
      const pos = $("#sym-pos");
      if (pos && i >= 0) pos.textContent = `${i + 1} / ${list.length}`;
    };

    const renderStats = (m) => {
      $("#t-name").textContent = m.name && m.name !== m.ticker ? "— " + m.name : "";
      const priceEl = $("#t-price");
      if (priceEl) {
        const prev = parseFloat(priceEl.textContent.replace(/[^0-9.\-]/g, "")) || null;
        priceEl.textContent = fmtNum(m.price);
        // Flash green/red when the live price actually moves
        if (prev != null && m.price != null && prev !== m.price) {
          priceEl.classList.remove("flash-up", "flash-down");
          void priceEl.offsetWidth; // restart the animation
          priceEl.classList.add(m.price > prev ? "flash-up" : "flash-down");
          clearTimeout(priceEl._flashTimer);
          priceEl._flashTimer = setTimeout(
            () => priceEl.classList.remove("flash-up", "flash-down"), 900);
        }
      }
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
      ].map(([k, v]) => `<div class="card"><div class="k">${k}</div><div class="v ${cls(v)}">${v}</div></div>`).join("");
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
        const reqIv = tf === 1 ? 1 : 5;
        const limit = tf === 15 ? 300 : 160;
        const res = await fetch(`/api/ticker/${ticker}?bars_limit=${limit}&interval=${reqIv}`);
        if (!res.ok) {
          stopLiveSim();
          $("#t-name").textContent = "— no data. Add this symbol to WATCHLIST.";
          setTimeout(loadTicker, 5000);
          return;
        }
        tickerData = await res.json();
        const served = tickerData.interval || 5;
        if (tf === 1 && served !== 1) {
          setTf(5, true);
          if (tfNote) tfNote.textContent = "1m unavailable here — showing 5m";
        } else if (tfNote) {
          tfNote.textContent = "";
        }
        if (tf === 15) tickerData.bars = aggregate(tickerData.bars, 3);
        renderStats(tickerData.metrics);
        renderBarsTable(tickerData.bars);
        renderChart();
        startLiveSim();
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
      if (cb) {
        cb.style.width = (s.confidence || 0) + "%";
        const tone = s.verdict.includes("BUY") ? "bull" : s.verdict.includes("SELL") ? "bear" : "flat";
        cb.className = "conf-bar progress-bar conf-" + tone;
      }
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
          <div class="pred-item ${p.target != null && p.target > p.entry ? "pos" : p.target != null ? "neg" : ""}"><span class="k">Target ${p.target != null && p.target > p.entry ? "▲" : p.target != null ? "▼" : ""}</span><span class="v">${p.target != null ? fmtNum(p.target) : "—"}</span></div>
          <div class="pred-item neg"><span class="k">Stop ${p.stop != null && p.stop < p.entry ? "▼" : p.stop != null ? "▲" : ""}</span><span class="v">${p.stop != null ? fmtNum(p.stop) : "—"}</span></div>
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

    $("#s-refresh").addEventListener("click", loadSignal);

    window.addEventListener("resize", renderChart);
    loadTicker();
    loadTape();
    setInterval(loadTicker, 10000);
    setInterval(loadTape, 20000);
    setInterval(loadSignal, 30000);
  }
})();
