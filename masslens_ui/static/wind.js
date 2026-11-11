/* WindLens 风透镜 主逻辑：112,142 窗数据束 → 三栏联动（单壳版：导出 initWind）。
   连续层（风况/响应强度/风压脉动/GNSS 动态）全程实测；
   模态参与量为 csd 逐窗谱峰（对照新模型 FE 基，A/B/C 显著度分级）；
   3D 为频域重构代表性合成：u=Σ w_j·φ_j·sin(2πf_j·t+ψ_j)。 */

import * as THREE from "three";
import { Viewer3D } from "/static/viewer3d.js?v=11";
import { $, showToast, parsePack, getJSON, fetchArrayBufferProgress,
         fitCanvas as sharedFitCanvas, pearson } from "/static/shared.js?v=11";

var state = {
  n: 0, win: 0, playing: true, speed: 60,
  viewer: null, modes: [], fadeModes: [],
  phi: null, phiFreqs: null, phiNorm: null,
  accMedian: 1, pressScale: 1, gnssScale: 1,
  dpr: Math.min(window.devicePixelRatio || 1, 2),
  seg: null, segT: 0, pressW: null, pressN: 0, pressScaleSeg: 1,
  viewMode: "shape",
  pod: null, podOn: true, podWP: null,
  _segToken: 0,
  visSpeed: 0.25, visT: 0, compBase: null, compAmpVis: 1
};

/* 画布适配（本模块 dpr 固定传入） */
function fitCanvas(cv) { return sharedFitCanvas(cv, state.dpr); }

function parseBundle(buf) {
  var p = parsePack(buf);
  var off = p.off, out = {};
  var layout = p.header.layout;
  for (var li = 0; li < layout.length; li++) {
    var name = layout[li];
    var shape = p.header.shapes[name];
    var count = 1;
    for (var si = 0; si < shape.length; si++) count *= shape[si];
    out[name] = new Float32Array(buf, off, count);
    out[name].shape_ = shape;
    off += count * 4;
  }
  out.header_ = p.header;
  return out;
}

/* ---------- 时间轴（全程风速曲线 + 可拖游标） ---------- */
function drawTimeline() {
  var cv = $("timeline");
  var wh = fitCanvas(cv); if (!wh) return;
  var W = wh[0], H = wh[1], ctx = wh[2];
  var n = state.n, wind = state.wind;
  var max = 0;
  for (var i = 0; i < n; i += 50) max = Math.max(max, wind[i]);
  max *= 1.05;
  ctx.clearRect(0, 0, W, H);
  // 每像素列 min~max 带
  for (var x = 0; x < W; x++) {
    var i0 = Math.floor(x / W * n), i1 = Math.max(Math.floor((x + 1) / W * n), i0 + 1);
    var lo = 1e9, hi = 0;
    for (var j = i0; j < i1; j++) { if (wind[j] < lo) lo = wind[j]; if (wind[j] > hi) hi = wind[j]; }
    var y0 = H - (lo / max) * H, y1 = H - (hi / max) * H;
    ctx.fillStyle = "rgba(56, 189, 248, 0.35)";
    ctx.fillRect(x, y1, 1, Math.max(y0 - y1, 1));
  }
  // 游标
  var cx = state.win / n * W;
  ctx.fillStyle = "#f59e0b";
  ctx.fillRect(cx - 1, 0, 2, H);
  // 高光事件刻度（第 9 晚：整体=琥珀 / 局部=青，置顶区域）
  if (state.events) {
    for (var ei = 0; ei < state.events.length; ei++) {
      var ev = state.events[ei];
      ctx.fillStyle = ev.cls === "local" ? "rgba(56, 189, 248, 0.85)"
                                         : "rgba(245, 158, 11, 0.85)";
      ctx.fillRect(ev.w / n * W, 0, 2, 6);
    }
    ctx.fillStyle = "rgba(219, 228, 245, 0.75)";
    ctx.font = "10px sans-serif";
    ctx.fillText("事件 " + state.eventStats.n + "（整体 " + state.eventStats.p1 +
                 " / 局部 " + state.eventStats.local + "）", W - 150, 10);
  }
  // 模态轨迹事件（第 17 晚：出生▲绿 / 消亡▼灰 / 迁移◆紫，贴顶第二排）
  if (state.trackEvents && state.trackEvents.length) {
    var te = state.trackEvents, c0 = 0, c1 = 0, c2 = 0;
    for (var ti = 0; ti < te.length; ti++) {
      var e = te[ti], ex = e[0] / n * W, ty = e[1];
      if (ty === 0) c0++; else if (ty === 1) c1++; else c2++;
      ctx.fillStyle = ty === 0 ? "rgba(52, 211, 153, 0.9)"
                    : (ty === 1 ? "rgba(148, 163, 184, 0.8)"
                                : "rgba(192, 132, 252, 0.9)");
      ctx.beginPath();
      if (ty === 0) { ctx.moveTo(ex, 8); ctx.lineTo(ex - 3, 14); ctx.lineTo(ex + 3, 14); }
      else if (ty === 1) { ctx.moveTo(ex, 14); ctx.lineTo(ex - 3, 8); ctx.lineTo(ex + 3, 8); }
      else { ctx.moveTo(ex, 8); ctx.lineTo(ex + 3, 11); ctx.lineTo(ex, 14); ctx.lineTo(ex - 3, 11); }
      ctx.closePath();
      ctx.fill();
    }
    ctx.fillStyle = "rgba(219, 228, 245, 0.75)";
    ctx.font = "10px sans-serif";
    ctx.fillText("模态事件 生" + c0 + " · 灭" + c1 + " · 迁" + c2, 8, 10);
  }
}

function seekTimeline(evt) {
  var cv = $("timeline");
  var r = cv.getBoundingClientRect();
  var frac = Math.min(Math.max((evt.clientX - r.left) / r.width, 0), 1);
  state.win = Math.min(Math.floor(frac * state.n), state.n - 1);
  if (state.seg) {
    var tAbs = state.win * 60 - state.seg.start_s;
    if (tAbs >= 0 && tAbs * state.seg.fs < state.seg.T) {
      state.segT = tAbs * state.seg.fs;   // 段内随动
    } else {
      unloadSegment();                     // 段外 seek = 退出段（逃生通道）
    }
  }
  onWinChange();
}

/* 卸载精华段：回到全季播放（第 12 晚修复"段锁死"） */
function unloadSegment() {
  state.seg = null;
  state.highlights = null;
  state.hlClass = {};
  var sel = $("segment-select");
  if (sel.value !== "") sel.value = "";
  if (state.viewer) state.viewer.setPressMix(0);
  var z = synthTick._pressScratch;
  if (z && state.viewer) { z.fill(0); state.viewer.setPress(z); }
  setPlaying(true);   // 主时间轴重新接管
  $("hl-label").textContent = "—";
  onWinChange();      // 恢复窗聚合风况显示
}

/* ---------- 小趋势图（m̂_a 稀疏点 / accRMS 线） ---------- */
function drawTrend(cvId, series, isSparse, color, cursorVal) {
  var cv = $(cvId);
  var wh = fitCanvas(cv); if (!wh) return;
  var W = wh[0], H = wh[1], ctx = wh[2];
  var n = state.n;
  var max = 0;
  for (var i = 0; i < n; i += 20) { var v = series[i]; if (v === v && v > max) max = v; }
  if (max <= 0) max = 1;
  max *= 1.1;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = color;
  ctx.fillStyle = color;
  if (isSparse) {
    for (i = 0; i < n; i++) {
      v = series[i];
      if (v === v) {
        ctx.globalAlpha = 0.55;
        ctx.fillRect(i / n * W, H - (v / max) * H, 1.5, 1.5);
      }
    }
    ctx.globalAlpha = 1;
    // 稀疏序列的滑动中位平滑线（静态数据，init 预计算）
    var sm = state.maSmooth;
    if (sm) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      var pen = false;
      for (i = 0; i < n; i++) {
        v = sm[i];
        if (v === v) {
          var y2 = H - (v / max) * H;
          if (pen) ctx.lineTo(i / n * W, y2);
          else { ctx.moveTo(i / n * W, y2); pen = true; }
        } else pen = false;
      }
      ctx.stroke();
    }
  } else {
    ctx.beginPath();
    for (i = 0; i < n; i++) {
      v = series[i];
      var y = H - (v / max) * H;
      if (i === 0) ctx.moveTo(i / n * W, y); else ctx.lineTo(i / n * W, y);
    }
    ctx.stroke();
  }
  var cx = state.win / n * W;
  ctx.fillStyle = "#f59e0b";
  ctx.fillRect(cx - 1, 0, 2, H);
  if (cursorVal === cursorVal) {
    ctx.fillStyle = "#fff";
    ctx.beginPath();
    ctx.arc(cx, H - (cursorVal / max) * H, 2.5, 0, 6.29);
    ctx.fill();
  }
}

/* ---------- 条形列表 ---------- */
function makeBars(elId, labels, cls) {
  var host = $(elId);
  host.innerHTML = "";
  var rows = [];
  for (var i = 0; i < labels.length; i++) {
    var row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = '<span class="bar-label">' + labels[i] + '</span>' +
      '<div class="bar-track"><div class="bar-fill ' + cls + '"></div></div>' +
      '<span class="bar-val">—</span>';
    host.appendChild(row);
    rows.push({ fill: row.querySelector(".bar-fill"), val: row.querySelector(".bar-val") });
  }
  return rows;
}

function setBars(rows, values, scale, fmt) {
  for (var i = 0; i < rows.length; i++) {
    var v = values[i];
    if (v === v) {
      rows[i].fill.style.width = Math.min(v / scale * 100, 100).toFixed(1) + "%";
      rows[i].val.textContent = fmt(v);
    } else {
      rows[i].fill.style.width = "0%";
      rows[i].val.textContent = "—";
    }
  }
}

/* 稀疏序列的滑动中位平滑（±halfWin 窗内 ≥minPts 个有效值才出点） */
function smoothSparse(series, halfWin, minPts) {
  var n = series.length, idxs = [];
  for (var i = 0; i < n; i++) if (series[i] === series[i]) idxs.push(i);
  var out = new Float32Array(n).fill(NaN);
  for (var k = 0; k < idxs.length; k++) {
    var i0 = idxs[k], vals = [];
    for (var j = 0; j < idxs.length; j++) {
      if (Math.abs(idxs[j] - i0) <= halfWin) vals.push(series[idxs[j]]);
    }
    if (vals.length >= minPts) {
      vals.sort(function (a, b) { return a - b; });
      out[i0] = vals[Math.floor(vals.length / 2)];
    }
  }
  return out;
}

function nearestFe(f) {
  var best = 1e9, j = 0;
  for (var m = 0; m < state.phiFreqs.length; m++) {
    var d = Math.abs(state.phiFreqs[m] - f);
    if (d < best) { best = d; j = m; }
  }
  return j;
}

/* ---------- 窗更新 ---------- */
function fmtTime(startS) {
  var d = Math.floor(startS / 86400) + 1;
  var sec = Math.floor(startS % 86400);
  var hh = Math.floor(sec / 3600), mm = Math.floor((sec % 3600) / 60);
  return "第 " + d + " 天 " + (hh < 10 ? "0" : "") + hh + ":" + (mm < 10 ? "0" : "") + mm;
}

function onWinChange() {
  var i = state.win, B = state;
  $("win-label").textContent = "窗 " + i + " · " + fmtTime(B.start_s[i]);
  var wv = B.wind[i], wd = B.wdir[i];
  $("wind-speed-big").innerHTML = (wv === wv ? wv.toFixed(2) : "—") + ' <span class="unit">m/s</span>';
  if (wd === wd) {
    $("compass-arrow").style.transform = "rotate(" + wd + "deg)";
    $("compass-deg").textContent = wd.toFixed(0) + "°";
  } else {
    $("compass-arrow").style.transform = "rotate(0deg)";
    $("compass-deg").textContent = "—";
  }
  $("chip-win").textContent = "窗 " + i;
  $("chip-wind").textContent = (wv === wv ? wv.toFixed(2) : "—") + " m/s";

  var pv = [];
  for (var k = 0; k < 12; k++) pv.push(B.press_rms[i * 12 + k]);
  setBars(B.pressBars, pv, B.pressScale, function (v) { return v.toFixed(3); });
  var gv = [];
  for (k = 0; k < 7; k++) {
    gv.push((B.gnss_rms[i * 21 + 3 * k] + B.gnss_rms[i * 21 + 3 * k + 1] +
             B.gnss_rms[i * 21 + 3 * k + 2]) / 3);
  }
  setBars(B.gnssBars, gv, B.gnssScale, function (v) { return v.toFixed(2); });

  // 识别峰：固定 4 槽位（布局不跳动；空槽显示 —）
  var NSLOT = 4;
  if (!B.peakSlots) {
    var host2 = $("peaks-bars");
    host2.innerHTML = "";
    B.peakSlots = [];
    for (k = 0; k < NSLOT; k++) {
      var row0 = document.createElement("div");
      row0.className = "bar-row";
      row0.innerHTML = '<span class="bar-label" style="width:96px">—</span>' +
        '<div class="bar-track"><div class="bar-fill"></div></div>' +
        '<span class="bar-val">—</span>';
      host2.appendChild(row0);
      B.peakSlots.push({ row: row0,
                         fill: row0.querySelector(".bar-fill"),
                         val: row0.querySelector(".bar-val"),
                         label: row0.querySelector(".bar-label") });
    }
  }
  var found = 0, allC = true;
  for (k = 0; k < NSLOT; k++) {
    var f = B.peaks[i * 20 + k * 4], amp = B.peaks[i * 20 + k * 4 + 1];
    var mac = B.peaks[i * 20 + k * 4 + 2], q = B.peaks[i * 20 + k * 4 + 3];
    var slot = B.peakSlots[k];
    if (amp !== amp) {
      slot.label.textContent = "—";
      slot.fill.style.width = "0%";
      slot.fill.className = "bar-fill";
      slot.val.textContent = "—";
      if (slot.row) slot.row.style.opacity = 1;
      continue;
    }
    found++;
    if (q < 1.5) allC = false;
    var qc = q < 0.5 ? "qA" : (q < 1.5 ? "qB" : "qC");
    var ql = q < 0.5 ? "A" : (q < 1.5 ? "B" : "C");
    slot.label.textContent = f.toFixed(3) + " Hz·FE" + (nearestFe(f) + 1);
    // 轨迹置信（第 17 晚）：持续 ≥minLen 窗的峰标"·N窗"，短暂轨迹整行压暗
    var tl = B.trackLen ? B.trackLen[i * 5 + k] : 0;
    slot.val.textContent = ql + " · " + mac.toFixed(2) +
      (tl >= B.trackMinLen ? " · " + (tl >= 1000 ? (tl / 1000).toFixed(1) + "k" : tl) + "窗" : "");
    if (slot.row) slot.row.style.opacity = (tl > 0 && tl < B.trackMinLen) ? 0.55 : 1;
    slot.fill.className = "bar-fill " + qc;
    slot.fill.style.width = Math.min(amp / B.peakAmpScale * 100, 100).toFixed(1) + "%";
  }
  $("peaks-empty").classList.toggle("show", found === 0);
  $("peaks-lowq").classList.toggle("show", found > 0 && allC);

  // m̂_a
  var ma = B.m_a[i];
  if (ma === ma) {
    $("ma-big").innerHTML = ma.toFixed(3) + ' <span class="unit">kg/m²</span>';
    $("ma-note").textContent = "本窗基频识别峰反演（1/f² 线性口径）";
  } else {
    $("ma-big").innerHTML = "—";
    $("ma-note").textContent = "当前窗无基频识别峰（趋势见灰点）";
  }

  drawTimeline();
  drawTrend("ma-trend", B.m_a, true, "#f59e0b", ma);
  drawTrend("acc-trend", B.acc_rms, false, "#38bdf8", B.acc_rms[i]);
  if (B.podA1) drawTrend("pod-trend", B.podA1, false, "#34d399", B.podA1[i]);

  updateModes(i);
  if (B.viewMode === "comp") buildComposite();
  updateSynthNote();
}

/* ---------- 实测形状 LS 混合（第 17 晚，替代"就近单模态"） ---------- */
/* 法方程 + 高斯消元（带岭正则），m≤~20 阶规模极小 */
function solveLS(A, u, m, n) {
  var G = [], b = [], i, j, k;
  for (i = 0; i < m; i++) {
    var gi = [];
    for (j = 0; j < m; j++) {
      var s = 0;
      for (k = 0; k < n; k++) s += A[i][k] * A[j][k];
      gi.push(s);
    }
    var bi = 0;
    for (k = 0; k < n; k++) bi += A[i][k] * u[k];
    G.push(gi); b.push(bi);
  }
  var lam = 1e-6 * m;
  for (i = 0; i < m; i++) G[i][i] += lam;
  for (i = 0; i < m; i++) {
    var p = i;
    for (j = i + 1; j < m; j++) if (Math.abs(G[j][i]) > Math.abs(G[p][i])) p = j;
    if (p !== i) { var t = G[i]; G[i] = G[p]; G[p] = t; t = b[i]; b[i] = b[p]; b[p] = t; }
    var d = G[i][i] || 1e-12;
    for (j = i + 1; j < m; j++) {
      var f = G[j][i] / d;
      for (k = i; k < m; k++) G[j][k] -= f * G[i][k];
      b[j] -= f * b[i];
    }
  }
  var w = new Array(m).fill(0);
  for (i = m - 1; i >= 0; i--) {
    var s2 = b[i];
    for (j = i + 1; j < m; j++) s2 -= G[i][j] * w[j];
    w[i] = s2 / (G[i][i] || 1e-12);
  }
  return w;
}

/* 实测 |u|(12,) → FE 基 LS 混合的全场形状（竖向传感口径）；拟合不出返回 null */
function blendShape(f0, u) {
  var B = state, rows = B.sensorRows;
  if (!rows || !u) return null;
  var k, nu = 0;
  for (k = 0; k < 12; k++) { if (u[k] !== u[k]) return null; nu += u[k] * u[k]; }
  if (nu <= 0) return null;
  nu = Math.sqrt(nu);
  var cand = [], m;
  for (m = 0; m < B.phiFreqs.length; m++) {
    if (Math.abs(B.phiFreqs[m] - f0) <= 0.30) cand.push(m);
  }
  if (cand.length < 2) {   // 太稀疏则取最近 3 阶兜底
    cand = B.phiFreqs.map(function (f, mm) { return [Math.abs(f - f0), mm]; })
      .sort(function (a, b2) { return a[0] - b2[0]; })
      .slice(0, 3).map(function (x) { return x[1]; });
  }
  var A = [];
  for (var ci = 0; ci < cand.length; ci++) {
    var ph = B.phiNorm[cand[ci]], ar = [];
    for (k = 0; k < 12; k++) ar.push(ph[rows[k] * 3 + 1]);   // 竖向（显示系 Y 分量）
    A.push(ar);
  }
  var w = solveLS(A, u, cand.length, 12);
  var res = 0;
  for (k = 0; k < 12; k++) {
    var pred = 0;
    for (ci = 0; ci < cand.length; ci++) pred += w[ci] * A[ci][k];
    res += (pred - u[k]) * (pred - u[k]);
  }
  if (Math.sqrt(res) / nu > 0.6) return null;   // 张成不出 → 回退就近单模态
  var nn3 = B.phiNorm[0].length, out = new Float32Array(nn3);
  for (ci = 0; ci < cand.length; ci++) {
    var ph2 = B.phiNorm[cand[ci]], wi = w[ci];
    for (var v = 0; v < nn3; v++) out[v] += wi * ph2[v];
  }
  var mx = 0;
  for (v = 0; v < nn3; v += 3) {
    var mg = Math.sqrt(out[v] * out[v] + out[v + 1] * out[v + 1] + out[v + 2] * out[v + 2]);
    if (mg > mx) mx = mg;
  }
  if (mx > 0) for (v = 0; v < nn3; v++) out[v] /= mx;
  return out;
}

/* ---------- 最大响应点跟踪（第 17 晚） ---------- */
/* 能量口径：E[v] = Σ_j w_j²·|φ_j[v]|²（异频交叉项时间平均≈0），取 argmax */
function computeMaxPoint() {
  var B = state;
  if (!B.coords || !B.maxMarker) return;
  var nn = B.coords.length / 3;
  if (!computeMaxPoint._e || computeMaxPoint._e.length !== nn) {
    computeMaxPoint._e = new Float32Array(nn);
  }
  var E = computeMaxPoint._e;
  E.fill(0);
  var has = false;
  for (var k = 0; k < B.modes.length; k++) {
    var md = B.modes[k];
    var w = md.wTarget !== undefined ? md.wTarget : 0;
    if (w <= 0) continue;
    has = true;
    var ph = md.phiBlend || B.phiNorm[md.phi];
    var w2 = w * w;
    for (var v = 0; v < nn; v++) {
      E[v] += w2 * (ph[v * 3] * ph[v * 3] + ph[v * 3 + 1] * ph[v * 3 + 1] +
                    ph[v * 3 + 2] * ph[v * 3 + 2]);
    }
  }
  var note = $("maxpoint-note");
  if (!has) {
    B.maxMarker.visible = false;
    if (note) note.textContent = "最大响应点：本窗无模态";
    B.maxNode = -1;
    return;
  }
  var bi = 0, bv = -1;
  for (v = 0; v < nn; v++) if (E[v] > bv) { bv = E[v]; bi = v; }
  B.maxNode = bi;
  B.maxMarker.position.set(B.coords[bi * 3], B.coords[bi * 3 + 1], B.coords[bi * 3 + 2]);
  B.maxMarker.visible = (B.viewMode !== "press");
  if (note) {
    note.textContent = "最大响应点 节点#" + (bi + 1) +
      "（跨 " + B.coords[bi * 3].toFixed(1) + " / 高 " +
      B.coords[bi * 3 + 1].toFixed(1) + " / 长 " + B.coords[bi * 3 + 2].toFixed(1) + " m）";
  }
}

/* ---------- 频域重构合成 ---------- */
function updateModes(i) {
  var B = state;
  var cand = [];
  for (var k = 0; k < 5; k++) {
    var f = B.peaks[i * 20 + k * 4], amp = B.peaks[i * 20 + k * 4 + 1];
    var qq = B.peaks[i * 20 + k * 4 + 3];
    if (amp === amp && f <= 3.0) cand.push({ f: f, amp: amp, q: qq, slot: k });  // 3.0 放宽：实测峰常在 2~3 Hz（用户反馈）
  }
  cand.sort(function (a, b) { return b.amp - a.amp; });
  cand = cand.slice(0, 4);   // top4（与中栏固定四槽一致，第 13 晚）
  var maxAmp = cand.length ? cand[0].amp : 1;
  // 持续振荡器（第 18 晚，用户反馈"机械周期性"）：新峰按频率道（±0.03 Hz）
  // 认领旧模态——相位/OU/w 全部延续，跨窗不再重置；未认领的旧模态淡出
  var pool = B.modes.slice();
  var next = [];
  for (k = 0; k < cand.length; k++) {
    var j = 0, best = 1e9;
    for (var m = 0; m < B.phiFreqs.length; m++) {
      var d = Math.abs(B.phiFreqs[m] - cand[k].f);
      if (d < best) { best = d; j = m; }
    }
    // 实测形状 LS 混合（有实测形状时替代就近单模态）
    var blend = null;
    if (B.peaksShape) {
      var u = [];
      for (var c12 = 0; c12 < 12; c12++) u.push(B.peaksShape[i * 60 + cand[k].slot * 12 + c12]);
      blend = blendShape(cand[k].f, u);
    }
    var md = null, bi = -1, bd = 0.03;
    for (var p = 0; p < pool.length; p++) {
      var dd = Math.abs(pool[p].freq - cand[k].f);
      if (dd < bd) { bd = dd; bi = p; }
    }
    if (bi >= 0) {
      md = pool.splice(bi, 1)[0];
      md.wTarget = cand[k].amp / maxAmp;
      md.q = cand[k].q;
      md.phi = j;
      md.phiBlend = blend;
    } else {
      md = { phi: j, phiBlend: blend, freq: cand[k].f, w: 0, q: cand[k].q,
             wTarget: cand[k].amp / maxAmp,
             phase: Math.random() * 2 * Math.PI, ou: 1 };  // 相位只在诞生时随机
    }
    next.push(md);
  }
  for (k = 0; k < pool.length; k++) {
    pool[k].wTarget = undefined;
    B.fadeModes.push(pool[k]);
  }
  B.modes = next;
  computeMaxPoint();
}

function gauss() {   // Box-Muller 标准正态（谐振子白噪声驱动用）
  var u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function synthTick(now) {
  var B = state;
  var dt = Math.min((now - (synthTick._last || now)) / 1000, 0.1);
  synthTick._last = now;
  if (B.viewer && B.phiNorm) {
    var tVis = (B.visT += dt);
    var scratch = synthTick._scratch;
    scratch.fill(0);
    var wi = Math.max(0, Math.min(B.n - 1, Math.floor(B.win)));  // 播放中为小数索引，取整防 NaN
    var intensity = Math.min(Math.max(B.acc_rms[wi] / B.accMedian, 0.35), 1.6);  // 幅度收敛（用户反馈大风太夸张）
    if (B.viewMode === "press") intensity = 0;  // 纯风压模式结构静息
    // 强度跨窗平滑（第 18 晚：窗阶跃本身也是节拍源）
    if (synthTick._iSm === undefined) synthTick._iSm = intensity;
    synthTick._iSm += (intensity - synthTick._iSm) * Math.min(dt / 1.2, 1);
    intensity = synthTick._iSm;
    var R = B.viewer.radius;
    var ampVis = 0.035 * R;   // 0.06→0.035：幅度收敛（用户反馈）
    var v2;
    if (B.viewMode === "comp") {
      // 复合：能量加权形态 × 慢速呼吸（用户反馈"复合也要看到动"）
      // 第 18 晚：呼吸相位连续累积 + OU 包络调制（打破固定正弦节拍）
      if (B.compBase) {
        scratch.set(B.compBase);
        if (B.compPhase === undefined) { B.compPhase = Math.random() * 2 * Math.PI; B.compOU = 1; }
        B.compPhase += 2 * Math.PI * 0.45 * B.visSpeed * dt;
        B.compOU += 0.7 * (1 - B.compOU) * dt +
                    0.85 * Math.sqrt(dt) * ((Math.random() + Math.random() + Math.random()) - 1.5) * 2;
        var env = 0.35 + 0.65 * Math.min(Math.max(B.compOU, 0.2), 2.0);
        var breathe = Math.sin(B.compPhase) * env;
        for (v2 = 0; v2 < scratch.length; v2++) scratch[v2] *= breathe;
      }
    } else {
      // 振型：多模态叠加慢放（视觉频率 = 实测频率 × visSpeed，频率比保留）
      // 第 19 晚定版：模态坐标 = 白噪声驱动二阶谐振子（窄带随机过程，
      // 相位/幅值皆游走）——取代 sin+OU，复合场连续流变、无周期节拍。
      // 注意字段：md.q 是质量等级编码，模态坐标另用 qc/qv（勿混）。
      var layers = B.modes.concat(B.fadeModes);
      for (var k = 0; k < layers.length; k++) {
        var md = layers[k];
        var ramp = md.wTarget !== undefined ? md.wTarget : 0;
        md.w += (ramp - md.w) * Math.min(dt / 0.4, 1);
        if (md.w < 0.003 && md.wTarget === undefined) continue;
        var om = 2 * Math.PI * md.freq * B.visSpeed;   // 视觉角频率 rad/s
        var zeta = 0.06;
        if (md.qc === undefined) { md.qc = gauss(); md.qv = om * gauss(); }
        var sigma = Math.sqrt(4 * zeta * om * om * om);  // stationary RMS(qc)≈1
        md.qv += (-2 * zeta * om * md.qv - om * om * md.qc +
                  sigma * gauss() * Math.sqrt(dt)) * dt;
        md.qc += md.qv * dt;
        var wgt = md.w * intensity;
        if (wgt <= 0) continue;
        var ph = md.phiBlend || B.phiNorm[md.phi];
        for (var v = 0; v < scratch.length; v++) scratch[v] += wgt * ph[v] * md.qc;
      }
      B.fadeModes = B.fadeModes.filter(function (md) { return md.w >= 0.003; });
    }
    for (v2 = 0; v2 < scratch.length; v2++) scratch[v2] *= ampVis;
    B.viewer.setShapeImmediate(scratch, ampVis);
    if (B.maxMarker) {
      var pu = 1 + 0.3 * Math.sin(2 * Math.PI * 1.2 * tVis);
      B.maxMarker.scale.set(pu, pu, pu);
      B.maxMarker.visible = (B.viewMode !== "press" && B.maxNode >= 0);
    }
  }
  // 精华段风压帧推进（10 Hz 实时循环；所有模式都推进——振型模式曾因此冻结，用户实机反馈）
  if (B.viewer && B.seg) {
    B.segT = (B.segT + dt * B.seg.fs) % B.seg.T;
    var fr = Math.floor(B.segT);
    // 段位置 → 主时间轴游标（双向联动，高光跳转随动）
    var wIdx = Math.max(0, Math.min(B.n - 1,
      Math.floor((B.seg.start_s + B.segT / B.seg.fs) / 60)));
    if (wIdx !== Math.floor(B.win)) {
      B.win = wIdx;
      onWinChangeThrottled();
    }
    if (fr !== synthTick._lastFr) {
      synthTick._lastFr = fr;
      // 风压场只在 press/both 模式计算写入（其余模式不动 GPU，但游标/时程照常）
      if (B.viewMode === "press" || B.viewMode === "both") {
        var W = B.pressW, pr = B.seg.press, N = B.pressN, sc = B.pressScaleSeg;
        var out = synthTick._pressScratch;
        var T2 = B.seg.T;
        if (B.podOn && B.pod && B.podWP) {   // POD 全局模态重构风压场
          var K = B.pod.k, V = B.pod.vecs, WP = B.podWP;
          var a = new Array(K).fill(0);
          for (var kk = 0; kk < K; kk++) {
            var s3 = 0;
            for (var c0 = 0; c0 < 12; c0++) s3 += V[c0][kk] * pr[c0 * T2 + fr];
            a[kk] = s3;
          }
          for (var ni = 0; ni < N; ni++) {
            var s2 = 0, base = ni * K;
            for (kk = 0; kk < K; kk++) s2 += WP[base + kk] * a[kk];
            out[ni] = Math.max(-1, Math.min(1, s2 / sc));
          }
        } else {                              // 12 点 IDW 插值（原始口径）
          for (ni = 0; ni < N; ni++) {
            s2 = 0; base = ni * 12;
            for (var c = 0; c < 12; c++) s2 += W[base + c] * pr[c * T2 + fr];
            out[ni] = Math.max(-1, Math.min(1, s2 / sc));
          }
        }
        B.viewer.setPress(out);
      }
      drawSensorChart();
      // 段内实况：风况卡 10 Hz 实测值 + 风压条 2 s 滚动 RMS + 风速微趋势
      var wsv = B.seg.speed[fr], wdv = B.seg.direct[fr];
      $("wind-speed-big").innerHTML = wsv.toFixed(2) + ' <span class="unit">m/s</span>';
      $("compass-arrow").style.transform = "rotate(" + wdv + "deg)";
      $("compass-deg").textContent = wdv.toFixed(0) + "°";
      $("chip-wind").textContent = wsv.toFixed(2) + " m/s";
      if (B.pressBars) {
        var T3 = B.seg.T, i0f = Math.max(0, fr - 19);
        var live = [];
        for (var c4 = 0; c4 < 12; c4++) {
          var ss2 = 0, cnt = 0, f2;
          for (f2 = i0f; f2 <= fr; f2++) {
            var pv2 = B.seg.press[c4 * T3 + f2];
            ss2 += pv2 * pv2; cnt++;
          }
          live.push(Math.sqrt(ss2 / Math.max(cnt, 1)));
        }
        setBars(B.pressBars, live, B.pressScale, function (v) { return v.toFixed(3); });
      }
      drawWindMicro(fr);
    }
  }
  // 播放推进（到末尾回卷循环，用户反馈"往右卡住"）
  if (B.playing) {
    B.win += B.speed * dt;
    if (B.win >= B.n - 1) B.win = 0;
    onWinChangeThrottled();
  }
  requestAnimationFrame(synthTick);
}

var _lastWin = -1;
function onWinChangeThrottled() {
  var w = Math.floor(state.win);
  if (w !== _lastWin) {
    _lastWin = w;
    state.win = w;
    onWinChange();
  }
}

function setPlaying(p) {
  state.playing = p;
  $("play-btn").textContent = p ? "⏸" : "▶";
}

/* ---------- 现场照片面板（第 17 晚） ---------- */
function showPhoto(ph) {
  var p = $("photo-panel");
  if (!p) return;
  $("photo-img").src = "/static/photos/" + ph.file;
  $("photo-cap").textContent = (ph.name || "") +
    (ph.caption ? "：" + ph.caption : "");
  p.hidden = false;
}

/* ---------- 初始化 ---------- */
export async function initWind() {
  try {
    var meta = await getJSON("/api/wind/meta");
    if (meta.source === "synthetic-demo") {
      $("wind-data-note").textContent = "合成风振演示 · 三个有限元振型叠加 · 测点时程为合成信号";
      $("main-w").classList.add("synthetic-demo");
    }
    if (!meta.has_bundle) {
      showToast("风振数据束未生成，请先运行 python -m windlens_engine.bundle");
      return;
    }
    var buf;
    if (new URLSearchParams(location.search).has("dl")) {
      buf = await (await fetch("/api/wind/bundle")).arrayBuffer();  // 无头验收/降级路径
    } else {
      buf = await fetchArrayBufferProgress("/api/wind/bundle", function (got, total) {
        var mb = (got / 1048576).toFixed(1);
        var tm = total > 0 ? (" / " + (total / 1048576).toFixed(0)) : "";
        $("loading-text").textContent = "正在加载风振数据束 … " + mb + tm + " MB";
      });
    }
    var B = state;
    var bd = parseBundle(buf);
    B.start_s = bd.start_s; B.wind = bd.wind; B.wind_max = bd.wind_max;
    B.wdir = bd.wdir; B.wdir_std = bd.wdir_std; B.acc_rms = bd.acc_rms;
    B.peaks = bd.peaks; B.m_a = bd.m_a;
    B.trackLen = bd.track_len || null;
    B.trackEvents = (bd.header_ && bd.header_.events) || [];
    B.trackMinLen = (bd.header_ && bd.header_.track_min_len) || 20;
    B.peaksShape = bd.peaks_shape || null;
    B.press_rms = bd.press_rms; B.gnss_rms = bd.gnss_rms;
    B.n = bd.start_s.length;

    var sorted = Array.prototype.slice.call(B.acc_rms).sort();
    B.accMedian = sorted[Math.floor(sorted.length / 2)] || 1;
    var ps = Array.prototype.slice.call(B.press_rms).sort();
    B.pressScale = ps[Math.floor(ps.length * 0.99)] || 1;
    var gs = Array.prototype.slice.call(B.gnss_rms).sort();
    B.gnssScale = gs[Math.floor(gs.length * 0.99)] || 1;
    var amps = [];
    for (var i = 0; i < B.peaks.length; i += 4) if (B.peaks[i + 1] === B.peaks[i + 1]) amps.push(B.peaks[i + 1]);
    amps.sort(function (a, b) { return a - b; });
    B.peakAmpScale = amps.length ? amps[Math.floor(amps.length * 0.98)] : 1;
    B.maSmooth = smoothSparse(B.m_a, 720, 3);   // ±12 h 滑动中位（第 4 晚）

    // 3D
    B.viewer = new Viewer3D($("viewport-w"));
    var gbuf = await (await fetch("/api/geometry")).arrayBuffer();
    var g = parsePack(gbuf);
    var n3 = g.header.node_count * 3;
    B.coords = new Float32Array(gbuf, g.off, n3);   // 节点坐标（最大响应点/局部放大用）
    B.viewer.setGeometry(
      B.coords,
      new Uint32Array(gbuf, g.off + n3 * 4, g.header.tri_index_count),
      new Uint32Array(gbuf, g.off + n3 * 4 + g.header.tri_index_count * 4,
                      g.header.line_index_count));

    // 模态基（前 20 阶，逐阶归一化到 |φ|max=1）
    var mbuf = await (await fetch("/api/modebase")).arrayBuffer();
    var mp = parsePack(mbuf);
    var nm = mp.header.n_modes, nn = mp.header.node_count;
    B.phiFreqs = mp.header.freqs;
    B.phiNorm = [];
    for (var m2 = 0; m2 < nm; m2++) {
      var arr = new Float32Array(mbuf, mp.off + m2 * nn * 3 * 4, nn * 3);
      var mx = 0;
      for (var v = 0; v < nn; v++) {
        var mags = Math.sqrt(arr[v * 3] * arr[v * 3] + arr[v * 3 + 1] * arr[v * 3 + 1] + arr[v * 3 + 2] * arr[v * 3 + 2]);
        if (mags > mx) mx = mags;
      }
      var nor = new Float32Array(nn * 3);
      for (v = 0; v < nn * 3; v++) nor[v] = arr[v] / (mx || 1);
      B.phiNorm.push(nor);
    }
    synthTick._scratch = new Float32Array(nn * 3);

    // 传感器标记
    var sensors = await getJSON("/api/wind/sensors");
    B.sensorRows = sensors.map(function (s) { return s.row >= 0 ? s.row : null; });
    if (B.sensorRows.some(function (r) { return r === null; })) B.sensorRows = null;
    var geoS = new THREE.SphereGeometry(B.viewer.radius * 0.006, 12, 12);
    sensors.forEach(function (s) {
      var mat = new THREE.MeshBasicMaterial({ color: s.gnss > 0 ? 0xf59e0b : 0x38bdf8 });
      var mesh = new THREE.Mesh(geoS, mat);
      mesh.position.set(s.x, s.y, s.z);
      B.viewer.scene.add(mesh);
    });
    // 最大响应点标记（第 17 晚：红色脉冲球，随窗跟踪）
    B.maxMarker = new THREE.Mesh(
      new THREE.SphereGeometry(B.viewer.radius * 0.009, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0xef4444 }));
    B.maxMarker.visible = false;
    B.viewer.scene.add(B.maxMarker);
    B.homeView = { pos: B.viewer.camera.position.clone(),
                   tgt: B.viewer.controls.target.clone() };

    // 现场照片挂接（第 17 晚框架）：manifest 有点位才显示
    B.photoMarkers = [];
    try {
      var photos = await getJSON("/api/wind/photos");
      photos.forEach(function (ph) {
        var mk = new THREE.Mesh(
          new THREE.OctahedronGeometry(B.viewer.radius * 0.008),
          new THREE.MeshBasicMaterial({ color: 0xc084fc }));
        mk.position.set(ph.x, ph.y, ph.z);
        mk.userData.photo = ph;
        B.viewer.scene.add(mk);
        B.photoMarkers.push(mk);
      });
    } catch (ePh) { /* 无照片点位时框架静默 */ }

    // 局部聚焦（双击模型）与照片点选（单击标记）
    var ray = new THREE.Raycaster(), dom = B.viewer.renderer.domElement;
    function pickAt(evt, targets) {
      var r = dom.getBoundingClientRect();
      ray.setFromCamera(
        { x: ((evt.clientX - r.left) / r.width) * 2 - 1,
          y: -((evt.clientY - r.top) / r.height) * 2 + 1 },
        B.viewer.camera);
      var hits = ray.intersectObjects(targets, false);
      return hits.length ? hits[0] : null;
    }
    dom.addEventListener("click", function (evt) {
      if (!B.photoMarkers.length) return;
      var h = pickAt(evt, B.photoMarkers);
      if (h) showPhoto(h.object.userData.photo);
    });
    dom.addEventListener("dblclick", function (evt) {
      var h = pickAt(evt, B.viewer.meshes);
      var ctr = B.viewer.controls;
      if (h) {
        ctr.target.copy(h.point);
        var dir = B.viewer.camera.position.clone().sub(h.point)
          .normalize().multiplyScalar(B.viewer.radius * 0.45);
        B.viewer.camera.position.copy(h.point.clone().add(dir));
        showToast("已聚焦局部（双击空白处复位）");
      } else {
        B.viewer.camera.position.copy(B.homeView.pos);
        ctr.target.copy(B.homeView.tgt);
      }
    });
    var pc = $("photo-close");
    if (pc) pc.addEventListener("click", function () {
      $("photo-panel").hidden = true;
    });

    // 控件
    var pLabels = [], k;
    for (k = 1; k <= 12; k++) pLabels.push("P" + k);
    B.pressBars = makeBars("press-bars", pLabels, "bar-fill");
    var gLabels = [];
    for (k = 1; k <= 7; k++) gLabels.push("G" + k);
    B.gnssBars = makeBars("gnss-bars", gLabels, "bar-fill amber");

    var sel = $("event-select");
    sel.appendChild(new Option("（选择大风时段）", ""));
    var idxs = [];
    for (i = 0; i < B.n; i++) idxs.push(i);
    idxs.sort(function (a, b) { return B.wind[b] - B.wind[a]; });
    var picked = [];
    for (i = 0; i < idxs.length && picked.length < 12; i++) {
      var w0 = idxs[i], ok = true;
      for (k = 0; k < picked.length; k++) if (Math.abs(picked[k] - w0) < 60) { ok = false; break; }
      if (ok) picked.push(w0);
    }
    picked.forEach(function (w0) {
      sel.appendChild(new Option(
        fmtTime(B.start_s[w0]) + " · " + B.wind[w0].toFixed(2) + " m/s", String(w0)));
    });
    sel.addEventListener("change", function () {
      if (sel.value !== "") { state.win = parseInt(sel.value, 10); onWinChange(); }
    });

    $("play-btn").addEventListener("click", function () { setPlaying(!state.playing); });
    $("speed-select").addEventListener("change", function () {
      state.speed = parseFloat(this.value);
    });
    $("speed-slider-w").addEventListener("input", function () {
      state.visSpeed = parseFloat(this.value);   // 动画速度（视觉频率 = 实测频率 × visSpeed）
    });

    // 精华段清单与视图切换（第 3 晚）
    var segs = await getJSON("/api/wind/segments");
    var segSel = $("segment-select");
    segSel.appendChild(new Option("（选择精华段加载）", ""));
    segs.forEach(function (s) {
      segSel.appendChild(new Option(
        s.name + " · " + fmtTime(s.start_s) + " · " + (s.dur_s / 60).toFixed(0) + " min",
        s.name));
    });
    segSel.addEventListener("change", function () {
      if (segSel.value === "") unloadSegment();   // 选空 = 卸载精华段
      else loadSegment(segSel.value);
    });
    var vtBtns = document.querySelectorAll(".vt-btn");
    for (var bi = 0; bi < vtBtns.length; bi++) {
      vtBtns[bi].addEventListener("click", function () { setViewMode(this.dataset.mode); });
    }
    $("hl-prev").addEventListener("click", function () { jumpHighlight(-1); });
    $("hl-next").addEventListener("click", function () { jumpHighlight(1); });
    $("pod-toggle").addEventListener("click", function () {
      state.podOn = !state.podOn;
      this.classList.toggle("off", !state.podOn);
    });
    // POD 模态表（第 7 晚，可选；无文件则静默降级为 12 点插值）
    try {
      state.pod = await getJSON("/api/wind/pod");
    } catch (e) {
      state.pod = null;
    }
    // POD 时程系数（第 8 晚：整体性脉动指数）
    try {
      var sb = await (await fetch("/api/wind/podseries")).arrayBuffer();
      var sp = parsePack(sb);
      var aAll = new Float32Array(sb, sp.off, sp.header.n_win * sp.header.k);
      B.podA1 = new Float32Array(sp.header.n_win);
      for (var pi = 0; pi < sp.header.n_win; pi++) B.podA1[pi] = aAll[pi * sp.header.k];
    } catch (e) {
      B.podA1 = null;
    }
    // 逐窗风压峰值（第 9 晚：高光事件合集）
    try {
      var mb = await (await fetch("/api/wind/pressmax")).arrayBuffer();
      var mp2 = parsePack(mb);
      B.pmax = new Float32Array(mb, mp2.off,
                                mp2.header.n_win * mp2.header.n_press);
      computeEvents();
    } catch (e) {
      B.pmax = null;
    }
    drawScatter();
    computeCorrCard();
    drawDirChart();
    drawPodWindChart();

    // 模块从 hidden 恢复后（单壳切页）重绘全部 2D 画布（冻结守卫首次未冻，
    // 可见后才冻并画）——配合 boot.js 切页时广播的 resize
    window.addEventListener("resize", function () {
      if (!state.n || $("main-w").hidden) return;
      onWinChange();
      drawScatter();
      drawDirChart();
      drawPodWindChart();
      drawSensorChart();
    });
    var dragging = false;
    $("timeline").addEventListener("pointerdown", function (e) {
      dragging = true;
      this.setPointerCapture(e.pointerId);   // 拖出窗口外也不卡死
      seekTimeline(e);
    });
    $("timeline").addEventListener("pointermove", function (e) { if (dragging) seekTimeline(e); });
    $("timeline").addEventListener("pointerup", function () { dragging = false; });

    setPlaying(true);
    var qp = new URLSearchParams(location.search);  // 深链接：?win=34988&play=0&seg=w5&mode=press
    if (qp.has("win")) {
      var w0 = parseInt(qp.get("win"), 10);
      if (w0 >= 0 && w0 < state.n) state.win = w0;
    }
    if (qp.get("play") === "0") setPlaying(false);
    // CDP 驱动复核用调试钩子（仅 ?debug=1 暴露）
    if (qp.has("debug")) {
      window.__ml = { state: state, loadSegment: loadSegment,
                      setViewMode: setViewMode, unloadSegment: unloadSegment,
                      onWinChange: onWinChange };
    }
    onWinChange();
    if (qp.has("seg")) {
      loadSegment(qp.get("seg")).then(function () {
        var m = qp.get("mode");
        if (m === "both" || m === "press" || m === "shape" || m === "comp") setViewMode(m);
      });
    }
    requestAnimationFrame(synthTick);
  } catch (e) {
    showToast("风振监测实验室初始化失败：" + e.message);
    throw e;
  }
}

/* ================= 第 3 晚：精华段 / 视图切换 / 实测时程 / 风析 ================= */

function setViewMode(m) {
  if (m !== "shape" && m !== "comp" && !state.seg) {
    showToast("请先在左栏选择精华段（10 Hz 序列）");
    return;
  }
  state.viewMode = m;
  var btns = document.querySelectorAll(".vt-btn");
  for (var i = 0; i < btns.length; i++) {
    btns[i].classList.toggle("active", btns[i].dataset.mode === m);
  }
  state.viewer.setPressMix(m === "press" ? 1.0 : (m === "both" ? 0.65 : 0));
  if (m === "shape") {
    var z = synthTick._pressScratch;
    if (z) { z.fill(0); state.viewer.setPress(z); }
  }
  if (m === "comp") buildComposite();
  updateSynthNote();
}

/* 右上标注：把当前模式的内容说清（几阶/是否代表性合成） */
function updateSynthNote() {
  var k = state.modes ? state.modes.length : 0;
  var hasC = state.modes && state.modes.some(function (md) { return md.q >= 1.5; });
  var m = state.viewMode, t;
  if (m === "comp") {
    t = "复合振型（能量加权 · K=" + k + " 阶）";
    if (k === 1) t += "·本窗仅 1 阶识别峰";
    if (k === 0) t = "复合振型（本窗无识别峰，结构静息）";
  } else if (m === "press") {
    t = state.podOn ? "脉动风压云图（POD 重构）" : "脉动风压云图（12 点插值）";
  } else if (m === "both") {
    t = "叠加（振型 × 风压 · K=" + k + " 阶）";
  } else {
    t = "频域重构（代表性合成 · K=" + k + " 阶）";
  }
  if (hasC && k > 0 && m !== "press") t += " · 含 C 级低置信峰";
  var hasBlend = state.modes && state.modes.some(function (md) { return md.phiBlend; });
  if (hasBlend && k > 0 && m !== "press") t += " · 测点形状混合";
  $("synth-note").textContent = t;
}

/* 复合振型（能量加权，φ 同相，u_c = Σ w_j·φ_j/√2）：静态形态，换窗交叉淡化 */
function buildComposite() {
  var B = state;
  if (!B.viewer || !synthTick._scratch) return;
  var comp = new Float32Array(synthTick._scratch.length);
  var wi = Math.max(0, Math.min(B.n - 1, Math.floor(B.win)));
  var intensity = Math.min(Math.max(B.acc_rms[wi] / B.accMedian, 0.35), 1.6);
  for (var k = 0; k < B.modes.length; k++) {
    var md = B.modes[k];
    var wgt = (md.wTarget !== undefined ? md.wTarget : 0) * intensity / Math.SQRT2;
    if (wgt <= 0) continue;
    var ph = md.phiBlend || B.phiNorm[md.phi];
    for (var v = 0; v < comp.length; v++) comp[v] += wgt * ph[v];
  }
  B.compBase = comp;   // 呼吸动画由 synthTick 渲染
}

async function loadSegment(name) {
  showToast("正在加载精华段 " + name + " …");
  var token = ++state._segToken;   // 竞态守卫：快速连选时后到者胜
  try {
    var buf = await (await fetch("/api/wind/segment?name=" + name)).arrayBuffer();
    if (token !== state._segToken) return;   // 已有更新的选择，丢弃本响应
    var p = parsePack(buf);
    var h = p.header, T = h.n_t, off = p.off;
    state.seg = {
      name: name, fs: h.fs, T: T, start_s: h.start_s,
      acc: new Float32Array(buf, off, 36 * T),
      gnss: new Float32Array(buf, off + 36 * T * 4, 21 * T),
      press: new Float32Array(buf, off + 57 * T * 4, 12 * T),
      speed: new Float32Array(buf, off + 69 * T * 4, T),
      direct: new Float32Array(buf, off + 70 * T * 4, T)
    };
    state.segT = 0;
    if (!state.pressW) {
      var wbuf = await (await fetch("/api/wind/press_weights")).arrayBuffer();
      var wp = parsePack(wbuf);
      state.pressN = wp.header.node_count;
      state.pressW = new Float32Array(wbuf, wp.off,
                                      wp.header.node_count * wp.header.n_press);
      synthTick._pressScratch = new Float32Array(state.pressN);
    }
    if (state.pod && !state.podWP) {   // WP = W·V（POD 模态上膜，第 7 晚）
      var K2 = state.pod.k, V2 = state.pod.vecs, W2 = state.pressW;
      var WP2 = new Float32Array(state.pressN * K2);
      for (var ni2 = 0; ni2 < state.pressN; ni2++) {
        for (var k3 = 0; k3 < K2; k3++) {
          var s4 = 0;
          for (var c3 = 0; c3 < 12; c3++) s4 += W2[ni2 * 12 + c3] * V2[c3][k3];
          WP2[ni2 * K2 + k3] = s4;
        }
      }
      state.podWP = WP2;
      var cum = 0;
      for (k3 = 0; k3 < K2; k3++) cum += state.pod.fracs[k3];
      var pt = $("pod-toggle");
      pt.textContent = "POD K=" + K2 + " · " + Math.round(cum * 100) + "%";
      pt.hidden = false;
    }
    var abs = [];
    var pr = state.seg.press;
    for (var i = 0; i < pr.length; i += 25) abs.push(Math.abs(pr[i]));
    abs.sort(function (a, b) { return a - b; });
    state.pressScaleSeg = abs[Math.floor(abs.length * 0.99)] || 1;
    state.highlights = computeHighlights(pr, state.seg.T, state.seg.fs);
    // 高光分类（第 8 晚）：P1 整体脉动主导 vs 局部脉动
    state.hlClass = {};
    if (state.pod && state.pod.vecs) {
      var V1 = [];
      for (var c9 = 0; c9 < 12; c9++) V1.push(state.pod.vecs[c9][0]);
      for (var h9 = 0; h9 < state.highlights.length; h9++) {
        var fr9 = state.highlights[h9], a1 = 0, tot = 0;
        for (c9 = 0; c9 < 12; c9++) {
          var pv9 = state.seg.press[c9 * state.seg.T + fr9];
          a1 += V1[c9] * pv9;
          tot += pv9 * pv9;
        }
        state.hlClass[state.highlights[h9]] =
          (tot > 0 && a1 * a1 / tot >= 0.5) ? "p1" : "local";
      }
    }
    updateHlLabel();
    setPlaying(false);  // 段加载后由 10 Hz 段驱动主时间轴，主播放暂停（双向联动）

    var sel = $("sensor-select");
    sel.innerHTML = "";
    var k;
    for (k = 0; k < 12; k++) {
      sel.appendChild(new Option("加速度 点" + (k + 1) + "（竖向）", "a" + k));
    }
    for (k = 0; k < 7; k++) {
      sel.appendChild(new Option("GNSS 点" + (k + 1) + "（竖向）", "g" + k));
    }
    setViewMode("press");
    showToast("精华段 " + name + " 已加载");
  } catch (e) {
    showToast("精华段加载失败：" + e.message);
  }
}

function drawSensorChart() {
  if (!state.seg) return;
  var sel = $("sensor-select");
  if (!sel.value) return;
  var kind = sel.value[0], idx = parseInt(sel.value.slice(1), 10);
  var seg = state.seg;
  var ch = idx * 3 + 2;  // 竖向 = 点主序第 3 分量（acc/gnss 同口径）
  var src = kind === "a" ? seg.acc : seg.gnss;
  var cv = $("sensor-chart");
  var wh = fitCanvas(cv); if (!wh) return;
  var W = wh[0], H = wh[1], ctx = wh[2];
  var T = seg.T, off = ch * T;
  var lo = 1e30, hi = -1e30, i;
  for (i = 0; i < T; i += 10) {
    var v = src[off + i];
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  var pad = (hi - lo) * 0.1 + 1e-9;
  lo -= pad; hi += pad;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = kind === "a" ? "#38bdf8" : "#f59e0b";
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (i = 0; i < T; i += Math.max(1, Math.floor(T / W))) {
    var x = i / T * W, y = H - (src[off + i] - lo) / (hi - lo) * H;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
  var cx = state.segT / T * W;
  ctx.fillStyle = "#fff";
  ctx.fillRect(cx - 1, 0, 2, H);
  // 高光瞬间刻度（底部小竖线，整体脉动=琥珀 / 局部=青）
  var hs = state.highlights || [];
  for (var hi2 = 0; hi2 < hs.length; hi2++) {
    var cls = state.hlClass ? state.hlClass[hs[hi2]] : null;
    ctx.fillStyle = cls === "local" ? "rgba(56, 189, 248, 0.7)"
                                    : "rgba(245, 158, 11, 0.65)";
    ctx.fillRect(hs[hi2] / T * W, H - 5, 1, 5);
  }
  ctx.fillStyle = "rgba(219, 228, 245, 0.6)";
  ctx.font = "10px sans-serif";
  ctx.fillText((state.segT / state.seg.fs).toFixed(1) + " s / " +
               (T / state.seg.fs).toFixed(0) + " s", 6, 12);
  updateHlLabel();
}

/* 风析：风速-响应散点 + 分箱中位线（数据束直出，静态绘制一次） */
function drawScatter() {
  var cv = $("wind-scatter");
  var wh = fitCanvas(cv); if (!wh) return;
  var W = wh[0], H = wh[1], ctx = wh[2];
  var n = state.n, wind = state.wind, acc = state.acc_rms;
  var xmax = 0, ymax = 0, i;
  for (i = 0; i < n; i += 50) {
    if (wind[i] > xmax) xmax = wind[i];
    if (acc[i] > ymax) ymax = acc[i];
  }
  xmax *= 1.05; ymax *= 1.05;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "rgba(56, 189, 248, 0.13)";
  for (i = 0; i < n; i += 5) {
    var x = wind[i] / xmax * W, y = H - acc[i] / ymax * H;
    ctx.fillRect(x, y, 1.4, 1.4);
  }
  // 分箱中位
  var bins = 10;
  ctx.strokeStyle = "#f59e0b";
  ctx.lineWidth = 2;
  ctx.beginPath();
  var first = true;
  for (var b = 0; b < bins; b++) {
    var lo2 = b / bins * xmax, hi2 = (b + 1) / bins * xmax;
    var vals = [];
    for (i = 0; i < n; i += 7) {
      if (wind[i] >= lo2 && wind[i] < hi2) vals.push(acc[i]);
    }
    if (vals.length < 8) continue;
    vals.sort(function (a2, b2) { return a2 - b2; });
    var med = vals[Math.floor(vals.length / 2)];
    var xm = (lo2 + hi2) / 2 / xmax * W, ym = H - med / ymax * H;
    if (first) { ctx.moveTo(xm, ym); first = false; } else ctx.lineTo(xm, ym);
  }
  ctx.stroke();
  // 定量读数（第 4 晚）：线性与 log-log 相关（log-log 只取风>0.5 的窗）
  if (!state._scatterStats) {
    var sx = [], sy = [], lx = [], ly = [];
    for (i = 0; i < n; i += 5) {
      sx.push(wind[i]); sy.push(acc[i]);
      if (wind[i] > 0.5) { lx.push(Math.log(wind[i])); ly.push(Math.log(acc[i])); }
    }
    state._scatterStats = "r(线性)=" + pearson(sx, sy) +
      " · r(log-log, 风>0.5)=" + pearson(lx, ly);
  }
  ctx.fillStyle = "rgba(219, 228, 245, 0.75)";
  ctx.font = "11px sans-serif";
  ctx.fillText(state._scatterStats, 8, 14);
}

/* ---------- 风析 · 相关性卡（第 6 晚，init 一次性计算） ---------- */
function meanCols(arr, ncol) {
  var n = arr.length / ncol, out = new Float32Array(n);
  for (var i = 0; i < n; i++) {
    var s = 0, c = 0;
    for (var k = 0; k < ncol; k++) {
      var v = arr[i * ncol + k];
      if (v === v) { s += v; c++; }
    }
    out[i] = c ? s / c : NaN;
  }
  return out;
}

function computeCorrCard() {
  var B = state;
  var press = meanCols(B.press_rms, 12);
  var gnss = meanCols(B.gnss_rms, 21);
  var amp = new Float32Array(B.n), i, k;
  for (i = 0; i < B.n; i++) {
    var s = 0, c = 0;
    for (k = 0; k < 5; k++) {
      var v = B.peaks[i * 20 + k * 4 + 1];
      if (v === v) { s += v; c++; }
    }
    amp[i] = c ? s / c : 0;
  }
  $("c-wg").textContent = pearson(B.wind, gnss);
  $("c-pg").textContent = pearson(press, gnss);
  $("c-wp").textContent = pearson(B.wind, press);
  var idx = [];
  for (i = 0; i < B.n; i++) if (amp[i] > 0) idx.push(i);
  var sub = function (a) { return idx.map(function (j) { return a[j]; }); };
  $("c-mg").textContent = idx.length > 100 ? pearson(sub(amp), sub(gnss)) : "—";
  $("c-mp").textContent = idx.length > 100 ? pearson(sub(amp), sub(press)) : "—";
}

/* ---------- 高光瞬间（精华段 |脉动风压| 峰导航） ---------- */
function computeHighlights(pr, T, fs) {
  var prom = new Float32Array(T);
  var i, c;
  for (i = 0; i < T; i++) {
    var mx = 0;
    for (c = 0; c < 12; c++) {
      var v = Math.abs(pr[c * T + i]);
      if (v > mx) mx = v;
    }
    prom[i] = mx;
  }
  var sorted = Array.prototype.slice.call(prom).sort(function (a, b) { return a - b; });
  var med = sorted[Math.floor(T / 2)];
  var thr = Math.max(med * 3, sorted[Math.floor(T * 0.75)]);
  var out = [], last = -1e9;
  for (i = 1; i < T - 1; i++) {
    if (prom[i] >= thr && prom[i] >= prom[i - 1] && prom[i] >= prom[i + 1] &&
        i - last >= 2 * fs) {
      out.push(i);
      last = i;
    }
  }
  return out;
}

function updateHlLabel() {
  var hs = state.highlights || [], lbl = "—";
  if (hs.length) {
    var k = 0;
    for (var i = 0; i < hs.length; i++) if (hs[i] <= state.segT + 0.5) k = i + 1;
    lbl = (k || "·") + " / " + hs.length;
    if (k > 0 && state.hlClass) {
      var cls = state.hlClass[hs[k - 1]];
      if (cls === "p1") lbl += " · 整体";
      else if (cls === "local") lbl += " · 局部";
    }
  }
  $("hl-label").textContent = lbl;
}

function jumpHighlight(dir) {
  var hs = state.highlights || [];
  if (!hs.length) {
    showToast("本段无显著脉动峰（或尚未加载精华段）");
    return;
  }
  var cur = state.segT, i;
  if (dir > 0) {
    for (i = 0; i < hs.length; i++) {
      if (hs[i] > cur + 1) { state.segT = hs[i]; updateHlLabel(); return; }
    }
    state.segT = hs[0];
  } else {
    for (i = hs.length - 1; i >= 0; i--) {
      if (hs[i] < cur - 1) { state.segT = hs[i]; updateHlLabel(); return; }
    }
    state.segT = hs[hs.length - 1];
  }
  updateHlLabel();
}

/* ---------- 风析 · 分风向响应（第 8 晚） ---------- */
function drawDirChart() {
  var cv = $("dir-chart");
  if (!cv) return;
  var wh = fitCanvas(cv); if (!wh) return;
  var W = wh[0], H = wh[1], ctx = wh[2];
  var B = state, n = B.n;
  var SEC = 8, bins = [[0, 2], [2, 4], [4, 6], [6, 8], [8, 14]];
  var counts = new Array(SEC).fill(0), med = [], s, b, i;
  for (s = 0; s < SEC; s++) med.push(bins.map(function () { return []; }));
  for (i = 0; i < n; i += 2) {
    var wd = B.wdir[i];
    if (wd !== wd) continue;               // NaN 风向防护
    s = Math.floor(((wd % 360) + 22.5) / 45) % SEC;
    counts[s]++;
    var wv = B.wind[i];
    for (b = 0; b < bins.length; b++) {
      if (wv >= bins[b][0] && wv < bins[b][1]) { med[s][b].push(B.acc_rms[i]); break; }
    }
  }
  var ymax = 0;
  var accSorted = Array.prototype.slice.call(B.acc_rms).sort(function (x, y) { return x - y; });
  ymax = (accSorted[Math.floor(accSorted.length * 0.98)] || 1) * 1.05;  // p98 防离群压扁
  ctx.clearRect(0, 0, W, H);
  var colors = ["#38bdf8", "#f59e0b", "#34d399", "#f472b6",
                "#a78bfa", "#fbbf24", "#22d3ee", "#fb7185"];
  var drawn = 0;
  for (s = 0; s < SEC; s++) {
    if (counts[s] < 1500) continue;
    ctx.strokeStyle = colors[s];
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    var pen = false;
    for (b = 0; b < bins.length; b++) {
      var v = med[s][b];
      if (v.length < 20) continue;
      v.sort(function (x, y) { return x - y; });
      var m = v[Math.floor(v.length / 2)];
      var x = (bins[b][0] + bins[b][1]) / 2 / 14 * W, y = H - m / ymax * H;
      if (pen) ctx.lineTo(x, y); else { ctx.moveTo(x, y); pen = true; }
    }
    ctx.stroke();
    ctx.fillStyle = colors[s];
    ctx.font = "10px sans-serif";
    ctx.fillText(s * 45 + "°", 8 + drawn * 34, 12);
    drawn++;
  }
}

/* ---------- 风析 · 整体性脉动传递（第 9 晚） ---------- */
function drawPodWindChart() {
  var cv = $("pod-wind-chart");
  if (!cv || !state.podA1) return;
  var wh = fitCanvas(cv); if (!wh) return;
  var W = wh[0], H = wh[1], ctx = wh[2];
  var B = state, n = B.n, i;
  var xmax = 0, ymax = 0;
  var ws = [], ps = [];
  for (i = 0; i < n; i += 20) {
    if (B.wind[i] > xmax) xmax = B.wind[i];
    if (B.podA1[i] > ymax) ymax = B.podA1[i];
  }
  xmax *= 1.05; ymax *= 1.05;
  ctx.clearRect(0, 0, W, H);
  // 散点（抽稀）
  ctx.fillStyle = "rgba(52, 211, 153, 0.13)";
  for (i = 0; i < n; i += 5) {
    var x = B.wind[i] / xmax * W, y = H - B.podA1[i] / ymax * H;
    ctx.fillRect(x, y, 1.4, 1.4);
  }
  // 分箱中位 + 幂律拟合（log-log 最小二乘，风>0.5）
  var NB = 10, bstats = [], lx = [], ly = [];
  for (var b = 0; b < NB; b++) {
    var lo = b / NB * xmax, hi = (b + 1) / NB * xmax, vals = [];
    for (i = 0; i < n; i += 7) {
      if (B.wind[i] >= lo && B.wind[i] < hi) {
        vals.push(B.podA1[i]);
        if (B.wind[i] > 0.5 && B.podA1[i] > 0) {
          lx.push(Math.log(B.wind[i])); ly.push(Math.log(B.podA1[i]));
        }
      }
    }
    if (vals.length >= 8) {
      vals.sort(function (a2, b2) { return a2 - b2; });
      bstats.push({ x: (lo + hi) / 2, y: vals[Math.floor(vals.length / 2)] });
    }
  }
  ctx.strokeStyle = "#34d399";
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (i = 0; i < bstats.length; i++) {
    var xx = bstats[i].x / xmax * W, yy = H - bstats[i].y / ymax * H;
    if (i === 0) ctx.moveTo(xx, yy); else ctx.lineTo(xx, yy);
  }
  ctx.stroke();
  // 斜率读数
  var mx = 0, my = 0, sxy = 0, sxx = 0;
  for (i = 0; i < lx.length; i++) { mx += lx[i]; my += ly[i]; }
  mx /= lx.length; my /= lx.length;
  for (i = 0; i < lx.length; i++) {
    sxy += (lx[i] - mx) * (ly[i] - my);
    sxx += (lx[i] - mx) * (lx[i] - mx);
  }
  var slope = sxy / sxx;
  ctx.fillStyle = "rgba(219, 228, 245, 0.75)";
  ctx.font = "11px sans-serif";
  ctx.fillText("幂律斜率 ≈ " + slope.toFixed(2) + "（V² 律 = 2.0）", 8, 14);
}

/* ---------- 高光事件合集（第 9 晚，叠加在主时间轴上） ---------- */
function computeEvents() {
  var B = state;
  if (!B.pmax || !B.podA1) return;
  var n = B.n, i, c;
  var strength = new Float32Array(n);
  for (i = 0; i < n; i++) {
    var mx = 0;
    for (c = 0; c < 12; c++) {
      var v = B.pmax[i * 12 + c];
      if (v > mx) mx = v;
    }
    strength[i] = mx;
  }
  var sorted = Array.prototype.slice.call(strength).sort(function (a, b) { return a - b; });
  var thr = sorted[Math.floor(sorted.length * 0.995)];
  // P1 能量占比：a1_rms² / 风压能量（12 通道 press_rms² 均值）
  var events = [], nP1 = 0;
  for (i = 0; i < n; i++) {
    if (strength[i] < thr) continue;
    var e2 = 0, cnt = 0;
    for (c = 0; c < 12; c++) {
      var rv = B.press_rms[i * 12 + c];
      if (rv === rv) { e2 += rv * rv; cnt++; }
    }
    e2 = cnt ? e2 / cnt : 0;
    var share = e2 > 0 ? (B.podA1[i] * B.podA1[i]) / e2 : 0;
    var cls = share >= 0.5 ? "p1" : "local";
    if (cls === "p1") nP1++;
    events.push({ w: i, cls: cls, s: strength[i] });
  }
  B.events = events;
  B.eventStats = { n: events.length, p1: nP1, local: events.length - nP1 };
}

/* ================= QA 自测探针（?qa=1 触发；逐段逐模式采样并报告） ================= */
function _qaSleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
/* rAF 帧步进：与 synthTick 同一时钟（虚拟时间下 setTimeout 会让 rAF 停摆） */
function _qaFrames(n) {
  return new Promise(function (r) {
    var c = 0;
    function step() { if (++c >= n) r(); else requestAnimationFrame(step); }
    requestAnimationFrame(step);
  });
}
function _qaWait(cond, ms, step) {
  var t0 = performance.now();
  return new Promise(function (resolve) {
    var h = setInterval(function () {
      if (cond() || performance.now() - t0 > ms) { clearInterval(h); resolve(); }
    }, step || 200);
  });
}

export async function runQa() {
  var log = [];
  var logEl = document.createElement("pre");
  logEl.id = "qa-log";
  logEl.style.cssText = "position:fixed;left:8px;bottom:8px;z-index:999;background:#0a0e1a;" +
    "color:#7ee787;font-size:11px;padding:10px;border:1px solid #1e2a45;max-width:92%;" +
    "max-height:50%;overflow:auto;white-space:pre-wrap;";
  document.body.appendChild(logEl);
  function L(s) { log.push(s); logEl.textContent = log.join("\n"); console.log("[QA] " + s); }

  await _qaWait(function () { return state.n > 0; }, 20000);
  if (!state.n) { L("FAIL: 数据束未就绪"); return; }
  L("数据束就绪 n=" + state.n);

  var segs = ["w1", "w5", "w7", "w10"];
  for (var si = 0; si < segs.length; si++) {
    var seg = segs[si];
    L(seg + ": 加载中…");
    await loadSegment(seg);
    L(seg + ": loadSegment 返回, seg=" + (state.seg ? "有" : "无"));
    await _qaWait(function () { return state.seg !== null; }, 8000);
    if (!state.seg) { L(seg + ": 段加载失败"); continue; }
    var modes0 = ["shape", "press", "both", "comp"];
    for (var mi = 0; mi < modes0.length; mi++) {
      L("  模式 " + modes0[mi] + " 开始");
      setViewMode(modes0[mi]);
      var windVals = [], pressV = [], segT0 = state.segT, maxU = 0;
      for (var t = 0; t < 8; t++) {
        await _qaFrames(15);   // 每步 15 帧（≈0.25 s），与 synthTick 同时钟
        var wtxt = $("wind-speed-big").textContent;
        windVals.push(parseFloat(wtxt));
        var pb = document.querySelector("#press-bars .bar-row:nth-child(4) .bar-fill");
        if (pb && pb.style.width) pressV.push(parseFloat(pb.style.width));
        if (state.viewer && state.viewer.shapeNew) {
          for (var q2 = 0; q2 < 3000; q2 += 10) {
            var av = Math.abs(state.viewer.shapeNew[q2]);
            if (av > maxU) maxU = av;
          }
        }
      }
      var wVar = Math.max.apply(null, windVals) - Math.min.apply(null, windVals);
      var pVar = pressV.length ? (Math.max.apply(null, pressV) - Math.min.apply(null, pressV)) : NaN;
      var adv = state.segT - segT0;
      L(seg + "/" + modes0[mi] + ": seg推进=" + adv.toFixed(0) + "帧 风速波动=" +
        wVar.toFixed(2) + " 风压条波动=" + (pVar === pVar ? pVar.toFixed(1) : "NaN") +
        "% 模态数=" + state.modes.length + " 最大位移=" + maxU.toExponential(1) +
        " 窗=" + Math.floor(state.win));
    }
    unloadSegment();
    L(seg + ": 卸载回主播放 OK, win=" + Math.floor(state.win));
  }
  L("QA 完成");
}

/* 段内风速微趋势（第 15 晚） */
function drawWindMicro(fr) {
  var cv = $("wind-micro");
  if (!cv || !state.seg) return;
  var wh = fitCanvas(cv); if (!wh) return;
  var W = wh[0], H = wh[1], ctx = wh[2];
  var sp = state.seg.speed;
  var i0 = Math.max(0, fr - 599);
  var lo = 1e9, hi = -1e9, i;
  for (i = i0; i <= fr; i++) {
    if (sp[i] < lo) lo = sp[i];
    if (sp[i] > hi) hi = sp[i];
  }
  var pad = (hi - lo) * 0.15 + 0.05;
  lo -= pad; hi += pad;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 1;
  ctx.beginPath();
  var n = fr - i0 + 1;
  for (var j = 0; j < n; j++) {
    var x = j / Math.max(n - 1, 1) * W;
    var y = H - (sp[i0 + j] - lo) / (hi - lo) * H;
    if (j === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();
  ctx.fillStyle = "rgba(219,228,245,0.6)";
  ctx.font = "10px sans-serif";
  ctx.fillText("段内 60 s", 6, 11);
}
