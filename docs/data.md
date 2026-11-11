# 数据与复现

## 随库示例

`data/masslens_shed_v2.h5` 是离线模态计算的示例数据库。当前模型的参考值为 mₐ = 0 kg/m² 时 f₁ ≈ 0.6401 Hz，以及 mₐ = 1.333 kg/m² 时 f₁ ≈ 0.5501 Hz。库的结构和单位见 [HDF5 格式 v1](数据格式_HDF5_v1.md)。

`examples/demo/basis_ext60.npz` 保存同一有限元网格的 60 阶频率和振型，`danger_combos.json` 是等权同相叠加计算的组合库。其余演示文件由 `python -m examples.generate_wind_demo` 生成，包含合成数据束、虚拟测点表、两个 10 Hz 片段和用于显示的风压统计量。

## 使用自己的风振数据

将数据束与其对应的测点表、时程段和可选的扩展模态基放到独立目录，设置 `WINDLENS_OUTPUT_DIR` 后启动软件。数据需要与 HDF5 示例库的有限元坐标系和节点序相符。如使用其他结构，也需替换模态库和相应结构配置。

| 环境变量 | 用途 |
| --- | --- |
| `WINDLENS_OUTPUT_DIR` | 风透镜数据束、可选时程与派生文件目录。默认读取 `examples/demo`。 |
| `WINDLENS_SENSOR_MAP` | 测点 CSV 的路径。默认读取输出目录下的 `sensor_map.csv`。 |
| `WINDLENS_DATA_DIR` | 离线处理的原始输入目录。默认 `monitoring`。 |

数据束格式见 `windlens_engine/bundle.py`。文件头声明按顺序排列的 float32 数组及形状，窗级谱峰的四列依次是频率、幅值、MAC 和质量等级。预处理和风况 CSV 的字段约定见 `windlens_engine/config.py` 与 `windlens_engine/bundle.py`。以下命令用于已有兼容监测记录、测点表及风况窗口表的离线处理。

```sh
python -m windlens_engine.csd
python -m windlens_engine.window_stats
python -m windlens_engine.bundle
```

原始通道排列与输入变量名按源工程约定，换用其他数据时需映射字段和单位。

## MAPDL 数据库重建

查看 `apdl/`、`masslens_data/sweep.py` 和 `masslens_data/build_db.py`。环境变量 `MASSLENS_MAPDL_EXE` 指向 MAPDL，`MASSLENS_MAPDL_WORK_DIR` 指向基态模型工作目录。原计算使用 ANSYS 2024 R2 的预应力线性摄动和附加质量扫描。此流程需要相容的 MAPDL 基态与重启动文件。随库 HDF5 支持直接运行插值、验证和界面。
