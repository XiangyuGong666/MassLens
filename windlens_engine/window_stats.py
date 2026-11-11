"""逐窗统计：流式扫大 .mat，算 112,142 个 60 s 窗的逐通道 RMS。

用法（项目根目录）：python -m windlens_engine.window_stats [--only pressure]
输出：data_wind/window_rms_pressure.npz、window_rms_gnss.npz（各 (112142, ch) float32）。

口径：风压数据为用户去基线后的"脉动风压"（非绝对风压，界面须标注）；
GNSS 为去基线后动态位移（单位未标定，疑为 mm）。逐窗 RMS = 波动强度，
不需要滤波——脉动口径下窗口内 RMS 即波动量。
"""
from __future__ import annotations

import argparse
import time

import h5py
import numpy as np

from . import config

WIN = 600  # 60 s × 10 Hz
N_WIN = config.N_SAMPLES // WIN   # 112,142 整窗（末尾不足一窗舍去）


def window_rms(key):
    """对 config.MAT[key] 的文件流式算逐窗逐通道 RMS。返回 (N_WIN, ch) float32。"""
    path, var, nch = config.MAT[key]
    out = np.empty((N_WIN, nch), dtype=np.float32)
    t0 = time.time()
    with h5py.File(path, "r") as h:
        d = h[var]
        for w in range(N_WIN):
            seg = d[:, w * WIN:(w + 1) * WIN]
            seg = seg - seg.mean(axis=1, keepdims=True)
            out[w] = np.sqrt((seg ** 2).mean(axis=1))
            if (w + 1) % 20000 == 0:
                dt = time.time() - t0
                print(f"  {key}: {w + 1}/{N_WIN} 窗, {dt:.0f}s, "
                      f"约 {dt / (w + 1) * (N_WIN - w - 1):.0f}s 剩余", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser(prog="windlens_engine.window_stats")
    ap.add_argument("--only", choices=["pressure", "gnss"], default=None)
    args = ap.parse_args()
    keys = [args.only] if args.only else ["pressure", "gnss"]
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    for k in keys:
        rms = window_rms(k)
        path = config.OUT_DIR / f"window_rms_{k}.npz"
        np.savez_compressed(path, rms=rms, win_s=WIN / config.FS)
        print(f"{k}: {rms.shape} → {path}", flush=True)


if __name__ == "__main__":
    main()
