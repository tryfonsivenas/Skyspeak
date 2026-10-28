"""Natural-language to program translation.

Providers:
- mock:      offline rule-based parser for Greek and English (no internet, deterministic)
- openai:    OpenAI Chat Completions API
- anthropic: Anthropic Messages API
- ollama:    local model through Ollama (no data leaves the laptop)
"""
import json
import os
import re
import time
import unicodedata

import httpx

from .commands import system_prompt


class LLMError(Exception):
    pass


def _extract_json(text):
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError("Το μοντέλο δεν επέστρεψε JSON.")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMError(f"Μη έγκυρο JSON από το μοντέλο: {e}")


def translate(text, cfg):
    """Return dict with keys: program, explanation, raw, provider, model, latency_ms."""
    lc = cfg["llm"]
    provider = lc.get("provider", "mock")
    t0 = time.time()
    if provider == "mock":
        prog, expl = mock_parse(text)
        raw = json.dumps({"program": prog, "explanation": expl}, ensure_ascii=False)
        data = {"program": prog, "explanation": expl}
    else:
        raw = _call_remote(text, lc, provider)
        data = _extract_json(raw)
    return {
        "program": data.get("program", []),
        "explanation": data.get("explanation", ""),
        "raw": raw,
        "provider": provider,
        "model": lc.get("model", "") if provider != "mock" else "rule-based",
        "latency_ms": int((time.time() - t0) * 1000),
        "temperature_used": None if provider == "mock" else LAST_CALL.get("temperature_used"),
    }


def _key(lc):
    env = lc.get("api_key_env") or ""
    key = os.environ.get(env, "") if env else ""
    if not key:
        raise LLMError(f"Δεν βρέθηκε κλειδί API στη μεταβλητή περιβάλλοντος «{env}».")
    return key


LAST_CALL = {"temperature_used": None}


def _post(url, headers, body, timeout):
    """POST; if the model rejects a fixed temperature (some newer models only allow the
    default), retry once without it and record that in LAST_CALL."""
    r = httpx.post(url, timeout=timeout, headers=headers, json=body)
    if r.status_code == 400 and "temperature" in body and "temperature" in r.text.lower():
        body = {k: v for k, v in body.items() if k != "temperature"}
        r = httpx.post(url, timeout=timeout, headers=headers, json=body)
        LAST_CALL["temperature_used"] = False
    else:
        LAST_CALL["temperature_used"] = "temperature" in body
    r.raise_for_status()
    return r


def _call_remote(text, lc, provider):
    SYSTEM_PROMPT = system_prompt(lc.get("prompt_variant", "default"))
    timeout = lc.get("timeout_seconds", 30)
    temp = lc.get("temperature", 0.0)
    model = lc.get("model")
    try:
        if provider == "openai":
            r = _post("https://api.openai.com/v1/chat/completions",
                      {"Authorization": f"Bearer {_key(lc)}"},
                      {"model": model or "gpt-4o-mini", "temperature": temp,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                    {"role": "user", "content": text}]}, timeout)
            return r.json()["choices"][0]["message"]["content"]
        if provider == "anthropic":
            r = _post("https://api.anthropic.com/v1/messages",
                      {"x-api-key": _key(lc), "anthropic-version": "2023-06-01"},
                      {"model": model or "claude-sonnet-5-5", "max_tokens": 1024,
                       "temperature": temp, "system": SYSTEM_PROMPT,
                       "messages": [{"role": "user", "content": text}]}, timeout)
            return "".join(b.get("text", "") for b in r.json()["content"] if b.get("type", "text") == "text")
        if provider == "ollama":
            r = httpx.post(lc.get("ollama_url", "http://localhost:11434").rstrip("/") + "/api/chat",
                           timeout=timeout,
                           json={"model": model or "llama3.1:8b", "stream": False, "format": "json",
                                 "options": {"temperature": temp},
                                 "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                              {"role": "user", "content": text}]})
            r.raise_for_status()
            return r.json()["message"]["content"]
    except httpx.HTTPError as e:
        raise LLMError(f"Σφάλμα επικοινωνίας με το μοντέλο ({provider}): {e}")
    raise LLMError(f"Άγνωστος πάροχος «{provider}».")


# ---------------------------------------------------------------- offline parser

NUM_WORDS = {
    "ενα": 1, "μια": 1, "μισο": 0.5, "δυο": 2, "δυομισι": 2.5, "τρια": 3, "τρεις": 3, "τεσσερα": 4,
    "τεσσερις": 4, "πεντε": 5, "εξι": 6, "επτα": 7, "εφτα": 7, "οκτω": 8, "οχτω": 8,
    "εννεα": 9, "εννια": 9, "δεκα": 10,
    "one": 1, "half": 0.5, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = s.replace("ς", "σ")
    for k, v in NUM_WORDS.items():
        s = re.sub(rf"\b{k.replace('ς', 'σ')}\b", str(v), s)
    return s


def _dist(seg, default=100):
    """Distance in cm from a segment, e.g. '1 μετρο', '50 εκ', '2m', '30 cm'."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(μετρ\w*|m\b|meter\w*|metre\w*|εκ\w*|cm|εκατοστ\w*|centimet\w*)?", seg)
    if not m:
        return default
    v = float(m.group(1).replace(",", "."))
    unit = m.group(2) or ""
    if unit.startswith(("μετρ", "m", "meter", "metre")) and not unit.startswith("εκ"):
        return int(round(v * 100))
    if unit:
        return int(round(v))
    return int(round(v * 100)) if v <= 5 else int(round(v))   # bare small number = metres


def _deg(seg, default=90):
    m = re.search(r"(\d+)\s*(?:μοιρ\w*|deg\w*|°)", seg)
    if m:
        return int(m.group(1))
    if re.search(r"πληρ|ολοκληρ|full|whole", seg):
        return 360
    m = re.search(r"(\d{2,3})", seg)
    return int(m.group(1)) if m else default


def _shape(seg):
    side = _dist(seg, 100)
    if re.search(r"τετραγων|square", seg):
        n = 4
    elif re.search(r"τριγων|triangle", seg):
        n = 3
    elif re.search(r"πενταγων|pentagon", seg):
        n = 5
    elif re.search(r"εξαγων|hexagon|κυκλ|circle", seg):
        n = 6
        if re.search(r"κυκλ|circle", seg):
            side = max(20, min(side, 100))
    else:
        return None
    turn = "ccw" if re.search(r"αριστερ|left", seg) else "cw"
    prog = []
    for _ in range(n):
        prog += [{"cmd": "forward", "value": side}, {"cmd": turn, "value": round(360 / n)}]
    return prog


def _segment(seg):
    seg = seg.strip()
    if not seg:
        return []
    reps = 1
    m = re.search(r"(?:επαναλ\w*|repeat)\D*(\d+)|(\d+)\s*(?:φορ\w*|times)", seg)
    if m:
        reps = int(m.group(1) or m.group(2))
        seg = re.sub(r"(?:επαναλ\w*|repeat)\D*\d+|\d+\s*(?:φορ\w*|times)", " ", seg)
    shape = _shape(seg)
    if shape:
        return shape * reps
    out = []
    if re.search(r"απογει|σηκω|ξεκιν|take ?off|start", seg):
        out.append({"cmd": "takeoff"})
    elif re.search(r"προσγει|land", seg):
        out.append({"cmd": "land"})
    elif re.search(r"φωτογραφ|photo|picture", seg):
        out.append({"cmd": "photo"})
    elif re.search(r"αιωρ|περιμεν|σταματ|hover|wait|stay", seg):
        m2 = re.search(r"(\d+)", seg)
        out.append({"cmd": "hover", "value": int(m2.group(1)) if m2 else 3})
    elif re.search(r"στριψ|γυρ|περιστρ|turn|rotate|spin", seg):
        cmd = "ccw" if re.search(r"αριστερ|left|counter", seg) else "cw"
        out.append({"cmd": cmd, "value": _deg(seg)})
    elif re.search(r"πανω απο|over\b|above", seg):
        pass   # "fly over X" refers to a place, ask for clarification instead
    elif re.search(r"ανεβ|πανω|ψηλ|up\b|higher|rise", seg):
        out.append({"cmd": "up", "value": _dist(seg, 50)})
    elif re.search(r"κατεβ|κατω|χαμηλ|down\b|lower", seg):
        out.append({"cmd": "down", "value": _dist(seg, 50)})
    elif re.search(r"πισω|back", seg):
        out.append({"cmd": "back", "value": _dist(seg)})
    elif re.search(r"αριστερ|left", seg):
        out.append({"cmd": "left", "value": _dist(seg)})
    elif re.search(r"δεξι|right", seg):
        out.append({"cmd": "right", "value": _dist(seg)})
    elif re.search(r"μπροστ|ευθει|forward|ahead|straight", seg):
        out.append({"cmd": "forward", "value": _dist(seg)})
    return out * reps


def mock_parse(text):
    s = _norm(text)
    parts = re.split(r",|;|\.(?!\d)|\bκαι\b|\bμετα\b|\bμετα απο αυτο\b|\bεπειτα\b|\bthen\b|\band\b|\bafter that\b", s)
    prog = []
    for p in parts:
        prog += _segment(p)
    greek = bool(re.search(r"[α-ωά-ώ]", text.lower()))
    if not prog:
        expl = ("Δεν κατάλαβα την εντολή. Δοκίμασε π.χ. «απογείωση, πήγαινε 1 μέτρο μπροστά, στρίψε δεξιά 90 μοίρες, προσγείωση»."
                if greek else "I did not understand. Try e.g. 'take off, go forward 1 metre, turn right 90 degrees, land'.")
        return [], expl
    expl = "Μετέτρεψα την οδηγία σου σε πρόγραμμα." if greek else "I converted your instruction into a program."
    return prog, expl
