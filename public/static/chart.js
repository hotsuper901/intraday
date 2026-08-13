/* TradingView-style candlestick chart renderer.
   - Hollow bullish / filled bearish candles (TV palette)
   - Right price axis with tagged last-price pill, bottom time axis
   - Floating OHLC tooltip + crosshair with axis tags
   - Live tweening between updates; callers pass a modified forming bar
     and it interpolates. */
(function () {
  const ET_FMT = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
  });

  const UP = "#26a69a";
  const DOWN = "#ef5350";
  const GRID = "rgba(255, 255, 255, 0.05)";
  const AXIS = "#8a8f98";
  const WICK_W = 1.2;

  function fmtTime(ts) {
    return ET_FMT.format(new Date(ts * 1000));
  }

  function smartDec(x) {
    const a = Math.abs(x);
    if (!isFinite(a) || a >= 1000) return 2;
    if (a >= 100) return 3;
    if (a >= 1) return 4;
    if (a >= 0.01) return 5;
    return 8;
  }

  function fmtPx(p) {
    const s = String(parseFloat(p.toFixed(smartDec(p))));
    const dot = s.indexOf(".");
    const i = dot === -1 ? s : s.slice(0, dot);
    const f = dot === -1 ? "" : s.slice(dot);
    return i.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + f;
  }

  function fmtVol(v) {
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(0) + "k";
    return String(Math.round(v));
  }

  const _state = new WeakMap(); // canvas -> { bars, raf, hoverIdx, mouse }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawCandles(canvas, bars, vwapPrice, onHover, opts) {
    opts = opts || {};
    const st = _state.get(canvas) || { bars: null, raf: null, hoverIdx: null, mouse: null };
    if (st.raf) cancelAnimationFrame(st.raf);
    const prev = st.bars;
    const target = bars.slice();
    const start = performance.now();
    const duration = prev ? 420 : 0;

    function render(arr) {
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
      const cssH = cssW < 500 ? 260 : 380;
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);

      if (!arr || arr.length === 0) {
        ctx.fillStyle = "#8a8f98";
        ctx.font = "13px sans-serif";
        ctx.fillText("no intraday bars — live quotes only", 12, 24);
        return;
      }

      const padL = 8, padR = 72, padT = 12, padB = 22;
      const volH = cssW < 500 ? 0 : 46, gap = cssW < 500 ? 0 : 4;
      const plotW = cssW - padL - padR;
      const priceH = cssH - padT - padB - volH - gap;
      st.cssW = cssW;

      let hi = -Infinity, lo = Infinity, maxVol = 0;
      for (const b of arr) {
        hi = Math.max(hi, b.high);
        lo = Math.min(lo, b.low);
        maxVol = Math.max(maxVol, b.volume || 0);
      }
      if (vwapPrice) { hi = Math.max(hi, vwapPrice); lo = Math.min(lo, vwapPrice); }
      const range = hi - lo || 1;
      hi += range * 0.05; lo -= range * 0.05;

      const x = (i) => padL + (i + 0.5) * (plotW / arr.length);
      const y = (p) => padT + (hi - p) / (hi - lo) * priceH;
      const cw = Math.max(1.5, Math.min(13, plotW / arr.length * 0.62));

      // ---- grid + price axis (right) ---------------------------------
      ctx.lineWidth = 1;
      ctx.font = "10.5px 'JetBrains Mono', 'SF Mono', Consolas, monospace";
      const levels = 6;
      for (let g = 0; g <= levels; g++) {
        const gy = padT + g * priceH / levels;
        const price = hi - (hi - lo) * g / levels;
        ctx.strokeStyle = GRID;
        ctx.beginPath();
        ctx.moveTo(padL, gy);
        ctx.lineTo(cssW - padR, gy);
        ctx.stroke();
        ctx.fillStyle = AXIS;
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(fmtPx(price), cssW - padR + 8, gy);
      }

      // ---- time axis (bottom) -----------------------------------------
      const step = Math.max(1, Math.ceil(arr.length / (cssW < 500 ? 4 : 8)));
      ctx.fillStyle = AXIS;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      for (let i = 0; i < arr.length; i += step) {
        ctx.fillText(fmtTime(arr[i].ts), x(i), padT + priceH + volH + gap + 5);
      }

      // ---- volume histogram --------------------------------------------
      if (volH > 0 && maxVol > 0) {
        const volTop = padT + priceH + gap;
        for (let i = 0; i < arr.length; i++) {
          const b = arr[i];
          const vh = (b.volume || 0) / maxVol * volH;
          ctx.fillStyle = b.close >= b.open ? "rgba(38,166,154,.35)" : "rgba(239,83,80,.35)";
          ctx.fillRect(x(i) - cw / 2, volTop + volH - vh, cw, vh);
        }
      }

      // ---- VWAP line -----------------------------------------------------
      if (vwapPrice && vwapPrice > 0) {
        ctx.strokeStyle = "rgba(251,191,36,.75)";
        ctx.setLineDash([5, 4]);
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(padL, y(vwapPrice));
        ctx.lineTo(cssW - padR, y(vwapPrice));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "rgba(251,191,36,.9)";
        ctx.textAlign = "left";
        ctx.textBaseline = "bottom";
        ctx.fillText("VWAP", padL + 2, y(vwapPrice) - 2);
      }

      // ---- candles (TV style: hollow bullish, filled bearish) ----------
      for (let i = 0; i < arr.length; i++) {
        const b = arr[i];
        const up = b.close >= b.open;
        const color = up ? UP : DOWN;
        // wick
        ctx.strokeStyle = color;
        ctx.lineWidth = WICK_W;
        ctx.beginPath();
        ctx.moveTo(x(i), y(b.high));
        ctx.lineTo(x(i), y(b.low));
        ctx.stroke();
        // body
        const top = y(Math.max(b.open, b.close));
        const hgt = Math.max(1.2, Math.abs(y(b.open) - y(b.close)));
        if (up) {
          ctx.strokeRect(x(i) - cw / 2, top, cw, hgt); // hollow
        } else {
          ctx.fillRect(x(i) - cw / 2, top, cw, hgt); // filled
        }
      }

      // ---- last-price line + axis tag -----------------------------------
      const lastB = arr[arr.length - 1];
      const lastUp = lastB.close >= lastB.open;
      const lc = lastUp ? UP : DOWN;
      const ly = y(lastB.close);
      ctx.strokeStyle = lc;
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, ly);
      ctx.lineTo(cssW - padR, ly);
      ctx.stroke();
      ctx.setLineDash([]);
      // pill tag at the right axis
      const tag = fmtPx(lastB.close);
      ctx.font = "10.5px 'JetBrains Mono', 'SF Mono', Consolas, monospace";
      const tw = ctx.measureText(tag).width + 10;
      ctx.fillStyle = lc;
      roundRect(ctx, cssW - padR + 4, ly - 8, Math.min(tw, padR - 8), 16, 4);
      ctx.fill();
      ctx.fillStyle = "#06131c";
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText(tag, cssW - padR + 9, ly + 0.5);

      // ---- watermark -------------------------------------------------------
      if (opts.symbol) {
        ctx.save();
        ctx.globalAlpha = 0.05;
        ctx.fillStyle = "#ededef";
        ctx.font = "700 24px 'JetBrains Mono', 'SF Mono', Consolas, monospace";
        ctx.textAlign = "left";
        ctx.textBaseline = "alphabetic";
        ctx.fillText(opts.symbol + (opts.interval ? " · " + opts.interval : ""), padL + 4, padT + priceH - 8);
        ctx.restore();
      }

      // ---- crosshair + floating tooltip -----------------------------------
      const hover = st.hoverIdx != null && st.hoverIdx >= 0 && st.hoverIdx < arr.length ? arr[st.hoverIdx] : null;
      if (hover) {
        const cx = x(st.hoverIdx);
        const cy = y(hover.close);
        ctx.strokeStyle = "rgba(34,211,238,.28)";
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(cx, padT); ctx.lineTo(cx, padT + priceH); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(padL, cy); ctx.lineTo(cssW - padR, cy); ctx.stroke();
        ctx.setLineDash([]);
        // axis price tag at cursor y
        const ctag = fmtPx(hover.close);
        ctx.font = "10.5px 'JetBrains Mono', 'SF Mono', Consolas, monospace";
        const ctw = ctx.measureText(ctag).width + 10;
        ctx.fillStyle = "#22d3ee";
        roundRect(ctx, cssW - padR + 4, cy - 8, Math.min(ctw, padR - 8), 16, 4);
        ctx.fill();
        ctx.fillStyle = "#06131c";
        ctx.textAlign = "left";
        ctx.textBaseline = "middle";
        ctx.fillText(ctag, cssW - padR + 9, cy + 0.5);
        // candle ring
        ctx.strokeStyle = "rgba(34,211,238,.8)";
        ctx.lineWidth = 1.1;
        ctx.strokeRect(x(st.hoverIdx) - cw / 2 - 2, y(Math.max(hover.open, hover.close)) - 2,
          cw + 4, Math.max(1.2, Math.abs(y(hover.open) - y(hover.close))) + 4);

        // floating OHLC tooltip
        const prevC = arr[Math.max(0, st.hoverIdx - 1)].close;
        const chg = prevC ? (hover.close - prevC) / prevC * 100 : 0;
        const bw = 158, bh = 96;
        let bx = Math.min(cx + 14, cssW - padR - bw - 4);
        if (cx + 14 + bw > cssW - padR) bx = cx - bw - 14;
        bx = Math.max(padL + 4, bx);
        let by = Math.max(padT + 4, cy - bh - 12);
        ctx.fillStyle = "rgba(10, 10, 12, 0.94)";
        ctx.strokeStyle = "rgba(255,255,255,.18)";
        ctx.lineWidth = 1;
        roundRect(ctx, bx, by, bw, bh, 8);
        ctx.fill();
        ctx.stroke();
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";
        const tx = bx + 10;
        ctx.font = "700 11.5px 'JetBrains Mono', 'SF Mono', Consolas, monospace";
        ctx.fillStyle = "#22d3ee";
        ctx.fillText(fmtTime(hover.ts) + " · " + opts.interval, tx, by + 14);
        ctx.font = "10.5px 'JetBrains Mono', 'SF Mono', Consolas, monospace";
        const row = (label, val, color, ry) => {
          ctx.fillStyle = "#8a8f98";
          ctx.fillText(label, tx, ry);
          ctx.fillStyle = color || "#e8eef8";
          ctx.textAlign = "right";
          ctx.fillText(val, bx + bw - 10, ry);
          ctx.textAlign = "left";
        };
        row("O", fmtPx(hover.open), hover.open > hover.close ? DOWN : UP, by + 30);
        row("H", fmtPx(hover.high), UP, by + 44);
        row("L", fmtPx(hover.low), DOWN, by + 58);
        row("C", fmtPx(hover.close), hover.close >= hover.open ? UP : DOWN, by + 72);
        row("V", fmtVol(hover.volume || 0), "#8b98ad", by + 86);
        ctx.fillStyle = chg >= 0 ? UP : DOWN;
        ctx.textAlign = "right";
        ctx.fillText((chg >= 0 ? "+" : "") + chg.toFixed(2) + "%", bx + bw - 10, by + 86);
        ctx.textAlign = "left";
      }

      // notify the DOM readout (mobile bottom bar)
      if (onHover && hover && st.hoverIdx != null) {
        onHover(`${fmtTime(hover.ts)}  O ${fmtPx(hover.open)}  H ${fmtPx(hover.high)}  L ${fmtPx(hover.low)}  C ${fmtPx(hover.close)}  V ${fmtVol(hover.volume || 0)}`);
      } else if (onHover) {
        onHover(null);
      }
    }

    function frame(now) {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3);
      const mid = target.map((b, i) => {
        const f = prev ? prev[i] : null;
        if (!f || f.ts !== b.ts) return b;
        return {
          ts: b.ts,
          open: f.open + (b.open - f.open) * eased,
          high: f.high + (b.high - f.high) * eased,
          low: f.low + (b.low - f.low) * eased,
          close: f.close + (b.close - f.close) * eased,
          volume: f.volume + (b.volume - f.volume) * eased,
        };
      });
      render(mid);
      if (p < 1) {
        st.raf = requestAnimationFrame(frame);
      } else {
        st.bars = target;
        st.raf = null;
      }
    }

    function idxAt(clientX) {
      const padL = 8, padR = 72;
      const cssW = st.cssW || (canvas.clientWidth || canvas.parentElement.clientWidth);
      const plotW = cssW - padL - padR;
      const idx = Math.round((clientX - padL) / (plotW / target.length) - 0.5);
      return (idx < 0 || idx >= target.length) ? null : idx;
    }
    const redraw = () => { if (!st.raf) st.raf = requestAnimationFrame(() => render(st.bars || target)); };

    canvas.onmousemove = (e) => {
      const rect = canvas.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      st.mouse = { x: cx, y: e.clientY - rect.top };
      st.hoverIdx = idxAt(cx);
      redraw();
    };
    canvas.onmouseleave = () => {
      st.hoverIdx = null;
      st.mouse = null;
      redraw();
    };
    canvas.addEventListener("touchstart", (e) => {
      const rect = canvas.getBoundingClientRect();
      const cx = e.touches[0].clientX - rect.left;
      st.hoverIdx = idxAt(cx);
      redraw();
    }, { passive: true });
    canvas.addEventListener("touchmove", (e) => {
      const rect = canvas.getBoundingClientRect();
      const cx = e.touches[0].clientX - rect.left;
      st.hoverIdx = idxAt(cx);
      redraw();
    }, { passive: true });
    canvas.addEventListener("touchend", () => {
      setTimeout(() => {
        st.hoverIdx = null;
        redraw();
      }, 1600);
    });

    st.raf = requestAnimationFrame(frame);
    _state.set(canvas, st);
  }

  window.drawCandles = drawCandles;
})();
