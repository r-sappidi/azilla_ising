#!/usr/bin/env python3
"""Remove only old compiler caches from explicit legacy Verilator build dirs.

Dry-run by default. Retains generated C++, executables, logs and traces.
Never follows symlinks or operates on results, tools, VCS or synthesis trees.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time


SUFFIXES = {".gch", ".o", ".a", ".d"}


def process_references():
    """Include paused jobs, mapped libraries, cwd, executables and open files."""
    references = []
    ancestors = set()
    parent = os.getppid()
    while parent > 1:
        ancestors.add(str(parent))
        parent = int(Path(f"/proc/{parent}/stat").read_text().rsplit(")", 1)[1].split()[1])
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            state = (process / "stat").read_text().rsplit(")", 1)[1].split()[0]
            if state == "Z":  # Zombies have no address space or open files.
                continue
            if (process / "comm").read_text().strip() in {"sshd", "gnome-keyring-d"}:
                continue  # Privilege-separated desktop/login services, not jobs.
            refs = [] if process.name in ancestors else [
                (process / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")]
            refs.append((process / "maps").read_text(errors="replace"))
            for link in [process / "cwd", process / "exe", *(process / "fd").iterdir()]:
                try:
                    refs.append(os.readlink(link))
                except FileNotFoundError:
                    pass
            references.append((process.name, "\n".join(refs)))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError as exc:
            raise RuntimeError(f"Cannot inspect own process {process.name}; refusing cleanup") from exc
    return references


def candidates(directory, cutoff, tracked):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"Not a real build directory: {directory}")
    if not list(directory.glob("V*.mk")):
        raise ValueError(f"Missing Verilator build marker: {directory}")
    # No recursive traversal: unexpected subtrees remain untouched.
    return sorted(p for p in directory.iterdir()
                  if not p.is_symlink() and p.is_file()
                  and p.suffix in SUFFIXES and p.stat().st_mtime < cutoff
                  and p not in tracked)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", action="append", required=True,
                        help="Explicit immediate child name of build/, beginning ising_mesh_floo_")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path, help="New JSONL audit file required with --apply")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    build = root / "build"
    if build.is_symlink():
        parser.error("build/ must not be a symlink")
    if args.apply and args.manifest is None:
        parser.error("--apply requires a new --manifest")
    tracked = {root / s for s in subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root).decode().split("\0") if s}
    references = process_references()
    selected = []
    for name in dict.fromkeys(args.build_dir):
        if Path(name).name != name or not name.startswith("ising_mesh_floo_"):
            parser.error(f"Not an allowed legacy build directory name: {name}")
        directory = build / name
        users = [pid for pid, refs in references if name in refs]
        if users:
            print(f"SKIP {name}: process references {','.join(users)}")
            continue
        files = candidates(directory, time.time() - 7 * 86400, tracked)
        total = sum(p.stat().st_size for p in files)
        print(f"{name}: {len(files)} cache files, {total / 1024**3:.3f} GiB")
        selected.extend(files)
    print(f"TOTAL {len(selected)} files, {sum(p.stat().st_size for p in selected) / 1024**3:.3f} GiB")
    if not args.apply:
        return
    with args.manifest.open("x") as audit:
        for p in selected:
            info = p.stat()
            record = {"path": str(p.relative_to(root)), "bytes": info.st_size,
                      "mtime_ns": info.st_mtime_ns, "action": "remove_compiler_cache"}
            audit.write(json.dumps(record) + "\n")
            audit.flush()
            os.fsync(audit.fileno())
            if p.is_symlink() or p.stat() != info:
                raise RuntimeError(f"File changed during cleanup: {p}")
            p.unlink()
    print("Removed compiler caches; regenerate with a clean Verilator rebuild if needed.")


if __name__ == "__main__":
    main()
