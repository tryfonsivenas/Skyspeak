"""Real-flight trials with the Tello: how far does the real landing point fall from the planned one?

Four fixed programs, five rounds, interleaved (P1 P2 P3 P4, P1 P2 P3 P4, ...), so that
battery level is spread evenly across programs. After each landing you measure the landing
point with a tape and type two numbers. Everything is saved to eval/flight_results/.

    python -m eval.flight_trial                 (real Tello, config mode is ignored)
    python -m eval.flight_trial --sim           (dry run without a drone, to learn the steps)
    python -m eval.flight_trial --summary       (only print the summary of saved flights)

Ctrl+C during a flight = emergency landing.
"""
import argparse
import csv
import datetime as dt
import glob
import math
import os
import statistics
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.drone import SimDrone, TelloDrone      # noqa: E402
from app.logger import SessionLogger            # noqa: E402
from app.safety import validate, simulate       # noqa: E402

OUT = os.path.join(ROOT, "eval", "flight_results")

PROGRAMS = {
    "P1": ("Ευθεία 150 εκ.", [{"cmd": "takeoff"}, {"cmd": "forward", "value": 150}, {"cmd": "land"}]),
    "P2": ("Τετράγωνο 100 εκ. (δεξιές στροφές)", [{"cmd": "takeoff"}] +
           [c for _ in range(4) for c in ({"cmd": "forward", "value": 100}, {"cmd": "cw", "value": 90})] +
           [{"cmd": "land"}]),
    "P3": ("Τρίγωνο 100 εκ. (δεξιές στροφές)", [{"cmd": "takeoff"}] +
           [c for _ in range(3) for c in ({"cmd": "forward", "value": 100}, {"cmd": "cw", "value": 120})] +
           [{"cmd": "land"}]),
    "P4": ("Σχήμα Γ: 100 μπροστά, 100 δεξιά", [{"cmd": "takeoff"}, {"cmd": "forward", "value": 100},
                                               {"cmd": "right", "value": 100}, {"cmd": "land"}]),
}


def planned_end(prog):
    p = simulate(prog)[-1]
    return round(p["x"]), round(p["y"])


def ask_float(msg):
    while True:
        s = input(msg).strip().replace(",", ".")
        if s.lower() in ("x", "skip"):
            return None
        try:
            return float(s)
        except ValueError:
            print("  Γράψε έναν αριθμό (π.χ. 12 ή -8), ή x αν δεν μετρήθηκε.")


def summary():
    files = sorted(glob.glob(os.path.join(OUT, "flights_*.csv")))
    rows = [r for f in files for r in csv.DictReader(open(f, encoding="utf-8"))]
    rows = [r for r in rows if r["error_cm"] not in ("", "None") and r["completed"] == "True"]
    if not rows:
        print("Δεν υπάρχουν ολοκληρωμένες πτήσεις με μέτρηση.")
        return
    print(f"\n{'prog':<5}{'n':>3}{'mean err cm':>13}{'SD':>7}{'max':>7}{'mean dx':>9}{'mean dy':>9}")
    allerr = []
    for k in PROGRAMS:
        rr = [r for r in rows if r["program"] == k]
        if not rr:
            continue
        e = [float(r["error_cm"]) for r in rr]
        dx = [float(r["dx_cm"]) for r in rr]
        dy = [float(r["dy_cm"]) for r in rr]
        allerr += e
        sd = statistics.stdev(e) if len(e) > 1 else 0
        print(f"{k:<5}{len(e):>3}{statistics.mean(e):>13.1f}{sd:>7.1f}{max(e):>7.1f}{statistics.mean(dx):>9.1f}{statistics.mean(dy):>9.1f}")
    print(f"\nΣύνολο: {len(allerr)} πτήσεις, μέσο σφάλμα {statistics.mean(allerr):.1f} εκ., μέγιστο {max(allerr):.1f} εκ.")
    print("Μέσα στον γεωφράχτη των 300 εκ. όλες:" , all(math.hypot(float(r['land_x_cm']), float(r['land_y_cm'])) <= 300 for r in rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--floor", default="", help="π.χ. 'πλακάκι ματ', 'ξύλο γυαλιστερό'")
    ap.add_argument("--light", default="", help="π.χ. 'φυσικό φως', 'λάμπες φθορισμού'")
    ap.add_argument("--wind", default="", help="π.χ. 'άπνοια', '1-2 Beaufort' (για εξωτερικό χώρο)")
    a = ap.parse_args()
    if a.summary:
        summary(); return

    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    for k, (_, prog) in PROGRAMS.items():
        assert validate(list(prog), cfg).ok, k
    os.makedirs(OUT, exist_ok=True)
    logger = SessionLogger(os.path.join(OUT, "logs"))
    drone = SimDrone(cfg, logger) if a.sim else TelloDrone(cfg, logger)
    print("Σύνδεση με το drone..." if not a.sim else "Προσομοίωση (χωρίς drone).")
    drone.connect()
    floor = a.floor or input("Τι δάπεδο; (π.χ. πλακάκι ματ): ").strip()
    light = a.light or input("Τι φωτισμός; (π.χ. φυσικό φως): ").strip()
    wind = a.wind or input("Άνεμος; (π.χ. άπνοια, 1-2 Beaufort, ή Enter αν είσαι σε εσωτερικό χώρο): ").strip()

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    path = os.path.join(OUT, f"flights_{stamp}.csv")
    cols = ["trial", "round", "program", "description", "planned_x_cm", "planned_y_cm", "dx_cm", "dy_cm",
            "land_x_cm", "land_y_cm", "error_cm", "completed", "duration_s", "battery_start", "battery_end",
            "floor", "light", "wind", "mode", "note"]
    f = open(path, "w", newline="", encoding="utf-8")
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader()

    trial = 0
    for rnd in range(1, a.rounds + 1):
        for key, (desc, prog) in PROGRAMS.items():
            trial += 1
            px, py = planned_end(prog)
            bat = drone.battery()
            while bat is not None and bat < 35 and not a.sim:
                input(f"\nΜπαταρία {bat}%. Άλλαξε μπαταρία, ξανασύνδεσε το Wi-Fi και πάτα Enter.")
                drone.connect(); bat = drone.battery()
            print(f"\n=== Πτήση {trial}/{a.rounds * len(PROGRAMS)}  ({key}: {desc})  μπαταρία {bat}% ===")
            print("Βάλε το drone πάνω στον σταυρό, με τη μύτη προς τη γραμμή «μπροστά».")
            if input("Enter για απογείωση, ή s + Enter για παράλειψη: ").strip().lower() == "s":
                continue
            t0 = time.time()
            completed = True
            try:
                drone.execute(prog)
                while drone.running:
                    time.sleep(0.2)
                if drone.last_error:
                    completed = False
                    print("Σφάλμα:", drone.last_error)
            except KeyboardInterrupt:
                print("\nΕΚΤΑΚΤΗ ΠΡΟΣΓΕΙΩΣΗ")
                drone.stop_and_land()
                completed = False
            dur = round(time.time() - t0, 1)
            print(f"Σχεδιασμένο σημείο προσγείωσης: {px:.0f} εκ. μπροστά, {py:.0f} εκ. αριστερά (αρνητικό = δεξιά).")
            print("Μέτρησε από τον σταυρό ως το κέντρο του drone:")
            lx = ask_float("  Πόσα εκ. ΜΠΡΟΣΤΑ από τον σταυρό; (πίσω = αρνητικό): ")
            ly = ask_float("  Πόσα εκ. ΑΡΙΣΤΕΡΑ από τον σταυρό; (δεξιά = αρνητικό): ")
            note = input("  Σημείωση (Enter αν τίποτα): ").strip()
            if lx is not None and ly is not None:
                dx, dy = lx - px, ly - py
                err = round(math.hypot(dx, dy), 1)
                print(f"  Απόκλιση: {err} εκ.")
            else:
                dx = dy = err = None
            w.writerow({"trial": trial, "round": rnd, "program": key, "description": desc,
                        "planned_x_cm": px, "planned_y_cm": py, "dx_cm": dx, "dy_cm": dy,
                        "land_x_cm": lx, "land_y_cm": ly, "error_cm": err, "completed": completed,
                        "duration_s": dur, "battery_start": bat, "battery_end": drone.battery(),
                        "floor": floor, "light": light, "wind": wind, "mode": "sim" if a.sim else "tello", "note": note})
            f.flush()
    f.close()
    print(f"\nΑποθηκεύτηκε: {path}")
    summary()


if __name__ == "__main__":
    main()
