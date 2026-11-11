"""风振精华段与风压权重：10 Hz 段数据服务 + 膜面脉动风压插值权重。

端点数据（由 masslens_ui.server 调用）：
- list_segments()：data_wind/w*.npz 的可用精华段清单
- load_segment(name)：读 npz → 各数组
- press_weights()：12 风压点 → 全节点 IDW 插值权重 (N,12) float32
"""
from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np

from . import config

SEG_DIR = config.OUT_DIR


def list_segments():
    """返回 [{name, start_s, dur_s, fs}]（按 start_s 排序）。"""
    out = []
    for p in sorted(SEG_DIR.glob("w*.npz")):
        try:
            z = np.load(p)
            out.append({"name": p.stem,
                        "start_s": float(z["meta_start_s"]),
                        "dur_s": float(z["meta_dur_s"]),
                        "fs": float(z["meta_fs"])})
        except Exception:
            continue
    out.sort(key=lambda d: d["start_s"])
    return out


def load_segment(name):
    """读精华段 npz。返回 dict(acc, gnss, pressure, speed, direct, time, meta)。"""
    safe = "".join(c for c in name if c.isalnum() or c in "_-")
    p = SEG_DIR / f"{safe}.npz"
    if not p.exists():
        return None
    z = np.load(p)
    keys = ("acc", "gnss", "pressure", "speed", "direct", "time")
    d = {k: z[k] for k in keys if k in z.files}
    meta = {k[5:]: (z[k].item() if z[k].ndim == 0 else z[k])
            for k in z.files if k.startswith("meta_")}
    d["meta"] = meta
    return d


_W_CACHE = None


def press_weights(h5_path=None, sensor_csv=None, power=2.0, eps2=25.0):
    """IDW 权重 (N,12)：行 = 节点，列 = 12 风压点，行和 = 1（缓存复用）。"""
    global _W_CACHE
    if _W_CACHE is not None:
        return _W_CACHE
    import csv as _csv
    h5_path = h5_path or config.H5_PATH
    sensor_csv = sensor_csv or config.SENSOR_MAP_CSV
    with h5py.File(h5_path, "r") as h:
        coords = h["geometry/coords"][:]
    with open(sensor_csv, encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip() and not ln.startswith("#")]
    sp = np.array([[float(r["fe_x_m"]), float(r["fe_y_height_m"]),
                    float(r["fe_z_m"])] for r in _csv.DictReader(lines)])
    d2 = ((coords[:, None, :] - sp[None, :, :]) ** 2).sum(axis=2)  # (N,12)
    w = 1.0 / (d2 + eps2) ** (power / 2.0)
    w = (w / w.sum(axis=1, keepdims=True)).astype(np.float32)
    _W_CACHE = w
    return w
