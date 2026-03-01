# WV-Steamer - ComfyUI Stem Separator
### Professional-Grade Stem Separator (WIP)

DISCLAIMER: Work-IN-PROGRESS

---

![ComfyUI
Compatible](https://img.shields.io/badge/ComfyUI-Compatible-brightgreen)
![Subprocess
Architecture](https://img.shields.io/badge/Architecture-Subprocess-orange)

---

# Overview

ComfyUI Stem Separator is a production-grade audio stem separation
extension for ComfyUI featuring:

- HT-Demucs support
- BS-RoFormer support
- GPU isolation via subprocess worker
- Real multi-stem output (STEM_DICT)
- ComfyUI-compliant lifecycle & UI integration
- No external service, no extra container

This extension is designed for real-world music production workloads and
long-running GPU sessions.

---

# Architecture Summary

## Core Principle

Subprocess worker for CUDA isolation, fully controlled via ComfyUI
extension framework.

- No monkeypatching
- No scheduler override
- No fork-based CUDA
- No separate service deployment

---

# Worker Architecture

## Lifecycle

1. First job → spawn worker (multiprocessing.spawn)
2. Worker initializes CUDA context
3. Model loads lazily and caches internally
4. Subsequent jobs reuse cache
5. "Unload Models" clears cache
6. "Restart Worker" hard-resets process

---

# IPC Design

## Job Schema (JSON)

```
{
  "model": "ht-demucs | roformer",
  "recipe": "balanced | vocals_clean | ...",
  "precision": "auto | fp16 | fp32",
  "chunk_mode": "auto | always | never",
  "chunk_seconds": 30,
  "overlap_seconds": 5,
  "normalize": "off | peak | rms",
  "input_path": "/basedir/temp/input.wav",
  "output_dir": "/basedir/output/"
}
```

## Execution Flow

Main Process:

1. Load AUDIO → temp file
2. Send job JSON via multiprocessing queue
3. Wait for response metadata 4. Load output files
  into AUDIO
  

Worker Process:

1. Receive job
2. Load / reuse model
3. Perform separation
4. Write stem WAV files
5. Return metadata JSON
  

No tensor transfer over pipe. No shared CUDA context.

---

# Nodes

## Vocal Split Nodes

- HT-Demucs Vocal Split
- BS-RoFormer Vocal Split

Input: AUDIO
Output: vocals AUDIO + instrumental AUDIO

---

## Multi-Stem Nodes

- HT-Demucs Multi-Stem (Recipe)
- BS-RoFormer Multi-Stem (Recipe)

Output: STEM_DICT

Example:

```
{
  "vocals": (tensor, sr),
  "drums": (tensor, sr),
  "bass": (tensor, sr),
  "other": (tensor, sr)
}
```

Instrumental = sum(non-vocal stems)

---

# AI Model

## HT-Demucs

- Balanced
- Vocals Clean
- Drums Tight
- Bass Solid
- Max Quality

## BS-RoFormer

- Clean Vocals
- Dense EDM Mix
- Reverb Heavy
- Max Quality

Each recipe defines chunking and overlap defaults internally.

---

# Precision Policy

Dropdown per node:

- auto (default)
- fp16
- fp32

Auto → fp16 on CUDA, fp32 on CPU.

---

# Chunk Policy

- auto
- always_chunk
- never_chunk

Must safely handle 10-minute audio on 16GB GPU.

---

# UI Controls

Web extension panel provides:

- Worker status
- Cached models list
- Unload Models button
- Restart Worker button

Backend routes:

```
POST /stem_separator/unload
POST /stem_separator/restart
GET  /stem_separator/status
```

---

# ComfyUI Compliance Roadmap

This extension:

- Uses standard ComfyUI node registration
- Uses PromptServer routes properly
- Uses official web extension mechanism
- Does not modify core scheduler
- Does not override execution engine
- Does not bind external ports

Subprocess is internal and fully managed by extension.

---

# Future Extensions (Roadmap)

- Batch separation
- Spectrogram preview node
- Stem loudness matching (LUFS)
- Streaming real-time preview
- Worker idle timeout
- Multi-GPU support

---

# License

MIT