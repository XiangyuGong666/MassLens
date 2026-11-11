"""MassLens/WindLens 桌面版：本地服务 + 原生窗口（WebView2），不依赖浏览器。

用法（项目根目录）：
    python -m masslens_ui.desktop [--page wind]
依赖：`pip install pywebview`；Windows 需 WebView2 运行时（Win10/11 自带，
本机 Edge 已装即可用）。打包 EXE（PyInstaller）属收官步骤，本模块按
frozen 友好设计（资源走 __file__ 相对路径）。
"""
from __future__ import annotations

import argparse
import threading

from .server import create_server


def run(page="masslens"):
    import webview

    httpd, app = create_server(port=0)          # 端口 0：系统自动分配
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"[desktop] 本地服务已就绪：127.0.0.1:{port}", flush=True)

    url = f"http://127.0.0.1:{port}/" + ("" if page == "masslens" else page)
    # 默认 1920×1080（大多数显示器可全显），小屏按可用区域钳制
    width, height = 1920, 1080
    try:
        scr = webview.screens[0]
        width = min(width, scr.width)
        height = min(height, scr.height - 70)   # 给任务栏留位
    except Exception:
        pass
    print(f"[desktop] 打开原生窗口 {width}×{height} → {url}", flush=True)
    webview.create_window("MassLens 质量透镜 · WindLens 风透镜", url,
                          width=width, height=height,
                          min_size=(1200, 700))
    webview.start(private_mode=True)             # 隐私模式：每次启动全新无缓存
    httpd.shutdown()


def main():
    ap = argparse.ArgumentParser(prog="masslens_ui.desktop")
    ap.add_argument("--page", choices=["masslens", "wind", "zen"], default="masslens",
                    help="启动后先显示哪个实验室")
    args = ap.parse_args()
    run(args.page)


if __name__ == "__main__":
    main()
