/* Lightweight canvas candlestick renderer with live animation.
   - Every update tweens ALL candles from their previous values.
   - Callers can pass a modified "forming" last bar; it interpolates too. */
(function () {
  const ET_FMT = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit",
  });

  function fmtTime(ts) {
    return ET_FMT.format(new Date(ts * 1000));
  }

  const _state = new WeakMap(); // canvas -> { bars, raf }

  function drawCandles(canvas, bars, vwapPrice, onHover) {
    const st = _state.get(canvas) || { bars: null, raf: null };
    if (st.raf) cancelAnimationFrame(st.raf);
    const prev = st.bars;
    const target = bars.slice();
    const start = performance.now();
    const duration = prev ? 650 : 0;

    function render(arr) {
      const ctx = canvas.getContext("2d");
      const dpr = window.devicePixelRatio || 1;
      const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
      const cssH = cssW < 500 ? 240 : 360;
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, cssW, cssH);

      if (!arr || arr.length === 0) {
        ctx.fillStyle = "#8b98ad";
        ctx.font = "13px sans-serif";
        ctx.fillText("no bars yet", 12, 24);
        return;
      }

      const padL = 10, padR = 64, padT = 12, padB = 18;
      const volH = cssW < 500 ? 0 : 56, gap = cssW < 500 ? 0 : 6;
      const plotW = cssW - padL - padR;
      const priceH = cssH - padT - padB - volH - gap;

      let hi = -Infinity, lo = Infinity, maxVol = 0;
      for (const b of arr) {
        hi = Math.max(hi, b.high);
        lo = Math.min(lo, b.low);
        maxVol = Math.max(maxVol, b.volume || 0);
      }
      if (vwapPrice) { hi = Math.max(hi, vwapPrice); lo = Math.min(lo, vwapPrice); }
      const range = hi - lo || 1;
      hi += range * 0.03; lo -= range * 0.03;

      const x = (i) => padL + (i + 0.5) * (plotW / arr.length);
      const y = (p) => padT + (hi - p) / (hi - lo) * priceH;
      const cw = Math.max(1, Math.min(12, plotW / arr.length * 0.62));

      // gridlines
      ctx.strokeStyle = "rgba(148,163,184,0.10)";
      ctx.lineWidth = 1;
      ctx.font = "10px 'JetBrains Mono', 'SF Mono', Consolas, monospace";
      ctx.fillStyle = "#8b98ad";
      for (let g = 0; g <= 4; g++) {
        const gy = padT + g * priceH / 4;
        const price = hi - (hi - lo) * g / 4;
        ctx.beginPath();
        ctx.moveTo(padL, gy);
        ctx.lineTo(cssW - padR, gy);
        ctx.stroke();
        ctx.fillText(price.toFixed(2), cssW - padR + 6, gy + 3);
      }

      // volume bars
      if (volH > 0) {
        const volTop = padT + priceH + gap;
        for (let i = 0; i < arr.length; i++) {
          const b = arr[i];
          const vh = maxVol ? (b.volume || 0) / maxVol * volH : 0;
          ctx.fillStyle = b.close >= b.open ? "rgba(52,211,153,.30)" : "rgba(251,113,133,.30)";
          ctx.fillRect(x(i) - cw / 2, volTop + volH - vh, cw, vh);
        }
      }

      // vwap line
      if (vwapPrice) {
        ctx.strokeStyle = "#fbbf24";
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        ctx.moveTo(padL, y(vwapPrice));
        ctx.lineTo(cssW - padR, y(vwapPrice));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = "#fbbf24";
        ctx.fillText("VWAP " + vwapPrice.toFixed(2), padL, y(vwapPrice) - 4);
      }

      // candles
      for (let i = 0; i < arr.length; i++) {
        const b = arr[i];
        const up = b.close >= b.open;
        const color = up ? "#34d399" : "#fb7185";
        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x(i), y(b.high));
        ctx.lineTo(x(i), y(b.low));
        ctx.stroke();
        const top = y(Math.max(b.open, b.close));
        const hgt = Math.max(1, Math.abs(y(b.open) - y(b.close)));
        if (up) {
          ctx.fillRect(x(i) - cw / 2, top, cw, hgt);
        } else {
          ctx.strokeRect(x(i) - cw / 2, top, cw, hgt);
        }
      }

      // hover (desktop) + touch inspection (phones)
      let hideTimer = null;
      function showAt(clientX) {
        const idx = Math.round((clientX - padL) / (plotW / arr.length) - 0.5);
        if (idx < 0 || idx >= arr.length) return;
        const b = arr[idx];
        if (onHover) {
          const ohlc = `O ${b.open.toFixed(2)}  H ${b.high.toFixed(2)}  L ${b.low.toFixed(2)}  C ${b.close.toFixed(2)}`;
          // Narrow screens: drop the time + volume so the readout always fits.
          onHover(cssW < 500 ? ohlc : `${fmtTime(b.ts)}  ${ohlc}  V ${(b.volume / 1000).toFixed(0)}k`);
        }
      }
      canvas.onmousemove = (e) => {
        showAt(e.clientX - canvas.getBoundingClientRect().left);
      };
      canvas.onmouseleave = () => { if (onHover) onHover(null); };
      canvas.addEventListener("touchstart", (e) => {
        showAt(e.touches[0].clientX - canvas.getBoundingClientRect().left);
        clearTimeout(hideTimer);
      }, { passive: true });
      canvas.addEventListener("touchmove", (e) => {
        showAt(e.touches[0].clientX - canvas.getBoundingClientRect().left);
        clearTimeout(hideTimer);
      }, { passive: true });
      canvas.addEventListener("touchend", () => {
        hideTimer = setTimeout(() => { if (onHover) onHover(null); }, 1600);
      });
    }

    function frame(now) {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
      const mid = target.map((b, i) => {
        const f = prev ? prev[i] : null;
        if (!f || f.ts !== b.ts) return b; // new bar appears instantly
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

    st.raf = requestAnimationFrame(frame);
    _state.set(canvas, st);
  }

  window.drawCandles = drawCandles;
})();
