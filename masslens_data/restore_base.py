"""崩溃兜底：把工作副本（am_pg_holes）的基态 db/rdb/r001/r002/ldhi 从
.bak_masslens 备份恢复回去。

用法：python -m masslens_data.restore_base
"""
import shutil

from . import config

EXTS = ("db", "rdb", "r001", "r002", "ldhi")


def main():
    for ext in EXTS:
        bak = config.WORK_DIR / f"{config.BASE_JOBNAME}.{ext}.bak_masslens"
        dst = config.WORK_DIR / f"{config.BASE_JOBNAME}.{ext}"
        if not bak.exists():
            print(f"无备份 {bak.name}，跳过")
            continue
        shutil.copy2(bak, dst)
        print(f"已恢复 {dst.name} ← {bak.name}")


if __name__ == "__main__":
    main()
