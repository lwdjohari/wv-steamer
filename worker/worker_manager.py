import os
import queue
import threading
import time
import multiprocessing as mp

from .protocol import make_msg, validate_msg, new_job_id
from .errors import WorkerError, ErrorCode
from .state import WorkerState

_EVENT_QUEUE_MAX = int(os.getenv("STEM_EVENT_QUEUE_MAX", "512"))
_HEARTBEAT_STALE_SEC = float(os.getenv("STEM_HEARTBEAT_STALE_SEC", "10"))
_CANCEL_GRACE_SEC = float(os.getenv("STEM_CANCEL_GRACE_SEC", "5"))
_SPAWN_TIMEOUT_SEC = float(os.getenv("STEM_SPAWN_TIMEOUT_SEC", "10"))


class WorkerManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._ctx = mp.get_context("spawn")

        self._proc = None
        self._req_q = None
        self._evt_q = None

        self._state = WorkerState.DEAD
        self._active_job_id = None
        self._last_error = None
        self._last_heartbeat_ts = 0.0

        import atexit
        atexit.register(self._atexit_cleanup)

    # ---------- public ----------
    def status_snapshot(self) -> dict:
        with self._lock:
            alive = self._proc is not None and self._proc.is_alive()
            return {
                "alive": bool(alive),
                "state": self._state,
                "active_job_id": self._active_job_id,
                "last_error": self._last_error,
                "last_heartbeat_ts": self._last_heartbeat_ts,
            }

    def unload_models(self) -> None:
        self._ensure_worker()
        self._send(make_msg("unload_models", None, {}))

    def restart_worker(self, force: bool = True) -> bool:
        interrupted = False
        with self._lock:
            interrupted = self._active_job_id is not None

        if interrupted and force:
            try:
                self.cancel_job(self._active_job_id)
                self._wait_cancel_grace(self._active_job_id)
            except Exception:
                pass

        self._hard_kill_worker()
        self._ensure_worker()
        return interrupted

    def cancel_job(self, job_id: str | None) -> bool:
        with self._lock:
            if self._active_job_id is None:
                return False
            if job_id is not None and job_id != self._active_job_id:
                return False
            jid = self._active_job_id

        self._send(make_msg("cancel_job", jid, {"job_id": jid}))
        return True

    def run_dummy_job_blocking(self, duration_sec: int, on_progress=None, interruption_check=None) -> str:
        self._ensure_worker()

        job_id = new_job_id()
        with self._lock:
            if self._active_job_id is not None:
                raise WorkerError(ErrorCode.INTERNAL, "Worker already has active job", {"active_job_id": self._active_job_id})
            self._active_job_id = job_id
            self._state = WorkerState.RUNNING
            self._last_error = None
            self._last_heartbeat_ts = time.time()

        self._send(make_msg("run_job", job_id, {"duration_sec": int(duration_sec)}))

        try:
            self._wait_for_completion(job_id, on_progress=on_progress, interruption_check=interruption_check)
            return job_id
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
                    if self._state != WorkerState.DEAD:
                        self._state = WorkerState.IDLE

    # ---------- internal ----------
    def _ensure_worker(self):
        with self._lock:
            alive = self._proc is not None and self._proc.is_alive()
            if alive:
                return

            self._req_q = self._ctx.Queue()
            self._evt_q = self._ctx.Queue(maxsize=_EVENT_QUEUE_MAX)

            from .worker_process import worker_main

            self._proc = self._ctx.Process(
                target=worker_main,
                args=(self._req_q, self._evt_q),
                daemon=False,
            )
            self._proc.start()

            self._state = WorkerState.IDLE
            self._active_job_id = None
            self._last_error = None
            self._last_heartbeat_ts = time.time()

        # Wait for a first status or just confirm alive quickly
        t0 = time.time()
        while time.time() - t0 < _SPAWN_TIMEOUT_SEC:
            if self._proc is not None and self._proc.is_alive():
                return
            time.sleep(0.05)
        raise WorkerError(ErrorCode.TIMEOUT, "Worker spawn timeout")

    def _send(self, msg: dict):
        validate_msg(msg, "main->worker")
        with self._lock:
            if self._req_q is None:
                raise WorkerError(ErrorCode.INTERNAL, "Worker request queue not initialized")
            q = self._req_q
        q.put(msg)

    def _wait_for_completion(self, job_id: str, on_progress=None, interruption_check=None):
        while True:
            # Stop button handling
            if interruption_check is not None:
                try:
                    interruption_check()
                except Exception:
                    # Graceful cancel then hard kill if still active
                    try:
                        self.cancel_job(job_id)
                        self._wait_cancel_grace(job_id)
                    finally:
                        with self._lock:
                            still_active = (self._active_job_id == job_id)
                        if still_active:
                            self._hard_kill_worker()
                    raise

            # Worker alive?
            with self._lock:
                proc = self._proc
                last_hb = self._last_heartbeat_ts
            if proc is None or not proc.is_alive():
                with self._lock:
                    self._state = WorkerState.DEAD
                raise WorkerError(ErrorCode.INTERNAL, "Worker died unexpectedly", {"job_id": job_id})

            # Heartbeat stale => hung
            if time.time() - last_hb > _HEARTBEAT_STALE_SEC:
                try:
                    self.cancel_job(job_id)
                    self._wait_cancel_grace(job_id)
                finally:
                    self._hard_kill_worker()
                raise WorkerError(ErrorCode.HUNG, "Worker heartbeat stale (hung). Worker terminated.", {"job_id": job_id})

            # Receive one event
            try:
                with self._lock:
                    eq = self._evt_q
                evt = eq.get(timeout=0.25)
            except queue.Empty:
                continue

            evt = validate_msg(evt, "worker->main")
            et = evt["type"]
            payload = evt.get("payload") or {}

            if et == "heartbeat":
                with self._lock:
                    self._last_heartbeat_ts = time.time()
                continue

            if et == "status":
                with self._lock:
                    self._state = payload.get("state", self._state)
                    self._active_job_id = payload.get("active_job_id", self._active_job_id)
                    self._last_error = payload.get("last_error", self._last_error)
                continue

            if et == "log":
                level = payload.get("level", "info")
                msg = payload.get("msg", "")
                print(f"[StemWorker][{job_id}][{level.upper()}] {msg}")
                continue

            if et == "progress":
                p = float(payload.get("p", 0.0))
                msg = str(payload.get("msg", ""))
                if on_progress:
                    on_progress(p, msg)
                continue

            if et == "error":
                code = payload.get("code", ErrorCode.INTERNAL)
                message = payload.get("message", "Worker error")
                detail = payload.get("detail", {})
                with self._lock:
                    self._last_error = {"code": code, "message": message, "detail": detail}
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                        self._state = WorkerState.IDLE
                raise WorkerError(code, message, detail)

            if et == "result":
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                        self._state = WorkerState.IDLE
                return

            # ignore unknown

    def _wait_cancel_grace(self, job_id: str):
        t0 = time.time()
        while time.time() - t0 < _CANCEL_GRACE_SEC:
            with self._lock:
                if self._active_job_id != job_id:
                    return
            time.sleep(0.1)

    def _hard_kill_worker(self):
        with self._lock:
            proc = self._proc
            self._proc = None
            self._req_q = None
            self._evt_q = None
            self._state = WorkerState.DEAD
            self._active_job_id = None

        if proc is None:
            return
        try:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=2.0)
        except Exception:
            pass

    def _atexit_cleanup(self):
        try:
            with self._lock:
                rq = self._req_q
                proc = self._proc
            if rq is not None:
                rq.put(make_msg("shutdown", None, {}))
            if proc is not None and proc.is_alive():
                proc.join(timeout=0.5)
        except Exception:
            pass
        finally:
            self._hard_kill_worker()


_singleton = None
_singleton_lock = threading.Lock()

def run_demucs_vocal_split_blocking(
    self,
    input_path: str,
    output_dir: str,
    model: str,
    precision: str,
    shifts: int,
    segment_sec: int,
    overlap: float,
    on_progress=None,
    interruption_check=None,
):
    self._ensure_worker()

    job_id = new_job_id()
    with self._lock:
        if self._active_job_id is not None:
            raise WorkerError(ErrorCode.INTERNAL, "Worker already has active job", {"active_job_id": self._active_job_id})
        self._active_job_id = job_id
        self._state = WorkerState.RUNNING
        self._last_error = None
        self._last_heartbeat_ts = time.time()

    payload = {
        "backend": "demucs",
        "mode": "vocal_split",
        "input_path": input_path,
        "output_dir": output_dir,
        "model": model,
        "precision": precision,
        "shifts": int(shifts),
        "segment_sec": int(segment_sec),
        "overlap": float(overlap),
    }
    self._send(make_msg("run_job", job_id, payload))

    try:
        result_payload = self._wait_for_completion_with_result(
            job_id,
            on_progress=on_progress,
            interruption_check=interruption_check,
        )
        return job_id, result_payload
    finally:
        with self._lock:
            if self._active_job_id == job_id:
                self._active_job_id = None
                if self._state != WorkerState.DEAD:
                    self._state = WorkerState.IDLE

def _wait_for_completion_with_result(self, job_id: str, on_progress=None, interruption_check=None) -> dict:
    while True:
        if interruption_check is not None:
            try:
                interruption_check()
            except Exception:
                try:
                    self.cancel_job(job_id)
                    self._wait_cancel_grace(job_id)
                finally:
                    with self._lock:
                        still_active = (self._active_job_id == job_id)
                    if still_active:
                        self._hard_kill_worker()
                raise

        with self._lock:
            proc = self._proc
            last_hb = self._last_heartbeat_ts
        if proc is None or not proc.is_alive():
            with self._lock:
                self._state = WorkerState.DEAD
            raise WorkerError(ErrorCode.INTERNAL, "Worker died unexpectedly", {"job_id": job_id})

        if time.time() - last_hb > _HEARTBEAT_STALE_SEC:
            try:
                self.cancel_job(job_id)
                self._wait_cancel_grace(job_id)
            finally:
                self._hard_kill_worker()
            raise WorkerError(ErrorCode.HUNG, "Worker heartbeat stale (hung). Worker terminated.", {"job_id": job_id})

        try:
            with self._lock:
                eq = self._evt_q
            evt = eq.get(timeout=0.25)
        except queue.Empty:
            continue

        evt = validate_msg(evt, "worker->main")
        et = evt["type"]
        payload = evt.get("payload") or {}

        if et == "heartbeat":
            with self._lock:
                self._last_heartbeat_ts = time.time()
            continue

        if et == "status":
            with self._lock:
                self._state = payload.get("state", self._state)
                self._active_job_id = payload.get("active_job_id", self._active_job_id)
                self._last_error = payload.get("last_error", self._last_error)
            continue

        if et == "log":
            level = payload.get("level", "info")
            msg = payload.get("msg", "")
            print(f"[StemWorker][{job_id}][{level.upper()}] {msg}")
            continue

        if et == "progress":
            if on_progress:
                on_progress(float(payload.get("p", 0.0)), str(payload.get("msg", "")))
            continue

        if et == "error":
            code = payload.get("code", ErrorCode.INTERNAL)
            message = payload.get("message", "Worker error")
            detail = payload.get("detail", {})
            with self._lock:
                self._last_error = {"code": code, "message": message, "detail": detail}
                if self._active_job_id == job_id:
                    self._active_job_id = None
                    self._state = WorkerState.IDLE
            raise WorkerError(code, message, detail)

        if et == "result":
            with self._lock:
                if self._active_job_id == job_id:
                    self._active_job_id = None
                    self._state = WorkerState.IDLE
            return payload
        
def get_worker_manager() -> WorkerManager:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = WorkerManager()
        return _singleton