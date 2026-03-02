import os
import time
import traceback

from .errors import WorkerError, ErrorCode

# Worker-side cache (lives in subprocess)
_DEMUCS_CACHE = {}


def _get_separator(model: str, device: str, shifts: int, overlap: float, segment_sec: int, log):
    """
    Cache key includes parameters that affect separator config.
    """
    key = (model, device, shifts, float(overlap), int(segment_sec))
    if key in _DEMUCS_CACHE:
        return _DEMUCS_CACHE[key]

    # Prefer demucs.api.Separator (Demucs v4+)
    try:
        from demucs.api import Separator
    except Exception as e:
        raise WorkerError(ErrorCode.MODEL_LOAD, f"demucs.api.Separator not found: {e}", {
            "hint": "Install Demucs v4+ (pip install demucs) or provide compatible demucs package.",
        })

    # Some demucs versions vary kwargs; build carefully
    kwargs = {
        "model": model,
        "device": device,
        "shifts": shifts,
        "overlap": overlap,
        "segment": segment_sec,
        "progress": False,
    }

    # Try to construct with best-effort kwargs
    try:
        sep = Separator(**kwargs)
    except TypeError:
        # Fallback: remove non-standard kwargs
        kwargs.pop("segment", None)
        sep = Separator(**kwargs)

    _DEMUCS_CACHE[key] = sep
    log("info", f"Demucs Separator cached key={key}")
    return sep


def demucs_vocal_split(
    input_path: str,
    output_dir: str,
    model: str,
    precision: str,
    shifts: int,
    segment_sec: int,
    overlap: float,
    log,
    progress,
    heartbeat,
    cancel_check,
):
    """
    Returns payload for 'result' event:
      {
        "mode": "vocal_split",
        "vocals_path": "...",
        "instrumental_path": "..."
      }
    """
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    os.makedirs(output_dir, exist_ok=True)

    progress(0.02, "Loading/caching Demucs model")
    heartbeat(0.02, "Loading model")

    sep = _get_separator(model=model, device=device, shifts=shifts, overlap=overlap, segment_sec=segment_sec, log=log)

    if cancel_check():
        raise WorkerError(ErrorCode.CANCELLED, "Cancelled before separation")

    # Load audio using demucs Separator (it handles decoding)
    # Separate returns dict stem->Tensor or similar depending on version
    progress(0.10, "Running separation")
    heartbeat(0.10, "Separating")

    use_fp16 = False
    if precision == "fp16":
        use_fp16 = True
    elif precision == "auto":
        use_fp16 = (device == "cuda")

    ctx = torch.cuda.amp.autocast(enabled=use_fp16) if device == "cuda" else torch.no_grad()

    try:
        with torch.inference_mode():
            with ctx:
                # demucs.api.Separator has `separate_audio_file(path)`
                # returns (orig, separated) or separated dict depending on version
                out = sep.separate_audio_file(input_path)

        # Normalize output structure
        # Common patterns:
        # - out is dict
        # - out is tuple (sr, stems) or (orig, stems)
        stems = None
        if isinstance(out, dict):
            stems = out
        elif isinstance(out, (tuple, list)) and len(out) >= 2 and isinstance(out[1], dict):
            stems = out[1]
        elif isinstance(out, (tuple, list)) and len(out) >= 1 and isinstance(out[0], dict):
            stems = out[0]

        if stems is None:
            raise WorkerError(ErrorCode.INTERNAL, f"Unexpected demucs output type: {type(out)}")

        # Need vocals + instrumental
        if "vocals" not in stems:
            raise WorkerError(ErrorCode.INTERNAL, f"'vocals' stem not found in outputs: {list(stems.keys())}")

        vocals = stems["vocals"]

        # Instrumental = sum(other stems)
        inst = None
        for k, v in stems.items():
            if k == "vocals":
                continue
            inst = v if inst is None else (inst + v)

        if inst is None:
            raise WorkerError(ErrorCode.INTERNAL, "No non-vocal stems to form instrumental")

        progress(0.85, "Writing stems to disk")
        heartbeat(0.85, "Writing")

        vocals_path = os.path.join(output_dir, "vocals.wav")
        inst_path = os.path.join(output_dir, "instrumental.wav")

        _write_wav_pcm16(vocals_path, vocals, log=log)
        _write_wav_pcm16(inst_path, inst, log=log)

        progress(1.0, "Done")
        heartbeat(1.0, "Done")

        return {
            "mode": "vocal_split",
            "vocals_path": vocals_path,
            "instrumental_path": inst_path,
        }

    except WorkerError:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        raise WorkerError(ErrorCode.INTERNAL, f"Demucs separation exception: {e}", {"trace": tb})


def _write_wav_pcm16(path: str, waveform, log):
    """
    waveform likely torch Tensor [C,T] or [T] float.
    Save as PCM16 wav without extra deps.
    """
    import wave
    import struct
    import torch

    if not hasattr(waveform, "dim"):
        raise WorkerError(ErrorCode.IO, "Waveform is not a torch Tensor")

    if waveform.dim() == 1:
        waveform = waveform.unsqueeze(0)
    waveform = waveform.detach().to("cpu").contiguous()

    # demucs often uses [C,T] float; sample rate unknown here
    # Separator usually outputs at the input SR, but API versions differ.
    # For now: assume 44100 if not known. (We’ll improve in Phase 2.1)
    sample_rate = 44100

    c = int(waveform.shape[0])
    n = int(waveform.shape[1])

    wf = waveform.clamp(-1.0, 1.0).mul(32767.0).round().to(torch.int16)

    with wave.open(path, "wb") as w:
        w.setnchannels(c)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        for i in range(n):
            frame = [int(wf[ch, i].item()) for ch in range(c)]
            w.writeframesraw(struct.pack("<" + "h" * c, *frame))

    log("info", f"Wrote {path}")