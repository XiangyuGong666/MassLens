"""插值器：query(params) -> (freqs, shapes)，UI 与数据库之间的唯一接口。

口径：
- 频率在 1/f²（即 1/ω² 差常数因子）空间线性插值。均布附加质量下
  1/ω² = (M₀ + m_a·Ã)/K̃ 对 m_a 严格线性（振型与刚度不变时），
  在该空间做线性插值最接近物理；留出点交叉验证见 validate.py。
- 振型在 MAC 跟踪对齐（含变号校正）后线性插值，再按两端点范数线性过渡
  重定标，近似保持质量归一化口径（精确 M 矩阵不在库中；该重定标的误差
  随两端点 MAC→1 消失，相邻工况 MAC>0.99 时可忽略）。
- params 目前只有 m_a（kg/m²）；多参数（分区权重等）预留 dict 接口。
"""
from __future__ import annotations

import numpy as np

from masslens_data import config, schema

from . import tracking


class Interpolator:
    """构建时完成模态跟踪与对齐并全部读入内存，query 只做 numpy 插值（<10 ms 量级）。"""

    def __init__(self, db_path=None, cases=None):
        """db_path 默认 config.H5_PATH；cases 可注入 [(m_a, freqs, shapes), ...]
        （交叉验证用子网格，省去重复读盘）。"""
        if cases is None:
            db_path = db_path or config.H5_PATH
            cases = [schema.load_case(db_path, name)
                     for name, _ in schema.list_cases(db_path)]
        cases = sorted(cases, key=lambda c: c[0])
        self.grid_ = np.array([c[0] for c in cases], dtype=np.float64)
        self.track_ = tracking.track_cases(cases)
        g = len(cases)
        m = self.track_.lane_of.shape[1]
        # 按通道重排频率与振型（含变号校正）：(G,M) / (G,M,N,3)
        self.freqs_ = np.stack([cases[k][1][self.track_.lane_of[k]]
                                for k in range(g)])
        self.shapes_ = np.stack(
            [cases[k][2][self.track_.lane_of[k]].astype(np.float64)
             * self.track_.signs[k, :, None, None] for k in range(g)])
        self.norms_ = np.linalg.norm(
            self.shapes_.reshape(g, m, -1), axis=2)  # (G,M) 各阶欧氏范数
        self.inv_f2_ = 1.0 / self.freqs_ ** 2        # (G,M) 1/f² 插值空间

    @property
    def grid_range(self):
        """可插值的 m_a 区间 (min, max)。"""
        return float(self.grid_[0]), float(self.grid_[-1])

    @property
    def n_lanes(self):
        return self.freqs_.shape[1]

    @staticmethod
    def _parse(params):
        if isinstance(params, dict):
            params = params["m_a"]
        return float(params)

    def _bracket(self, m_a):
        lo, hi = self.grid_range
        if not (lo - 1e-12 <= m_a <= hi + 1e-12):
            raise ValueError(
                f"超出数据库范围：m_a={m_a}，可插值区间 [{lo}, {hi}] kg/m²")
        m_a = min(max(m_a, lo), hi)
        k1 = int(np.searchsorted(self.grid_, m_a, side="right"))
        k1 = min(max(k1, 1), len(self.grid_) - 1)
        k0 = k1 - 1
        span = self.grid_[k1] - self.grid_[k0]
        alpha = 0.0 if span == 0 else float((m_a - self.grid_[k0]) / span)
        return k0, k1, alpha

    def query(self, params):
        """params: m_a 数值（kg/m²）或 {"m_a": m_a}。

        返回 (freqs (M,), shapes (M,N,3))：频率与振型均按通道序
        （= m_a=0 时的频率升序），振型已跨工况对齐并校正变号。
        """
        m_a = self._parse(params)
        k0, k1, a = self._bracket(m_a)
        y = (1.0 - a) * self.inv_f2_[k0] + a * self.inv_f2_[k1]
        freqs = 1.0 / np.sqrt(y)
        shapes = (1.0 - a) * self.shapes_[k0] + a * self.shapes_[k1]
        target = (1.0 - a) * self.norms_[k0] + a * self.norms_[k1]
        cur = np.linalg.norm(shapes.reshape(self.n_lanes, -1), axis=1)
        shapes = shapes * (target / cur)[:, None, None]
        return freqs, shapes.astype(np.float32)

    def diagnostics(self, params):
        """UI 用的内部状态（第 4 晚 MAC 条/误差角标）：两端点振型逐通道 MAC 等。"""
        m_a = self._parse(params)
        k0, k1, a = self._bracket(m_a)
        s0 = self.shapes_[k0].reshape(self.n_lanes, -1)
        s1 = self.shapes_[k1].reshape(self.n_lanes, -1)
        dot = np.abs(np.einsum("ij,ij->i", s0, s1))
        mac_bracket = dot ** 2 / (np.einsum("ij,ij->i", s0, s0)
                                  * np.einsum("ij,ij->i", s1, s1))
        return {"m_a": m_a, "bracket": (k0, k1, a),
                "mac_bracket": mac_bracket,
                "min_mac_track": float(self.track_.lane_mac[1:].min()),
                "n_crossings": len(self.track_.crossings)}
