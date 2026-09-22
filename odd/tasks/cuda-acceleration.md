# Feature: CUDA Acceleration (feature/cuda-acceleration)

## Objective
Run the whole pipeline — faster-whisper transcription, Argos translation (on CPU if needed), and PyAnnote speaker diarization — on the NVIDIA RTX 3080 GPU. Currently torch is a CPU build so everything runs on CPU.

## Problem
All three models load and run on CPU. The user has an RTX 3080 (10 GB, driver 616.56, CUDA UMD 13.4) that sits idle. Diarization of 87 s of video takes 52 s on CPU; transcription of 29 s takes ~24 s total. GPU should cut both dramatically.

## Why now
Diarization and transcription are the compute-heavy parts of the app. The MKV header workaround (WAV temp decode) is already merged on main. GPU makes the interactive transcription loop feel real.

## Scope
- Replace CPU torch builds with CUDA builds matching the installed version set (torch 2.14.0+cu130, torchaudio 2.11.0+cu130, torchcodec 0.16.0+cu130).
- Provide GPU runtime for faster-whisper (ctranslate2) — verify whether its bundled cuDNN9 + pip cublas is enough or system CUDA libs are needed.
- Move PyAnnote pipeline to GPU (`Diarizer` gains device support).
- Config: keep `WHISPER_DEVICE=auto` semantics; `auto` should resolve to CUDA now. Diarizer resolves device internally (cuda when available).
- Verify with the user's real files (both MKVs) and record before/after timing.
- Update requirements.txt pins so a fresh install gets CUDA wheels from the pytorch index.

## Constraints
- Windows 11, Python 3.14.3, venv `.venv`.
- Keep the FFmpeg-shared PATH bootstrap in place (still needed by torchcodec).
- Must not break CPU-only cold path: if CUDA unavailable, fall back to CPU (auto behavior).
- Conventional commit messages; the GPU switch itself is a single work-unit commit.

## Checklist
- [X] T1: Explore available CUDA wheels and GPU runtime requirements (report: cu130 index for torch/torchaudio/torchcodec; ctranslate2 ships cudnn64_9.dll; driver CUDA 13.4)
- [X] T2: Install CUDA stack in venv (torch+torchaudio+torchcodec cu130) and verify `torch.cuda.is_available()` + faster-whisper GPU run
- [X] T3: Diarizer runs on GPU; verify diarization on both real MKVs
- [X] T4: Update requirements.txt + config defaults; full pipeline end-to-end and timing comparison
- [ ] T5: Work-unit commit on feature branch (requires work-unit-commits + chained-pr boundaries if >400 lines; forecast is small so single commit)
- [X] T6 (if needed): PATH bootstrap for CUDA runtime DLLs (like FFmpeg) if ctranslate2 can't find cublas — not needed: ctranslate2 wheel bundles cudnn64_9.dll + found cuBLAS in torch cu130 runtime

## Acceptance criteria
- `torch.cuda.is_available()` is True in the venv with the cu130 wheels.
- `Diarizer(HF_TOKEN).diarize(...)` works on both real MKVs on GPU and is faster than CPU.
- `Pipeline.transcribe_and_translate(..., diarize=True)` works end-to-end on GPU and is faster than the CPU baseline.
- Fallback to CPU still works when CUDA is unavailable (auto).
- Fresh install instructions in requirements.txt produce the CUDA wheels.

## Verification evidence
- Baseline CPU timings: 29 s MKV diarize-only 18.8 s; 87 s MKV diarize-only 52.1 s; 29 s full transcribe+translate+diarize 24.4 s.
- After (RTX 3080, cu130 wheels):
  - `torch.cuda.is_available()` True; `torch 2.14.0+cu130`, `torchaudio 2.11.0+cu130`, `torchcodec 0.16.0+cu130`.
  - faster-whisper tiny GPU loads in 0.9 s; 10 s WAV transcribe 1.3 s.
  - Diarize-only: 29 s MKV 8.0 s (18.8 s CPU); 87 s MKV 1.9 s (52.1 s CPU → 27x).
  - Full transcribe+translate+diarize on 29 s MKV: 11.2 s total (24.4 s CPU → 2.2x), 3 speakers, same segments.
  - `pip check` clean; Argos import OK; GUI offscreen smoke OK (checkbox default true).