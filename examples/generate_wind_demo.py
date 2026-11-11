"""Build deterministic synthetic WindLens inputs from the bundled FE mode base."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from masslens_data import schema
from masslens_ui.server import pack
from windlens_engine.bundle import LAYOUT, TOPN
from windlens_engine.config import H5_PATH

DEMO = Path(__file__).resolve().parent / "demo"
N_WIN = 180
WIN_S = 60
FS = 10
GNSS_LOCS = (1, 2, 7, 8, 9, 11, 12)


def virtual_sensors(geom):
    coords = geom["coords"]
    anchors = np.array([(x, y, z) for z in (42, 115, 201, 275)
                        for x, y in ((-34, 30), (0, 42), (34, 30))])
    return np.array([np.argmin(((coords - p) ** 2).sum(axis=1)) for p in anchors])


def build():
    rng = np.random.default_rng(451)
    geom = schema.load_geometry(H5_PATH)
    rows = virtual_sensors(geom)
    with (DEMO / "sensor_map.csv").open("w", encoding="utf-8", newline="") as fh:
        fh.write("# Synthetic demo sensors selected from the example mesh.\n")
        writer = csv.writer(fh)
        writer.writerow(["loc", "fe_x_m", "fe_y_height_m", "fe_z_m",
                         "node_row", "ansys_node", "gnss_point"])
        for loc, row in enumerate(rows, 1):
            gnss = GNSS_LOCS.index(loc) + 1 if loc in GNSS_LOCS else 0
            writer.writerow([loc, *geom["coords"][row], row, geom["node_ids"][row], gnss])

    with np.load(DEMO / "basis_ext60.npz") as db:
        modes = [0, 6, 13]
        freqs = db["freqs"][modes]
        shapes = db["shapes"][modes][:, rows, :]
    starts = np.arange(N_WIN) * WIN_S
    win = np.arange(N_WIN)
    gust = np.exp(-((win - 52) / 17) ** 2) + 0.65 * np.exp(-((win - 124) / 22) ** 2)
    wind = 4.2 + 1.0 * np.sin(win / 12) + 8.5 * gust
    wdir = (240 + 32 * np.sin(win / 33)) % 360

    n_samp = WIN_S * FS
    t = np.arange(N_WIN * n_samp) / FS
    scales = np.array([1.0, 0.8, 0.67])
    participation = ((0.6 + gust[:, None]) * scales
                     * (1 + 0.16 * np.sin(win[:, None] / 13 + np.arange(3))))
    phases = 2 * np.pi * freqs[:, None] * t + np.arange(3)[:, None]
    modal_q = 0.12 * np.repeat(participation.T, n_samp, axis=1) * np.sin(phases)
    acc = (-(2 * np.pi * freqs) ** 2)[:, None] * modal_q
    acc = shapes.reshape(3, 36).T @ acc
    gnss = shapes[:, np.array(GNSS_LOCS) - 1, :].reshape(3, 21).T @ modal_q * 1000
    sensor_phase = np.linspace(0, 1.8, 12)[:, None]
    press = (np.sin(2 * np.pi * 0.09 * t + sensor_phase)
             + 0.55 * np.sin(2 * np.pi * 0.21 * t - 2 * sensor_phase)
             + 0.28 * rng.normal(size=(12, t.size)))
    press *= 1.2 * np.repeat(wind, n_samp)
    speed = np.repeat(wind, n_samp) + 0.7 * np.sin(t / 4)
    direct = np.repeat(wdir, n_samp) + 4 * np.sin(t / 8)

    def windows(signal):
        return signal.reshape(-1, N_WIN, n_samp).transpose(1, 0, 2)

    def rms(windowed):
        return np.sqrt(np.mean(windowed ** 2, axis=-1))

    pw = windows(press)
    peaks = np.full((N_WIN, TOPN, 4), np.nan, dtype=np.float32)
    peaks[:, :3, 0] = freqs
    peaks[:, :3, 1] = participation
    peaks[:, :3, 2] = 1.0
    peaks[:, :3, 3] = 0.0
    peak_shapes = np.full((N_WIN, TOPN, 12), np.nan, dtype=np.float32)
    peak_shapes[:, :3, :] = shapes[:, :, 1]
    track_len = np.zeros((N_WIN, TOPN), dtype=np.float32)
    track_len[:, :3] = N_WIN
    arrays = {
        "start_s": starts,
        "wind": wind,
        "wind_max": windows(speed).max(axis=2)[:, 0],
        "wdir": wdir,
        "wdir_std": windows(direct).std(axis=2)[:, 0],
        "acc_rms": rms(windows(acc)).mean(axis=1),
        "peaks": peaks,
        "m_a": np.zeros(N_WIN),
        "track_len": track_len,
        "peaks_shape": peak_shapes,
        "press_rms": rms(pw),
        "gnss_rms": rms(windows(gnss)),
    }
    header = {"n_win": N_WIN, "win_s": WIN_S, "topn": TOPN, "layout": LAYOUT,
              "shapes": {key: list(arrays[key].shape) for key in LAYOUT},
              "events": [], "track_min_len": 20, "source": "synthetic-demo"}
    (DEMO / "window_bundle.bin").write_bytes(
        pack(header, *(arrays[key].astype("<f4") for key in LAYOUT)))

    for name, first in (("w1", 45), ("w2", 119)):
        segment = slice(first * n_samp, (first + 4) * n_samp)
        np.savez_compressed(
            DEMO / f"{name}.npz", meta_start_s=first * WIN_S, meta_dur_s=4 * WIN_S,
            meta_fs=FS, time=t[segment], acc=acc[:, segment].astype("f4"),
            gnss=gnss[:, segment].astype("f4"), pressure=press[:, segment].astype("f4"),
            speed=speed[segment].astype("f4"), direct=direct[segment].astype("f4"))

    centered = pw - pw.mean(axis=2, keepdims=True)
    values, vectors = np.linalg.eigh(np.cov(centered.transpose(1, 0, 2).reshape(12, -1)))
    values, vectors = values[::-1], vectors[:, ::-1]
    np.savez_compressed(DEMO / "pod_pressure.npz", eigvals=values, eigvecs=vectors,
                        n_used=t.size)
    np.savez_compressed(DEMO / "pod_series.npz", a_rms=rms(vectors[:, :4].T @ centered))
    np.savez_compressed(DEMO / "window_pressmax.npz", pmax=np.abs(pw).max(axis=2))
    print(f"Synthetic WindLens demo: {N_WIN} windows, {len(rows)} virtual sensors.")


if __name__ == "__main__":
    build()
