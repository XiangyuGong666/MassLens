/* 模态组合库（模块一子集）：离线穷举的最不利组合浏览 + 3D 合成形态显示。
   数据源 /api/wind/danger（windlens_engine.danger 产物）；3D 复用 MassLens viewer。 */

import * as THREE from "/static/vendor/three.module.min.js";
import { $, getJSON, showToast } from "/static/shared.js?v=11";

var lib = null, basisCache = null, marker = null, activeRow = null;

// CDP 用钩子（前置，防绑定失败时钩子也丢）
window.__combos = { openPanel: openPanel, showCombo: showCombo,
                    getLib: function () { return lib; },
                    $test: $ };

function fmtFreqs(fs) { return fs.map(function (f) { return f.toFixed(2); }).join(" / "); }

function ensureBasis() {
  if (basisCache) return Promise.resolve(basisCache);
  return fetch("/api/modebase").then(function (r) { return r.arrayBuffer(); })
    .then(function (buf) {
      // 二进制包：头 4 字节 JSON 长度 + 头 + 数组
      var dv = new DataView(buf), hlen = dv.getUint32(0, true);
      var hdr = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));
      var off = 4 + hlen, nm = hdr.n_modes, nn = hdr.node_count;
      var shapes = [];
      for (var m = 0; m < nm; m++) {
        shapes.push(new Float32Array(buf, off + m * nn * 3 * 4, nn * 3));
      }
      basisCache = { shapes: shapes, freqs: hdr.freqs, nodeCount: nn };
      return basisCache;
    });
}

function renderTable(k) {
  var rows = lib["K" + k] || [];
  var tb = document.getElementById("combo-table");
  var html = "<thead><tr><th>#</th><th>模态编号</th><th>频率 Hz</th>" +
             "<th>最大位移</th><th>最不利点</th></tr></thead><tbody>";
  rows.forEach(function (r, i) {
    html += '<tr class="combo-row" data-i="' + i + '">' +
      "<td>" + (i + 1) + "</td>" +
      "<td>" + r.modes.join("、") + "</td>" +
      "<td>" + fmtFreqs(r.freqs) + "</td>" +
      '<td class="maxu">' + r.maxU.toFixed(3) + "</td>" +
      "<td>#" + r.node + "</td></tr>";
  });
  tb.innerHTML = html + "</tbody>";
  tb.querySelectorAll("tr.combo-row").forEach(function (tr) {
    tr.addEventListener("click", function () {
      tb.querySelectorAll("tr").forEach(function (x) { x.classList.remove("active"); });
      tr.classList.add("active");
      showCombo(rows[parseInt(tr.dataset.i, 10)]);
    });
  });
}

function showCombo(row) {
  ensureBasis().then(function (bc) {
    var st = window.__masslens && window.__masslens.state;
    if (!st || !st.viewer) { showToast("3D 视图未就绪"); return; }
    var nn = bc.nodeCount, field = new Float32Array(nn * 3);
    row.modes.forEach(function (mid) {
      var ph = bc.shapes[mid - 1];
      if (!ph) return;
      for (var v = 0; v < field.length; v++) field[v] += ph[v];
    });
    var maxAbs = 0;
    for (var v = 0; v < field.length; v += 3) {
      var m = Math.sqrt(field[v] * field[v] + field[v + 1] * field[v + 1] +
                        field[v + 2] * field[v + 2]);
      if (m > maxAbs) maxAbs = m;
    }
    st.viewer.setShapeImmediate(field, maxAbs);
    // 多热点标记（第 22 晚：红/橙/金 三色显示多个高响应区，不再单点）
    var scene = st.viewer.scene;
    if (!showCombo._markers) showCombo._markers = [];
    var spots = (row.hotspots && row.hotspots.length ? row.hotspots : [row.coords]);
    var colors = [0xef4444, 0xf97316, 0xfbbf24, 0xa3e635, 0x22d3ee];
    while (showCombo._markers.length < spots.length) {
      var mk = new THREE.Mesh(new THREE.SphereGeometry(st.viewer.radius * 0.009, 16, 16),
                              new THREE.MeshBasicMaterial({ color: 0xffffff }));
      scene.add(mk);
      showCombo._markers.push(mk);
    }
    showCombo._markers.forEach(function (mk, i) {
      if (i < spots.length) {
        mk.material.color.setHex(colors[Math.min(i, colors.length - 1)]);
        mk.position.set(spots[i][0], spots[i][1], spots[i][2]);
        mk.visible = true;
      } else mk.visible = false;
    });
    marker = showCombo._markers[0];   // 关闭面板时兼容旧引用
    showToast("已显示组合 " + row.modes.join("、") +
              "（等权同相）· " + spots.length + " 个热点");
  }).catch(function (e) { showToast("显示失败：" + e.message); });
}

function openPanel() {
  var p = document.getElementById("combo-panel");
  if (!p) { showToast("面板元素未找到"); return; }
  p.hidden = false;
  if (lib) return;
  getJSON("/api/wind/danger").then(function (d) {
    lib = d;
    renderTable(document.getElementById("combo-k").value);
  }).catch(function () {
    showToast("组合库未生成（先跑 windlens_engine.danger）");
  });
}

function bindComboUI() {
  try {
    document.getElementById("combo-lib-btn").addEventListener("click", openPanel);
    document.getElementById("combo-close").addEventListener("click", function () {
      document.getElementById("combo-panel").hidden = true;
      if (showCombo._markers) showCombo._markers.forEach(function (mk) { mk.visible = false; });
    });
    document.getElementById("combo-k").addEventListener("change", function () {
      if (lib) renderTable(this.value);
    });
    window.__comboBound = true;
  } catch (e) {
    window.__comboBound = (e && e.stack) ? e.stack : String(e);
  }
}
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bindComboUI);
} else {
  bindComboUI();
}
