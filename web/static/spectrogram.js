// クライアント側スペクトログラム描画（オンデマンド・サーバ非依存）。
// renderSpectrogram(canvas, audioUrl): WAVをfetch→decode→STFT→canvas描画。

(function () {
  // 反復 radix-2 FFT（in-place, re/im 配列）。n は2の冪。
  function fft(re, im) {
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const ang = -2 * Math.PI / len;
      const wr = Math.cos(ang), wi = Math.sin(ang);
      for (let i = 0; i < n; i += len) {
        let cwr = 1, cwi = 0;
        for (let k = 0; k < len / 2; k++) {
          const a = i + k, b = i + k + len / 2;
          const tr = re[b] * cwr - im[b] * cwi;
          const ti = re[b] * cwi + im[b] * cwr;
          re[b] = re[a] - tr; im[b] = im[a] - ti;
          re[a] += tr; im[a] += ti;
          const ncwr = cwr * wr - cwi * wi;
          cwi = cwr * wi + cwi * wr; cwr = ncwr;
        }
      }
    }
  }

  function viridis(t) { // 簡易カラーマップ 0..1 → [r,g,b]
    t = Math.max(0, Math.min(1, t));
    const r = Math.round(255 * Math.min(1, Math.max(0, 1.6 * t - 0.3)));
    const g = Math.round(255 * Math.min(1, Math.max(0, 1.4 * t)));
    const b = Math.round(255 * Math.min(1, Math.max(0, 1.2 * (1 - t) - 0.1) + 0.3 * t));
    return [r, g, b];
  }

  window.renderSpectrogram = async function (canvas, audioUrl) {
    if (canvas.dataset.rendered) return;
    canvas.dataset.rendered = "1";
    try {
      const buf = await (await fetch(audioUrl)).arrayBuffer();
      const ac = new (window.AudioContext || window.webkitAudioContext)();
      const audio = await ac.decodeAudioData(buf);
      let data = audio.getChannelData(0);
      const sr = audio.sampleRate;
      // 16kHzへ間引き（鳥声帯域 ~0-8kHz で十分・描画軽量化）
      const target = 16000;
      if (sr > target) {
        const step = sr / target, n = Math.floor(data.length / step);
        const ds = new Float32Array(n);
        for (let i = 0; i < n; i++) ds[i] = data[Math.floor(i * step)];
        data = ds;
      }
      const FFT = 512, HOP = 256, half = FFT / 2;
      const cols = Math.max(1, Math.floor((data.length - FFT) / HOP));
      const w = Math.min(cols, 900);
      canvas.width = w; canvas.height = half;
      const ctx = canvas.getContext("2d");
      const img = ctx.createImageData(w, half);
      const win = new Float32Array(FFT);
      for (let i = 0; i < FFT; i++) win[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (FFT - 1)); // Hann
      let mn = Infinity, mx = -Infinity;
      const mags = new Float32Array(w * half);
      for (let c = 0; c < w; c++) {
        const off = Math.floor(c * cols / w) * HOP;
        const re = new Float32Array(FFT), im = new Float32Array(FFT);
        for (let i = 0; i < FFT; i++) re[i] = (data[off + i] || 0) * win[i];
        fft(re, im);
        for (let f = 0; f < half; f++) {
          const m = Math.log10(1e-7 + Math.hypot(re[f], im[f]));
          mags[c * half + f] = m; if (m < mn) mn = m; if (m > mx) mx = m;
        }
      }
      const rng = (mx - mn) || 1;
      for (let c = 0; c < w; c++) {
        for (let f = 0; f < half; f++) {
          const t = (mags[c * half + f] - mn) / rng;
          const [r, g, b] = viridis(t);
          const y = half - 1 - f; // 低周波を下に
          const idx = (y * w + c) * 4;
          img.data[idx] = r; img.data[idx + 1] = g; img.data[idx + 2] = b; img.data[idx + 3] = 255;
        }
      }
      ctx.putImageData(img, 0, 0);

      // ===== 判定域(推定)ハイライト: 最大活性の約3秒窓 =====
      // BirdNETは最も確信の高い窓で発火するので「鳴いている＝判定された」箇所の近似。
      const colE = new Float32Array(w);
      for (let c = 0; c < w; c++) {
        let s = 0;
        for (let f = 1; f < half; f++) s += Math.max(0, mags[c * half + f] - mn);
        colE[c] = s;
      }
      const totalDur = cols * HOP / 16000;            // 秒
      let winCols = Math.round(w * Math.min(3, totalDur) / totalDur);
      winCols = Math.max(8, Math.min(w, winCols));
      let run = 0;
      for (let c = 0; c < winCols; c++) run += colE[c];
      let best = run, bestStart = 0;
      for (let c = winCols; c < w; c++) {
        run += colE[c] - colE[c - winCols];
        if (run > best) { best = run; bestStart = c - winCols + 1; }
      }
      const x = bestStart, ww = winCols;
      ctx.save();
      ctx.fillStyle = "rgba(255,235,59,0.18)";
      ctx.fillRect(x, 0, ww, half);
      ctx.strokeStyle = "rgba(255,235,59,0.9)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(x + 0.5, 0.5, ww - 1, half - 1);
      const t0 = (bestStart / w) * totalDur, t1 = ((bestStart + winCols) / w) * totalDur;
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillRect(x, 0, Math.min(ww, 130), 14);
      ctx.fillStyle = "#ffeb3b"; ctx.font = "10px sans-serif";
      ctx.fillText(`判定域(推定) ${t0.toFixed(1)}-${t1.toFixed(1)}s`, x + 3, 11);
      ctx.restore();
    } catch (e) {
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#c0392b"; ctx.font = "12px sans-serif";
      ctx.fillText("スペクトログラム生成失敗: " + e.message, 4, 16);
    }
  };
})();
