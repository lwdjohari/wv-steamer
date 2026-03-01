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

    # --------------------------
    # Public API
    # --------------------------

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
        # Phase 1: safe no-op semantics in worker.
        self._ensure_worker()
        self._send(make_msg("unload_models", None, {}))

    def restart_worker(self, force: bool = True) -> bool:
        """
        Returns True if an active job was interrupted.
        """
        with self._lock:
            interrupted = self._active_job_id is not None

        if interrupted and force:
            try:
                self.cancel_job(job_id=self._active_job_id)
            except Exception:
                pass
            self._hard_kill_worker()

        else:
            self._hard_kill_worker()

        # Lazily respawn on demand, but we can spawn now to validate.
        self._ensure_worker()
        return interrupted

    def cancel_job(self, job_id: str | None) -> bool:
        with self._lock:
            if self._active_job_id is None:
                return False
            if job_id is not None and job_id != self._active_job_id:
                # Cancel only current job; ignore mismatched job_id
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
            return self._wait_for_completion(job_id, on_progress=on_progress, interruption_check=interruption_check)
        finally:
            with self._lock:
                if self._active_job_id == job_id:
                    # If we are exiting due to exception/cancel, clear state.
                    self._active_job_id = None
                    if self._state != WorkerState.DEAD:
                        self._state = WorkerState.IDLE

    # --------------------------
    # Internal
    # --------------------------

    def _ensure_worker(self):
        with self._lock:
            alive = self._proc is not None and self._proc.is_alive()
            if alive:
                return

            # Fresh spawn
            self._req_q = self._ctx.Queue()
            self._evt_q = self._ctx.Queue(maxsize=_EVENT_QUEUE_MAX)

            from .worker_process import worker_main
            self._proc = self._ctx.Process(
                target=worker_main,
                args=(self._req_q, self._evt_q),
                daemon=False,  # must not daemonize
            )
            self._proc.start()

            self._state = WorkerState.IDLE
            self._active_job_id = None
            self._last_error = None
            self._last_heartbeat_ts = time.time()

    def _send(self, msg: dict):
        validate_msg(msg, "main->worker")
        with self._lock:
            if self._req_q is None:
                raise WorkerError(ErrorCode.INTERNAL, "Worker request queue not initialized")
            q = self._req_q
        q.put(msg)

    def _wait_for_completion(self, job_id: str, on_progress=None, interruption_check=None) -> str:
        deadline_hb = time.time() + _HEARTBEAT_STALE_SEC

        while True:
            # If user pressed Stop, this should raise.
            if interruption_check is not None:
                try:
                    interruption_check()
                except Exception:
                    # Attempt graceful cancel, then hard-kill fallback.
                    try:
                        self.cancel_job(job_id)
                        self._wait_cancel_grace(job_id)
                    finally:
                        # Even if graceful worked, ensure we’re not hung
                        # If still active, hard-kill.
                        with self._lock:
                            still_active = (self._active_job_id == job_id)
                        if still_active:
                            self._hard_kill_worker()
                    raise

            # Worker liveness
            with self._lock:
                alive = self._proc is not None and self._proc.is_alive()
                last_hb = self._last_heartbeat_ts
            if not alive:
                with self._lock:
                    self._state = WorkerState.DEAD
                raise WorkerError(ErrorCode.INTERNAL, "Worker died unexpectedly", {"job_id": job_id})

            # Heartbeat stale detection while RUNNING
            if time.time() - last_hb > _HEARTBEAT_STALE_SEC:
                # Hung
                try:
                    self.cancel_job(job_id)
                    self._wait_cancel_grace(job_id)
                finally:
                    self._hard_kill_worker()
                raise WorkerError(ErrorCode.HUNG, "Worker heartbeat stale (hung). Worker terminated.", {"job_id": job_id})

            # Read one event with short timeout
            evt = None
            try:
                with self._lock:
                    eq = self._evt_q
                evt = eq.get(timeout=0.25)
            except queue.Empty:
                continue

            try:
                evt = validate_msg(evt, "worker->main")
            except Exception as e:
                # Protocol violation; treat as internal error
                with self._lock:
                    self._last_error = {"code": ErrorCode.INTERNAL, "message": f"Bad worker event: {e}"}
                continue

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
                if on_progress is not None:
                    on_progress(p, msg)
                continue

            if et == "error":
                code = payload.get("code", ErrorCode.INTERNAL)
                message = payload.get("message", "Worker error")
                detail = payload.get("detail", {})
                with self._lock:
                    self._last_error = {"code": code, "message": message, "detail": detail}
                    # Clear active job on terminal error
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                        self._state = WorkerState.IDLE
                raise WorkerError(code, message, detail)

            if et == "result":
                # Terminal success
                with self._lock:
                    if self._active_job_id == job_id:
                        self._active_job_id = None
                        self._state = WorkerState.IDLE
                return job_id

            # Unknown event: ignore
            continue

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
        # Best effort: ask worker to shutdown, then terminate.
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


def get_worker_manager() -> WorkerManager:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = WorkerManager()
        return _singleton