"""The whole suite, its test files side by side: each file is `unittest discover -s tests -p <file>`, as the plain
`python -m unittest discover -s tests` runs it, in a process of its own, the slowest first (from the last run's times);
a file that took over SPLIT_ABOVE seconds last time runs one test to a process (`-k`), so no one file holds the whole
run up. Prints the failures' output, each file's time with --times, and "OK" only when every test passed.

    python tests/run_parallel.py [--jobs N] [--times] [pattern ...]
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TESTS = Path(__file__).resolve().parent
TIMES = TESTS / "runs" / "test-times.json"   # (tests/runs is not kept in the repo)
SPLIT_ABOVE = 20.0                           # seconds: a file this slow runs a test to a process


def run(path: Path, test: str | None = None) -> tuple[Path, int, str, float, int]:
    start = time.time()
    command = [sys.executable, "-m", "unittest", "discover", "-s", str(TESTS), "-p", path.name]
    if test:
        command += ["-k", test]
    proc = subprocess.run(command, cwd=TESTS.parent, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    ran = re.search(r"^Ran (\d+) tests?", out, re.M)
    return path, proc.returncode, out, time.time() - start, int(ran.group(1)) if ran else 0


def tree() -> str:
    """The code as it stands: HEAD and what is changed on top of it (git), or "" outside a git checkout. A merge or
    edit while the suite runs gives its later files other code than its earlier ones (T-FLAKE-1: two tests failed in
    one run while a batch was merged, then passed 24 of 24 alone)."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=TESTS.parent, capture_output=True, text=True).stdout
        # (what is changed, by content: a file edited again shows the same in a status list)
        diff = subprocess.run(["git", "diff", "HEAD", "--", ".", ":!tests/runs"], cwd=TESTS.parent,
                              capture_output=True).stdout
        new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=TESTS.parent,
                             capture_output=True).stdout
        return head.strip() + hashlib.sha1(diff + new).hexdigest()
    except OSError:
        return ""


def tests_of(path: Path) -> list[str]:
    """Its tests' names, Class.method (what -k picks out), or [] when they cannot be listed."""
    import unittest
    out = []

    def walk(suite):
        for t in suite:
            if isinstance(t, unittest.TestSuite):
                walk(t)
            elif not type(t).__name__.startswith("_Failed"):
                out.append(f"{type(t).__name__}.{t._testMethodName}")
    try:
        walk(unittest.TestLoader().discover(str(TESTS), pattern=path.name))
    except Exception:
        return []
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=min(16, os.cpu_count() or 4))
    parser.add_argument("--times", action="store_true", help="print each file's time")
    parser.add_argument("patterns", nargs="*", help="only these files (glob, e.g. test_belt*)")
    args = parser.parse_args()
    files = sorted({p for pat in (args.patterns or ["test_*.py"])
                    for p in TESTS.glob(pat if pat.endswith(".py") else pat + ".py")})
    try:
        last = json.loads(TIMES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    files.sort(key=lambda p: -float(last.get(p.name, 60)))   # (the slowest first: the run ends with the short ones)
    jobs = []
    for p in files:
        split = tests_of(p) if float(last.get(p.name, 0)) > SPLIT_ABOVE else []
        jobs += [(p, t) for t in split] if split else [(p, None)]
    start, total, failed, times = time.time(), 0, [], {}
    before = tree()
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for future in as_completed([pool.submit(run, p, t) for p, t in jobs]):
            path, code, out, took, ran = future.result()
            total += ran
            times[path.name] = round(times.get(path.name, 0.0) + took, 1)   # (a split file: its tests' sum)
            if code != 0:
                failed.append(path.name)
                print(f"\n=== {path.name} ({took:.0f}s) ===\n{out.strip()}\n", flush=True)
    try:
        TIMES.parent.mkdir(parents=True, exist_ok=True)
        TIMES.write_text(json.dumps({**last, **times}, indent=1, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    if args.times:
        for name, took in sorted(times.items(), key=lambda kv: -kv[1]):
            print(f"{took:7.1f}s  {name}")
    print(f"\nRan {total} tests in {time.time() - start:.1f}s ({len(files)} files, {len(jobs)} processes, "
          f"{args.jobs} at a time)")
    if tree() != before:   # (a run over code that changed under it proves nothing either way)
        print("FAILED (the code changed while the suite ran: a commit, merge or edit; run it again)")
        return 1
    print(f"FAILED ({len(set(failed))} files: {', '.join(sorted(set(failed)))})" if failed else "OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
