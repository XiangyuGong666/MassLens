/* MassLens 主逻辑：状态、API 调用、三栏联动（单壳版：导出 initMasslens 由 boot 编排）。
   数据流：滑块/预设/阶次 → /api/query → 3D 振型 + 频率数值 + MAC 质量条同步刷新。
   过渡体系：形状 GPU 交叉淡化（viewer3d）、数值渐变（tween）、变化高亮（flash）。 */

import { Viewer3D } from "/static/viewer3d.js?v=11";
import { $, showToast, parsePack, getJSON } from "/static/shared.js?v=11";

var state = {
  mA: 0,
  mode: 1,
  meta: null,
  viewer: null,
  freqs: null,
  inflight: false,
  pending: false,
  debug: false,
  lastMs: 0,
  gen: 0
};
window.__masslens = { state: state };   // 模态组合库等子模块复用 3D 视图句柄

/* ---------- 变化高亮（短暂，重触发式） ---------- */
function flash(el) {
  el.classList.remove("flash");
  void el.offsetWidth;  // 强制回流以重触发动画
  el.classList.add("flash");
}

/* ---------- 数值渐变（250 ms ease-out；连续更新时指数追赶） ---------- */
var tweens = {};
var tweenLoopRunning = false;

function tween(id, to, fmt) {
  var cur = tweens[id] ? tweens[id].shown : to;
  tweens[id] = { from: cur, to: to, shown: cur, t0: performance.now(), fmt: fmt };
  if (!tweenLoopRunning) {
    tweenLoopRunning = true;
    requestAnimationFrame(tweenTick);
  }
}

function tweenTick(now) {
  var alive = false;
  var ids = Object.keys(tweens);
  for (var i = 0; i < ids.length; i++) {
    var tw = tweens[ids[i]];
    var k = Math.min(1, (now - tw.t0) / 250);
    var e = 1 - Math.pow(1 - k, 3);
    tw.shown = tw.from + (tw.to - tw.from) * e;
    $(ids[i]).textContent = tw.fmt(tw.shown);
    if (k < 1) alive = true; else delete tweens[ids[i]];
  }
  if (alive) requestAnimationFrame(tweenTick);
  else tweenLoopRunning = false;
}

/* ---------- 公式（M(m_a) 随参数实时改写） ---------- */
function renderFormulas() {
  if (!window.katex) return;  // vendor 缺失时退化为纯文本占位
  window.katex.render("(K - \\omega^2 M(m_a))\\,\\phi = 0", $("formula-eq"),
                      { displayMode: true, throwOnError: false });
  renderMassFormula();
}

function renderMassFormula() {
  if (!window.katex) return;
  window.katex.render(
    "M(m_a) = M_0 + \\htmlClass{flash-num}{" + state.mA.toFixed(3) +
    "}\\,A_{\\mathrm{mem}}",
    $("formula-mass"), { displayMode: false, throwOnError: false, trust: true });
}

function buildControls(meta) {
  var slider = $("ma-slider");
  slider.min = meta.grid.min;
  slider.max = meta.grid.max;
  slider.step = ((meta.grid.max - meta.grid.min) / 400).toFixed(4);
  slider.value = state.mA;
  slider.addEventListener("input", function () {
    state.mA = parseFloat(slider.value);
    $("preset-select").value = "";   // 手动拖过滑块 → 预设回退"（手动）"
    updateMaReadout();
    flash($("ma-value"));
    refresh();
  });

  var sel = $("preset-select");
  sel.appendChild(new Option("（手动）", ""));
  var names = Object.keys(meta.presets);
  var i;
  for (i = 0; i < names.length; i++) {
    sel.appendChild(new Option(names[i], String(meta.presets[names[i]])));
  }
  sel.addEventListener("change", function () {
    if (sel.value === "") return;
    state.mA = parseFloat(sel.value);
    slider.value = state.mA;
    updateMaReadout();
    flash($("ma-value"));
    refresh();
  });

  var strip = $("mode-strip");
  for (i = 1; i <= meta.n_modes; i++) {
    var btn = document.createElement("button");
    btn.className = "mode-btn";
    btn.textContent = String(i);
    btn.dataset.mode = String(i);
    btn.addEventListener("click", function () {
      selectMode(parseInt(this.dataset.mode, 10));
    });
    strip.appendChild(btn);
  }
  updateMaReadout();
}

function updateMaReadout() {
  $("ma-value").textContent = state.mA.toFixed(3);
  var slider = $("ma-slider");
  var g = state.meta.grid;
  var pct = ((state.mA - g.min) / (g.max - g.min)) * 100;
  slider.style.setProperty("--fill", pct + "%");
}

function selectMode(i) {
  state.mode = i;
  var btns = document.querySelectorAll(".mode-btn");
  for (var k = 0; k < btns.length; k++) {
    btns[k].classList.toggle("active", parseInt(btns[k].dataset.mode, 10) === i);
  }
  return refresh();
}

async function refresh() {
  if (state.inflight) { state.pending = true; return; }  // latest-wins
  state.inflight = true;
  var gen = ++state.gen;                    // 代际：响应只服务当时的请求
  var reqMode = state.mode, reqMa = state.mA;  // 捕获请求时的参数
  var t0 = performance.now();
  try {
    var url = "/api/query?m_a=" + reqMa + "&mode=" + reqMode;
    var resp = await fetch(url);
    if (gen !== state.gen) return;          // 已有更新请求在途，丢弃本响应
    if (!resp.ok) {
      var j = await resp.json();
      showToast(j.error || "出错了");
      return;
    }
    var buf = await resp.arrayBuffer();
    if (gen !== state.gen) return;
    state.lastMs = performance.now() - t0;
    var p = parsePack(buf);
    var shape = new Float32Array(buf, p.off, state.meta.node_count * 3);
    state.viewer.setShape(shape, p.header.max_abs);
    updateNumbers(p.header);
  } catch (e) {
    if (gen === state.gen) showToast("连接服务失败，请确认服务已启动");
  } finally {
    state.inflight = false;
    if (state.pending) { state.pending = false; refresh(); }
  }
}

function fmtShift(f, f0) {
  var d = (f - f0) / f0 * 100;
  return (d >= 0 ? "+" : "") + d.toFixed(2) + "%";
}

function fmtHz(v) { return v.toFixed(4) + " Hz"; }

function updateNumbers(h) {
  var m = state.meta;
  var mode = state.mode;
  var f = h.freq;
  var f0 = m.freqs0[mode - 1];
  $("cur-mode-label").textContent = "第 " + mode + " 阶";
  $("chip-mode").textContent = "第 " + mode + " 阶";
  tween("cur-freq", f, fmtHz);
  tween("chip-freq", f, fmtHz);
  flash($("cur-freq"));
  flash($("chip-freq"));
  $("cur-shift").textContent = "相对基准（m_a=0）" + fmtShift(f, f0);
  renderMassFormula();
  updateQuality(h.diag);

  var tbody = $("freq-tbody");
  tbody.innerHTML = "";
  var rows = Math.min(10, h.freqs.length);
  for (var i = 0; i < rows; i++) {
    var tr = document.createElement("tr");
    if (i + 1 === mode) tr.className = "active";
    var td1 = document.createElement("td");
    td1.textContent = "第 " + (i + 1) + " 阶";
    var td2 = document.createElement("td");
    td2.textContent = h.freqs[i].toFixed(4);
    var td3 = document.createElement("td");
    td3.textContent = fmtShift(h.freqs[i], m.freqs0[i]);
    tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3);
    (function (idx) {
      tr.addEventListener("click", function () { selectMode(idx + 1); });
    })(i);
    tbody.appendChild(tr);
  }
}

/* ---------- 插值质量：MAC 渐变条 + 低置信提示 + 误差角标（不阻断） ---------- */
function updateQuality(d) {
  if (!d) return;
  var mac = d.mac;
  var txt = $("mac-text");
  txt.textContent = "MAC = " + mac.toFixed(5);
  txt.style.color = mac < 0.85 ? "#f87171" : (mac < 0.95 ? "#fbbf24" : "");
  var frac = Math.min(Math.max((mac - 0.8) / 0.2, 0), 1);
  $("mac-mask").style.width = ((1 - frac) * 100).toFixed(1) + "%";
  $("mac-hint").hidden = mac >= 0.85;   // 与文字红色阈值统一（三档口径归一）

  var badge = $("err-badge");
  if (d.err && d.err.max_freq_err > d.err_tol) {
    $("err-text").textContent = (d.err.max_freq_err * 100).toFixed(2) + "%";
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

export async function initMasslens() {
  try {
    state.debug = new URLSearchParams(location.search).has("debug");
    state.meta = await getJSON("/api/meta");
    buildControls(state.meta);

    state.viewer = new Viewer3D($("viewport-m"));
    $("speed-slider").addEventListener("input", function () {
      state.viewer.setSpeed(parseFloat(this.value));
    });
    if (state.debug) {
      $("perf").hidden = false;
      setInterval(function () {
        if (state.viewer) {
          $("perf").textContent = state.lastMs.toFixed(0) + " ms · " +
            state.viewer.fps.toFixed(0) + " fps";
        }
      }, 500);
    }

    var gbuf = await (await fetch("/api/geometry")).arrayBuffer();
    var g = parsePack(gbuf);
    var n3 = g.header.node_count * 3;
    var coords = new Float32Array(gbuf, g.off, n3);
    var tris = new Uint32Array(gbuf, g.off + n3 * 4, g.header.tri_index_count);
    var lines = new Uint32Array(gbuf, g.off + n3 * 4 + g.header.tri_index_count * 4,
                                g.header.line_index_count);
    state.viewer.setGeometry(coords, tris, lines);

    var qp = new URLSearchParams(location.search);  // 深链接：?m_a=0.9&mode=3
    if (qp.has("m_a")) {
      var v = parseFloat(qp.get("m_a"));
      if (isFinite(v)) {
        state.mA = Math.min(Math.max(v, state.meta.grid.min), state.meta.grid.max);
        $("ma-slider").value = state.mA;
        updateMaReadout();
      }
    }
    if (qp.has("mode")) {
      var md = parseInt(qp.get("mode"), 10);
      if (md >= 1 && md <= state.meta.n_modes) state.mode = md;
    }

    renderFormulas();
    await selectMode(state.mode);
  } catch (e) {
    showToast("附加质量实验室初始化失败：" + e.message);
    throw e;
  }
}
