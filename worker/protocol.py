import time
import uuid

from .errors import WorkerError, ErrorCode


PROTOCOL_VERSION = 1


def now_ts() -> float:
    return time.time()


def new_job_id() -> str:
    return uuid.uuid4().hex


def make_msg(msg_type: str, job_id: str | None, payload: dict | None = None) -> dict:
    return {
        "v": PROTOCOL_VERSION,
        "type": msg_type,
        "ts": now_ts(),
        "job_id": job_id,
        "payload": payload or {},
    }


def validate_msg(m: dict, direction: str) -> dict:
    # direction: "main->worker" or "worker->main" (for diagnostics only)
    if not isinstance(m, dict):
        raise WorkerError(ErrorCode.INTERNAL, f"Invalid message type ({direction}): not a dict", {"m": repr(m)})

    v = m.get("v")
    t = m.get("type")
    if v != PROTOCOL_VERSION:
        raise WorkerError(ErrorCode.INTERNAL, f"Protocol version mismatch ({direction})", {"got": v, "want": PROTOCOL_VERSION})
    if not isinstance(t, str) or not t:
        raise WorkerError(ErrorCode.INTERNAL, f"Missing/invalid type ({direction})", {"m": m})

    # job_id may be None for some control/status messages
    if "job_id" not in m:
        raise WorkerError(ErrorCode.INTERNAL, f"Missing job_id ({direction})", {"m": m})

    payload = m.get("payload")
    if payload is None:
        m["payload"] = {}
    elif not isinstance(payload, dict):
        raise WorkerError(ErrorCode.INTERNAL, f"Invalid payload ({direction})", {"m": m})

    return m