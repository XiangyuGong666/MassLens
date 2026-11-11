"""第 3 晚冒烟测试：真实启动 HTTP 服务，逐接口端到端校验。

用法（项目根目录）：python -m unittest tests.test_ui -v
校验：/api/meta、/api/geometry、/api/query（与 Interpolator 直算对照）、
越界 400 人话提示、首页与 vendor 静态资源可访问。
"""
from __future__ import annotations

import json
import struct
import threading
import unittest
import urllib.request
import urllib.error

import numpy as np

from masslens_data import schema, config
from masslens_interp import Interpolator
from masslens_ui.server import create_server


def _get(url, expect=200):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _unpack(buf):
    (hlen,) = struct.unpack("<I", buf[:4])
    header = json.loads(buf[4:4 + hlen])
    return header, 4 + hlen


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd, cls.app = create_server(port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.itp = Interpolator()  # 直算对照组

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_meta(self):
        status, body = _get(self.base + "/api/meta")
        meta = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(meta["n_modes"], 20)
        self.assertEqual(meta["grid"]["min"], 0.0)
        self.assertEqual(meta["grid"]["max"], 2.0)
        self.assertEqual(len(meta["grid"]["points"]), 15)
        self.assertIn("大雨", meta["presets"])
        self.assertEqual(meta["node_count"], 11009)
        self.assertAlmostEqual(meta["freqs0"][0], config.F1_REF_MA0, places=3)

    def test_geometry_pack(self):
        status, buf = _get(self.base + "/api/geometry")
        self.assertEqual(status, 200)
        header, off = _unpack(buf)
        self.assertEqual(off % 4, 0)
        n = header["node_count"]
        geom = schema.load_geometry(config.H5_PATH)
        self.assertEqual(n, len(geom["node_ids"]))
        self.assertEqual(len(buf), off + n * 12
                         + header["tri_index_count"] * 4
                         + header["line_index_count"] * 4)
        coords = np.frombuffer(buf, dtype=np.float32, count=3, offset=off)
        np.testing.assert_allclose(coords, geom["coords"][0], atol=1e-5)

    def test_query_matches_interpolator(self):
        status, buf = _get(self.base + "/api/query?m_a=0.45&mode=3")
        self.assertEqual(status, 200)
        header, off = _unpack(buf)
        freqs_ref, shapes_ref = self.itp.query(0.45)
        np.testing.assert_allclose(header["freqs"], freqs_ref, rtol=1e-9)
        self.assertAlmostEqual(header["freq"], freqs_ref[2], places=12)
        shape = np.frombuffer(buf, dtype=np.float32,
                              count=11009 * 3, offset=off).reshape(11009, 3)
        np.testing.assert_allclose(shape, shapes_ref[2], atol=1e-7)
        self.assertGreater(header["max_abs"], 0)

        diag = header["diag"]
        self.assertGreater(diag["mac"], 0.99)       # 两端点振型 MAC（所选阶次）
        self.assertGreater(diag["mac_min"], 0.99)
        self.assertGreater(diag["track_min"], 0.99)  # 全程跟踪 MAC 最小值
        self.assertEqual(diag["err_tol"], 0.01)
        self.assertIsNotNone(diag["err"])            # interp_error.json 已固化
        self.assertLess(diag["err"]["max_freq_err"], 0.05)

    def test_error_estimate_intervals(self):
        """区间误差估计：端点落到正确区间，数值与固化表一致。"""
        est0 = self.app.error_estimate(0.0)
        est_end = self.app.error_estimate(2.0)
        self.assertIsNotNone(est0)
        self.assertLess(est0["max_freq_err"], 0.05)
        self.assertLess(est_end["max_freq_err"], 0.05)
        mid = self.app.error_estimate(0.45 + 1e-9)   # (0.45, 0.6) 区间内
        self.assertLess(mid["max_freq_err"], 0.05)

    def test_out_of_range_plain_message(self):
        status, body = _get(self.base + "/api/query?m_a=9.9&mode=1")
        self.assertEqual(status, 400)
        msg = json.loads(body)["error"]
        self.assertIn("超出数据库范围", msg)

    def test_bad_mode(self):
        status, body = _get(self.base + "/api/query?m_a=0.5&mode=99")
        self.assertEqual(status, 400)
        self.assertIn("阶次超出范围", json.loads(body)["error"])

    def test_static_served(self):
        for path, marker in [
                ("/", "MassLens"),
                ("/static/style.css", "--bg0"),
                ("/static/app.js", "Viewer3D"),
                ("/static/vendor/three.module.min.js", "Three.js"),
                ("/static/vendor/addons/controls/OrbitControls.js", "OrbitControls"),
                ("/static/vendor/katex/katex.min.js", "katex"),
                ("/static/vendor/katex/fonts/KaTeX_Main-Regular.woff2", None)]:
            status, body = _get(self.base + path)
            self.assertEqual(status, 200, path)
            if marker:
                self.assertIn(marker.encode(), body, path)

    def test_path_traversal_blocked(self):
        status, _ = _get(self.base + "/static/../masslens_data/config.py")
        self.assertIn(status, (403, 404))


if __name__ == "__main__":
    unittest.main()
