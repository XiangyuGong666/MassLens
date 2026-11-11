"""风振引擎测试：分解器正确性（合成数据）、m̂_a 反演、识别表装载、传感器映射。

用法（项目根目录）：python -m unittest tests.test_windlens -v
"""
from __future__ import annotations

import unittest

import numpy as np

from windlens_engine import config, decompose, participate


def _synthetic(seed=3, m=6, n_acc=36, n_gnss=21, T=4000, fs=10.0):
    """合成基与 q(t)：随机正交振型 + 各阶谐波模态坐标。"""
    rng = np.random.default_rng(seed)
    qa, _ = np.linalg.qr(rng.normal(size=(n_acc, m)))
    phi_acc = qa.T * 1e-3
    qg, _ = np.linalg.qr(rng.normal(size=(n_gnss, m)))
    phi_gnss = qg.T * 1e-3
    freqs = 0.55 + 0.12 * np.arange(m)
    t = np.arange(T) / fs
    q = np.stack([1e-3 * (1 + 0.3 * j) * np.sin(2 * np.pi * f * t + j)
                  for j, f in enumerate(freqs)])
    return freqs, phi_acc, phi_gnss, q


class TestDecomposer(unittest.TestCase):
    """引擎正确性：由模型生成传感器信号，分解必须还原 q（合成高 SNR 情形）。"""

    def test_q_recovery(self):
        freqs, phi_acc, phi_gnss, q = _synthetic()
        om2 = (2 * np.pi * freqs) ** 2
        y_acc = (-om2[:, None] * phi_acc).T @ q          # (36,T) ← 模型 a=-ω²φq
        y_gnss = phi_gnss.T @ q
        rng = np.random.default_rng(1)
        y_acc = y_acc + 0.02 * rng.normal(size=y_acc.shape) * y_acc.std()
        y_gnss = y_gnss + 0.02 * rng.normal(size=y_gnss.shape) * y_gnss.std()
        dec = decompose.ModalDecomposer(freqs, phi_acc, phi_gnss)
        q_hat = dec.q_series(y_acc, y_gnss)
        for j in range(len(freqs)):
            c = np.corrcoef(q[j], q_hat[j])[0, 1]
            self.assertGreater(abs(c), 0.99, f"模态{j + 1} 还原相关 {c:.3f}")

    def test_leave_one_out_synthetic(self):
        freqs, phi_acc, phi_gnss, q = _synthetic()
        om2 = (2 * np.pi * freqs) ** 2
        y_acc = (-om2[:, None] * phi_acc).T @ q
        y_gnss = phi_gnss.T @ q
        c = 5
        keep = [i for i in range(36) if i != c]
        dec = decompose.ModalDecomposer(freqs, phi_acc, phi_gnss, acc_rows=keep)
        q_hat = dec.q_series(y_acc[keep], y_gnss)
        acc_hat = (-om2 * phi_acc[:, c]) @ q_hat
        self.assertGreater(decompose.r2(y_acc[c], acc_hat), 0.95)


class TestMAInversion(unittest.TestCase):
    def test_reference_point(self):
        """f1=0.550 → m̂_a ≈ 1.333 kg/m²（新模型参考点）。"""
        m = participate.m_a_from_f1(0.550)
        self.assertAlmostEqual(m, 1.333, delta=0.05)

    def test_bare_freq_gives_zero(self):
        self.assertAlmostEqual(participate.m_a_from_f1(config.H5_PATH and 0.6401), 0.0, delta=0.01)


class TestSensorMap(unittest.TestCase):
    def test_layout(self):
        sm = config.load_sensor_map()
        self.assertEqual(len(sm), 12)
        gnss = [s["loc"] for s in sm if s["gnss_point"] > 0]
        self.assertEqual(gnss, [1, 2, 7, 8, 9, 11, 12])
        for s in sm:
            self.assertLessEqual(abs(s["fe"][0]), 57.5 + 1e-6)
            self.assertTrue(0 <= s["fe"][2] <= 317)


@unittest.skipUnless(config.DATA_DIR.joinpath("scan_peak_details.csv").is_file(),
                     "requires field-monitoring identification table")
class TestPeakLoader(unittest.TestCase):
    """真实识别表（慢：流式读 108 万行）。"""

    @classmethod
    def setUpClass(cls):
        cls.peaks = participate.load_peaks()

    def test_load_peaks_real(self):
        p = self.peaks
        self.assertGreater(len(p), 40000)
        self.assertTrue((p["freq"] >= 0.1).all() and (p["freq"] <= 3.0).all())
        self.assertTrue(np.all(np.diff(p["start_s"]) >= 0))  # 已按窗排序
        # 全量重扫表 B/C/D 三级无 A；按 A/B 过滤后应为 {B}（或含 A 时的 {A,B}）
        self.assertLessEqual(set(np.unique(p["quality"].tolist())), {"A", "B"})
        self.assertGreater(len(set(np.unique(p["quality"].tolist()))), 0)

    def test_timeline_aggregation(self):
        recs = participate.timeline(self.peaks, 218700, 219300, top_n=4)
        self.assertTrue(recs)
        for r in recs:
            self.assertLessEqual(len(r["peaks"]), 4)
            self.assertIn("m_a_hat", r)


if __name__ == "__main__":
    unittest.main()
