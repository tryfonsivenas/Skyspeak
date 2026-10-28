"""Research logging: one JSONL file per session, one event per line."""
import json
import os
import threading
import uuid
from datetime import datetime, timezone


class SessionLogger:
    def __init__(self, directory):
        os.makedirs(directory, exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        self.path = os.path.join(directory, f"session_{self.session_id}.jsonl")
        self._lock = threading.Lock()
        self.log("session_start")

    def log(self, event, **data):
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "session": self.session_id, "event": event, **data}
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec
