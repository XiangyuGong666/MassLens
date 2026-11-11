"""留出点交叉验证：逐点留出、用子网格重建插值器、与真实工况对比。

用法（项目根目录）：python -m masslens_interp.validate [--tol 0.05]
指标：频率相对误差（逐通道）、振型 MAC（预测 vs 真实，按全网格跟踪的通道对应）。
验收：所有留出点 × 所有通道的频率相对误差 < tol（默认 5%）。退出码 0/1。
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from masslens_data import config, schema

from . import tracking
from .interpolator import Interpolator


def load_cases(db_path=None):
    db_path = db_path or config.H5_PATH
    cases = [schema.load_case(db_path, name)
             for name, _ in schema.list_cases(db_path)]
    return sorted(cases, key=lambda c: c[0])


def loo(cases, tol=0.05):
    """留一法交叉验证。返回 (rows, max_freq_err, min_mac)。

    rows: 每留出点一行 dict(held_m_a, max_freq_err, min_mac, worst_lane)。
    内部点才能留出（端点留出会变成外推，不在验收范围）。
    """
    full_track = tracking.track_cases(cases)  # 全网格跟踪：通道对应关系的真值
    rows = []
    for k in range(1, len(cases) - 1):
        held = cases[k]
        sub = cases[:k] + cases[k + 1:]
        itp = Interpolator(cases=sub)
        freqs_q, shapes_q = itp.query(held[0])
        _, freqs_a, shapes_a = held
        lanes = full_track.lane_of[k]          # 通道 j → 真实工况的本地阶次
        f_act = freqs_a[lanes]
        err = np.abs(freqs_q - f_act) / f_act
        mac = np.array([tracking.mac_matrix(shapes_q[j:j + 1],
                                            shapes_a[l:l + 1])[0, 0]
                        for j, l in enumerate(lanes)])
        rows.append({"held_m_a": held[0],
                     "max_freq_err": float(err.max()),
                     "worst_lane": int(err.argmax()),
                     "min_mac": float(mac.min())})
    return rows, max(r["max_freq_err"] for r in rows), \
        min(r["min_mac"] for r in rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.05, help="频率误差容差，默认 0.05")
    ap.add_argument("--save", action="store_true",
                    help="把误差表写入 data/interp_error.json（UI 误差角标用）")
    args = ap.parse_args()

    cases = load_cases()
    grid = [c[0] for c in cases]
    print(f"数据库 {config.H5_PATH.name}：{len(cases)} 工况，"
          f"m_a ∈ [{grid[0]}, {grid[-1]}]，每工况 {cases[0][1].shape[0]} 阶")

    tr = tracking.track_cases(cases)
    print(f"跟踪质量：相邻工况指派 MAC 全程最小值 = {tr.lane_mac[1:].min():.6f}"
          f"（阈值参考 0.99）；换序点数 = {len(tr.crossings)}")
    for k, lane_freqs in tr.crossings:
        print(f"  换序 @ 工况{k}（m_a={grid[k]}）：通道频率 "
              + ", ".join(f"{f:.4f}" for f in lane_freqs[:8]) + " …")

    print(f"\n留一法交叉验证（{len(cases) - 2} 个内部点 × "
          f"{cases[0][1].shape[0]} 通道）：")
    print(f"{'held m_a':>10}{'最大频率误差':>14}{'最差通道':>10}{'最小振型MAC':>14}")
    rows, max_err, min_mac = loo(cases, args.tol)
    for r in rows:
        print(f"{r['held_m_a']:>10.4f}{r['max_freq_err'] * 100:>13.3f}%"
              f"{r['worst_lane']:>10}{r['min_mac']:>14.5f}")
    print(f"\n汇总：最大频率误差 {max_err * 100:.3f}%，最小振型 MAC {min_mac:.5f}")
    ok = max_err < args.tol
    print(f"结论：{'PASS' if ok else 'FAIL'}（容差 {args.tol * 100:.0f}%）")
    if args.save:
        path = save_error_table(cases, rows, args.tol)
        print(f"误差表已写入：{path}")
    sys.exit(0 if ok else 1)


def save_error_table(cases, rows, tol, path=None):
    """把留出点误差按插值区间固化成 JSON（UI 误差角标的数据源）。

    区间 (grid[i], grid[i+1]) 的误差取两端工况留出点误差之最大
    （端点工况无留出值时取唯一的相邻内部点）。
    """
    import datetime
    import json

    path = path or (config.DATA_DIR / "interp_error.json")
    grid = [c[0] for c in cases]
    by_ma = {r["held_m_a"]: r for r in rows}
    intervals = []
    for i in range(len(grid) - 1):
        contrib = [by_ma[grid[j]] for j in (i, i + 1) if grid[j] in by_ma]
        intervals.append({
            "lo": grid[i], "hi": grid[i + 1],
            "max_freq_err": max(r["max_freq_err"] for r in contrib),
            "min_mac": min(r["min_mac"] for r in contrib)})
    doc = {"generated": datetime.datetime.now().isoformat(timespec="seconds"),
           "tol": tol, "grid": grid, "intervals": intervals,
           "overall": {"max_freq_err": max(r["max_freq_err"] for r in rows),
                       "min_mac": min(r["min_mac"] for r in rows)}}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


if __name__ == "__main__":
    main()
