import time

try:
    import comfy.model_management as mm
except Exception:
    mm = None

from ..worker.worker_manager import get_worker_manager


class StemWorkerTestNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "duration_sec": ("INT", {"default": 60, "min": 1, "max": 600}),
                "emit_logs": ("BOOLEAN", {"default": True}),
            }
        }

    RETURN_TYPES = ("AUDIO", "AUDIO", "STRING")
    RETURN_NAMES = ("vox_audio", "inst_audio", "status")
    FUNCTION = "run"
    CATEGORY = "audio/stem_separator"

    def run(self, audio, duration_sec: int, emit_logs: bool):
        wm = get_worker_manager()

        def on_progress(p: float, msg: str):
            # Keep it conservative: avoid spamming.
            # This also shows in ComfyUI console.
            if emit_logs:
                print(f"[StemWorkerTest][progress] {p:.2%} - {msg}")

        start = time.time()
        job_id = None
        try:
            job_id = wm.run_dummy_job_blocking(
                duration_sec=duration_sec,
                on_progress=on_progress,
                interruption_check=self._check_interrupted,
            )
            elapsed = time.time() - start
            return (audio, f"OK: job={job_id} elapsed={elapsed:.2f}s")
        except Exception as e:
            elapsed = time.time() - start
            return (audio, f"ERROR: job={job_id} elapsed={elapsed:.2f}s err={e}")

    def _check_interrupted(self):
        # This is the best-practice pattern used across many ComfyUI nodes.
        if mm is None:
            return
        # If user presses Stop, this will raise.
        mm.throw_exception_if_processing_interrupted()