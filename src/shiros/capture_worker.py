"""Poll only explicitly granted files. Revoked grants are checked again on every write."""

import threading
from uuid import UUID

from sqlalchemy import Engine, text

from shiros.capture import CaptureService, safe_path
from shiros.capture_adapters import normalize
from shiros.file_library import MAX_FILE_BYTES


class CaptureWorker:
    def __init__(self, engine: Engine, scope: UUID):
        self.engine, self.scope = engine, scope
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="shiros-capture")
        self.seen = {}

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(5)

    def loop(self):
        while not self.stop.wait(2):
            try:
                self.poll()
            except Exception:
                pass  # No transcript or credentials in process logs.

    def poll(self):
        with self.engine.connect() as c:
            grants = (
                c.execute(
                    text(
                        "SELECT * FROM capture_grants WHERE scope_id=:s AND "
                        "enabled AND (source_path IS NOT NULL OR native_endpoint "
                        "IS NOT NULL)"
                    ),
                    {"s": self.scope},
                )
                .mappings()
                .all()
            )
        for grant in grants:
            try:
                if grant["native_endpoint"]:
                    from shiros.capture_native import collect_native

                    packet = collect_native(dict(grant))
                    CaptureService(self.engine, grant["actor_id"], self.scope).ingest(
                        grant["id"], packet
                    )
                    continue
                path = safe_path(grant["source_path"])
                stamp = (path.stat().st_mtime_ns, path.stat().st_size)
                if self.seen.get(grant["id"]) == stamp:
                    continue
                if stamp[1] > MAX_FILE_BYTES:
                    raise ValueError("capture.source_too_large")
                packet = normalize(
                    grant["provider"],
                    grant["conversation_id"],
                    path.read_bytes(),
                    include_files=grant["include_files"],
                    attachment_root=grant["attachment_root"],
                )
                service = CaptureService(self.engine, grant["actor_id"], self.scope)
                for offset in range(0, len(packet.messages), 100):
                    if self.stop.is_set():
                        break
                    service.ingest(
                        grant["id"],
                        packet.model_copy(
                            update={"messages": packet.messages[offset : offset + 100]}
                        ),
                    )
                self.seen[grant["id"]] = stamp
            except Exception as error:
                code = (
                    str(error)
                    if isinstance(error, (ValueError, PermissionError))
                    and str(error).startswith(("capture.", "privacy.", "files.", "memory."))
                    else "capture.source_unavailable"
                )
                with self.engine.begin() as c:
                    c.execute(
                        text("UPDATE capture_grants SET last_error=:code WHERE id=:id AND enabled"),
                        {"code": code[:100], "id": grant["id"]},
                    )
