/* MassLens/WindLens 共享工具（单壳双实验室共用）。
   原则：本文件只放与模块状态无关的纯函数；模块状态各自留在 app.js / wind.js。 */

export var $ = function (id) { return document.getElementById(id); };

export function showToast(msg) {
  var t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(function () { t.hidden = true; }, 3600);
}

/* 二进制包解析：[u32 头长][头部 JSON（4 字节对齐）][数组字节] */
export function parsePack(buf) {
  var dv = new DataView(buf);
  var hlen = dv.getUint32(0, true);
  var header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, hlen)));
  return { header: header, off: 4 + hlen };
}

export async function getJSON(url) {
  var r = await fetch(url);
  if (!r.ok) {
    var j = await r.json().catch(function () { return {}; });
    throw new Error(j.error || ("请求失败：" + r.status));
  }
  return r.json();
}

/* 带进度的二进制下载（加载遮罩实时显示 MB） */
export async function fetchArrayBufferProgress(url, onProgress) {
  var resp = await fetch(url);
  if (!resp.ok) throw new Error("下载失败：" + url + " " + resp.status);
  var total = parseInt(resp.headers.get("Content-Length") || "0", 10);
  var reader = resp.body.getReader();
  var chunks = [], got = 0;
  for (;;) {
    var r = await reader.read();
    if (r.done) break;
    chunks.push(r.value);
    got += r.value.length;
    if (onProgress) onProgress(got, total);
  }
  var out = new Uint8Array(got), off = 0;
  for (var i = 0; i < chunks.length; i++) {
    out.set(chunks[i], off);
    off += chunks[i].length;
  }
  return out.buffer;
}

/* 画布适配（dpr 由调用方给；冻结 CSS 高度防正反馈无限长高） */
export function fitCanvas(cv, dpr) {
  if (!cv.dataset.fixedH) {
    if (cv.clientHeight === 0) return null;  // 隐藏模块无布局盒：可见后再冻，勿冻 0px
    cv.dataset.fixedH = "1";
    cv.style.height = cv.clientHeight + "px";
  }
  var r = cv.getBoundingClientRect();
  var d = dpr || 1;
  var w = Math.min(Math.max(1, Math.round(r.width * d)), 8192);
  var h = Math.min(Math.max(1, Math.round(r.height * d)), 8192);
  if (cv.width !== w || cv.height !== h) {
    cv.width = w;
    cv.height = h;
  }
  var ctx = cv.getContext("2d");
  ctx.setTransform(d, 0, 0, d, 0, 0);
  return [r.width, r.height, ctx];
}

export function pearson(x, y) {
  var n = Math.min(x.length, y.length), mx = 0, my = 0, i, c = 0;
  for (i = 0; i < n; i++) {
    if (x[i] === x[i] && y[i] === y[i]) { mx += x[i]; my += y[i]; c++; }
  }
  if (c < 3) return NaN;
  mx /= c; my /= c;
  var sxy = 0, sxx = 0, syy = 0;
  for (i = 0; i < n; i++) {
    if (x[i] === x[i] && y[i] === y[i]) {
      var dx = x[i] - mx, dy = y[i] - my;
      sxy += dx * dy; sxx += dx * dx; syy += dy * dy;
    }
  }
  return (sxy / Math.sqrt(sxx * syy)).toFixed(2);
}
