"""深化引擎：逐窗风压峰值（max|p|）+ 按 P1 指数峰值自动选段。

用法（项目根目录）：
    python -m windlens_engine.deepen pressmax   # 逐窗 max|p| → window_pressmax.npz
    python -m windlens_engine.deepen segments   # P1 峰自动选段 → data_wind/w7..wNN.npz
"""
from __future__ import annotations

import argparse
import time

import h5py
import numpy as np

from . import config, extract, pod

WIN = 600


def pressmax():
    """全季 12 通道逐窗 max|p|（流式）→ window_pressmax.npz。"""
    path, var, nch = config.MAT["pressure"]
    with h5py.File(path, "r") as h:
        d = h[var]
        n_win = d.shape[1] // WIN
        out = np.empty((n_win, nch), dtype=np.float32)
        t0 = time.time()
        for w in range(n_win):
            seg = d[:, w * WIN:(w + 1) * WIN]
            out[w] = np.abs(seg).max(axis=1)
            if (w + 1) % 30000 == 0:
                print(f"  pressmax {w + 1}/{n_win}, {time.time() - t0:.0f}s", flush=True)
    path_out = config.OUT_DIR / "window_pressmax.npz"
    np.savez_compressed(path_out, pmax=out)
    print(f"→ {path_out}  shape={out.shape}")


def auto_segments(n_seg=6, min_gap_h=1.0, half_dur_s=300.0):
    """按 P1 指数（pod_series a_1 RMS）峰值自动选段并提取 npz。"""
    ser = pod.load_series()
    if ser is None:
        raise RuntimeError("缺 pod_series.npz，先跑 python -m windlens_engine.pod --series")
    a1 = ser[:, 0]
    existing = [s["start_s"] + s["dur_s"] / 2 for s in
                __import__("windlens_engine.segments", fromlist=["segments"]
                           ).list_segments()]
    gap = min_gap_h * 3600
    cand = np.argsort(-a1)
    picked = []
    for i in cand:
        c = (i + 0.5) * 60.0  # 窗中心秒
        if any(abs(c - e) < gap for e in existing):
            continue
        if any(abs(c - p) < gap for p in picked):
            continue
        picked.append(c)
        if len(picked) >= n_seg:
            break
    # 复用已存在段名
    taken = {s["name"] for s in
             __import__("windlens_engine.segments", fromlist=["segments"]
                        ).list_segments()}
    n_next = 7
    for c in picked:
        while f"w{n_next}" in taken:
            n_next += 1
        start = max(c - half_dur_s, 0.0)
        name = f"w{n_next}"
        path, health = extract.extract_window(start, half_dur_s * 2, name)
        bad = sum(len(h["bad_channels"]) for h in health.values())
        print(f"  {name}: 中心 {c / 86400:.2f} d, P1_RMS={a1[int(c // 60)]:.4f}, "
              f"坏通道 {bad} → {path}", flush=True)
        taken.add(name)
        n_next += 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="windlens_engine.deepen")
    ap.add_argument("action", choices=["pressmax", "segments"])
    args = ap.parse_args()
    if args.action == "pressmax":
        pressmax()
    else:
        auto_segments()
