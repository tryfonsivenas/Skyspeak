"""Validation and safety checks applied to every program before it can fly."""
import math
from .commands import COMMANDS, MOVE_CMDS


class ValidationResult:
    def __init__(self, program, errors, warnings, path):
        self.program = program
        self.errors = errors
        self.warnings = warnings
        self.path = path

    @property
    def ok(self):
        return not self.errors

    def to_dict(self):
        return {"program": self.program, "errors": self.errors, "warnings": self.warnings,
                "path": self.path, "ok": self.ok}


def simulate(program):
    """Return a list of points {x, y, z, heading, cmd} starting at the origin on the ground.
    x is forward of the take-off heading, y is left, z is up (cm). Heading in degrees, 0 = initial facing."""
    x = y = z = 0.0
    heading = 0.0
    airborne = False
    pts = [{"x": 0, "y": 0, "z": 0, "heading": 0, "cmd": "start"}]
    for c in program:
        name, v = c["cmd"], c.get("value")
        if name == "takeoff":
            airborne, z = True, 80.0   # Tello hovers at roughly 80 cm after takeoff
        elif name == "land":
            airborne, z = False, 0.0
        elif name in ("up", "down"):
            z += v if name == "up" else -v
        elif name in ("forward", "back", "left", "right"):
            ang = math.radians(heading + {"forward": 0, "left": 90, "back": 180, "right": -90}[name])
            x += v * math.cos(ang)
            y += v * math.sin(ang)
        elif name == "cw":
            heading -= v
        elif name == "ccw":
            heading += v
        pts.append({"x": round(x, 1), "y": round(y, 1), "z": round(z, 1),
                    "heading": round(heading % 360, 1), "cmd": name, "airborne": airborne})
    return pts


def validate(raw_program, cfg):
    s = cfg["safety"]
    errors, warnings, program = [], [], []

    if not isinstance(raw_program, list):
        return ValidationResult([], ["Το πρόγραμμα δεν είναι λίστα εντολών."], [], [])

    for i, c in enumerate(raw_program):
        if not isinstance(c, dict) or "cmd" not in c:
            errors.append(f"Εντολή {i+1}: μη έγκυρη μορφή.")
            continue
        name = str(c["cmd"]).strip().lower()
        if name not in COMMANDS:
            errors.append(f"Εντολή {i+1}: η εντολή «{name}» δεν επιτρέπεται.")
            continue
        needs, lo, hi = COMMANDS[name][0], COMMANDS[name][1], COMMANDS[name][2]
        item = {"cmd": name}
        if needs:
            try:
                v = int(round(float(c.get("value"))))
            except (TypeError, ValueError):
                errors.append(f"Εντολή {i+1} ({name}): λείπει ή είναι λάθος η τιμή.")
                continue
            if v < lo or v > hi:
                errors.append(f"Εντολή {i+1} ({name}): η τιμή {v} είναι εκτός ορίων ({lo} έως {hi}).")
                continue
            item["value"] = v
        program.append(item)

    if errors:
        return ValidationResult(program, errors, warnings, [])

    if not program:
        return ValidationResult([], ["Το πρόγραμμα είναι κενό."], [], [])

    if s.get("require_takeoff_first", True) and program[0]["cmd"] != "takeoff":
        program.insert(0, {"cmd": "takeoff"})
        warnings.append("Προστέθηκε απογείωση στην αρχή.")
    if s.get("auto_land_at_end", True) and program[-1]["cmd"] != "land":
        program.append({"cmd": "land"})
        warnings.append("Προστέθηκε προσγείωση στο τέλος.")

    if len(program) > s["max_commands"]:
        errors.append(f"Πάρα πολλές εντολές ({len(program)}). Το όριο είναι {s['max_commands']}.")

    # flight-state checks
    airborne = False
    for i, c in enumerate(program):
        if c["cmd"] == "takeoff":
            if airborne:
                errors.append(f"Εντολή {i+1}: απογείωση ενώ το drone ήδη πετάει.")
            airborne = True
        elif c["cmd"] == "land":
            airborne = False
        elif not airborne:
            errors.append(f"Εντολή {i+1} ({c['cmd']}): το drone δεν έχει απογειωθεί.")

    path = simulate(program)
    for p in path:
        if p["z"] > s["max_height_cm"]:
            errors.append(f"Το drone θα ξεπεράσει το μέγιστο ύψος ({s['max_height_cm']} εκ.).")
            break
    for p in path:
        if p.get("airborne") and p["z"] < 20:
            errors.append("Το drone θα κατέβει πολύ χαμηλά (κάτω από 20 εκ.) ενώ πετάει.")
            break
    for p in path:
        if math.hypot(p["x"], p["y"]) > s["max_radius_cm"]:
            errors.append(f"Το drone θα απομακρυνθεί περισσότερο από {s['max_radius_cm']} εκ. από το σημείο απογείωσης.")
            break

    return ValidationResult(program, errors, warnings, path)
