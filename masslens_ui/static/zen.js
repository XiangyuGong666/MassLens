/* 膜 · 纯净模式（第 19 晚）：全屏膜面 + 连续模态叠加动画；
   点击膜面 → 时间冻结，三个主导模态平滑炸开（各自独立 GPU 振动 + 权重标注）；
   再点 → 平滑复合恢复。数据与双实验室同束（/api/geometry /modebase /wind/bundle）。 */

import * as THREE from "three";
import { Viewer3D } from "/static/viewer3d.js?v=11";
import { $, showToast, parsePack, getJSON, fetchArrayBufferProgress } from "/static/shared.js?v=11";

var state = {
  n: 0, win: 0, playing: true, speed: 60, visSpeed: 0.25, visT: 0,
  modes: [], fadeModes: [], phiNorm: null, phiFreqs: null,
  coords: null, peaks: null, peaksShape: null, wind: null, acc_rms: null,
  start_s: null, accMedian: 1, exploded: false, wasPlaying: true,
  explodeT: 0,           // 0=复合 1=全炸开（动画插值）
  clones: [], labels: [],
};

var DAY0 = new Date("2022-10-11T00:00:00");
function fmtDate(sec) {
  if (state.syntheticDemo) {
    var minutes = Math.floor(sec / 60);
    return "演示 " + String(Math.floor(minutes / 60)).padStart(2, "0") +
           ":" + String(minutes % 60).padStart(2, "0");
  }
  var d = new Date(DAY0.getTime() + sec * 1000);
  return (d.getMonth() + 1) + "-" + d.getDate() + " " +
         String(d.getHours()).padStart(2, "0") + ":" +
         String(d.getMinutes()).padStart(2, "0");
}

/* ---------- 数据束解析（与 wind.js 同布局） ---------- */
function parseBundle(buf) {
  var p = parsePack(buf);
  var off = p.off, out = {};
  var layout = p.header.layout;
  for (var li = 0; li < layout.length; li++) {
    var name = layout[li], shape = p.header.shapes[name], count = 1;
    for (var si = 0; si < shape.length; si++) count *= shape[si];
    out[name] = new Float32Array(buf, off, count);
    out[name].shape_ = shape;
    off += count * 4;
  }
  out.header_ = p.header;
  return out;
}

/* ---------- 主视图 ---------- */
var viewer;
function setupViewer() {
  viewer = new Viewer3D($("stage"));
  viewer.renderer.setClearColor(0x000000, 0);   // 透出 CSS 径向背景
}

/* ---------- 模态克隆（爆炸视图用，GPU 独立振动） ---------- */
var cloneGroup;
function makeCloneMaterial(baseRGB) {
  // 复用 Viewer3D 的 LUT 与着色器：aShapeOld=aShapeNew=本模态形状，uMix 恒 1
  var mat = viewer.membraneMat.clone();
  mat.uniforms = THREE.UniformsUtils.clone(viewer.membraneMat.uniforms);
  mat.uniforms.uBase.value = new THREE.Vector3(baseRGB[0], baseRGB[1], baseRGB[2]);
  return mat;
}

function disposeClones() {
  state.clones.forEach(function (cl) {
    cloneGroup.remove(cl.meshM, cl.meshL);
    cl.meshM.geometry.dispose();
    cl.meshL.geometry.dispose();
    cl.matM.dispose(); cl.matL.dispose();
  });
  state.clones = [];
}

/* 单克隆构造（膜+索几何副本，GPU 独立振动） */
function makeOneClone(freq, ph, k) {
  var B = state;
  var geoM = new THREE.BufferGeometry();
  geoM.setAttribute("position", new THREE.BufferAttribute(B.coords, 3));
  geoM.setAttribute("aShapeOld", new THREE.BufferAttribute(ph.slice(), 3));
  geoM.setAttribute("aShapeNew", new THREE.BufferAttribute(ph.slice(), 3));
  geoM.setIndex(new THREE.BufferAttribute(B.tris, 1));
  var matM = makeCloneMaterial([0.16, 0.20, 0.30]);
  matM.uniforms.uMaxAbs.value = 1.0;
  var meshM = new THREE.Mesh(geoM, matM);

  var geoL = new THREE.BufferGeometry();
  geoL.setAttribute("position", new THREE.BufferAttribute(B.coords, 3));
  geoL.setAttribute("aShapeOld", new THREE.BufferAttribute(ph.slice(), 3));
  geoL.setAttribute("aShapeNew", new THREE.BufferAttribute(ph.slice(), 3));
  geoL.setIndex(new THREE.BufferAttribute(B.lines, 1));
  var matL = makeCloneMaterial([0.30, 0.38, 0.55]);
  matL.uniforms.uMaxAbs.value = 1.0;
  var meshL = new THREE.LineSegments(geoL, matL);

  cloneGroup.add(meshM, meshL);
  return { meshM: meshM, meshL: meshL, matM: matM, matL: matL,
           freq: freq, phase: Math.random() * 2 * Math.PI,
           weight: 0, ou: 1, index: k, ph: ph.slice() };
}

function cloneShapeUpdate(cl, ph) {
  cl.ph = ph.slice();
  cl.meshM.geometry.attributes.aShapeOld.array.set(ph);
  cl.meshM.geometry.attributes.aShapeNew.array.set(ph);
  cl.meshL.geometry.attributes.aShapeOld.array.set(ph);
  cl.meshL.geometry.attributes.aShapeNew.array.set(ph);
  cl.meshM.geometry.attributes.aShapeOld.needsUpdate = true;
  cl.meshM.geometry.attributes.aShapeNew.needsUpdate = true;
  cl.meshL.geometry.attributes.aShapeOld.needsUpdate = true;
  cl.meshL.geometry.attributes.aShapeNew.needsUpdate = true;
}

/* 增量更新克隆集（第 20 晚：爆炸不再冻结时间——权重随窗变化、
   消亡退出、新模态补入；阶数随能量覆盖动态调整） */
function updateCloneSet() {
  var B = state;
  var i = Math.max(0, Math.min(B.n - 1, Math.floor(B.win)));
  var cand = [];
  for (var k = 0; k < 5; k++) {
    var f = B.peaks[i * 20 + k * 4], amp = B.peaks[i * 20 + k * 4 + 1];
    if (amp === amp && f <= 3.0) cand.push({ f: f, amp: amp, slot: k });
  }
  cand.sort(function (a, b) { return b.amp - a.amp; });
  var total = 0;
  cand.forEach(function (c) { total += c.amp * c.amp; });
  if (!cand.length || total <= 0) { if (B.exploded) merge(); return; }
  var K = cand.length, cover = 0;
  for (k = 0; k < cand.length; k++) {
    cover += cand[k].amp * cand[k].amp / total;
    if (k + 1 >= 3 && cover >= 0.80) { K = k + 1; break; }
  }
  B.coverage = cover;
  cand = cand.slice(0, K);

  var pool = B.clones.slice(), next = [];
  cand.forEach(function (md, k) {
    var j = 0, best = 1e9;
    for (var m = 0; m < B.phiFreqs.length; m++) {
      var d = Math.abs(B.phiFreqs[m] - md.f);
      if (d < best) { best = d; j = m; }
    }
    var ph = B.phiNorm[j];
    if (B.peaksShape) {
      var u = [];
      for (var c12 = 0; c12 < 12; c12++) u.push(B.peaksShape[i * 60 + md.slot * 12 + c12]);
      var bl = blendShape(md.f, u);
      if (bl) ph = bl;
    }
    var bi = -1, bd = 0.03;
    for (var p = 0; p < pool.length; p++) {
      var dd = Math.abs(pool[p].freq - md.f);
      if (dd < bd) { bd = dd; bi = p; }
    }
    var cl;
    if (bi >= 0) {
      cl = pool.splice(bi, 1)[0];
      cloneShapeUpdate(cl, ph);
      cl.freq = md.f;
    } else {
      cl = makeOneClone(md.f, ph, k);
    }
    cl.weight = (md.amp * md.amp) / total;
    cl.index = k;
    next.push(cl);
  });
  pool.forEach(function (cl) {
    cloneGroup.remove(cl.meshM, cl.meshL);
    cl.meshM.geometry.dispose(); cl.meshL.geometry.dispose();
    cl.matM.dispose(); cl.matL.dispose();
  });
  B.clones = next;
  // 单模态观察的模态若已消亡 → 自动退回爆炸视图
  if (B.soloFreq !== null &&
      !B.clones.some(function (cl) { return Math.abs(cl.freq - B.soloFreq) < 0.03; })) {
    B.soloFreq = null;
    B._camDirty = true;
  }
  if (B._prevK === undefined) B._prevK = next.length;
  if (B._prevK !== next.length) B._camDirty = true;   // 阶数变化需重排视角
  B._prevK = next.length;
  refreshLabels();
}

/* 初次爆炸建集（等价于清空后 updateCloneSet） */
function buildClones() {
  disposeClones();
  updateCloneSet();
}


/* ---------- 标签 ---------- */
function buildLabels() {
  var host = $("mode-labels");
  host.innerHTML = "";
  state.labels = state.clones.map(function (cl, k) {
    var el = document.createElement("div");
    el.className = "mlabel";
    el.innerHTML =
      '<div class="m-name">模态 ' + (k + 1) + '</div>' +
      '<div class="m-freq">' + cl.freq.toFixed(3) + ' Hz</div>' +
      '<div class="m-bar"><i style="width:' + (cl.weight * 100).toFixed(1) + '%"></i></div>' +
      '<div class="m-w">' + (cl.weight * 100).toFixed(0) + '%</div>';
    host.appendChild(el);
    return el;
  });
}

/* 标签随窗刷新（权重/阶数/覆盖随时间变化） */
function refreshLabels() {
  buildLabels();
  state.labels.forEach(function (el) { el.classList.add("show"); });
  var cn = $("cover-note");
  if (cn) cn.textContent = "前 " + state.clones.length + " 阶模态 · 能量覆盖 " +
    (state.coverage * 100).toFixed(0) + "%";
  updateDangerNote();
}

/* 最不利组合对照（第 21 晚）：当前组合 vs 离线穷举的最不利组合 */
var dangerLib = null;
function updateDangerNote() {
  var el = $("danger-note");
  if (!el) return;
  var B = state;
  if (B.syntheticDemo || !dangerLib || !B.clones.length) { el.textContent = ""; el.className = ""; return; }
  var K = B.clones.length, rows = dangerLib["K" + K];
  if (!rows || !rows.length) { el.textContent = ""; el.className = ""; return; }
  // 当前组合最大位移（同相包络，权重 = sqrt(能量份额)）
  var field = new Float32Array(B.coords.length);
  B.clones.forEach(function (cl) {
    var w = Math.sqrt(cl.weight), ph = cl.ph;
    for (var v = 0; v < field.length; v++) field[v] += w * ph[v];
  });
  var maxU = 0;
  for (var v = 0; v < field.length; v += 3) {
    var m = Math.sqrt(field[v] * field[v] + field[v + 1] * field[v + 1] +
                      field[v + 2] * field[v + 2]);
    if (m > maxU) maxU = m;
  }
  var cur = B.clones.map(function (c) { return c.freq; });
  var best = null;
  rows.forEach(function (r) {
    var sim = r.freqs.filter(function (f) {
      return cur.some(function (cf) { return Math.abs(cf - f) < 0.03; });
    }).length / K;
    if (!best || sim > best.sim || (sim === best.sim && r.maxU > best.row.maxU)) {
      best = { sim: sim, row: r };
    }
  });
  if (!best) { el.textContent = ""; el.className = ""; return; }
  var ratio = maxU / best.row.maxU;
  var modeTxt = best.row.modes.join("、");
  var fTxt = best.row.freqs.map(function (f) { return f.toFixed(2); }).join(" / ");
  el.innerHTML = "最不利组合：模态 " + modeTxt + "（" + fTxt + " Hz）· 相似 " +
    Math.round(best.sim * 100) + "% · 位移比 " + ratio.toFixed(2) +
    "<br><span class='danger-avoid'>建议：降低模态 " + best.row.modes[0] +
    "（" + best.row.freqs[0].toFixed(2) + " Hz）权重可规避</span>";
  el.className = (best.sim >= 0.6 && ratio >= 0.5) ? "warn" : "safe";
}

var _proj = new THREE.Vector3();
function layoutLabels() {
  var W = window.innerWidth, H = window.innerHeight;
  var soloOn = state.soloFreq !== null;
  state.clones.forEach(function (cl, k) {
    var el = state.labels[k];
    if (!el) return;
    // 单模态时只显示被点选模态的标签
    el.style.display = (!soloOn || Math.abs(cl.freq - state.soloFreq) < 0.03) ? "" : "none";
    // 标签锚在克隆几何中心下缘（随爆炸动画跟动）
    var c = cl.meshM.position.clone();
    c.y -= viewer.radius * 0.28 * cl.meshM.scale.y;
    _proj.copy(c).project(viewer.camera);
    var x = (_proj.x * 0.5 + 0.5) * W, y = (-_proj.y * 0.5 + 0.5) * H;
    el.style.left = x + "px";
    el.style.top = (y + 14) + "px";
  });
}

/* ---------- 播放引擎（持续振荡器 + OU 幅值调制，同 wind.js 口径） ---------- */
function updateModes() {
  var B = state, i = Math.max(0, Math.min(B.n - 1, Math.floor(B.win)));
  var cand = [];
  for (var k = 0; k < 5; k++) {
    var f = B.peaks[i * 20 + k * 4], amp = B.peaks[i * 20 + k * 4 + 1];
    var qq = B.peaks[i * 20 + k * 4 + 3];
    if (amp === amp && f <= 3.0) cand.push({ f: f, amp: amp, q: qq, slot: k });
  }
  cand.sort(function (a, b) { return b.amp - a.amp; });
  cand = cand.slice(0, 4);
  var maxAmp = cand.length ? cand[0].amp : 1;
  var pool = B.modes.slice(), next = [];
  for (k = 0; k < cand.length; k++) {
    var j = 0, best = 1e9;
    for (var m = 0; m < B.phiFreqs.length; m++) {
      var d = Math.abs(B.phiFreqs[m] - cand[k].f);
      if (d < best) { best = d; j = m; }
    }
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
      md.phi = j; md.phiBlend = blend;
    } else {
      md = { phi: j, phiBlend: blend, freq: cand[k].f, w: 0, q: cand[k].q,
             wTarget: cand[k].amp / maxAmp,
             phase: Math.random() * 2 * Math.PI, ou: 1 };
    }
    next.push(md);
  }
  for (k = 0; k < pool.length; k++) { pool[k].wTarget = undefined; B.fadeModes.push(pool[k]); }
  B.modes = next;
}

/* 实测形状 LS 混合（与 wind.js 同算法：±0.3 Hz 子空间 + 岭正则） */
function blendShape(f0, u) {
  var B = state;
  if (!u) return null;
  var k, nu = 0;
  for (k = 0; k < 12; k++) { if (u[k] !== u[k]) return null; nu += u[k] * u[k]; }
  if (nu <= 0) return null;
  nu = Math.sqrt(nu);
  var cand = [], m;
  for (m = 0; m < B.phiFreqs.length; m++) {
    if (Math.abs(B.phiFreqs[m] - f0) <= 0.30) cand.push(m);
  }
  if (cand.length < 2) {
    cand = B.phiFreqs.map(function (f, mm) { return [Math.abs(f - f0), mm]; })
      .sort(function (a, b2) { return a[0] - b2[0]; })
      .slice(0, 3).map(function (x) { return x[1]; });
  }
  var A = [];
  for (var ci = 0; ci < cand.length; ci++) {
    var ph = B.phiNorm[cand[ci]], ar = [];
    for (k = 0; k < 12; k++) ar.push(ph[B.sensorRows[k] * 3 + 1]);
    A.push(ar);
  }
  var w = solveLS(A, u, cand.length, 12), res = 0;
  for (k = 0; k < 12; k++) {
    var pred = 0;
    for (ci = 0; ci < cand.length; ci++) pred += w[ci] * A[ci][k];
    res += (pred - u[k]) * (pred - u[k]);
  }
  if (Math.sqrt(res) / nu > 0.6) return null;
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

/* ---------- 最大位移/主应变追踪（第 21 晚） ---------- */
var maxTrack = {
  tri: null,          // 三角形 CST 差分预计算（应变用）
  curD: null, curS: null,   // 当前最大位移/主应变点（THREE.Mesh）
  trailD: [], trailS: [],   // 历史轨迹球（琥珀 / 浅青）
  trailN: 240, idxD: 0, idxS: 0, countD: 0, countS: 0,
  lastEval: 0, lastWin: -1,
};

function prepStrainTriangles() {
  // 每个三角形：局部正交基 + CST 差分系数（常应变三角形）
  var B = state, n = B.tris.length / 3, C = B.coords;
  var data = new Float64Array(n * 15);
  for (var t = 0; t < n; t++) {
    var a = B.tris[t * 3], b = B.tris[t * 3 + 1], c = B.tris[t * 3 + 2];
    var p1 = new THREE.Vector3(C[a * 3], C[a * 3 + 1], C[a * 3 + 2]);
    var p2 = new THREE.Vector3(C[b * 3], C[b * 3 + 1], C[b * 3 + 2]);
    var p3 = new THREE.Vector3(C[c * 3], C[c * 3 + 1], C[c * 3 + 2]);
    var e1 = p2.clone().sub(p1);
    var x2 = e1.length(); e1.normalize();
    var e2raw = p3.clone().sub(p1);
    var nrm = e1.clone().cross(e2raw).normalize();   // 面法向
    var e2 = nrm.clone().cross(e1).normalize();
    var x3 = e2raw.dot(e1), y3 = e2raw.dot(e2);
    var A2 = x2 * y3 - x3 * 0;                       // 2A = x2*y3（y2=0）
    var b1 = -y3, b2 = y3, b3 = 0;
    var c1 = x3 - x2, c2 = -x3, c3 = x2;
    var cen = p1.clone().add(p2).add(p3).multiplyScalar(1 / 3);
    data.set([a, b, c, e1.x, e1.y, e1.z, e2.x, e2.y, e2.z,
              b1 / A2, b2 / A2, c1 / A2, c2 / A2, c3 / A2, 0], t * 15);
    // 存中心点另外放（避免结构体冗余）
    if (!maxTrack.centers) maxTrack.centers = new Float32Array(n * 3);
    maxTrack.centers[t * 3] = cen.x;
    maxTrack.centers[t * 3 + 1] = cen.y;
    maxTrack.centers[t * 3 + 2] = cen.z;
  }
  maxTrack.tri = data;
}

function makeMarker(color, size) {
  var m = new THREE.Mesh(new THREE.SphereGeometry(size, 14, 14),
                         new THREE.MeshBasicMaterial({ color: color }));
  m.visible = false;
  viewer.scene.add(m);
  return m;
}

function setupMaxTrack() {
  var R = viewer.radius;
  maxTrack.curD = makeMarker(0xef4444, R * 0.009);   // 红：最大位移
  maxTrack.curS = makeMarker(0x22d3ee, R * 0.009);   // 青：最大主应变
  for (var i = 0; i < maxTrack.trailN; i++) {
    maxTrack.trailD.push(makeMarker(0xf59e0b, R * 0.004));  // 琥珀轨迹
    maxTrack.trailS.push(makeMarker(0x67e8f9, R * 0.004));  // 浅青轨迹
  }
  prepStrainTriangles();
}

/* 每 ~0.5 s 评估一次当前场：最大位移点 + 最大主应变点，并推入轨迹 */
function evalMaxTrack() {
  var B = state;
  if (!scratch || !maxTrack.tri) return;
  var N = B.coords.length / 3, bestI = 0, bestV = -1;
  for (var v = 0; v < N; v++) {
    var m = scratch[v * 3] * scratch[v * 3] + scratch[v * 3 + 1] * scratch[v * 3 + 1] +
            scratch[v * 3 + 2] * scratch[v * 3 + 2];
    if (m > bestV) { bestV = m; bestI = v; }
  }
  var dpos = new THREE.Vector3(B.coords[bestI * 3], B.coords[bestI * 3 + 1],
                               B.coords[bestI * 3 + 2]);
  // 主应变：逐三角形 CST 差分
  var T = maxTrack.tri, nT = T.length / 15, bestT = -1, bestE = -1;
  for (var t = 0; t < nT; t++) {
    var o = t * 15, a = T[o], b = T[o + 1], c = T[o + 2];
    var e1x = T[o + 3], e1y = T[o + 4], e1z = T[o + 5];
    var e2x = T[o + 6], e2y = T[o + 7], e2z = T[o + 8];
    var b1 = T[o + 9], b2 = T[o + 10], c1 = T[o + 11], c2 = T[o + 12], c3 = T[o + 13];
    var u1 = scratch[a * 3] * e1x + scratch[a * 3 + 1] * e1y + scratch[a * 3 + 2] * e1z;
    var u2 = scratch[b * 3] * e1x + scratch[b * 3 + 1] * e1y + scratch[b * 3 + 2] * e1z;
    var u3 = scratch[c * 3] * e1x + scratch[c * 3 + 1] * e1y + scratch[c * 3 + 2] * e1z;
    var v1 = scratch[a * 3] * e2x + scratch[a * 3 + 1] * e2y + scratch[a * 3 + 2] * e2z;
    var v2 = scratch[b * 3] * e2x + scratch[b * 3 + 1] * e2y + scratch[b * 3 + 2] * e2z;
    var v3 = scratch[c * 3] * e2x + scratch[c * 3 + 1] * e2y + scratch[c * 3 + 2] * e2z;
    var exx = b1 * u1 + b2 * u2;             // b3=0 省一项
    var eyy = c1 * v1 + c2 * v2 + c3 * v3;
    var exy = (c1 * u1 + c2 * u2 + c3 * u3 + b1 * v1 + b2 * v2) * 0.5;
    var mid = (exx + eyy) * 0.5, dev = Math.sqrt(((exx - eyy) * 0.5) ** 2 + exy * exy);
    var e = Math.max(Math.abs(mid + dev), Math.abs(mid - dev));
    if (e > bestE) { bestE = e; bestT = t; }
  }
  var C = maxTrack.centers;
  var spos = new THREE.Vector3(C[bestT * 3], C[bestT * 3 + 1], C[bestT * 3 + 2]);
  var show = !B.exploded;
  maxTrack.curD.visible = show;
  maxTrack.curS.visible = show;
  if (show) {
    maxTrack.curD.position.copy(dpos);
    maxTrack.curS.position.copy(spos);
  }
  // 轨迹：数据时间 30 min 内的点（数据窗 60 s/窗，最多 30 个数据点）
  if (Math.floor(B.win) !== maxTrack.lastWin) {
    maxTrack.lastWin = Math.floor(B.win);
    var td = maxTrack.trailD[maxTrack.idxD % maxTrack.trailN];
    td.position.copy(dpos); td.visible = show;
    maxTrack.idxD++;
    if (maxTrack.idxD > 30) {   // 只留最近 30 个数据点（30 min）
      maxTrack.trailD[(maxTrack.idxD - 31 + maxTrack.trailN * 4) % maxTrack.trailN].visible = false;
    }
    var ts = maxTrack.trailS[maxTrack.idxS % maxTrack.trailN];
    ts.position.copy(spos); ts.visible = show;
    maxTrack.idxS++;
    if (maxTrack.idxS > 30) {
      maxTrack.trailS[(maxTrack.idxS - 31 + maxTrack.trailN * 4) % maxTrack.trailN].visible = false;
    }
  }
  // 读数卡（归一化幅值口径，不冒充实测 mm）
  var el = $("maxtrack-note");
  if (el) {
    el.textContent = "最大位移 节点#" + (bestI + 1) + "（幅值 " +
      Math.sqrt(bestV).toFixed(2) + "） · 最大主应变 单元#" + (bestT + 1) +
      "（估计 " + bestE.toFixed(3) + "）";
  }
}

/* ---------- 帧循环 ---------- */
var scratch, iSm = 1, _last = 0, camTween = null;
function gauss() {
  var u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function tick(now) {
  requestAnimationFrame(tick);
  var dt = Math.min((now - (_last || now)) / 1000, 0.1);
  _last = now;
  var B = state;
  B.visT += dt;

  if (B.playing) {
    var step = dt * B.speed / 60;
    var oldI = Math.floor(B.win);
    B.win = Math.min(B.win + step, B.n - 1);
    if (Math.floor(B.win) !== oldI) {
      if (B.exploded) updateCloneSet();
      else updateModes();
    }
  }

  // 复合面：模态坐标 = 白噪声驱动二阶谐振子（窄带随机过程，相位/幅值皆游走）
  // q'' + 2ζωq' + ω²q = ξ(t)，取 Var(q)=1 的噪声强度 σ=√(4ζω³)；
  // 第 19 晚定版：彻底取代 sin+OU（用户要"流变"，不要任何周期节拍）
  if (!B.exploded && scratch && B.phiNorm) {
    var wi = Math.max(0, Math.min(B.n - 1, Math.floor(B.win)));
    var intensity = Math.min(Math.max(B.acc_rms[wi] / B.accMedian, 0.35), 1.6);
    iSm += (intensity - iSm) * Math.min(dt / 1.2, 1);
    intensity = iSm;
    scratch.fill(0);
    var layers = B.modes.concat(B.fadeModes);
    for (var k = 0; k < layers.length; k++) {
      var md = layers[k];
      var ramp = md.wTarget !== undefined ? md.wTarget : 0;
      md.w += (ramp - md.w) * Math.min(dt / 0.4, 1);
      if (md.w < 0.003 && md.wTarget === undefined) continue;
      var om = 2 * Math.PI * md.freq * B.visSpeed;   // 视觉角频率 rad/s
      var zeta = 0.06;                                // 阻尼比：决定包络随机游走带宽
      if (md.qc === undefined) { md.qc = gauss(); md.qv = om * gauss(); }
      var sigma = Math.sqrt(4 * zeta * om * om * om); // 使 stationary RMS(q)≈1
      // 半隐式欧拉（q̇ 先更新、q 后更新，本频段数值稳定）
      md.qv += (-2 * zeta * om * md.qv - om * om * md.qc +
                sigma * gauss() * Math.sqrt(dt)) * dt;
      md.qc += md.qv * dt;
      var wgt = md.w * intensity;
      if (wgt <= 0) continue;
      var ph = md.phiBlend || B.phiNorm[md.phi];
      for (var v = 0; v < scratch.length; v++) scratch[v] += wgt * ph[v] * md.qc;
    }
    B.fadeModes = B.fadeModes.filter(function (md) { return md.w >= 0.003; });
    var ampVis = 0.035 * viewer.radius;
    for (v = 0; v < scratch.length; v++) scratch[v] *= ampVis;
    viewer.setShapeImmediate(scratch, ampVis);
    if (now - maxTrack.lastEval > 500) {   // 最大位移/主应变每 0.5 s 评估一次
      maxTrack.lastEval = now;
      evalMaxTrack();
    }
  }

  // 克隆：GPU 独立振动（爆炸期间主时间冻结，各自 OU）
  state.clones.forEach(function (cl) {
    cl.ou += 0.9 * (1 - cl.ou) * dt +
             0.9 * Math.sqrt(dt) * ((Math.random() + Math.random() + Math.random()) - 1.5) * 2;
    var env = Math.min(Math.max(cl.ou, 0.3), 2.0);
    var amp = 0.035 * viewer.radius * Math.sqrt(cl.weight) * env;
    var ph = 2 * Math.PI * cl.freq * state.visSpeed * state.visT + cl.phase;
    cl.matM.uniforms.uAmp.value = amp;
    cl.matL.uniforms.uAmp.value = amp;
    cl.matM.uniforms.uPhase.value = ph;
    cl.matL.uniforms.uPhase.value = ph;
  });

  // 爆炸/复合过渡（过渡结束后也逐帧平滑：阶数变化/单模态聚焦）
  if (camTween) {
    camTween.t = Math.min(camTween.t + dt / 0.85, 1);
    var e = easeInOut(camTween.t);
    state.explodeT = camTween.from + (camTween.to - camTween.from) * e;
    if (camTween.t >= 1) camTween = null;
  }
  if (B.exploded) applyExplode(state.explodeT);
  layoutLabels();

  // UI 自动隐藏（复合态闲置 4 s 全净；鼠标活动/分解时浮现）
  if (!B.exploded && now - (state.lastMove || 0) > 4000) {
    document.body.classList.add("ui-hidden");
  } else {
    document.body.classList.remove("ui-hidden");
  }

  $("chip-date").textContent = fmtDate(B.start_s[Math.floor(B.win)] || 0) +
    " · 窗 " + Math.floor(B.win);
  $("chip-wind").textContent = (B.wind[Math.floor(B.win)] || 0).toFixed(2) + " m/s";
  if (!B._seeking) {
    $("time-slider").value = Math.floor(B.win);
    $("time-text").textContent = fmtDate(B.start_s[Math.floor(B.win)] || 0);
  }
}

function easeInOut(t) { return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; }

function applyExplode(t) {
  // 散开/缩小/相机随 explodeT 插值；单模态时只显示被点选克隆并聚焦
  var B = state;
  var spread = viewer.radius * 1.25, shrink = 1 - 0.45 * t;
  state.clones.forEach(function (cl, k) {
    var targetX = (k - (state.clones.length - 1) / 2) * spread * t;
    if (cl.curX === undefined) cl.curX = targetX;
    cl.curX += (targetX - cl.curX) * 0.25;              // 位置平滑（阶数变化滑动）
    cl.meshM.position.set(cl.curX, 0, 0);
    cl.meshL.position.set(cl.curX, 0, 0);
    var sc = cl.curS === undefined ? shrink : (cl.curS += (shrink - cl.curS) * 0.25);
    cl.curS = sc;
    cl.meshM.scale.setScalar(sc);
    cl.meshL.scale.setScalar(sc);
    var solo = (B.soloFreq !== null) && Math.abs(cl.freq - B.soloFreq) < 0.03;
    cl.meshM.visible = (B.soloFreq === null) || solo;
    cl.meshL.visible = cl.meshM.visible;
  });
  var base = viewer.homeView || { pos: viewer.camera.position.clone(),
                                  tgt: viewer.controls.target.clone() };
  // 相机只在状态切换时调整（_camDirty/过渡中）；用户拖动后不再强拉，
  // 否则每帧跟 OrbitControls 输入打架——爆炸视图"转不动"的根因
  if (B._camDirty || camTween) {
    if (B.soloFreq !== null && state.clones.length) {
      // 单模态：相机聚焦到被点选克隆（中心 = 网格偏移 + 模型中心×缩放）
      var soloCl = null;
      for (var k2 = 0; k2 < state.clones.length; k2++) {
        if (Math.abs(state.clones[k2].freq - B.soloFreq) < 0.03) { soloCl = state.clones[k2]; break; }
      }
      if (soloCl) {
        var cc = soloCl.meshM.position.clone().add(base.tgt.clone().multiplyScalar(soloCl.curS || 1));
        viewer.controls.target.lerp(cc, 0.15);
        var dirS = viewer.camera.position.clone().sub(cc).normalize()
          .multiplyScalar(viewer.radius * 0.9);
        viewer.camera.position.lerp(cc.clone().add(dirS), 0.15);
      }
    } else {
      var dir = base.pos.clone().sub(base.tgt);
      var camPos = base.tgt.clone().add(dir.multiplyScalar(1 + 0.75 * t));
      viewer.camera.position.lerp(camPos, 0.25);
      viewer.controls.target.lerp(base.tgt, 0.25);
    }
  }
  viewer.meshes.forEach(function (m) { m.visible = t < 0.02; });
}

/* ---------- 爆炸/复合 ---------- */
function explode() {
  var B = state;
  if (B.exploded || !B.modes.length) return;
  B.exploded = true;
  B.wasPlaying = B.playing;
  B.playing = false;                      // 定格爆炸：暂停时间，静看当下模态占比
  B.soloFreq = null;
  B._camDirty = true;
  // 爆炸时隐藏最大追踪标记（复合块被跳过会残留空中，用户实机反馈）
  maxTrack.curD.visible = false;
  maxTrack.curS.visible = false;
  maxTrack.trailD.forEach(function (m) { m.visible = false; });
  maxTrack.trailS.forEach(function (m) { m.visible = false; });
  viewer.homeView = { pos: viewer.camera.position.clone(),
                      tgt: viewer.controls.target.clone() };
  buildClones();
  buildLabels();
  camTween = { t: 0, from: 0, to: 1 };
  state.labels.forEach(function (el) { el.classList.add("show"); });
  $("btn-merge").hidden = false;
  $("hint").classList.add("gone");
}

function merge() {
  var B = state;
  if (!B.exploded) return;
  B._camDirty = true;
  camTween = { t: 0, from: 1, to: 0 };
  state.labels.forEach(function (el) { el.classList.remove("show"); });
  $("btn-merge").hidden = true;
  // 到位后清理克隆并恢复
  setTimeout(function () {
    disposeClones();
    state.labels = [];
    $("mode-labels").innerHTML = "";
    $("cover-note").textContent = "";
    $("danger-note").textContent = "";
    $("danger-note").className = "";
    B.exploded = false;
    B.playing = B.wasPlaying;
    updateModes();
    // 恢复最大追踪标记（当前点 + 最近 30 个轨迹点）
    maxTrack.curD.visible = true;
    maxTrack.curS.visible = true;
    for (var i = 0; i < 30; i++) {
      maxTrack.trailD[(maxTrack.idxD - 1 - i + maxTrack.trailN * 4) % maxTrack.trailN].visible = true;
      maxTrack.trailS[(maxTrack.idxS - 1 - i + maxTrack.trailN * 4) % maxTrack.trailN].visible = true;
    }
  }, 900);
}

/* ---------- 交互 ---------- */
function setupInput() {
  var dom = viewer.renderer.domElement, ray = new THREE.Raycaster();
  var downX = 0, downY = 0;
  dom.addEventListener("pointerdown", function (e) {
    downX = e.clientX; downY = e.clientY;
    state._camDirty = false;              // 用户接管相机：停止一切强拉
  });
  dom.addEventListener("pointerup", function (e) {
    if (Math.hypot(e.clientX - downX, e.clientY - downY) > 6) return;  // 拖动不算点击
    var r = dom.getBoundingClientRect();
    ray.setFromCamera({ x: ((e.clientX - r.left) / r.width) * 2 - 1,
                        y: -((e.clientY - r.top) / r.height) * 2 + 1 }, viewer.camera);
    if (state.exploded) {
      // 点中某个模态克隆 → 单模态观察；点空白 → 单模态回分解 / 分解回复合
      var hits = [];
      state.clones.forEach(function (cl) {
        hits.push.apply(hits, ray.intersectObjects([cl.meshM, cl.meshL], false));
      });
      if (hits.length) {
        var obj = hits[0].object;
        var cl = state.clones.find(function (c) { return c.meshM === obj || c.meshL === obj; });
        if (cl) { state.soloFreq = cl.freq; state._camDirty = true; return; }
      }
      if (state.soloFreq !== null) { state.soloFreq = null; state._camDirty = true; return; }
      merge();
      return;
    }
    if (ray.intersectObjects(viewer.meshes, false).length) explode();
  });
  document.addEventListener("pointermove", function () { state.lastMove = performance.now(); });
  $("btn-merge").addEventListener("click", merge);
  $("btn-play").addEventListener("click", function () {
    if (state.exploded) { merge(); return; }
    state.playing = !state.playing;
    $("btn-play").textContent = state.playing ? "❚❚" : "▶";
  });
  $("speed").addEventListener("change", function () {
    state.speed = parseFloat(this.value);
  });
  var sl = $("time-slider");
  sl.addEventListener("input", function () {
    state._seeking = true;
    state.win = parseInt(this.value, 10);
    updateModes();
    if (state.exploded) {   // 爆炸中拖动时间轴：同步刷新三模态与权重
      buildClones();
      buildLabels();
      state.labels.forEach(function (el) { el.classList.add("show"); });
      applyExplode(state.explodeT);
    }
    $("time-text").textContent = fmtDate(state.start_s[Math.floor(state.win)] || 0);
  });
  sl.addEventListener("change", function () { state._seeking = false; });
  window.addEventListener("keydown", function (e) {
    if (e.code === "Space") { e.preventDefault(); $("btn-play").click(); }
  });
}

/* ---------- 初始化 ---------- */
async function init() {
  setupViewer();
  var meta = await getJSON("/api/wind/meta");
  state.syntheticDemo = meta.source === "synthetic-demo";
  $("demo-label").hidden = !state.syntheticDemo;
  if (!meta.has_bundle) {
    $("loading-text").textContent = "风振数据束未生成";
    return;
  }
  var buf = await fetchArrayBufferProgress("/api/wind/bundle", function (got, total) {
    $("loading-text").textContent = "正在准备数据 … " +
      (got / 1048576).toFixed(1) + " / " + (total / 1048576).toFixed(0) + " MB";
  });
  var B = state;
  var bd = parseBundle(buf);
  B.start_s = bd.start_s; B.wind = bd.wind; B.acc_rms = bd.acc_rms;
  B.peaks = bd.peaks; B.peaksShape = bd.peaks_shape || null;
  B.n = bd.start_s.length;
  $("time-slider").max = B.n - 1;
  var sorted = Array.prototype.slice.call(B.acc_rms).sort();
  B.accMedian = sorted[Math.floor(sorted.length / 2)] || 1;

  var gbuf = await (await fetch("/api/geometry")).arrayBuffer();
  var g = parsePack(gbuf), n3 = g.header.node_count * 3;
  B.coords = new Float32Array(gbuf, g.off, n3);
  B.tris = new Uint32Array(gbuf, g.off + n3 * 4, g.header.tri_index_count);
  B.lines = new Uint32Array(gbuf, g.off + n3 * 4 + g.header.tri_index_count * 4,
                            g.header.line_index_count);
  viewer.setGeometry(B.coords, B.tris, B.lines);
  scratch = new Float32Array(n3);
  setupMaxTrack();

  var mbuf = await (await fetch("/api/modebase")).arrayBuffer();
  var mp = parsePack(mbuf), nm = mp.header.n_modes, nn = mp.header.node_count;
  B.phiFreqs = mp.header.freqs;
  B.phiNorm = [];
  for (var m2 = 0; m2 < nm; m2++) {
    var arr = new Float32Array(mbuf, mp.off + m2 * nn * 3 * 4, nn * 3);
    var mx = 0;
    for (var v = 0; v < nn; v++) {
      var mg = Math.sqrt(arr[v * 3] * arr[v * 3] + arr[v * 3 + 1] * arr[v * 3 + 1] + arr[v * 3 + 2] * arr[v * 3 + 2]);
      if (mg > mx) mx = mg;
    }
    var nor = new Float32Array(nn * 3);
    for (v = 0; v < nn * 3; v++) nor[v] = arr[v] / (mx || 1);
    B.phiNorm.push(nor);
  }
  var sensors = await getJSON("/api/wind/sensors");
  B.sensorRows = sensors.map(function (s) { return s.row; });
  try { dangerLib = await getJSON("/api/wind/danger"); } catch (eD) { dangerLib = null; }

  cloneGroup = new THREE.Group();
  viewer.scene.add(cloneGroup);

  updateModes();
  setupInput();
  $("loading").classList.add("done");
  setTimeout(function () { $("hint").classList.add("gone"); }, 7000);
  requestAnimationFrame(tick);

  var qp = new URLSearchParams(location.search);
  if (qp.has("win")) { B.win = parseInt(qp.get("win"), 10) || 0; updateModes(); }
  if (qp.get("play") === "0") { B.playing = false; $("btn-play").textContent = "▶"; }
  if (qp.has("debug")) window.__zen = { state: state, explode: explode,
                                        merge: merge, viewer: viewer,
                                        updateModes: updateModes,
                                        updateCloneSet: updateCloneSet,
                                        maxTrack: maxTrack };
}

init().catch(function (e) {
  console.error(e);
  var lt = $("loading-text");
  if (lt) {
    lt.textContent = "加载失败：" + (e && e.message ? e.message : e) +
      "（点左上角返回实验室）";
    lt.style.color = "#fda4af";
  }
  showToast("加载失败：" + (e && e.message ? e.message : e));
});
