"""mesh.cdb（CDWRITE,DB 格式）解析：节点坐标 + 单元连接，按材料分膜/索。

解析器改编自 07_模态计算_附加质量/app/added_mass_spectrum.py 的 parse_cdb。
材料号口径：mat=1 膜（SHELL181），mat=2 索（LINK180）。
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np


def _fixed_fields(line, widths):
    out, pos = [], 0
    for w in widths:
        out.append(line[pos:pos + w].strip())
        pos += w
    return out


def _fmt_widths(fmt_line):
    """解析 cdb 格式行，如 '(3i9,6e21.13e3)' → [9]*3+[21]*6。"""
    fmt = fmt_line.strip().strip("()")
    widths = []
    for tok in fmt.split(","):
        m = re.match(r"\s*(\d*)([a-zA-Z])(\d+)", tok)
        if m:
            rep = int(m.group(1)) if m.group(1) else 1
            widths += [int(m.group(3))] * rep
    return widths


def parse_cdb(cdb_path):
    """返回 nodes={id: (x,y,z)}, elems=[(mat, etype, [node ids])]。"""
    nodes, elems = {}, []
    lines = Path(cdb_path).read_text(errors="ignore").splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("NBLOCK"):
            widths = _fmt_widths(lines[i + 1])
            n_exp = int(ln.split(",")[3])
            cnt = 0
            i += 2
            while cnt < n_exp and i < len(lines):
                if lines[i].startswith(("EBLOCK", "CMBLOCK", "N,")):
                    break
                f = _fixed_fields(lines[i], widths)
                try:
                    nid = int(f[0])
                    xyz = [float(f[k]) if k < len(f) and f[k] else 0.0
                           for k in (3, 4, 5)]
                    nodes[nid] = (xyz[0], xyz[1], xyz[2])
                    cnt += 1
                except (ValueError, IndexError):
                    pass
                i += 1
            continue
        if ln.startswith("EBLOCK"):
            w0 = _fmt_widths(lines[i + 1])
            per_line, width = len(w0), w0[0]
            n_exp = int(ln.split(",")[3])
            cnt = 0
            i += 2
            while cnt < n_exp and i < len(lines):
                if lines[i].startswith(("NBLOCK", "CMBLOCK", "E,")):
                    break
                vals = _fixed_fields(lines[i], [width] * per_line)
                ints = [int(v) for v in vals if v]
                while len(ints) < 11:
                    i += 1
                    ints += [int(v) for v in _fixed_fields(lines[i], [width] * per_line) if v]
                nnode = ints[8]
                while len(ints) < 11 + nnode:
                    i += 1
                    ints += [int(v) for v in _fixed_fields(lines[i], [width] * per_line) if v]
                mat, etype = ints[0], ints[1]
                elems.append((mat, etype, ints[11:11 + nnode]))
                cnt += 1
                i += 1
            continue
        i += 1
    return nodes, elems


def load_geometry(cdb_path):
    """解析为数据库几何：node_ids 升序 + coords + elem_shell/elem_link（0-based 下标）。

    SHELL181 三角单元（3 节点）末位重复第 3 节点补齐为 4 列（可视化约定，
    见 docs/数据格式_HDF5_v1.md）。
    """
    nodes, elems = parse_cdb(cdb_path)
    node_ids = np.array(sorted(nodes), dtype=np.int32)
    coords = np.array([nodes[int(n)] for n in node_ids], dtype=np.float64)
    idx = {int(n): k for k, n in enumerate(node_ids)}

    shell, link, n_tri = [], [], 0
    for mat, _etype, conn in elems:
        conn = [idx[n] for n in conn]
        if mat == 1:
            if len(conn) == 3:
                conn = conn + [conn[2]]
                n_tri += 1
            shell.append(conn[:4])
        elif mat == 2:
            link.append(conn[:2])
    geom = {"node_ids": node_ids, "coords": coords,
            "elem_shell": np.asarray(shell, dtype=np.int32).reshape(-1, 4),
            "elem_link": np.asarray(link, dtype=np.int32).reshape(-1, 2)}
    return geom, n_tri
