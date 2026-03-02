import queue
import time
import traceback

from .protocol import make_msg, validate_msg, PROTOCOL_VERSION
from .state import WorkerState
from .errors import ErrorCode


def _try_put(evt_q, msg) -> bool:
    try:
        evt_q.put_nowait(msg)
        return True
    except Exception:
        return False


def _emit_status(evt_q, state, active_job_id, cached_models, last_error):
    _try_put(evt_q, make_msg("status", active_job_id, {
        "state": state,
        "cached_models": list(cached_models),
        "active_job_id": active_job_id,
        "last_error": last_error,
    }))

def _demucs_vocal_split_impl(req_q,evt_q, payload, log, progress, heartbeat, cached_models, active_job_id, cancel_job_id, last_error):   
        backend = str(payload.get("backend", "dummy"))
        mode = str(payload.get("mode", "dummy"))

        if backend == "demucs" and mode == "vocal_split":
            try:
                from .demucs_impl import demucs_vocal_split
                res = demucs_vocal_split(
                    input_path=payload["input_path"],
                    output_dir=payload["output_dir"],
                    model_name=payload["model_name"],
                    template_name=payload.get("template_name", "balanced"),
                    allow_download=bool(payload.get("allow_download", False)),
                    device_mode=payload.get("device_mode", "auto"),
                    gpu_index=int(payload.get("gpu_index", 0)),
                    precision=payload.get("precision", "auto"),
                    demucs_knobs=dict(payload.get("demucs", {})),
                    log=log,
                    progress=progress,
                    heartbeat=heartbeat,
                    cancel_check=lambda: (cancel_job_id == active_job_id),
                )
                _try_put(evt_q, make_msg("result", active_job_id, res))
                log("info", "Demucs vocal_split complete")
            except Exception as e:
                tb = traceback.format_exc()
                _try_put(evt_q, make_msg("error", active_job_id, {
                    "code": getattr(e, "code", ErrorCode.INTERNAL),
                    "message": str(e),
                    "detail": getattr(e, "detail", {"trace": tb}),
                }))
            # Cleanup job state and continue loop
            active_job_id = None
            cancel_job_id = None
            state = WorkerState.IDLE
            _emit_status(evt_q, state, active_job_id, cached_models, last_error)
            

def worker_main(req_q, evt_q):
    """
    Subprocess worker loop.

    Phase 2 behavior:
    - Supports run_job 
    - demucs vocal split (vocals + instrumental) as first real job
    - cancel_job
    - unload_models 
    - shutdown
    - Emits status/progress/heartbeat/log/result/error
    """
    state = WorkerState.IDLE
    active_job_id = None
    cancel_job_id = None
    cached_models = []
    last_error = None

    def log(level: str, msg: str):
        _try_put(evt_q, make_msg("log", active_job_id, {"level": level, "msg": msg}))

    def progress(p: float, msg: str):
        _try_put(evt_q, make_msg("progress", active_job_id, {"p": float(p), "msg": str(msg)}))

    def heartbeat(p: float, msg: str):
        _try_put(evt_q, make_msg("heartbeat", active_job_id, {"state": state, "p": float(p), "msg": str(msg)}))

    log("info", f"Stem worker started (protocol v={PROTOCOL_VERSION})")
    _emit_status(evt_q, state, active_job_id, cached_models, last_error)

    while True:
        try:
            raw = req_q.get()
            m = validate_msg(raw, "main->worker")
        except Exception as e:
            last_error = {"code": ErrorCode.INTERNAL, "message": f"Bad request msg: {e}"}
            log("error", last_error["message"])
            _emit_status(evt_q, state, active_job_id, cached_models, last_error)
            continue

        t = m["type"]
        job_id = m.get("job_id")
        payload = m.get("payload") or {}

        if t == "shutdown":
            log("info", "Shutdown requested")
            _emit_status(evt_q, state, active_job_id, cached_models, last_error)
            return

        if t == "unload_models":
            # Phase 1: no real models yet, but keep semantics stable.
            log("info", "Unload models (Phase 1 no-op)")
            cached_models.clear()
            _emit_status(evt_q, state, active_job_id, cached_models, last_error)
            continue

        if t == "cancel_job":
            cancel_job_id = payload.get("job_id") or job_id
            log("warn", f"Cancel requested for job={cancel_job_id}")
            _emit_status(evt_q, state, active_job_id, cached_models, last_error)
            continue

        if t != "run_job":
            log("warn", f"Unknown request type: {t}")
            continue

        # -------- run_job --------

        if state != WorkerState.IDLE:
            log("error", f"Worker busy (state={state}); rejecting run_job")
            _try_put(evt_q, make_msg("error", job_id, {
                "code": ErrorCode.INTERNAL,
                "message": "Worker is busy",
                "detail": {"state": state},
            }))
            continue

        active_job_id = job_id
        cancel_job_id = None
        last_error = None

        backend = str(payload.get("backend", "dummy"))
        mode = str(payload.get("mode", "dummy"))

        if backend == "dummy":
            log("info", f"Received run_job request for dummy mode (job_id={job_id})")

            state = WorkerState.RUNNING
            _emit_status(evt_q, state, active_job_id, cached_models, last_error)

            duration_sec = int(payload.get("duration_sec", 5))
            duration_sec = max(1, min(duration_sec, 3600))

            log("info", f"Starting dummy job {active_job_id} duration={duration_sec}s")

            start = time.time()
            last_hb_ts = 0.0
            last_prog_ts = 0.0

            try:
                while True:
                    elapsed = time.time() - start
                    p = min(1.0, elapsed / float(duration_sec))

                    # Heartbeat 1 Hz
                    if time.time() - last_hb_ts >= 1.0:
                        last_hb_ts = time.time()
                        heartbeat(p, f"Dummy running {elapsed:.1f}/{duration_sec}s")

                    # Progress max 10/sec, but we keep it at 2/sec in Phase 1
                    if time.time() - last_prog_ts >= 0.5:
                        last_prog_ts = time.time()
                        progress(p, f"Dummy progress {elapsed:.1f}/{duration_sec}s")

                    # Check cancel
                    if cancel_job_id == active_job_id:
                        state = WorkerState.CANCELLING
                        _emit_status(evt_q, state, active_job_id, cached_models, last_error)
                        log("warn", f"Job cancelled: {active_job_id}")
                        _try_put(evt_q, make_msg("error", active_job_id, {
                            "code": ErrorCode.CANCELLED,
                            "message": "Job cancelled",
                            "detail": {},
                        }))
                        break

                    # Inline control handling (non-blocking)
                    try:
                        ctrl_raw = req_q.get_nowait()
                        ctrl = validate_msg(ctrl_raw, "main->worker")
                        ct = ctrl["type"]
                        cpl = ctrl.get("payload") or {}
                        if ct == "cancel_job":
                            cancel_job_id = cpl.get("job_id") or ctrl.get("job_id")
                            log("warn", f"Cancel requested (inline) for job={cancel_job_id}")
                        elif ct == "shutdown":
                            log("info", "Shutdown requested during job; cancelling and exiting")
                            _try_put(evt_q, make_msg("error", active_job_id, {
                                "code": ErrorCode.CANCELLED,
                                "message": "Server shutting down",
                                "detail": {},
                            }))
                            return
                        elif ct == "unload_models":
                            log("info", "Unload requested during job (Phase 1 ignored)")
                        else:
                            log("warn", f"Ignoring control msg during job: {ct}")
                    except queue.Empty:
                        pass

                    if elapsed >= duration_sec:
                        _try_put(evt_q, make_msg("result", active_job_id, {
                            "mode": "dummy",
                            "duration_sec": elapsed,
                        }))
                        log("info", f"Dummy job complete: {active_job_id}")
                        break

                    time.sleep(0.05)

            except Exception as e:
                tb = traceback.format_exc()
                last_error = {"code": ErrorCode.INTERNAL, "message": str(e), "trace": tb}
                log("error", f"Worker exception: {e}")
                _try_put(evt_q, make_msg("error", active_job_id, {
                    "code": ErrorCode.INTERNAL,
                    "message": "Worker exception",
                    "detail": {"exception": str(e), "trace": tb},
                }))

            # Cleanup job
            active_job_id = None
            cancel_job_id = None
            state = WorkerState.IDLE
            _emit_status(evt_q, state, active_job_id, cached_models, last_error)

        elif backend == "demucs" and mode == "vocal_split":
            log("info", f"Received run_job request for demucs vocal_split (job_id={job_id})")

            try:
                from .demucs_impl import demucs_vocal_split
            except Exception as e:
                _try_put(evt_q, make_msg("error", active_job_id, {
                    "code": ErrorCode.MODEL_LOAD,
                    "message": f"Demucs backend not available: {e}",
                    "detail": {"hint": "pip install demucs (inside ComfyUI env)"},
                }))
            else:
                try:
                    input_path = payload["input_path"]
                    output_dir = payload["output_dir"]
                    model = payload.get("model", "htdemucs")
                    precision = payload.get("precision", "auto")
                    shifts = int(payload.get("shifts", 1))
                    segment_sec = int(payload.get("segment_sec", 20))
                    overlap = float(payload.get("overlap", 0.25))

                    def cancel_check():
                        return cancel_job_id == active_job_id

                    res = demucs_vocal_split(
                        input_path=input_path,
                        output_dir=output_dir,
                        model=model,
                        precision=precision,
                        shifts=shifts,
                        segment_sec=segment_sec,
                        overlap=overlap,
                        log=log,
                        progress=progress,
                        heartbeat=heartbeat,
                        cancel_check=cancel_check,
                    )

                    _try_put(evt_q, make_msg("result", active_job_id, res))
                    log("info", "Demucs vocal_split complete")

                except Exception as e:
                    tb = traceback.format_exc()
                    _try_put(evt_q, make_msg("error", active_job_id, {
                        "code": ErrorCode.INTERNAL,
                        "message": f"Demucs vocal_split failed: {e}",
                        "detail": {"trace": tb},
                    }))
        else:
            log("warn", f"Unknown job backend/mode: {backend}/{mode}")
            _try_put(evt_q, make_msg("error", active_job_id, {
                "code": ErrorCode.INTERNAL,
                "message": f"Unknown job backend/mode: {backend}/{mode}",
            }))




        