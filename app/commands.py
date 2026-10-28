"""Allowed command set (subset of the Tello SDK 2.0) and its JSON schema.

Every program is a list of objects such as {"cmd": "forward", "value": 100}.
The LLM is only allowed to produce these commands; anything else is rejected.
"""

# name: (needs_value, min, max, unit, Greek label, English label)
COMMANDS = {
    "takeoff": (False, None, None, None, "Απογείωση", "Take off"),
    "land":    (False, None, None, None, "Προσγείωση", "Land"),
    "up":      (True, 20, 500, "cm", "Άνοδος", "Up"),
    "down":    (True, 20, 500, "cm", "Κάθοδος", "Down"),
    "forward": (True, 20, 500, "cm", "Μπροστά", "Forward"),
    "back":    (True, 20, 500, "cm", "Πίσω", "Back"),
    "left":    (True, 20, 500, "cm", "Αριστερά", "Left"),
    "right":   (True, 20, 500, "cm", "Δεξιά", "Right"),
    "cw":      (True, 1, 360, "deg", "Περιστροφή δεξιά", "Rotate right"),
    "ccw":     (True, 1, 360, "deg", "Περιστροφή αριστερά", "Rotate left"),
    "hover":   (True, 1, 10, "s", "Αιώρηση", "Hover"),
    "photo":   (False, None, None, None, "Φωτογραφία", "Take photo"),
}

MOVE_CMDS = {"up", "down", "forward", "back", "left", "right"}


def describe(cmd: dict, lang: str = "el") -> str:
    spec = COMMANDS[cmd["cmd"]]
    label = spec[4] if lang == "el" else spec[5]
    if spec[0]:
        unit = {"cm": "εκ." if lang == "el" else "cm", "deg": "°", "s": "δ." if lang == "el" else "s"}[spec[3]]
        return f"{label} {cmd['value']} {unit}"
    return label


SYSTEM_PROMPT = """You translate a student's instruction for a small educational drone (Ryze Tello) into a program.
The student may write in Greek or English. Reply with ONLY a JSON object, no prose, in this exact form:
{"program": [{"cmd": "...", "value": ...}, ...], "explanation": "one short sentence in the student's language"}

Allowed commands (value is an integer):
- takeoff (no value)
- land (no value)
- up, down, forward, back, left, right: value in centimetres, 20 to 500
- cw (rotate clockwise / right), ccw (rotate counter-clockwise / left): value in degrees, 1 to 360
- hover: value in seconds, 1 to 10
- photo (no value)

Rules:
- 1 metre = 100 cm. Convert all distances to centimetres.
- The program must start with takeoff and end with land unless the student clearly asks otherwise.
- For shapes, expand them into explicit moves and turns (e.g. a square of side 1 m = forward 100, cw 90, repeated 4 times).
- Never invent commands outside the list. If the instruction is unsafe, impossible or unclear,
  return {"program": [], "explanation": "<short question asking the student to clarify, in their language>"}.
- Do not include any personal data. Do not add comments inside the JSON.
"""


LIMITS_ADDENDUM = """
Classroom safety limits (they are stricter than the drone's own limits):
- After take-off the drone hovers at about 80 cm above the floor.
- It must never be higher than 200 cm above the floor.
- While flying it must never be lower than 20 cm above the floor.
- It must never be further than 300 cm horizontally from the take-off point.
- A program may contain at most 20 commands, including takeoff and land.
Check the whole program against these limits, adding up all movements. If the instruction would break any limit,
return {"program": [], "explanation": "<short explanation of the limit and a question, in the student's language>"}.
"""


def system_prompt(variant="default"):
    """'default' is the prompt used by the application; 'limits' adds the classroom limits (ablation)."""
    return SYSTEM_PROMPT + (LIMITS_ADDENDUM if variant == "limits" else "")
