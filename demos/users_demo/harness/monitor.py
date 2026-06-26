#!/usr/bin/env python3
import glob
import json
import os
import time
from pathlib import Path

REPRO = Path(__file__).resolve().parent.parent
LOG = REPRO / "monitor.log"
ALERT = REPRO / "monitor_alerts.log"
STUCK_MIN = 20.0
MODELS = ["fugu-ultra", "gpt55", "opus48", "gemini"]


def _proc_alive(needle: str) -> bool:
    try:
        out = os.popen(f"pgrep -fa '{needle}' 2>/dev/null").read()
        return any(needle in ln for ln in out.splitlines())
    except Exception:
        return False


def _age_min(p: Path) -> float:
    try:
        return (time.time() - p.stat().st_mtime) / 60.0
    except Exception:
        return 1e9


def classify(demo: Path, mk: str):
    d = demo / mk
    meta = d / "meta.json"
    if meta.exists():
        try:
            m = json.load(open(meta))
        except Exception:
            m = {}
        if "rc" in m:
            nfile = len(glob.glob(str(d / "*.html")))
            ok = (m.get("rc") == 0)
            return ("done-ok" if ok else "done-fail"), \
                   f"rc={m.get('rc')} elapsed={m.get('elapsed_s','?')}s html={nfile}"
    ev = d / "events.jsonl"
    if not ev.exists():
        return ("starting" if (d / "launch.log").exists() else "not-started"), ""
    alive = _proc_alive(str(d))
    age = _age_min(ev)
    n = sum(1 for _ in open(ev, errors="ignore"))
    state = "running" if alive else "exited?"
    if alive and age > STUCK_MIN:
        state = "STUCK"
    return state, f"events={n} idle={age:.1f}m"


def main():
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = [f"===== {ts} ====="]
    alerts = []
    for demo in sorted(glob.glob(str(REPRO / "demo*"))):
        demo = Path(demo)
        if not demo.is_dir():
            continue
        for mk in MODELS:
            if not (demo / mk).exists():
                continue
            state, extra = classify(demo, mk)
            lines.append(f"  {demo.name:22s} {mk:11s} {state:11s} {extra}")
            if state in ("STUCK", "done-fail", "exited?"):
                alerts.append(f"{ts} {demo.name}/{mk}: {state} {extra}")
    snap = "\n".join(lines)
    with open(LOG, "a") as f:
        f.write(snap + "\n")
    if alerts:
        with open(ALERT, "a") as f:
            f.write("\n".join(alerts) + "\n")
    print(snap)
    if alerts:
        print("ALERTS:\n" + "\n".join(alerts))


if __name__ == "__main__":
    main()
