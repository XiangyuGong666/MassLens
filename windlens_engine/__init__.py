"""WindLens 风透镜 —— 数据引擎（风振重构第 1 晚定稿）。

从 10 Hz 监测数据（风压/加速度/GNSS 位移/风速风向）还原结构振动信息的
数据层。经 2026-10-03 零对照检验：逐帧时域 WLS 模态分解在本数据集 SNR
不足（带内功率比 ≈1，详见 docs/进度_风振第1晚.md），因此：

- decompose.py：WLS 分解引擎（数学正确，供高 SNR 数据/合成测试/未来升级）
- participate.py：窗级模态参与量（以论文2 CSD-MAC 实测识别表为数据源，
  含 f1 → m̂_a 反演）——app 中栏数据源
- extract.py / preprocess.py / basis.py / validate.py：切窗、滤波、
  传感器处模态基、交叉验证管线

UI 层（第 2 晚起）只经本包取数，不直接碰 .mat 大文件。
"""
