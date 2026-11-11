"""第 2 晚单元测试：模态跟踪 + 插值器 + 留出点误差验收（<5%）。

用法（项目根目录）：python -m unittest tests.test_interp -v
"""
from __future__ import annotations

import unittest

import numpy as np

from masslens_data import config, schema
from masslens_interp import tracking, validate
from masslens_interp.interpolator import Interpolator


def _load_cases():
    return sorted([schema.load_case(config.H5_PATH, name)
                   for name, _ in schema.list_cases(config.H5_PATH)],
                  key=lambda c: c[0])


class TestTrackingSynthetic(unittest.TestCase):
    """合成数据：换序 + 变号必须被跟踪恢复（不依赖数据库）。"""

    def test_swap_and_sign_flip(self):
        rng = np.random.default_rng(7)
        q, _ = np.linalg.qr(rng.normal(size=(15, 4)))  # 4 个正交振型（15 自由度）
        base = q.T.reshape(4, 5, 3)                    # (M=4, N=5, 3)
        f0 = np.array([0.60, 0.70, 0.80, 0.90])
        cases = [(0.0, f0, base)]
        for k in range(1, 4):  # 通道 0/1 互换且变号逐工况交替
            order = [1, 0, 2, 3]
            sgn = np.array([(-1) ** k, (-1) ** (k + 1), 1, -1])
            shapes = base[order] * sgn[:, None, None]
            shapes += 0.01 * k * rng.normal(size=shapes.shape)  # 轻微演化
            cases.append((0.5 * k, f0[order] * (1 - 0.05 * k), shapes))
        tr = tracking.track_cases(cases)
        for k in range(1, 4):
            self.assertEqual(list(tr.lane_of[k]), [1, 0, 2, 3])
            self.assertTrue((tr.lane_mac[k] > 0.9).all())

    def test_mac_matrix_diagonal(self):
        rng = np.random.default_rng(3)
        shapes = rng.normal(size=(6, 10, 3))
        mac = tracking.mac_matrix(shapes, shapes)
        np.testing.assert_allclose(np.diag(mac), 1.0, atol=1e-12)


class TestInterpolator(unittest.TestCase):
    """针对真实数据库（data/masslens_shed_v1.h5）。"""

    @classmethod
    def setUpClass(cls):
        cls.cases = _load_cases()
        cls.itp = Interpolator(cases=cls.cases)
        cls.track = tracking.track_cases(cls.cases)

    def test_query_at_grid_points_exact(self):
        """网格点上 query 必须精确还原该工况（按通道序）的频率与振型。"""
        for k, (m_a, freqs, shapes) in enumerate(self.cases):
            f_q, s_q = self.itp.query(m_a)
            np.testing.assert_allclose(f_q, freqs[self.track.lane_of[k]],
                                       rtol=1e-9, err_msg=f"m_a={m_a} 频率")
            aligned = shapes[self.track.lane_of[k]].astype(np.float64) \
                * self.track.signs[k, :, None, None]
            mac = np.diag(tracking.mac_matrix(s_q.astype(np.float64), aligned))
            self.assertGreater(mac.min(), 1 - 1e-6, f"m_a={m_a} 振型")

    def test_reference_points(self):
        f0, _ = self.itp.query(0.0)
        self.assertLess(abs(f0[0] - config.F1_REF_MA0) / config.F1_REF_MA0,
                        config.F1_TOL)
        fr, _ = self.itp.query(config.MA_REF)
        self.assertLess(abs(fr[0] - config.F1_REF_MA) / config.F1_REF_MA,
                        config.F1_TOL)

    def test_monotonic_f1(self):
        xs = np.linspace(*self.itp.grid_range, 50)
        f1 = [self.itp.query(float(x))[0][0] for x in xs]
        self.assertTrue(all(b <= a + 1e-12 for a, b in zip(f1, f1[1:])))

    def test_dict_params_equivalent(self):
        f1, s1 = self.itp.query(0.45)
        f2, s2 = self.itp.query({"m_a": 0.45})
        np.testing.assert_array_equal(f1, f2)
        np.testing.assert_array_equal(s1, s2)

    def test_out_of_range_raises(self):
        lo, hi = self.itp.grid_range
        for bad in (lo - 0.01, hi + 0.01):
            with self.assertRaises(ValueError):
                self.itp.query(bad)

    def test_loo_freq_error_below_5pct(self):
        """验收标准 4：留出点频率插值误差 < 5%（全部内部点 × 全部通道）。"""
        _, max_err, _ = validate.loo(self.cases)
        self.assertLess(max_err, 0.05)


if __name__ == "__main__":
    unittest.main()
