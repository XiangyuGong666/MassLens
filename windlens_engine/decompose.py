"""WLS 模态分解引擎：每时刻从传感器读数解模态坐标 q(t)。

模型（逐时刻）：
    加速度通道：a_k(t) ≈ Σ_j (−ω_j²) φ_j(x_k)·q_j(t)   （窄带模态响应近似，
        第 j 阶响应集中在 ω_j 附近时成立——模态分解的标准口径）
    GNSS 通道：  d_k(t) = Σ_j φ_j(x_k)·q_j(t)            （位移直接叠加）

合起来是 57×M 的线性系统 y = Aq，权重 w_acc / w_gnss 按传感器可信度给。
A 只依赖基与权重，正规方程的伪逆 G = (AᵀWA)⁻¹AᵀW 一次性预计算，
之后每帧 q = G·y 是一次矩阵-向量乘（微秒级）。
"""
from __future__ import annotations

import numpy as np

TWO_PI = 2.0 * np.pi


class ModalDecomposer:
    """freqs (M,)、phi_acc (M,36)、phi_gnss (M,21) 与权重确定后，分解是纯线性映射。"""

    def __init__(self, freqs, phi_acc, phi_gnss, w_acc=1.0, w_gnss=1.0,
                 acc_rows=None, gnss_rows=None):
        """acc_rows/gnss_rows: 只使用部分通道（留一法验证用）；默认全部。"""
        m = len(freqs)
        omega2 = (TWO_PI * freqs) ** 2
        ar = slice(None) if acc_rows is None else acc_rows
        gr = slice(None) if gnss_rows is None else gnss_rows
        a_acc = -omega2[:, None] * phi_acc[:, ar]      # (M, n_acc)
        a_gnss = phi_gnss[:, gr]                       # (M, n_gnss)
        a = np.concatenate([a_acc * w_acc, a_gnss * w_gnss], axis=1)  # (M, n)
        self.design_ = a.T                              # (n, M)
        self.g_ = np.linalg.pinv(self.design_)          # (M, n) 伪逆
        self.w = (w_acc, w_gnss)
        self.rows = (ar, gr)
        self.n_modes = m

    def q_series(self, y_acc, y_gnss):
        """y_acc (n_acc,T)、y_gnss (n_gnss,T) → q (M,T)。权重在构建时已并入。"""
        w_acc, w_gnss = self.w
        y = np.concatenate([y_acc * w_acc, y_gnss * w_gnss], axis=0)
        return self.g_ @ y

    def reconstruct_channels(self, q, freqs, phi_acc, phi_gnss):
        """由 q 反算传感器通道信号（留一法/拟合质量用）。返回 (acc_hat, gnss_hat)，
        形状 (T, 通道数)——注意与本包惯用的 (通道, T) 相反。"""
        omega2 = (TWO_PI * freqs) ** 2
        acc_hat = (-omega2[:, None] * phi_acc).T @ q
        gnss_hat = phi_gnss.T @ q
        return acc_hat.T, gnss_hat.T


def r2(y, yhat):
    """决定系数（对窄带振动另报相关系数更直观）。"""
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def corr(y, yhat):
    sy, sh = float(np.std(y)), float(np.std(yhat))
    if sy == 0 or sh == 0:
        return float("nan")
    return float(np.corrcoef(y, yhat)[0, 1])
