import os
import tempfile
import time

try:
    import torch
except Exception:
    torch = None

try:
    import comfy.model_management as mm
except Exception:
    mm = None

try:
    from comfy.utils import ProgressBar
except Exception:
    ProgressBar = None

from ..worker.worker_manager import get_worker_manager


def _audio_unpack(audio):
    """
    Be tolerant: different ComfyUI audio nodes can represent audio differently.
    Try common shapes:
      - dict: {"waveform": Tensor, "sample_rate": int}
      - tuple: (Tensor, sample_rate)
    """
    if isinstance(audio, dict):
        wf = audio.get("waveform", None)
        sr = audio.get("sample_rate", None)
        return wf, int(sr) if sr is not None else None
    if isinstance(audio, (tuple, list)) and len(audio) >= 2:
        return audio[0], int(audio[1])
    return None, None


class DemucsVocalSplitNode:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "audio": ("AUDIO",),
                "model": (["htdemucs", "htdemucs_ft"], {"default": "htdemucs"}),
                "precision": (["auto", "fp16", "fp32"], {"default": "auto"}),
                "shifts": ("INT", {"default": 1, "min": 0, "max": 10}),
                "segment_sec": ("INT", {"default": 20, "min": 1, "max": 60}),
                "overlap": ("FLOAT", {"default": 0.25, "min": 0.0, "max": 0.99}),
            }
        }

    RETURN_TYPES = ("AUDIO", "AUDIO", "STRING")
    RETURN_NAMES = ("vocals", "instrumental", "status")
    FUNCTION = "run"
    CATEGORY = "audio/stem_separator"

    def run(self, audio, model, precision, shifts, segment_sec, overlap):
        wm = get_worker_manager()

        wf, sr = _audio_unpack(audio)
        if wf is None or sr is None:
            return (audio, audio, "ERROR: unsupported AUDIO format (expected dict{waveform,sample_rate} or (waveform,sr))")

        if torch is None:
            return (audio, audio, "ERROR: torch not available in main process")

        # Ensure waveform is on CPU for serialization -> wav file
        try:
            wf_cpu = wf.detach().to("cpu")
        except Exception:
            wf_cpu = wf

        # temp workspace
        tmp_root = os.getenv("COMFYUI_TEMP_DIR") or tempfile.gettempdir()
        job_dir = os.path.join(tmp_root, "comfyui_stem_separator", time.strftime("%Y%m%d"))
        os.makedirs(job_dir, exist_ok=True)

        input_wav = os.path.join(job_dir, f"demucs_in_{int(time.time()*1000)}.wav")
        out_dir = os.path.join(job_dir, f"demucs_out_{int(time.time()*1000)}")
        os.makedirs(out_dir, exist_ok=True)

        # Write input wav (PCM16) – no external deps
        try:
            self._write_wav_pcm16(input_wav, wf_cpu, sr)
        except Exception as e:
            return (audio, audio, f"ERROR: failed to write temp wav: {e}")

        pbar = ProgressBar(100) if ProgressBar is not None else None
        last_step = -1

        def on_progress(p: float, msg: str):
            nonlocal last_step
            if pbar is not None:
                step = int(max(0.0, min(1.0, p)) * 100)
                if step != last_step:
                    delta = step - last_step if last_step >= 0 else step
                    if delta > 0:
                        pbar.update(delta)
                    last_step = step

        try:
            job_id, result = wm.run_demucs_vocal_split_blocking(
                input_path=input_wav,
                output_dir=out_dir,
                model=model,
                precision=precision,
                shifts=int(shifts),
                segment_sec=int(segment_sec),
                overlap=float(overlap),
                on_progress=on_progress,
                interruption_check=self._check_interrupted,
            )

            # Load output stems back into AUDIO tensors
            vocals_path = result["vocals_path"]
            inst_path = result["instrumental_path"]
            vocals_audio = self._read_wav_to_audio(vocals_path)
            inst_audio = self._read_wav_to_audio(inst_path)

            return (vocals_audio, inst_audio, f"OK: job={job_id}")

        except Exception as e:
            return (audio, audio, f"ERROR: {e}")

    def _check_interrupted(self):
        if mm is None:
            return
        mm.throw_exception_if_processing_interrupted()

    # ---------------- WAV IO (stdlib) ----------------

    def _write_wav_pcm16(self, path, waveform, sample_rate: int):
        """
        waveform: torch Tensor [channels, samples] or [samples] float range [-1..1].
        """
        import wave
        import struct

        if hasattr(waveform, "dim"):
            if waveform.dim() == 1:
                waveform = waveform.unsqueeze(0)
            # [C, T]
            waveform = waveform.contiguous()
        else:
            raise RuntimeError("waveform must be a torch Tensor")

        c = int(waveform.shape[0])
        n = int(waveform.shape[1])

        # clamp and convert to int16
        wf = waveform.clamp(-1.0, 1.0).mul(32767.0).round().to(torch.int16)

        with wave.open(path, "wb") as w:
            w.setnchannels(c)
            w.setsampwidth(2)  # int16
            w.setframerate(int(sample_rate))

            # interleave frames
            # wf: [C, T] -> frames: T * C
            for i in range(n):
                frame = []
                for ch in range(c):
                    frame.append(int(wf[ch, i].item()))
                w.writeframesraw(struct.pack("<" + "h"*c, *frame))

    def _read_wav_to_audio(self, path):
        """
        Read PCM16 wav to ComfyUI AUDIO dict: {"waveform": Tensor, "sample_rate": int}
        """
        import wave
        import array
        import torch

        with wave.open(path, "rb") as w:
            c = w.getnchannels()
            sr = w.getframerate()
            nframes = w.getnframes()
            sampwidth = w.getsampwidth()
            if sampwidth != 2:
                raise RuntimeError(f"Unsupported WAV sampwidth={sampwidth} (expected 16-bit PCM)")

            raw = w.readframes(nframes)
            data = array.array("h")
            data.frombytes(raw)

        # data is interleaved
        total = len(data)
        frames = total // c
        # build tensor [C, T]
        wf = torch.empty((c, frames), dtype=torch.float32)
        idx = 0
        for i in range(frames):
            for ch in range(c):
                wf[ch, i] = float(data[idx]) / 32768.0
                idx += 1

        return {"waveform": wf, "sample_rate": int(sr)}