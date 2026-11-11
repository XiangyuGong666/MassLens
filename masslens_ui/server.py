"""MassLens 本地服务器：静态三栏页面 + 插值 API（只做插值与渲染，不做有限元）。

用法（项目根目录下）：
    python -m masslens_ui [--port 8000] [--no-browser]

API：
    GET /api/meta                 参数网格、预设工况、基准频率、结构信息（JSON）
    GET /api/geometry             几何二进制包：coords f32 + 三角面索引 u32 + 线索引 u32
    GET /api/query?m_a=..&mode=.. 插值二进制包：头部 JSON（20 阶频率等）+ 振型 f32

二进制包格式：前 4 字节小端 uint32 = 头部 JSON 长度（已补齐到 4 字节对齐），
随后是头部 JSON，再随后依次是各数组的原始字节（均为 4 字节元素，天然对齐）。
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from masslens_data import config, schema
from masslens_interp import Interpolator
from windlens_engine import config as wind_config

STATIC_DIR = Path(__file__).resolve().parent / "static"

# UI 误差角标阈值：所在区间留出点频率误差估计超过该值时角标提示（不阻断使用）
ERR_BADGE_TOL = 0.01
ERROR_TABLE_PATH = config.DATA_DIR / "interp_error.json"  # validate --save 生成
WIND_BUNDLE_PATH = wind_config.OUT_DIR / "window_bundle.bin"
WIND_SENSORS_CSV = wind_config.SENSOR_MAP_CSV

MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8", ".json": "application/json",
        ".woff2": "font/woff2", ".png": "image/png", ".svg": "image/svg+xml",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def pack(header_dict, *arrays):
    """[u32 头长][头部 JSON（补空格对齐到 4）][各数组字节]。"""
    h = json.dumps(header_dict).encode("utf-8")
    h += b" " * ((-4 - len(h)) % 4)
    return struct.pack("<I", len(h)) + h + b"".join(
        np.asarray(a).tobytes() for a in arrays)


class App:
    """服务状态：插值器 + 几何（启动时构建一次，之后全部为只读，线程安全）。"""

    def __init__(self):
        t0 = __import__("time").time()
        self.itp = Interpolator()
        geom = schema.load_geometry(config.H5_PATH)
        self.build_seconds = __import__("time").time() - t0

        coords = np.ascontiguousarray(geom["coords"], dtype=np.float32)
        shell = geom["elem_shell"].astype(np.int64)
        quad = shell[:, 3] != shell[:, 2]  # 三角单元末位重复第 3 节点（格式文档约定）
        tris = np.concatenate([shell[:, :3], shell[quad][:, [0, 2, 3]]])
        lines = geom["elem_link"].astype(np.uint32)
        self.geom_pack = pack(
            {"node_count": int(len(coords)),
             "tri_index_count": int(tris.size),
             "line_index_count": int(lines.size)},
            coords, np.ascontiguousarray(tris, dtype=np.uint32), lines)

        lo, hi = self.itp.grid_range
        freqs0, _ = self.itp.query(lo)
        presets = {k: v for k, v in config.PRESETS.items() if not k.startswith("_")}
        self.err_intervals = self._load_error_table()
        self.meta = {
            "structure": schema.STRUCTURE_DESC,
            "grid": {"min": lo, "max": hi,
                     "points": [float(x) for x in self.itp.grid_]},
            "n_modes": int(self.itp.n_lanes),
            "freqs0": [float(f) for f in freqs0],
            "presets": presets,
            "reference_points": {"f1_ma0_hz": config.F1_REF_MA0,
                                 "ma_ref_kg_m2": config.MA_REF,
                                 "f1_ma_ref_hz": config.F1_REF_MA},
            "node_count": int(len(coords)),
            "n_shell": int(len(geom["elem_shell"])),
            "n_link": int(len(geom["elem_link"])),
            "units": schema.UNITS}

    def _load_error_table(self):
        """读 validate --save 固化的留出点误差表；缺失/损坏时静默降级为无估计。"""
        try:
            doc = json.loads(ERROR_TABLE_PATH.read_text(encoding="utf-8"))
            return doc["intervals"]
        except (OSError, ValueError, KeyError):
            return None

    def wind_bundle(self):
        """风振数据束字节（首次成功读盘缓存；未生成不缓存，便于后生成即用）。"""
        if self._wind_bundle is None:
            try:
                self._wind_bundle = WIND_BUNDLE_PATH.read_bytes()
            except OSError:
                return None
        return self._wind_bundle

    def wind_sensors(self):
        """12 个测点的 FE 坐标（3D 标记用），跳过 # 注释行。

        v2 表另带 node_row（h5 节点行号，实测形状 LS 混合的传感基行定位用）。
        """
        import csv as _csv
        out = []
        try:
            with open(WIND_SENSORS_CSV, encoding="utf-8") as fh:
                lines = [ln for ln in fh if ln.strip() and not ln.startswith("#")]
            for row in _csv.DictReader(lines):
                out.append({"loc": int(row["loc"]),
                            "x": float(row["fe_x_m"]),
                            "y": float(row["fe_y_height_m"]),
                            "z": float(row["fe_z_m"]),
                            "gnss": int(row["gnss_point"]),
                            "row": int(row["node_row"])
                            if row.get("node_row") else -1})
        except OSError:
            pass
        return out

    def modebase(self):
        """模态基：优先 60 阶扩展库（WindLens 高频实测峰用真高阶振型），
        否则退回库内 m_a=0 前 20 阶。"""
        if self._modebase is None:
            ext = wind_config.OUT_DIR / "basis_ext60.npz"
            if ext.exists():
                z = np.load(ext)
                freqs, shapes = z["freqs"], z["shapes"]
                header = {"n_modes": int(shapes.shape[0]),
                          "node_count": int(shapes.shape[1]),
                          "freqs": [float(f) for f in freqs],
                          "extended": True}
                self._modebase = pack(header, np.ascontiguousarray(shapes))
                return self._modebase
            import h5py as _h5
            with _h5.File(config.H5_PATH, "r") as h:
                for name in h["cases"]:
                    if float(h["cases"][name].attrs["m_a"]) == 0.0:
                        freqs = h["cases"][name]["freqs"][:]
                        shapes = h["cases"][name]["shapes"][:]
                        break
                else:
                    raise RuntimeError("数据库中没有 m_a=0 工况")
            header = {"n_modes": int(shapes.shape[0]),
                      "node_count": int(shapes.shape[1]),
                      "freqs": [float(f) for f in freqs]}
            self._modebase = pack(header, np.ascontiguousarray(shapes))
        return self._modebase

    def wind_bundle_gz(self):
        """gzip 版数据束（浮点数组约可压 40%+，缓存复用）。"""
        if self._wind_bundle_gz is None:
            raw = self.wind_bundle()
            if raw is None:
                return None
            import gzip
            self._wind_bundle_gz = gzip.compress(raw, compresslevel=6)
        return self._wind_bundle_gz

    _wind_bundle = None
    _wind_bundle_gz = None
    _modebase = None

    def query(self, m_a, mode):
        """返回 (status, body)。mode 为 1-based（与界面"第 N 阶"一致）。"""
        n = self.itp.n_lanes
        if not 1 <= mode <= n:
            return 400, {"error": f"阶次超出范围：可选 1~{n} 阶"}
        try:
            freqs, shapes = self.itp.query(m_a)
            diag = self.itp.diagnostics(m_a)
        except ValueError as e:
            return 400, {"error": str(e)}
        shape = np.ascontiguousarray(shapes[mode - 1])  # float32 (N,3)
        header = {"m_a": m_a, "mode": mode,
                  "freq": float(freqs[mode - 1]),
                  "freqs": [float(f) for f in freqs],
                  "max_abs": float(np.linalg.norm(shape, axis=1).max()),
                  "diag": {"mac": float(diag["mac_bracket"][mode - 1]),
                           "mac_min": float(diag["mac_bracket"].min()),
                           "track_min": diag["min_mac_track"],
                           "err": self.error_estimate(m_a),
                           "err_tol": ERR_BADGE_TOL}}
        return 200, pack(header, shape)

    def error_estimate(self, m_a):
        """所在区间的留出点误差估计（来自 validate --save 的固话表），无表返回 None。"""
        if not self.err_intervals:
            return None
        import bisect
        los = [iv["lo"] for iv in self.err_intervals]
        i = min(max(bisect.bisect_right(los, m_a) - 1, 0),
                len(self.err_intervals) - 1)
        iv = self.err_intervals[i]
        return {"max_freq_err": iv["max_freq_err"], "min_mac": iv["min_mac"]}


class Handler(BaseHTTPRequestHandler):
    app: App = None  # create_server 注入

    def log_message(self, fmt, *args):  # 静默访问日志，只保留错误
        pass

    def _send(self, status, body, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        try:
            self._route()
        except Exception as e:  # 出错只给一句人话（界面铁律）
            self._send_json(500, {"error": f"服务内部出错：{e}"})

    def _route(self):
        url = urlparse(self.path)
        path = url.path
        if path == "/" or path == "/masslens" or path == "/wind":
            return self._static(STATIC_DIR / "home.html")
        if path == "/zen":
            return self._static(STATIC_DIR / "zen.html")
        if path.startswith("/static/"):
            rel = Path(*path[len("/static/"):].split("/"))
            target = (STATIC_DIR / rel).resolve()
            if not target.is_relative_to(STATIC_DIR):  # 防路径穿越
                return self._send_json(403, {"error": "禁止访问"})
            return self._static(target)
        if path == "/api/meta":
            return self._send_json(200, self.app.meta)
        if path == "/api/geometry":
            return self._send(200, self.app.geom_pack, "application/octet-stream")
        if path == "/api/wind/meta":
            bundle = self.app.wind_bundle()
            header = {}
            if bundle is not None:
                (hl,) = struct.unpack("<I", bundle[:4])
                header = json.loads(bundle[4:4 + hl])
            return self._send_json(200, {
                "has_bundle": bundle is not None,
                "n_win": header.get("n_win"), "win_s": header.get("win_s", 60.0),
                "source": header.get("source"),
                "notes": header.get("notes", "")})
        if path == "/api/wind/sensors":
            return self._send_json(200, self.app.wind_sensors())
        if path == "/api/wind/danger":
            # 最不利模态组合（windlens_engine.danger 离线穷举产物）
            fp = wind_config.OUT_DIR / "danger_combos.json"
            if not fp.is_file():
                return self._send_json(404, {"error": "danger_combos.json 未生成"})
            return self._send_json(200, json.loads(fp.read_text(encoding="utf-8")))
        if path == "/api/wind/photos":
            # 现场照片挂接（第 17 晚框架）：static/photos/manifest.json 存在才有点位
            mf = STATIC_DIR / "photos" / "manifest.json"
            if not mf.is_file():
                return self._send_json(200, [])
            try:
                return self._send_json(200, json.loads(
                    mf.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                return self._send_json(200, [])
        if path == "/api/wind/bundle":
            bundle = self.app.wind_bundle()
            if bundle is None:
                return self._send_json(404, {
                    "error": "风振数据束未生成：请先运行 python -m windlens_engine.bundle"})
            accept = self.headers.get("Accept-Encoding", "")
            if "gzip" in accept:
                gz = self.app.wind_bundle_gz()
                if gz is not None:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Content-Length", str(len(gz)))
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(gz)
                    return
            return self._send(200, bundle, "application/octet-stream")
        if path == "/api/modebase":
            return self._send(200, self.app.modebase(), "application/octet-stream")
        if path == "/api/wind/segments":
            from windlens_engine import segments as _segs
            return self._send_json(200, _segs.list_segments())
        if path == "/api/wind/segment":
            from windlens_engine import segments as _segs
            qs = parse_qs(url.query)
            name = qs.get("name", [""])[0]
            seg = _segs.load_segment(name)
            if seg is None:
                return self._send_json(404, {"error": f"精华段不存在：{name}"})
            header = {"name": name, "fs": float(seg["meta"]["fs"]),
                      "start_s": float(seg["meta"]["start_s"]),
                      "dur_s": float(seg["meta"]["dur_s"]),
                      "n_acc": int(seg["acc"].shape[0]),
                      "n_gnss": int(seg["gnss"].shape[0]),
                      "n_press": int(seg["pressure"].shape[0]),
                      "n_t": int(seg["acc"].shape[1])}
            body = pack(header,
                        np.ascontiguousarray(seg["acc"], dtype=np.float32),
                        np.ascontiguousarray(seg["gnss"], dtype=np.float32),
                        np.ascontiguousarray(seg["pressure"], dtype=np.float32),
                        np.ascontiguousarray(seg["speed"], dtype=np.float32),
                        np.ascontiguousarray(seg["direct"], dtype=np.float32))
            return self._send(200, body, "application/octet-stream")
        if path == "/api/wind/press_weights":
            from windlens_engine import segments as _segs
            w = _segs.press_weights()
            header = {"node_count": int(w.shape[0]), "n_press": int(w.shape[1])}
            return self._send(200, pack(header, np.ascontiguousarray(w)),
                              "application/octet-stream")
        if path == "/api/wind/pod":
            from windlens_engine import pod as _pod
            doc = _pod.load_pod()
            if doc is None:
                return self._send_json(404, {
                    "error": "POD 未生成：请先运行 python -m windlens_engine.pod"})
            return self._send_json(200, {
                "k": doc["k"],
                "fracs": doc["eigvals"],
                "vecs": doc["vecs"].tolist(),
                "n_used": doc["n_used"]})
        if path == "/api/wind/podseries":
            from windlens_engine import pod as _pod
            s = _pod.load_series()
            if s is None:
                return self._send_json(404, {
                    "error": "POD 时程未生成：请运行 python -m windlens_engine.pod --series"})
            header = {"n_win": int(s.shape[0]), "k": int(s.shape[1])}
            return self._send(200, pack(header, np.ascontiguousarray(s)),
                              "application/octet-stream")
        if path == "/api/wind/pressmax":
            p = wind_config.OUT_DIR / "window_pressmax.npz"
            if not p.exists():
                return self._send_json(404, {
                    "error": "风压峰值未生成：请运行 python -m windlens_engine.deepen pressmax"})
            z = np.load(p)["pmax"].astype(np.float32)
            header = {"n_win": int(z.shape[0]), "n_press": int(z.shape[1])}
            return self._send(200, pack(header, np.ascontiguousarray(z)),
                              "application/octet-stream")
        if path == "/api/query":
            qs = parse_qs(url.query)
            try:
                m_a = float(qs["m_a"][0])
                mode = int(qs.get("mode", ["1"])[0])
            except (KeyError, ValueError, IndexError):
                return self._send_json(400, {"error": "参数无法解析：需要 m_a 数值与 mode 整数"})
            status, body = self.app.query(m_a, mode)
            if status == 200:
                return self._send(200, body, "application/octet-stream")
            return self._send_json(status, body)
        return self._send_json(404, {"error": "路径不存在"})

    def _static(self, target: Path):
        if not target.is_file():
            return self._send_json(404, {"error": "文件不存在"})
        ctype = MIME.get(target.suffix.lower(), "application/octet-stream")
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # vendor 库（three.js/KaTeX/字体）长缓存；其余不缓存（开发迭代即时生效）
        if "vendor" in target.parts:
            self.send_header("Cache-Control", "public, max-age=604800")
        else:
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def create_server(port=8000, host="127.0.0.1"):
    """构建 App 并绑定端口（port=0 自动分配，测试用）。返回 (httpd, app)。"""
    app = App()
    # 关闭地址复用：http.server 默认 allow_reuse_address=True，Windows 下会
    # 两个进程同绑一个端口、先到者静默应答（幽灵 404 的温床）
    ThreadingHTTPServer.allow_reuse_address = False
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    Handler.app = app
    return httpd, app


def main(argv=None):
    ap = argparse.ArgumentParser(prog="masslens_ui")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    print("MassLens 质量透镜 —— 正在加载数据库并构建插值器 …", flush=True)
    # 先构建 App（数据库缺失等错误按原样抛出，勿被端口重试吞掉）
    app = App()
    port = args.port
    httpd = None
    Handler.app = app
    for _ in range(20):  # 端口被占则顺延
        try:
            ThreadingHTTPServer.allow_reuse_address = False
            httpd = ThreadingHTTPServer((args.host, port), Handler)
            break
        except OSError:
            port += 1
    if httpd is None:
        print("启动失败：8000~8019 端口均被占用", file=sys.stderr)
        sys.exit(1)

    lo, hi = app.itp.grid_range
    url = f"http://{args.host}:{port}"
    print(f"  数据库：{config.H5_PATH.name}"
          f"（{len(app.itp.grid_)} 工况 × {app.itp.n_lanes} 阶，"
          f"m_a ∈ [{lo}, {hi}] kg/m²）", flush=True)
    print(f"  插值器构建：{app.build_seconds:.2f} s", flush=True)
    print(f"  请在浏览器打开：{url}", flush=True)
    print("  Ctrl+C 退出", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")


if __name__ == "__main__":
    main()
