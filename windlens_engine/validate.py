"""离线验证：留一传感器重构、GNSS 单位自标定、q(t) 谱峰核对。

用法（项目根目录）：python -m windlens_engine.validate [--npz data_wind/w218700.npz]
校验项：
 1. 留一法：每次拿掉 1 个加速度通道重做分解，重构该通道，报 R²/相关系数
 2. GNSS 单位标定：仅用加速度分解出 q(t)，预测 GNSS 通道位移并与实测比增益
 3. q(t) 功率谱峰与 FE/实测频率的对应
"""
from __future__ import annotations

import argparse

import numpy as np
from scipy.signal import welch

from . import basis, config, decompose, extract, preprocess


def leave_one_out(bas, y_acc, y_gnss, w_acc=1.0, w_gnss=0.0, n_ch=36):
    """36 个加速度通道轮流留一。返回 [(ch, r2, corr), ...]。

    默认 w_gnss=0：GNSS 位移单位未标定（疑为 mm），与 m/s² 级加速度同权混入
    会让 GNSS 行主导设计矩阵、分解爆炸；GNSS 一律待单位标定后再加权。
    """
    freqs, phi_acc, phi_gnss = bas["freqs"], bas["phi_acc"], bas["phi_gnss"]
    rows = []
    for c in range(n_ch):
        keep = [i for i in range(n_ch) if i != c]
        dec = decompose.ModalDecomposer(freqs, phi_acc, phi_gnss,
                                        w_acc, w_gnss, acc_rows=keep)
        q = dec.q_series(y_acc[keep], y_gnss)
        omega2 = (2 * np.pi * freqs) ** 2
        acc_hat = (-omega2 * phi_acc[:, c]) @ q   # 留出的第 c 通道（向量×矩阵）
        rows.append((c, decompose.r2(y_acc[c], acc_hat),
                     decompose.corr(y_acc[c], acc_hat)))
    return rows


def gnss_calibration(bas, y_acc, y_gnss, w_acc=1.0):
    """只用加速度分解 q(t)，预测 GNSS 位移，回归实测得增益与相关。"""
    dec = decompose.ModalDecomposer(bas["freqs"], bas["phi_acc"], bas["phi_gnss"],
                                    w_acc=w_acc, w_gnss=0.0)
    q = dec.q_series(y_acc, np.zeros_like(y_gnss))
    disp_hat = bas["phi_gnss"].T @ q                        # (21,T)
    out = []
    for c in range(y_gnss.shape[0]):
        dh = disp_hat[c]                                     # 第 c 通道时程
        g = float(dh @ y_gnss[c] / (dh @ dh)) if dh @ dh > 0 else float("nan")
        out.append((c, g, decompose.corr(y_gnss[c], dh)))
    return out


def psd_check(q, freqs_fe, fs=config.FS):
    """q(t) 各通道 welch 谱主峰 vs FE 频率。"""
    rows = []
    for j in range(q.shape[0]):
        f, p = welch(q[j], fs=fs, nperseg=min(2048, q.shape[1]))
        fpk = f[np.argmax(p[(f > 0.2)]) + int(np.sum(f <= 0.2))]
        rows.append((j, float(fpk), float(freqs_fe[j])))
    return rows


def main():
    ap = argparse.ArgumentParser(prog="windlens_engine.validate")
    ap.add_argument("--npz", default=None)
    ap.add_argument("--w-gnss", type=float, default=0.0,
                    help="分解时 GNSS 权重（默认 0=纯加速度，留 GNSS 做独立检验）")
    args = ap.parse_args()

    npz = args.npz
    if npz is None:
        cands = sorted(config.OUT_DIR.glob("w*.npz"),
                       key=lambda p: p.stat().st_mtime)
        if not cands:
            raise SystemExit("data_wind/ 下没有窗口 npz；请先 extract 或用 --npz 指定")
        npz = str(cands[-1])  # 最新修改的窗口文件（统计 npz 不在 w* 模式内）
    print(f"窗口: {npz}")
    w, meta = extract.load_window(npz)
    w = preprocess.preprocess_window(w)
    bas = basis.build_basis()
    print(f"基: {len(bas['freqs'])} 阶, GNSS 位置 {bas['gnss_locs']}")

    print("\n[1] 留一法加速度通道重构（36 通道）:")
    rows = leave_one_out(bas, w["acc"], w["gnss"], w_gnss=args.w_gnss)
    r2s = np.array([r[1] for r in rows])
    corrs = np.array([r[2] for r in rows])
    for c, r2v, cv in rows:
        print(f"  ch{c + 1:>2} (loc{c // 3 + 1} {'xyz'[c % 3]}): R²={r2v:6.3f} corr={cv:6.3f}")
    print(f"  汇总: R² 中位 {np.median(r2s):.3f} 最小 {r2s.min():.3f} | "
          f"corr 中位 {np.median(corrs):.3f} 最小 {corrs.min():.3f}")

    print("\n[2] GNSS 单位标定（加速度-only 分解 → 预测 GNSS 位移）:")
    cal = gnss_calibration(bas, w["acc"], w["gnss"])
    for c, g, cv in cal:
        p, comp = c // 3 + 1, "xyz"[c % 3]
        print(f"  GNSS点{p} {comp}: 增益 {g:10.3f}  corr={cv:6.3f}")

    print("\n[3] q(t) 谱峰 vs FE 频率:")
    dec = decompose.ModalDecomposer(bas["freqs"], bas["phi_acc"], bas["phi_gnss"],
                                    w_gnss=args.w_gnss)
    q = dec.q_series(w["acc"], w["gnss"])
    for j, fpk, ffe in psd_check(q, bas["freqs"]):
        print(f"  模态{j + 1:>2}: 谱峰 {fpk:.3f} Hz  FE {ffe:.3f} Hz")


if __name__ == "__main__":
    main()
