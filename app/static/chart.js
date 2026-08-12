/* Lightweight canvas candlestick renderer. No dependencies. */
(function () {
  const ET_FMT = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit",
  });

  function fmtTime(ts) {
    return ET_FMT.format(new Date(ts * 1000));
  }

  function drawCandles(canvas, bars, vwapPrice, onHover) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || canvas.parentElement.clientWidth;
    const cssH = 360;
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, cssW, cssH);

    if (!bars || bars.length === 0) {
      ctx.fillStyle = "#7d8aa0";
      ctx.font = "13px sans-serif";
      ctx.fillText("no bars yet", 12, 24);
      return;
    }

    const padL = 10, padR = 64, padT = 12, padB = 18;
    const volH = 56, gap = 6;
    const plotW = cssW - padL - padR;
    const priceH = cssH - padT - padB - volH - gap;

    let hi = -Infinity, lo = Infinity, maxVol = 0;
    for (const b of bars) {
      hi = Math.max(hi, b.high);
      lo = Math.min(lo, b.low);
      maxVol = Math.max(maxVol, b.volume || 0);
    }
    if (vwapPrice) { hi = Math.max(hi, vwapPrice); lo = Math.min(lo, vwapPrice); }
    const range = hi - lo || 1;
    hi += range * 0.03; lo -= range * 0.03;

    const x = (i) => padL + (i + 0.5) * (plotW / bars.length);
    const y = (p) => padT + (hi - p) / (hi - lo) * priceH;
    const cw = Math.max(1, Math.min(12, plotW / bars.length * 0.62));

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
    const volTop = padT + priceH + gap;
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i];
      const vh = maxVol ? (b.volume || 0) / maxVol * volH : 0;
      ctx.fillStyle = b.close >= b.open ? "rgba(52,211,153,.30)" : "rgba(251,113,133,.30)";
      ctx.fillRect(x(i) - cw / 2, volTop + volH - vh, cw, vh);
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
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i];
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

    // hover
    canvas.onmousemove = function (e) {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const idx = Math.round((mx - padL) / (plotW / bars.length) - 0.5);
      if (idx < 0 || idx >= bars.length) return;
      const b = bars[idx];
      if (onHover) {
        onHover(
          `${fmtTime(b.ts)}  O ${b.open.toFixed(2)}  H ${b.high.toFixed(2)}  L ${b.low.toFixed(2)}  ` +
          `C ${b.close.toFixed(2)}  V ${(b.volume / 1000).toFixed(0)}k`
        );
      }
    };
  }

  window.drawCandles = drawCandles;
})();
