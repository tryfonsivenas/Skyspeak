import os
import time

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))

from app.llm import mock_parse, _extract_json, LLMError
from app.safety import validate


def cmds(p):
    return [(c["cmd"], c.get("value")) for c in p]


@pytest.mark.parametrize("text,expected", [
    ("Απογείωση, πήγαινε 1 μέτρο μπροστά, στρίψε δεξιά 90 μοίρες, προσγείωση",
     [("takeoff", None), ("forward", 100), ("cw", 90), ("land", None)]),
    ("ανέβα μισό μέτρο", [("up", 50)]),
    ("στρίψε αριστερά 45 μοίρες", [("ccw", 45)]),
    ("take off, go back 30 cm then land", [("takeoff", None), ("back", 30), ("land", None)]),
    ("κάνε μια πλήρη περιστροφή", [("cw", 360)]),
])
def test_mock_parse(text, expected):
    assert cmds(mock_parse(text)[0]) == expected


def test_square_expands():
    p, _ = mock_parse("κάνε ένα τετράγωνο με πλευρά 1 μέτρο")
    assert len(p) == 8 and p[0] == {"cmd": "forward", "value": 100}


def test_unclear_returns_empty():
    assert mock_parse("πέτα πάνω από το σχολείο")[0] == []


def test_validate_adds_takeoff_and_land():
    r = validate([{"cmd": "forward", "value": 100}], CFG)
    assert r.ok and r.program[0]["cmd"] == "takeoff" and r.program[-1]["cmd"] == "land"


def test_validate_rejects_unknown_and_out_of_range():
    assert not validate([{"cmd": "flip", "value": "l"}], CFG).ok
    assert not validate([{"cmd": "forward", "value": 900}], CFG).ok
    assert not validate([{"cmd": "forward", "value": 5}], CFG).ok


def test_geofence_and_ceiling():
    assert not validate([{"cmd": "forward", "value": 400}], CFG).ok
    assert not validate([{"cmd": "up", "value": 200}], CFG).ok      # 80 + 200 > 200
    assert not validate([{"cmd": "down", "value": 70}], CFG).ok     # below 20 cm while flying


def test_square_closes_loop():
    p, _ = mock_parse("τετράγωνο 1 μέτρο")
    r = validate(p, CFG)
    last = r.path[-2]   # before landing
    assert abs(last["x"]) < 1 and abs(last["y"]) < 1


def test_extract_json():
    assert _extract_json('Here: {"program": [], "explanation": "x"}')["explanation"] == "x"
    with pytest.raises(LLMError):
        _extract_json("no json")


def test_api_end_to_end():
    from fastapi.testclient import TestClient
    import app.main as m
    m.CFG["sim_speedup"] = 0.01
    c = TestClient(m.app)
    assert c.get("/").status_code == 200
    d = c.post("/api/generate", json={"text": "μπροστά 1 μέτρο και στρίψε δεξιά", "student_code": "S01"}).json()
    assert d["ok"] and d["program"][1]["label"] == "Μπροστά 100 εκ."
    raw = [{k: v for k, v in x.items() if k in ("cmd", "value")} for x in d["program"]]
    assert c.post("/api/execute", json={"program": raw}).json()["started"]
    for _ in range(100):
        if not c.get("/api/status").json()["running"]:
            break
        time.sleep(0.05)
    assert c.get("/api/status").json()["step"] == len(raw) - 1
    # client-side tampering is caught by re-validation
    assert c.post("/api/execute", json={"program": [{"cmd": "takeoff"}, {"cmd": "forward", "value": 500}]}).status_code == 400
    log = open(m.LOG.path, encoding="utf-8").read()
    assert '"event": "generate"' in log and '"event": "execute_end"' in log
