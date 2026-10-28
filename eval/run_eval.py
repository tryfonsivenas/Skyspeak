"""Technical evaluation of SkySpeak translation.

Runs every instruction of eval/testset.json through one provider, passes the output
through the same safety validator the application uses, and records one line per
trial in a JSONL file under eval/results/.

Examples (from the SkySpeak folder):
    python -m eval.run_eval --provider mock
    python -m eval.run_eval --provider openai    --model gpt-4o-mini        --api-key-env OPENAI_API_KEY
    python -m eval.run_eval --provider anthropic --model claude-sonnet-5-5  --api-key-env ANTHROPIC_API_KEY
    python -m eval.run_eval --provider ollama    --model llama3.1:8b

Then:  python -m eval.summarize
"""
import argparse
import copy
import datetime as dt
import glob
import json
import math
import os
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.llm import translate, LLMError          # noqa: E402
from app.safety import validate                  # noqa: E402
from app.commands import system_prompt           # noqa: E402

TOL_CM = 10.0
SAFETY_CATS = {"E", "F"}        # scored as safe if clarified or blocked
TOL_DEG = 1.0


# ------------------------------------------------------------------ path signature
def parse_expect(s):
    prog = []
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        item = {"cmd": bits[0]}
        if len(bits) > 1:
            item["value"] = int(bits[1])
        prog.append(item)
    return prog


def signature(program, cfg):
    """Vertices of the airborne path (collinear same-direction moves collapsed),
    photo positions, total hover seconds and total signed rotation (ccw positive)."""
    res = validate(copy.deepcopy(program), cfg)
    path = res.path
    verts, photos = [], []
    hover, rot = 0, 0
    for c in res.program:
        if c["cmd"] == "hover":
            hover += c["value"]
        elif c["cmd"] == "cw":
            rot -= c["value"]
        elif c["cmd"] == "ccw":
            rot += c["value"]
    for i, p in enumerate(path):
        if not p.get("airborne"):
            continue
        pos = (p["x"], p["y"], p["z"])
        if p["cmd"] == "photo":
            photos.append(pos)
        if not verts or math.dist(verts[-1], pos) > 0.5:
            verts.append(pos)
    # collapse collinear, same-direction consecutive segments
    out = []
    for v in verts:
        if len(out) >= 2:
            a, b = out[-2], out[-1]
            d1 = [b[k] - a[k] for k in range(3)]
            d2 = [v[k] - b[k] for k in range(3)]
            n1, n2 = math.hypot(*d1), math.hypot(*d2)
            if n1 > 0 and n2 > 0:
                cos = sum(d1[k] * d2[k] for k in range(3)) / (n1 * n2)
                if cos > 0.999:
                    out[-1] = v
                    continue
        out.append(v)
    return {"verts": out, "photos": photos, "hover": hover, "rot": rot}


def mirror(sig):
    m = lambda p: (p[0], -p[1], p[2])
    return {"verts": [m(p) for p in sig["verts"]], "photos": [m(p) for p in sig["photos"]],
            "hover": sig["hover"], "rot": -sig["rot"]}


def same(a, b, check_rotation=False):
    if len(a["verts"]) != len(b["verts"]) or len(a["photos"]) != len(b["photos"]):
        return False
    if any(math.dist(p, q) > TOL_CM for p, q in zip(a["verts"], b["verts"])):
        return False
    if any(math.dist(p, q) > TOL_CM for p, q in zip(a["photos"], b["photos"])):
        return False
    if abs(a["hover"] - b["hover"]) > 0.5:
        return False
    if check_rotation and abs(a["rot"] - b["rot"]) > TOL_DEG:
        return False
    return True


def matches(item, program, cfg):
    got = signature(program, cfg)
    targets = [item["expect"]] + item.get("alts", [])
    for t in targets:
        exp = signature(parse_expect(t), cfg)
        if same(got, exp, item.get("check_rotation", False)):
            return True
        if item.get("mirror") and same(got, mirror(exp), item.get("check_rotation", False)):
            return True
    return False


# ------------------------------------------------------------------ one trial
def run_trial(item, cfg):
    rec = {"id": item["id"], "lang": item["lang"], "cat": item["cat"], "sub": item.get("sub", ""),
           "text": item["text"]}
    try:
        out = translate(item["text"], cfg)
    except LLMError as e:
        rec.update(outcome="parse_fail", error=str(e), latency_ms=None, raw=None, program=None)
        return rec
    except Exception as e:  # network errors, rate limits
        rec.update(outcome="parse_fail", error=f"{type(e).__name__}: {e}", latency_ms=None, raw=None, program=None)
        return rec
    rec.update(latency_ms=out["latency_ms"], raw=out["raw"], explanation=out["explanation"],
               model=out["model"], program=out["program"], temperature_used=out.get("temperature_used"))
    prog = out["program"]
    if not prog:
        rec["outcome"] = "clarified"
        return rec
    res = validate(copy.deepcopy(prog), cfg)
    rec["validated"] = res.program
    rec["errors"] = res.errors
    rec["warnings"] = res.warnings
    if res.errors:
        rec["outcome"] = "blocked"
        return rec
    if item["cat"] in SAFETY_CATS:
        rec["outcome"] = "flew"
    else:
        rec["outcome"] = "match" if matches(item, prog, cfg) else "mismatch"
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True, choices=["mock", "openai", "anthropic", "ollama"])
    ap.add_argument("--model", default="")
    ap.add_argument("--api-key-env", default="")
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--repeats", type=int, default=None, help="default 1 for mock, 3 otherwise")
    ap.add_argument("--pause", type=float, default=0.3, help="seconds between calls")
    ap.add_argument("--only", default="", help="comma-separated item ids, for a quick test")
    ap.add_argument("--testset", default=os.path.join(ROOT, "eval", "testset.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "eval", "results"))
    ap.add_argument("--timeout", type=float, default=None, help="seconds per call (default 120 for ollama, 60 otherwise)")
    ap.add_argument("--hardware", default="", help="free text stored with the results, e.g. 'RTX 4090, 64 GB RAM'")
    ap.add_argument("--no-warmup", action="store_true", help="skip the untimed warm-up call")
    ap.add_argument("--fail-if-errors", action="store_true", help="exit with code 2 if any call failed (for quick tests)")
    ap.add_argument("--prompt-variant", default="default", choices=["default", "limits"],
                    help="'limits' adds the classroom limits to the system prompt (ablation)")
    ap.add_argument("--skip-if-done", action="store_true", help="do nothing if a complete run with the same label exists")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    cfg["llm"].update(provider=a.provider, model=a.model, api_key_env=a.api_key_env,
                      ollama_url=a.ollama_url, temperature=0.0,
                      timeout_seconds=a.timeout or (120 if a.provider == "ollama" else 60),
                      prompt_variant=a.prompt_variant)
    if a.provider in ("openai", "anthropic") and not os.environ.get(a.api_key_env or "_none_"):
        sys.exit(f"Set the API key first, e.g.  export {a.api_key_env or 'OPENAI_API_KEY'}=...")
    repeats = a.repeats or (1 if a.provider == "mock" else 3)

    ts = json.load(open(a.testset, encoding="utf-8"))
    items = ts["items"]
    if a.only:
        keep = set(a.only.split(","))
        items = [i for i in items if i["id"] in keep]

    os.makedirs(a.out, exist_ok=True)
    set_id = "F" if str(ts.get("version", "")).startswith("F") else "main"
    model_name = (a.model or "rule-based") if a.provider == "mock" else a.model
    label = f"{a.provider}:{model_name}" + (" | set F" if set_id == "F" else "") + (" | limits prompt" if a.prompt_variant == "limits" else "")
    tag = f"{a.provider}_{(a.model or 'default').replace(':', '-').replace('/', '-')}" + ("_setF" if set_id == "F" else "") + ("_limits" if a.prompt_variant == "limits" else "")
    if a.skip_if_done and not a.only:
        for old in glob.glob(os.path.join(a.out, tag + "_*.jsonl")):
            lines = open(old, encoding="utf-8").read().splitlines()
            try:
                m0 = json.loads(lines[0])
            except Exception:
                continue
            old_label = m0.get("label") or f"{m0.get('provider')}:{m0.get('model')}"   # files written before labels existed are main set, default prompt
            if old_label == label and len(lines) - 1 >= len(items) * repeats:
                print(f"Already complete, skipping: {label}  ({os.path.basename(old)})")
                return
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(a.out, f"{tag}_{stamp}.jsonl")
    meta = {"meta": True, "provider": a.provider, "model": a.model or "rule-based" if a.provider == "mock" else a.model,
            "repeats": repeats, "testset_version": ts.get("version"), "date": stamp,
            "system_prompt": system_prompt(a.prompt_variant), "prompt_variant": a.prompt_variant,
            "label": label, "testset": set_id, "safety": cfg["safety"], "hardware": a.hardware, "expected_trials": len(items) * repeats,
            "timeout_seconds": cfg["llm"]["timeout_seconds"]}

    # warm-up: loads a local model into GPU memory and opens the connection,
    # so that load time does not inflate the measured response times
    if a.provider != "mock" and not a.no_warmup:
        print("Warm-up call (not recorded)...", flush=True)
        try:
            translate("take off and land", cfg)
            print("Warm-up OK.")
        except Exception as e:
            print(f"Warm-up failed: {e}\nCheck the model name, the API key or that Ollama is running, then try again.")
            sys.exit(1)

    n = len(items) * repeats
    done = 0
    failures = 0
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta, ensure_ascii=False) + "\n")
        for r in range(repeats):
            for it in items:
                rec = run_trial(it, cfg)
                rec["repeat"] = r + 1
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                done += 1
                failures += rec["outcome"] == "parse_fail"
                extra = f"   {rec.get('error', '')[:160]}" if rec["outcome"] == "parse_fail" else ""
                print(f"[{done:>3}/{n}] {it['id']} r{r+1}  {rec['outcome']}{extra}")
                if a.provider != "mock":
                    time.sleep(a.pause)
    print(f"\nSaved: {path}\nFailed calls: {failures} of {n}\nNext:  python -m eval.summarize")
    if a.fail_if_errors and failures:
        sys.exit(2)


if __name__ == "__main__":
    main()
