"""HDF5 数据库格式 v1 的读写封装（格式文档见 docs/数据格式_HDF5_v1.md）。

布局：
    /                       attrs: format_version, structure, ansys_version, created, units
    /geometry/node_ids      (N,)  int32    ANSYS 节点号（升序；振型行序与此一致）
    /geometry/coords        (N,3) float64  节点坐标 m
    /geometry/elem_shell    (E1,4) int32   SHELL181 连接（node_ids 的 0-based 下标）
    /geometry/elem_link     (E2,2) int32   LINK180 连接
    /cases/case_XXXX/       attrs: m_a [kg/m²], dens_eff [kg/m³], grid_index
        freqs               (M,) float64   Hz，LP 重启动原始模态序
        shapes              (M,N,3) float32 质量归一化振型位移
"""
from __future__ import annotations

import datetime

import h5py
import numpy as np

FORMAT_VERSION = "1.0"

STRUCTURE_DESC = ("浙能乐清电厂3号气承式膜结构煤棚：317 m 长 × 115 m 跨 × 45 m 高，"
                  "±45°正交索网加劲，内压 270 Pa；SHELL181 膜 + LINK180 索共节点")
UNITS = {"length": "m", "mass": "kg", "freq": "Hz", "m_a": "kg/m^2",
         "density": "kg/m^3"}


def create_db(path, node_ids, coords, elem_shell, elem_link, ansys_version="2024 R2"):
    """新建数据库并写入几何（几何全库只存一份）。"""
    node_ids = np.asarray(node_ids, dtype=np.int32)
    coords = np.asarray(coords, dtype=np.float64)
    with h5py.File(path, "w") as h5:
        h5.attrs["format_version"] = FORMAT_VERSION
        h5.attrs["structure"] = STRUCTURE_DESC
        h5.attrs["ansys_version"] = ansys_version
        h5.attrs["created"] = datetime.datetime.now().isoformat(timespec="seconds")
        h5.attrs["units"] = str(UNITS)
        g = h5.create_group("geometry")
        g.create_dataset("node_ids", data=node_ids)
        g.create_dataset("coords", data=coords)
        g.create_dataset("elem_shell", data=np.asarray(elem_shell, dtype=np.int32))
        g.create_dataset("elem_link", data=np.asarray(elem_link, dtype=np.int32))
        h5.create_group("cases")


def add_case(path, name, m_a, dens_eff, grid_index, freqs, shapes):
    """追加一个工况。shapes: (M,N,3)，自动转 float32。"""
    freqs = np.asarray(freqs, dtype=np.float64)
    shapes = np.asarray(shapes, dtype=np.float32)
    with h5py.File(path, "a") as h5:
        if name in h5["cases"]:
            del h5["cases"][name]
        c = h5["cases"].create_group(name)
        c.attrs["m_a"] = float(m_a)
        c.attrs["dens_eff"] = float(dens_eff)
        c.attrs["grid_index"] = int(grid_index)
        c.create_dataset("freqs", data=freqs)
        c.create_dataset("shapes", data=shapes,
                         compression="gzip", compression_opts=4)


def load_geometry(path):
    with h5py.File(path, "r") as h5:
        g = h5["geometry"]
        return {"node_ids": g["node_ids"][:], "coords": g["coords"][:],
                "elem_shell": g["elem_shell"][:], "elem_link": g["elem_link"][:]}


def list_cases(path):
    """返回 [(case_name, m_a), ...]，按 m_a 升序。"""
    with h5py.File(path, "r") as h5:
        pairs = [(name, float(h5["cases"][name].attrs["m_a"]))
                 for name in h5["cases"]]
    return sorted(pairs, key=lambda t: t[1])


def load_case(path, name):
    """返回 (m_a, freqs (M,), shapes (M,N,3))。"""
    with h5py.File(path, "r") as h5:
        c = h5["cases"][name]
        return float(c.attrs["m_a"]), c["freqs"][:], c["shapes"][:]
