# Feature: Transcription Reliability (odd/transcription-reliability)

## Objective
Make the transcription complete and reliable: stop the mid-sentence chop, stop losing
the quiet background voice, and stop inventing text during music and silence.

## Problem
The maintainer reports three symptoms, in his own words:
1. The transcription "is not correct, or it stops halfway, incomplete".
2. The panel output "stops halfway" rather than ending a sentence.
3. "I lose the background voice".

Measured facts, not impressions:
- The stored transcription is NOT incomplete. It covers 2.13 -> 468.85 of 468.9 s.
- It is chopped into 45 fragments and **0 of 45 end in a sentence**. The output unit is
  the whisper 30 s window, not the sentence. That is symptom 2, and it is structural.
- The marked-range path clamps a segment's TEXT while only moving its timestamp
  (`app/services/transcriber.py:99-103`). Measured on the reference MKV: a 4 s mark shows
  1109 chars where the correct answer is 422. A mark is always shorter than a whisper
  segment (median 9.1 s, max 31.2 s), so the clamp cannot work by construction.
- The background voice was measured as a real 15 dB level difference under ambient
  music, not a code defect.
- No model swap fixes any of the three symptoms above. Symptom 1 and 2 are structural;
  symptom 3 is the only one where the engine is the right lever.

## Why now
The maintainer deprioritized the in/out marks redesign ("I do not really like how it
works, we will leave it for later") and made transcription completeness the priority.

## Scope
- F0: version the measurement harness inside the repo. Today every probe lives in
  `%TEMP%\opencode\` (45+ files) and Windows will delete it.
- F1: baseline of the five structural criteria on the reference MKV, current code.
- F2: the faster-whisper decode options the app never passes. No new dependencies, so
  the result stays attributable.
- F3: change the output unit from a whisper window to a sentence.
- F4: compare three engines with the same five criteria.
- F5: decide with data, and only then abstract a model interface.

Out of scope: the in/out marks redesign (postponed by the maintainer).

## Constraints
- Windows + Python 3.14.3. **NVIDIA NeMo is unusable here**: its own support matrix
  excludes Windows and Python >= 3.14. This removes Parakeet TDT and Canary.
- RTX 3080, 10 GB VRAM. Every candidate fits in fp16.
- `transformers` is NOT installed. Qwen3-ASR and Cohere need it.
- F2 adds no dependency on purpose.
- Conventional commits, English artifacts, no AI attribution.

## The five criteria
| # | Metric | Symptom attacked | Baseline |
|---|--------|------------------|----------|
| M1 | `frac_frase_completa`, `mediana_dur`, `mediana_chars` | stops halfway | **measured: 0/45 = 0 %** |
| M2 | `huecos_con_señal`, `cola_perdida`, `cobertura` | incomplete | never measured |
| M3 | `chars_en_silencio`, `ratio_rep_ngram` | reliability | never measured |
| M4 | `chars_por_hablante`, `delta_chars_banda_vocal` | background voice | never measured |
| M5 | `segundos_por_segundo_audio`, `pico_vram_mb` | cost on this GPU | ~27x diarize |

M2 and M3 are what discriminate between engines. M4 carries two independent instruments
on purpose: if the lost background voice shows up in only one of them, the defect is in
the instrument, not in the audio.

## Accepted risk
No manual ground truth. The maintainer chose structural criteria over transcribing a
2-3 min reference by hand. Consequence: **wrong words are invisible to this benchmark.** A
model can achieve perfect coverage with wrong text and still pass. M4 partly covers it,
because background speech only surfaces if it is intelligible, but the number does not
prove accuracy. A manual reference remains available as a later contrast.

## Deliverable: configurable decode options
faster-whisper 1.2.1 already exposes everything F2 needs, verified against the installed
signature rather than documentation:
| Option | Default | App today |
|--------|---------|-----------|
| `condition_on_previous_text` | True | not passed -> ON, the reliability risk |
| `word_timestamps` | False | not passed -> OFF |
| `no_speech_threshold` | 0.6 | not passed |
| `log_prob_threshold` | -1.0 | not passed |
| `compression_ratio_threshold` | 2.4 | not passed |
| `temperature` | [0.0 ... 1.0] | active by default |

`chunk_length` stays at its 30 s default: lowering it to 10 s costs +3.5 % absolute WER.
`beam_size` stays at 5: beam search helps Whisper (it is counterproductive on Parakeet,
which is a second reason that model was out).

## Checklist
- [x] F0: harness versioned in the repo, committed. 20/20 unit tests green.
- [x] F1: baseline of M1-M5 on the reference MKV. See evidence.
- [ ] F2: decode options exposed through config and applied.
- [ ] F3: output unit is a sentence, not a window.
- [ ] F4: three engines compared with the same criteria.
- [ ] F5: decision recorded, interface abstracted only if warranted.

## Progress
- **F0 done.** `tests/asr/metrics.py` holds M1-M5 as pure functions over (segments,
  waveform); `tools/bench_asr.py` runs one transcription and scores it;
  `tests/asr/test_metrics.py` covers them on synthetic waveforms in 0.02 s.
  `tools/bench_asr.py` deliberately does NOT route through `Transcriber`, because the
  production path hardcodes its options and the point is to vary them.
  The old probes in `%TEMP%\opencode\` are the historical record and are NOT copied in;
  this harness supersedes them for transcription work.
- **F0 found a real defect while being written.** `coverage()` did not count the head
  gap, so a transcription that lost the first seconds of a file scored as clean. Caught
  by `test_head_omission_counts_like_any_other`, fixed, now covered.
- **F1 done, and it refuted one of our own hypotheses.** See below.
- Not delegating: subagent dispatch has failed three times in this client
  ("OpenCode's free tier can only be used from within OpenCode"), so these files were
  written inline rather than spending a fourth attempt on it.

## Verification evidence

### F1 baseline, reference MKV, `large-v3-turbo`, app options `{beam_size:5, vad_filter:True}`
Signal: 468.95 s, 23445 frames, noise floor **-69.3 dBFS**, speech_frac 0.691.

| Family | Result | Reading |
|--------|--------|---------|
| M1 | **0/45 complete (0 %)**, median 9.08 s, median 96 chars | the chop, confirmed |
| M2 | span 0.995, speech_covered 0.978, **4 gaps carrying 5.68 s of real speech** | 4 concrete omissions |
| M3 | chars_in_silence 27.8 %, **fully_in_silence 0**, partly 44, repeat 0.036 | no hallucination found |
| M4 | **-219 chars (-6.1 %)** | not frequency-masked |
| M5 | 8.0 s wall, **58x real-time** | plenty of headroom |

**M1 is the defect.** 44 of 45 segments straddle a pause, which is the mechanical cause:
whisper emits fixed 30 s windows, so every window boundary lands mid-thought. No model
change touches this. F3 is the fix.

**M3 refuted our hypothesis.** We expected text invented over the music bed. It is not
there: **zero** segments sit entirely on silence, and the 4-gram repeat ratio is 0.036,
so there is no runaway loop either. The 27.8 % is 44 segments each *partly* silent,
which is what continuous speech chopped into windows looks like. Recorded as a guard,
not as a fault. (`compression_ratio_threshold=2.4` is active by default and is the
likely reason the loop never starts.)

**M4 says the background voice is level-masked, not frequency-masked.** Restricting to
300-3400 Hz produced *less* text, not more. Combined with the earlier 15 dB level
measurement, a band-pass cannot recover this voice. This closes a line of enquiry: do
not spend time on EQ or filtering.

**M5 VRAM is 0.0 and that is correct, not missing.** faster-whisper runs on CTranslate2
and never touches the torch allocator. Renamed to `peak_torch_vram_mb` so the number
cannot be misread. It only becomes meaningful for a torch-based engine in F4.

## Next step
F2: `condition_on_previous_text=False` and `word_timestamps=True`, then re-measure.
