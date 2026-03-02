import time

try:
    import comfy.model_management as mm
except Exception:
    mm = None

try:
    from comfy.utils import ProgressBar
except Exception:
    ProgressBar = None

from ..worker.worker_manager import get_worker_manager


class StemWorkerTestNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "duration_sec": ("INT", {"default": 5, "min": 1, "max": 120}),
            }
        }

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "status")
    FUNCTION = "run"
    CATEGORY = "audio/stem_separator"

    def run(self, audio, duration_sec: int):
        wm = get_worker_manager()

        pbar = ProgressBar(duration_sec) if ProgressBar is not None else None
        last_step = -1

        def on_progress(p: float, msg: str):
            nonlocal last_step
            # Map progress (0..1) to steps (0..duration_sec)
            step = int(p * duration_sec)
            if pbar is not None and step != last_step:
                # Update progress bar conservatively
                delta = max(0, step - last_step)
                if last_step < 0:
                    delta = step
                if delta > 0:
                    pbar.update(delta)
                last_step = step
            # Also log minimal info
            # print(f"[StemWorkerTest] {p:.2%} {msg}")

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
        if mm is None:
            return
        mm.throw_exception_if_processing_interrupted()