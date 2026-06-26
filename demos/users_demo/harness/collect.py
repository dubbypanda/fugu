#!/usr/bin/env python3
import glob
import json
import os
import re
from pathlib import Path

REPRO = Path(__file__).resolve().parent.parent
DEMOS = ["demo1_subway", "demo2_rocketleague", "demo3_traderdesk",
         "demo4_terrain", "demo5_crossy_road", "demo6_rube_goldberg"]
MODELS = ["fugu-ultra", "gpt55", "opus48", "gemini"]
FENCE = re.compile(r"```(?:html|HTML|xml|javascript|js)?\s*\n(.*?)```", re.DOTALL)
IGNORE = {"events.jsonl", "launch.log", "run.err", "meta.json", "last_message.txt",
          "rerun.log", "rerun2.log", "extracted_index.html"}
PRUNE = {"node_modules", ".git", ".venv", "dist-ssr", ".cache"}


def cell_files(d: Path):
    out = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in PRUNE]
        for f in files:
            if f in IGNORE or f.endswith(".png"):
                continue
            p = Path(root) / f
            try:
                out.append((str(p.relative_to(d)), p.stat().st_size))
            except Exception:
                pass
    return out


def is_self_contained(path: Path) -> bool:
    try:
        t = path.read_text(errors="ignore")
    except Exception:
        return False
    refs = re.findall(r'<script[^>]*\bsrc=["\']([^"\']+)["\']', t, re.I)
    local = [r for r in refs if not r.startswith(("http://", "https://", "data:", "//"))]
    return len(local) == 0


def extract_code(text):
    blocks = FENCE.findall(text or "")
    return max(blocks, key=len).strip() if blocks else None


def metrics(d: Path):
    m = {"elapsed_s": None, "rc": None}
    try:
        meta = json.load(open(d / "meta.json"))
        m["elapsed_s"] = meta.get("elapsed_s"); m["rc"] = meta.get("rc")
    except Exception:
        pass
    tools = 0
    ev = d / "events.jsonl"
    if ev.exists():
        for ln in open(ev, errors="ignore"):
            if "command_execution" in ln and '"item.completed"' in ln:
                tools += 1
    m["tool_calls"] = tools
    return m


def main():
    out_dir = REPRO / "collected"; out_dir.mkdir(exist_ok=True)
    summary = []
    for demo in DEMOS:
        for mk in MODELS:
            d = REPRO / demo / mk
            if not d.exists():
                continue
            files = cell_files(d)
            htmls = [(f, s) for f, s in files if f.lower().endswith((".html", ".htm"))]
            htmls.sort(key=lambda fs: (fs[0].count("/"),
                                       0 if fs[0].lower().endswith("index.html") else 1,
                                       -fs[1]))
            deliverable = dsize = source = self_contained = None
            if htmls:
                deliverable, dsize = htmls[0]; source = "file"
                self_contained = is_self_contained(d / deliverable)
            else:
                lm = d / "last_message.txt"
                if lm.exists():
                    code = extract_code(lm.read_text(errors="ignore"))
                    if code and any(x in code.lower() for x in ("<html", "<script", "<!doctype")):
                        (d / "extracted_index.html").write_text(code)
                        deliverable, dsize, source = "extracted_index.html", len(code), "from_message"
                        self_contained = is_self_contained(d / "extracted_index.html")
            src = [(f, s) for f, s in files
                   if f.lower().endswith((".js", ".ts", ".css", ".py", ".tsx", ".jsx", ".mjs"))
                   and "/dist/" not in f]
            src_bytes = sum(s for _, s in src)
            mt = metrics(d)
            summary.append({
                "demo": demo, "model": mk, **mt,
                "deliverable": deliverable, "deliverable_bytes": dsize,
                "deliverable_source": source, "self_contained": self_contained,
                "n_source_files": len(src), "src_bytes": src_bytes,
                "kind": ("single-file" if self_contained else ("multi-file" if deliverable else "none")),
            })
    json.dump(summary, open(out_dir / "summary.json", "w"), indent=2)

    lines = ["# Reproduction sweep — artifacts & metrics", "",
             "All 4 models driven by **codex** at max effort (fugu-ultra xhigh via codex-fugu; "
             "GPT-5.5 xhigh / Opus 4.8 xhigh / Gemini 3.1 high via codex→OpenRouter; "
             "Opus through the reasoning-strip proxy). `node_modules` pruned.", "",
             "| Demo | Model | rc | time | tools | deliverable | entry size | project src | kind |",
             "|---|---|---|---|---|---|---|---|---|"]

    def fmt(b):
        if not b:
            return "—"
        return f"{b}B" if b < 1024 else f"{b/1024:.0f}KB"

    for r in summary:
        t = f"{r['elapsed_s']}s" if r["elapsed_s"] else "?"
        deliv = r["deliverable"] or "—"
        proj = fmt(r.get("src_bytes")) + (f" ({r['n_source_files']}f)" if r.get("n_source_files") else "")
        lines.append(f"| {r['demo']} | {r['model']} | {r['rc']} | {t} | {r['tool_calls']} | "
                     f"`{deliv}` | {fmt(r['deliverable_bytes'])} | {proj} | {r['kind']} |")
    (out_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
