"""Drone backends. SimDrone needs no hardware; TelloDrone uses djitellopy (pip install djitellopy).

Programs run in a background thread so the UI can poll progress and press emergency land at any time.
"""
import threading
import time

from .safety import simulate


class BaseDrone:
    mode = "base"

    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        self.running = False
        self.step = -1
        self.program = []
        self.last_error = None
        self._stop = threading.Event()
        self._thread = None

    # --- to override
    def connect(self): ...
    def battery(self): return None
    def height(self): return None
    def send(self, cmd): ...
    def emergency_land(self): ...

    def status(self):
        return {"mode": self.mode, "battery": self.battery(), "height": self.height(),
                "running": self.running, "step": self.step, "total": len(self.program),
                "error": self.last_error}

    def execute(self, program):
        if self.running:
            raise RuntimeError("Εκτελείται ήδη πρόγραμμα.")
        bat = self.battery()
        min_bat = self.cfg["safety"]["min_battery_percent"]
        if bat is not None and bat < min_bat:
            raise RuntimeError(f"Χαμηλή μπαταρία ({bat}%). Χρειάζεται τουλάχιστον {min_bat}%.")
        self.program, self.step, self.last_error = program, -1, None
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        pause = self.cfg["tello"]["command_pause_s"]
        self.logger.log("execute_start", program=self.program, mode=self.mode, battery=self.battery())
        try:
            for i, c in enumerate(self.program):
                if self._stop.is_set():
                    break
                self.step = i
                t0 = time.time()
                self.send(c)
                self.logger.log("command_done", index=i, command=c, duration_ms=int((time.time() - t0) * 1000),
                                battery=self.battery(), height=self.height())
                time.sleep(pause)
            self.logger.log("execute_end", completed=not self._stop.is_set(), steps=self.step + 1)
        except Exception as e:   # any SDK error: try to land
            self.last_error = str(e)
            self.logger.log("execute_error", error=str(e), index=self.step)
            try:
                self.emergency_land()
            except Exception:
                pass
        finally:
            self.running = False

    def stop_and_land(self):
        self._stop.set()
        self.logger.log("emergency", step=self.step)
        self.emergency_land()
        self.running = False


class SimDrone(BaseDrone):
    mode = "sim"

    def connect(self):
        self._bat, self._z = 100, 0.0
        self.logger.log("connect", mode="sim")

    def battery(self): return self._bat
    def height(self): return int(self._z)

    def send(self, c):
        name, v = c["cmd"], c.get("value", 0)
        speed = self.cfg["tello"]["speed_cm_s"]
        if name == "takeoff":
            dur, self._z = 2.0, 80.0
        elif name == "land":
            dur, self._z = 2.0, 0.0
        elif name in ("up", "down", "forward", "back", "left", "right"):
            dur = v / speed
            if name == "up": self._z += v
            if name == "down": self._z -= v
        elif name in ("cw", "ccw"):
            dur = v / 90
        elif name == "hover":
            dur = v
        else:
            dur = 0.5
        end = time.time() + min(dur, 10) * self.cfg.get("sim_speedup", 1.0)
        while time.time() < end and not self._stop.is_set():
            time.sleep(0.05)
        self._bat = max(0, self._bat - 1)

    def emergency_land(self):
        self._z = 0.0


class TelloDrone(BaseDrone):
    mode = "tello"

    def connect(self):
        from djitellopy import Tello   # imported lazily so sim mode needs no extra package
        self.t = Tello()
        self.t.connect()
        self.t.set_speed(self.cfg["tello"]["speed_cm_s"])
        self.logger.log("connect", mode="tello", battery=self.t.get_battery())
        self._photo_n = 0

    def battery(self):
        try: return self.t.get_battery()
        except Exception: return None

    def height(self):
        try: return self.t.get_height()
        except Exception: return None

    def send(self, c):
        name, v = c["cmd"], c.get("value")
        t = self.t
        if name == "takeoff": t.takeoff()
        elif name == "land": t.land()
        elif name == "up": t.move_up(v)
        elif name == "down": t.move_down(v)
        elif name == "forward": t.move_forward(v)
        elif name == "back": t.move_back(v)
        elif name == "left": t.move_left(v)
        elif name == "right": t.move_right(v)
        elif name == "cw": t.rotate_clockwise(v)
        elif name == "ccw": t.rotate_counter_clockwise(v)
        elif name == "hover":
            end = time.time() + v
            while time.time() < end and not self._stop.is_set():
                time.sleep(0.1)
        elif name == "photo":
            self._take_photo()

    def _take_photo(self):
        import os
        try:
            import cv2
            t = self.t
            t.streamon()
            time.sleep(1.5)
            frame = t.get_frame_read().frame
            os.makedirs("photos", exist_ok=True)
            self._photo_n += 1
            fn = f"photos/{self.logger.session_id}_{self._photo_n}.jpg"
            cv2.imwrite(fn, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            t.streamoff()
            self.logger.log("photo", file=fn)
        except Exception as e:
            self.logger.log("photo_failed", error=str(e))

    def emergency_land(self):
        try:
            self.t.land()
        except Exception:
            self.t.emergency()   # cuts motors: last resort


def make_drone(cfg, logger):
    d = TelloDrone(cfg, logger) if cfg.get("mode") == "tello" else SimDrone(cfg, logger)
    d.connect()
    return d


__all__ = ["make_drone", "SimDrone", "TelloDrone", "simulate"]
