"""POD（特征正交分解）脉动风压：全季 12 通道协方差特征分解。

用法（项目根目录）：python -m windlens_engine.pod [--sub 4]
产物：data_wind/pod_pressure.npz
  eigvals (12,)      特征值（方差，降序）
  eigvecs (12,12)    特征向量（列 = 空间模态 P_k，12 风压点口径）
  mean (12,)         全季通道均值（分解前去均值用）
  n_used             使用样本数

口径：脉动风压（去基线，非绝对风压）。先逐窗（60 s）去均值再累计协方差，
与窗级统计同口径；--sub N 对时间抽稀（默认 1=不抽稀）。
"""
from __future__ import annotations

import argparse
import time

import h5py
import numpy as np

from . import config

WIN = 600  # 60 s × 10 Hz


def compute_cov(sub=1):
    """流式累计 12 通道脉动风压协方差。返回 (C (12,12), mean, n_used)。"""
    path, var, nch = config.MAT["pressure"]
    acc = np.zeros((nch, nch), dtype=np.float64)
    ssum = np.zeros(nch, dtype=np.float64)
    n_used = 0
    t0 = time.time()
    with h5py.File(path, "r") as h:
        d = h[var]
        n_win = d.shape[1] // WIN
        for w in range(n_win):
            seg = d[:, w * WIN:(w + 1) * WIN:sub].astype(np.float64)
            seg -= seg.mean(axis=1, keepdims=True)
            ssum += seg.mean(axis=1)  # ≈0，仅记录
            acc += seg @ seg.T
            n_used += seg.shape[1]
            if (w + 1) % 30000 == 0:
                print(f"  {w + 1}/{n_win} 窗, {time.time() - t0:.0f}s", flush=True)
    return acc / max(n_used - 1, 1), ssum / max(n_win, 1), n_used


def run(sub=1):
    c, mean, n_used = compute_cov(sub)
    eigvals, eigvecs = np.linalg.eigh(c)
    order = np.argsort(-eigvals)
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]
    # 符号约定：模态最大绝对值分量取正（显示一致性）
    for k in range(eigvecs.shape[1]):
        j = int(np.argmax(np.abs(eigvecs[:, k])))
        if eigvecs[j, k] < 0:
            eigvecs[:, k] = -eigvecs[:, k]
    out = config.OUT_DIR / "pod_pressure.npz"
    np.savez_compressed(out, eigvals=eigvals, eigvecs=eigvecs,
                        mean=mean, n_used=n_used)
    total = eigvals.sum()
    print(f"方差贡献率: 前1 {eigvals[0] / total * 100:.1f}%  "
          f"前2 {eigvals[:2].sum() / total * 100:.1f}%  "
          f"前3 {eigvals[:3].sum() / total * 100:.1f}%  "
          f"前4 {eigvals[:4].sum() / total * 100:.1f}%")
    print(f"→ {out}")
    return eigvals, eigvecs


_DOC_CACHE = None


def load_pod(k_frac=0.80, k_max=4):
    """读 pod_pressure.npz → dict(eigvals, fracs, K, vecs (12,K))；无文件返回 None。

    K = 累计方差 ≥ k_frac 所需的最少模态数（≤k_max）。
    """
    global _DOC_CACHE
    path = config.OUT_DIR / "pod_pressure.npz"
    if not path.exists():
        return None
    if _DOC_CACHE is None:
        z = np.load(path)
        _DOC_CACHE = {"eigvals": z["eigvals"], "eigvecs": z["eigvecs"],
                      "n_used": int(z["n_used"])}
    eig = _DOC_CACHE["eigvals"]
    total = float(eig.sum())
    fracs = [float(v / total) for v in eig]
    cum = np.cumsum(fracs)
    k = int(min(np.searchsorted(cum, k_frac) + 1, k_max))
    return {"eigvals": fracs, "k": k,
            "vecs": _DOC_CACHE["eigvecs"][:, :k],
            "n_used": _DOC_CACHE["n_used"]}


def compute_series(k_modes=4):
    """全季逐窗 POD 时程系数 RMS。返回 (n_win, k_modes) float32。"""
    z = np.load(config.OUT_DIR / "pod_pressure.npz")
    vecs = z["eigvecs"][:, :k_modes]                     # (12,K)
    path, var, nch = config.MAT["pressure"]
    with h5py.File(path, "r") as h:
        d = h[var]
        n_win = d.shape[1] // WIN
        out = np.empty((n_win, k_modes), dtype=np.float32)
        t0 = time.time()
        for w in range(n_win):
            seg = d[:, w * WIN:(w + 1) * WIN].astype(np.float64)
            seg -= seg.mean(axis=1, keepdims=True)
            a = vecs.T @ seg                               # (K,T)
            out[w] = np.sqrt((a ** 2).mean(axis=1))
            if (w + 1) % 30000 == 0:
                print(f"  series {w + 1}/{n_win} 窗, {time.time() - t0:.0f}s",
                      flush=True)
    return out


def run_series(k_modes=4):
    out = compute_series(k_modes)
    path = config.OUT_DIR / "pod_series.npz"
    np.savez_compressed(path, a_rms=out)
    print(f"→ {path}  shape={out.shape}")
    return out


_SER_CACHE = None


def load_series():
    """读 pod_series.npz → (n_win,K) float32；无文件返回 None。"""
    global _SER_CACHE
    path = config.OUT_DIR / "pod_series.npz"
    if not path.exists():
        return None
    if _SER_CACHE is None:
        _SER_CACHE = np.load(path)["a_rms"].astype(np.float32)
    return _SER_CACHE


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="windlens_engine.pod")
    ap.add_argument("--sub", type=int, default=1)
    ap.add_argument("--series", action="store_true",
                    help="计算全季 POD 时程系数 RMS（pod_series.npz）")
    args = ap.parse_args()
    if args.series:
        run_series()
    else:
        run(args.sub)
