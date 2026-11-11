"""MAC 矩阵逐参数步模态跟踪 —— 全项目最重要的正确性保障。

防止模态换序/变号导致插值错误：以第 0 个工况（m_a 最小）的原始阶次为锚建立
"通道"（lane），之后每个参数步用上一步已对齐振型与本步振型的 MAC 矩阵做
匈牙利最优指派，并逐通道校正变号（φ 与 -φ 等价，不校正会把插值拉成 0）。

跨工况 MAC 用普通内积：振型各自质量归一（φᵀMφ=1），均布附加质量下 M 随 m_a
线性缓变，相邻步 MAC>0.99 时与质量加权 MAC 的差别可忽略（validate.py 报告
全程最小值，作为该近似的经验校验）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment


def mac_matrix(shapes_a, shapes_b):
    """(Ma,N,3) × (Mb,N,3) → MAC 矩阵 (Ma,Mb)，行对应 a、列对应 b。"""
    a = shapes_a.reshape(shapes_a.shape[0], -1).astype(np.float64)
    b = shapes_b.reshape(shapes_b.shape[0], -1).astype(np.float64)
    dot = a @ b.T
    na = np.einsum("ij,ij->i", a, a)
    nb = np.einsum("ij,ij->i", b, b)
    return np.abs(dot) ** 2 / (na[:, None] * nb[None, :])


@dataclass
class TrackResult:
    """逐工况的通道对应关系（G 个工况、M 个通道）。

    lane_of[k, j]  工况 k 中属于通道 j 的本地阶次号（0-based）
    lane_mac[k, j] 该指派的 MAC（锚点工况 k=0 恒为 1）
    signs[k, j]    ±1：工况 k 的本地振型乘上它即与通道参考同向
    crossings      换序记录 [(k, lane_freqs), ...]：工况 k 处各通道频率
                   排序与锚点序不一致（通道身份不变，频率序交叉）
    """
    lane_of: np.ndarray
    lane_mac: np.ndarray
    signs: np.ndarray
    crossings: list = field(default_factory=list)


def track_cases(cases):
    """cases: [(m_a, freqs (M,), shapes (M,N,3)), ...]，按 m_a 升序。

    返回 TrackResult；锚点 = cases[0]。lane_of[k] 即"通道 → 本地阶次"置换。
    """
    g = len(cases)
    if g < 2:
        raise ValueError("模态跟踪至少需要 2 个工况")
    m = cases[0][1].shape[0]
    for k in range(1, g):
        if cases[k][1].shape[0] != m or cases[k][2].shape[0] != m:
            raise ValueError(f"工况 {k} 阶数 {cases[k][1].shape[0]} ≠ 锚点 {m}："
                             f"跟踪要求各工况阶数一致（否则通道会静默错位）")
    lane_of = np.zeros((g, m), dtype=np.int32)
    lane_mac = np.ones((g, m), dtype=np.float64)
    signs = np.ones((g, m), dtype=np.int8)
    lane_of[0] = np.arange(m)

    prev = cases[0][2].astype(np.float64)  # 上一步已对齐振型（通道序）
    crossings = []
    for k in range(1, g):
        _, freqs, shapes = cases[k]
        cur = shapes.astype(np.float64)
        mac = mac_matrix(prev, cur)
        rows, cols = linear_sum_assignment(-mac)  # 匈牙利指派：最大化总 MAC
        for r, c in zip(rows, cols):
            lane_of[k, r] = c
            lane_mac[k, r] = mac[r, c]
            s = float(np.dot(prev[r].ravel(), cur[c].ravel()))
            signs[k, r] = -1 if s < 0 else 1
        prev = cur[lane_of[k]] * signs[k, :, None, None]  # 本步对齐结果作下步参考
        lane_freqs = freqs[lane_of[k]]
        if not np.all(np.diff(lane_freqs) > 0):
            crossings.append((k, lane_freqs.copy()))
    return TrackResult(lane_of, lane_mac, signs, crossings)
