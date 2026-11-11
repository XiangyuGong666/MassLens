"""切窗：从 10 Hz 大 .mat 文件切出指定时段到紧凑 npz。

用法（项目根目录）：
    python -m windlens_engine.extract --start 218700 --dur 600 --out w1
切窗口径：样本号 = round(秒 × 10)；只切片读取，绝不全量装载大文件。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import h5py
import numpy as np
import scipy.io

from . import config


def extract_window(start_s, dur_s, out_path=None):
    """切出 [start_s, start_s+dur_s) 的全部通道。返回 (npz 路径, 健康报告 dict)。"""
    i0 = int(round(start_s * config.FS))
    if i0 < 0:
        raise ValueError(f"start_s={start_s} 为负：超出记录范围")
    n = int(round(dur_s * config.FS))
    if i0 >= config.N_SAMPLES:
        raise ValueError(f"start_s={start_s} 超出记录范围（共 {config.N_SAMPLES} 样本）")
    i1 = min(i0 + n, config.N_SAMPLES)
    out = {"i0": i0, "i1": i1, "start_s": start_s, "dur_s": dur_s, "fs": config.FS}

    arrs = {}
    for name, (path, key, nch) in config.MAT.items():
        with h5py.File(path, "r") as h:
            arrs[name] = h[key][:, i0:i1]
    for name, (path, key) in config.V5_MAT.items():
        v = scipy.io.loadmat(path)[key]
        arrs[name] = np.asarray(v, dtype=np.float64).reshape(-1)[i0:i1]

    # 健康报告：各文件各通道的有限值率与非零率
    health = {}
    for name, (path, key, nch) in config.MAT.items():
        a = arrs[name]
        finite = np.isfinite(a).mean(axis=1)
        nonzero = (np.abs(a) > 0).mean(axis=1)
        health[name] = {"finite_min": float(finite.min()),
                        "nonzero_min": float(nonzero.min()),
                        "bad_channels": [int(c) for c in range(nch)
                                         if finite[c] < 1 or nonzero[c] < 0.99]}

    out_path = out_path or (config.OUT_DIR / f"w{int(start_s)}.npz")
    out_path = Path(out_path)
    if out_path.suffix != ".npz":
        out_path = config.OUT_DIR / (out_path.name + ".npz")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_path, **arrs, **{f"meta_{k}": v for k, v in out.items()})
    return out_path, health


def load_window(npz_path):
    """读回窗口：dict(arrays) + meta。"""
    z = np.load(npz_path)
    w = {k: z[k] for k in z.files if not k.startswith("meta_")}
    meta = {k[5:]: (z[k].item() if z[k].ndim == 0 else z[k])
            for k in z.files if k.startswith("meta_")}
    return w, meta


def main():
    ap = argparse.ArgumentParser(prog="windlens_engine.extract")
    ap.add_argument("--start", type=float, required=True, help="起点秒（共时间轴）")
    ap.add_argument("--dur", type=float, default=600.0, help="时长秒，默认 600")
    ap.add_argument("--out", default=None, help="输出 npz（默认 data_wind/w<start>.npz）")
    args = ap.parse_args()

    path, health = extract_window(args.start, args.dur, args.out)
    print(f"窗口 [{args.start}, {args.start + args.dur}) s → {path}")
    for name, h in health.items():
        status = "OK" if not h["bad_channels"] else f"坏通道 {h['bad_channels']}"
        print(f"  {name}: 有限值≥{h['finite_min']:.3f} 非零≥{h['nonzero_min']:.3f} {status}")


if __name__ == "__main__":
    main()
