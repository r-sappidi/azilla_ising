#!/usr/bin/env python3
"""Wait for all six source-bound certificates, then run the frozen width plan."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
from check_paper_validation_gate import validate_certificate


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    while True:
        plan = json.loads(args.plan.read_text())
        configs = json.loads(Path(plan["configs_manifest"]).read_text())
        blocked = []
        for row in configs["configs"]:
            for mode in ("cir", "cores-only"):
                errors = validate_certificate(
                    Path(plan["certificates"][str(row["width"])][mode]),
                    mode, Path(row["path"]))
                if errors:
                    blocked.append(f"x{row['width']}/{mode}")
        if not blocked:
            break
        print(datetime.now(timezone.utc).isoformat(), "WAIT", ", ".join(blocked), flush=True)
        time.sleep(60)
    subprocess.run([sys.executable, str(Path(__file__).with_name("run_memory_width_sweep.py")),
                    "run", "--plan", str(args.plan), "--output", str(args.output)], check=True)


if __name__ == "__main__":
    main()
