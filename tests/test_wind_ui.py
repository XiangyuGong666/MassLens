"""WindLens 服务端冒烟测试：/wind 页面、/api/wind/*、/api/modebase 与数据束一致性。

用法（项目根目录）：python -m unittest tests.test_wind_ui -v
"""
from __future__ import annotations

import json
import struct
import threading
import unittest
import urllib.request
import urllib.error

import numpy as np

from masslens_ui.server import create_server


def _get(url, expect=200):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _unpack(buf):
    (hlen,) = struct.unpack("<I", buf[:4])
    header = json.loads(buf[4:4 + hlen])
    return header, 4 + hlen


class TestWindServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.app = create_server(port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_wind_page(self):
        status, body = _get(self.base + "/wind")
        self.assertEqual(status, 200)
        self.assertIn("WindLens".encode(), body)
        self.assertIn("风振监测实验室".encode(), body)

    def test_zen_page(self):
        status, body = _get(self.base + "/zen")
        self.assertEqual(status, 200)
        self.assertIn("合成风振演示".encode(), body)

    def test_home_nav(self):
        status, body = _get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn("/wind".encode(), body)

    def test_wind_meta(self):
        status, body = _get(self.base + "/api/wind/meta")
        meta = json.loads(body)
        self.assertEqual(status, 200)
        self.assertTrue(meta["has_bundle"])
        self.assertEqual(meta["n_win"], 180)
        self.assertEqual(meta["source"], "synthetic-demo")

    def test_wind_sensors(self):
        status, body = _get(self.base + "/api/wind/sensors")
        ss = json.loads(body)
        self.assertEqual(len(ss), 12)
        gnss = [s["loc"] for s in ss if s["gnss"] > 0]
        self.assertEqual(sorted(gnss), [1, 2, 7, 8, 9, 11, 12])

    def test_bundle_layout(self):
        status, buf = _get(self.base + "/api/wind/bundle")
        self.assertEqual(status, 200)
        header, off = _unpack(buf)
        self.assertEqual(off % 4, 0)
        n = header["n_win"]
        self.assertEqual(n, 180)
        total = off
        for name in header["layout"]:
            cnt = int(np.prod(header["shapes"][name]))
            total += cnt * 4
        self.assertEqual(len(buf), total)  # 字节数与头部声明严格一致
        # 抽查 start_s 单调与 wind 非负
        start_s = np.frombuffer(buf, dtype=np.float32, count=n, offset=off)
        self.assertTrue(np.all(np.diff(start_s) > 0))
        n_wind = int(np.prod(header["shapes"]["wind"]))
        wind = np.frombuffer(buf, dtype=np.float32, count=n_wind,
                             offset=off + n * 4)
        self.assertTrue((wind >= 0).all())
        self.assertGreater(wind.max(), 10.0)

    def test_modebase(self):
        status, buf = _get(self.base + "/api/modebase")
        self.assertEqual(status, 200)
        header, off = _unpack(buf)
        self.assertGreaterEqual(header["n_modes"], 20)  # 20 阶或 60 阶扩展基
        self.assertEqual(header["node_count"], 11009)
        expect = off + header["n_modes"] * 11009 * 3 * 4
        self.assertEqual(len(buf), expect)
        self.assertAlmostEqual(header["freqs"][0], 0.64005, places=4)  # 新模型裸基频
        if header.get("extended"):
            self.assertGreater(header["freqs"][-1], 2.3)  # 覆盖实测 2~3 Hz 峰

    def test_segments_list(self):
        status, body = _get(self.base + "/api/wind/segments")
        segs = json.loads(body)
        self.assertEqual(status, 200)
        names = [s["name"] for s in segs]
        for want in ("w1", "w2"):
            self.assertIn(want, names)
        for s in segs:
            self.assertGreater(s["dur_s"], 0)
            self.assertEqual(s["fs"], 10.0)

    def test_segment_pack(self):
        status, buf = _get(self.base + "/api/wind/segment?name=w1")
        self.assertEqual(status, 200)
        header, off = _unpack(buf)
        t = header["n_t"]
        self.assertEqual(header["n_press"], 12)
        expect = off + (36 + 21 + 12 + 1 + 1) * t * 4
        self.assertEqual(len(buf), expect)
        press = np.frombuffer(buf, dtype=np.float32, count=12 * t,
                              offset=off + 57 * t * 4)
        self.assertTrue(np.isfinite(press).all())
        self.assertGreater(np.abs(press).max(), 0)
        status2, _ = _get(self.base + "/api/wind/segment?name=nope")
        self.assertEqual(status2, 404)

    def test_press_weights(self):
        status, buf = _get(self.base + "/api/wind/press_weights")
        self.assertEqual(status, 200)
        header, off = _unpack(buf)
        n, np_ = header["node_count"], header["n_press"]
        self.assertEqual((n, np_), (11009, 12))
        w = np.frombuffer(buf, dtype=np.float32, count=n * np_, offset=off
                          ).reshape(n, np_)
        np.testing.assert_allclose(w.sum(axis=1), 1.0, atol=1e-5)
        self.assertTrue((w >= 0).all())

    def test_pod_endpoint(self):
        status, body = _get(self.base + "/api/wind/pod")
        doc = json.loads(body)
        self.assertEqual(status, 200)
        self.assertIn(doc["k"], range(1, 5))
        self.assertEqual(len(doc["vecs"]), 12)
        self.assertEqual(len(doc["vecs"][0]), doc["k"])
        self.assertAlmostEqual(sum(doc["fracs"]), 1.0)
        self.assertGreater(sum(doc["fracs"][:doc["k"]]), 0.80)
        # 模态正交性（从文件复核）
        import numpy as np
        from windlens_engine import config
        z = np.load(config.OUT_DIR / "pod_pressure.npz")
        g = z["eigvecs"].T @ z["eigvecs"]
        np.testing.assert_allclose(g, np.eye(12), atol=1e-6)


if __name__ == "__main__":
    unittest.main()
