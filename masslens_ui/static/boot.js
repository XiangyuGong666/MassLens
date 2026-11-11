/* boot：单壳编排——两模块并行初始化、标签秒切、深链接与历史管理。
   模块切换是纯 DOM 显隐（无整页重载）；首载并行拉齐两边数据。 */

import { $, showToast } from "/static/shared.js?v=11";
import { initMasslens } from "/static/app.js?v=11";
import { initWind, runQa } from "/static/wind.js?v=11";

var NOTES = {
  masslens: "浙能乐清电厂 3 号气承式膜结构煤棚 · 317×115×45 m · ±45° 正交索网 · 内压 270 Pa",
  wind: "风振时程 · 60 s/窗 · 脉动风压"
};

function currentPage() {
  return location.pathname.indexOf("/wind") === 0 ? "wind" : "masslens";
}

function show(page) {
  $("main-m").hidden = page !== "masslens";
  $("main-w").hidden = page !== "wind";
  var tabs = document.querySelectorAll(".nav-tab");
  for (var i = 0; i < tabs.length; i++) {
    tabs[i].classList.toggle("active", tabs[i].dataset.page === page);
  }
  $("structure-note").textContent = NOTES[page];
  // 模块从 hidden 恢复后 2D 画布重获布局盒：广播 resize 让画布重冻结重绘
  window.dispatchEvent(new Event("resize"));
}

function goto(page) {
  if (page === currentPage()) { show(page); return; }
  history.pushState({ page: page }, "", page === "wind" ? "/wind" : "/");
  show(page);
}

var tabs = document.querySelectorAll(".nav-tab");
for (var i = 0; i < tabs.length; i++) {
  tabs[i].addEventListener("click", function (e) {
    e.preventDefault();
    goto(this.dataset.page);
  });
}
window.addEventListener("popstate", function () { show(currentPage()); });

show(currentPage());

/* 并行初始化：首载等两边齐活，之后标签切换零等待；
   失败提示由各模块 catch 内给出（此处不再重复 toast） */
$("loading-text").textContent = "正在加载（两实验室并行）…";
var p1 = initMasslens();
var p2 = initWind();
Promise.allSettled([p1, p2]).then(function () {
  $("loading").classList.add("done");
  if (new URLSearchParams(location.search).has("qa")) {
    runQa().catch(function (e) { console.log("[QA] 异常: " + e.message); });
  }
});
