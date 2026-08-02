import argparse
import re
import shutil
import sys
from pathlib import Path


def stage_tk_runtime(output_dir):
    python_base = Path(sys.base_prefix)
    source_tcl = python_base / "tcl" / "tcl8.6"
    source_tk = python_base / "tcl" / "tk8.6"
    if not source_tcl.is_dir() or not source_tk.is_dir():
        raise FileNotFoundError(f"Tcl/Tk runtime was not found under {python_base}")

    target = Path(output_dir)
    if target.exists():
        shutil.rmtree(target)
    tcl_target = target / "_tcl_data"
    tk_target = target / "_tk_data"
    shutil.copytree(source_tcl, tcl_target)
    shutil.copytree(source_tk, tk_target)

    # Some embeddable Python distributions ship a Tcl package whose exact
    # self-requirement fails after relocation even though the ABI is 8.6.
    # Keep the ABI requirement while allowing the relocated patch level.
    init_path = tcl_target / "init.tcl"
    init_text = init_path.read_text(encoding="utf-8")
    init_text = re.sub(
        r"package require -exact Tcl 8\.6\.\d+",
        "package require Tcl 8.6",
        init_text,
        count=1,
    )
    init_path.write_text(init_text, encoding="utf-8")
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    target = stage_tk_runtime(args.output)
    print(f"Staged Tcl/Tk runtime: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
