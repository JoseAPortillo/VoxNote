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
- [x] F0: harness versioned in the repo, committed. 41/41 unit tests green.
- [x] F1: baseline of M1-M5 on the reference MKV. See evidence.
- [x] F2: decode options measured. **Negative result — keep the current options.**
- [x] F3: sentence grouper built and measured. **Superseded: M1 IS reachable.** The
  cause is `condition_on_previous_text=True` decaying the punctuation, not the audio.
  Chunked re-prompting takes M1 from 0.000 to 0.381 on the reference file.
- [ ] F6: build the chunked path (fresh prompt per chunk + warmup seams + grouping) and
  measure it against this baseline before touching production.

## F6 verdict: the chunked path fixes punctuation and breaks coverage
Built and measured. The harness gained `--chunks` and `--warmup`; it decodes contiguous
ranges, pads each by the warmup margin, keeps words by midpoint so no word is duplicated
across an overlap, and groups the survivors into sentences. The margin pattern mirrors
`Transcriber.transcribe_segments`; the midpoint filter replaces that clamping because the
ranges here are contiguous rather than disjoint.

Reference file, 468.95 s, `large-v3-turbo`, `condition_on_previous_text=False` where noted:

| Configuration | M1 complete | M2 coverage | speech in gaps | chars |
|---------------|-------------|-------------|-----------------|-------|
| **current app options** | 0.000 | **0.979** | **5.69 s** | 3684 |
| chunk 60 s + prompt + cond OFF | 0.557 | 0.745 | 71.21 s | 3498 |
| chunk 60 s + prompt + cond ON | **0.919** | 0.693 | 74.71 s | 3439 |

Punctuation is genuinely solved and the gain is large, so the mechanism above is real.
The strategy is still rejected, because **M2 is non-negotiable**: 71 s to 75 s of speech
lands in gaps, roughly 12 % of the material. Buying punctuation with dropped content is
the wrong trade for a transcription tool, and no amount of M1 improvement makes up for
words the user never sees.

Holding `condition_on_previous_text=True` does not rescue coverage (0.693, marginally
worse), so the loss is not the conditioning flag. The clue is the shape of the loss:
30 gapped regions across 8 chunks is three to four per chunk, and a 90 s padded chunk is
three whisper windows of 30 s. The gaps land on **whisper's internal 30 s window seams**,
not on the seven chunk boundaries this harness controls, which is why padding the chunk
boundaries does not close them.

Next hypothesis, not yet tested: place chunk boundaries in the **detected long silences**
instead of on a fixed 60 s grid, so no boundary ever cuts speech. The detector and the
123 regions already exist in `tests/asr/metrics.py`. That addresses the seams this
harness controls; it does not by itself address whisper's internal seams, so it is a
necessary test and not a predicted fix.
- [x] F4: `large-v3` compared against turbo. **Turbo wins on coverage and speed.**
- [ ] F5: decision recorded; interface abstracted only if warranted.

## The finding that reframes everything (F3)
Reading the actual output, which three phases of aggregates had failed to do:
**the reference transcript contains 0 periods, 0 commas, 0 question marks and 0
exclamation marks across 3543 characters.** It is run-on text. Whisper never places a
sentence boundary in it.

So the F1 conclusion was **wrong**. The chop is not 30 s window boundaries cutting
sentences; there are no sentences in the output to cut. Every framing that followed from
"the window lands mid-thought" was reasoning about a cause that does not exist.

Confirmed across engines and levers, on the reference MKV:

| Lever | M1 complete | notes |
|-------|-------------|-------|
| turbo, current options | 0.000 | 45 units |
| turbo, `word_timestamps` | 0.000 | 44 units |
| turbo, `condition_on_previous_text=False` | 0.111 | 45 units, from the F2 sweep |
| turbo, `initial_prompt` asking for punctuation | 0.000 / 0.056 | prompt does not restore it |
| turbo, sentence grouping at 1.2 s pauses | **0.000** | 39 units, chars preserved |
| **large-v3** (full model) | **0.000** | 55 units, 3813 chars |

The full model punctuates nothing either. **M1 as defined is unachievable on this audio
with whisper**, and no decode option, prompt, grouping or model change reaches it.
Reporting it as fixable without this evidence would have spent the whole exercise on a
target that does not exist.

## The real cause: the prompt decays within one transcribe() call (F3, corrected)
The first reading of this phase was wrong twice, and both corrections came from checking
the underlying thing instead of trusting a metric.

**Correction 1 — the speaker does make phrase breaks.** Inter-region pauses on the
reference file: 56 of 1.0 s or more (22.7 % of the file), 43 of 1.2 s or more, 21 of 2.0 s
or more. Whisper's units average 10.4 s, which matches those pauses, so segmentation was
already pause-aligned and the units were not arbitrarily chopped. The conclusion that "the
speaker does not punctuate, so option C" was **refuted by its own falsifier**.

**Correction 2 — punctuation is recoverable.** `initial_prompt` punctuates a fresh 90 s
window completely: 15 periods, 3 exclamation marks and 1 question mark in 730 characters,
against zero without it. But over the whole file the same prompt yields **1 period**. The
mechanism is `condition_on_previous_text=True`: every 30 s window is conditioned on the
model's own output, and once that output has no punctuation the next window inherits and
reinforces it. The prompt conditions the start of the file and its influence decays.

Measured on the full reference file, counting characters directly rather than via M1:

| Configuration | `.` | `,` | units ending a sentence |
|---------------|-----|-----|-------------------------|
| current app options | **0** | 0 | 0/45 = 0.000 |
| `initial_prompt` only | 1 | 0 | 0/40 = 0.000 |
| `initial_prompt` + `condition_on_previous_text=False` | 11 | 2 | 2/39 = 0.051 |
| `condition_on_previous_text=False` only | 11 | 6 | 5/45 = 0.111 |
| **`initial_prompt` re-applied per 60 s chunk** | **42** | **16** | 16/42 = **0.381** |
| _(ceiling: one fresh 90 s window)_ | 15 in 730 | 3 | density 0.0205/char |

Chunked re-prompting reaches 0.0122 punctuation per character against the 0.0031 of a
single prompted call. It works because it removes the reinforcement loop: a fresh
`initial_prompt` per chunk resets the prior each time.

**The trade, stated plainly.** Chunking restores punctuation but introduces seams: 3442
characters against the 3684 single-call baseline, and a chunk boundary lands mid-sentence
("...really arguing standing kind" | "This close and front facing"). So it relocates the
defect rather than eliminating it, and closing the seams needs the warmup overlap
`clipper.RANGE_WARMUP` already implements and already measured, plus the sentence
grouper to absorb the joins. That is a real design, not a guess, and it is not built yet.

### What the grouper is actually worth
`app/services/sentences.py` works: 738 timed words → 39 units, 3548 chars against a
3543-char window baseline, every unit closed at a pause rather than at a window edge,
none overlapping, median 73 chars against 96. Text is preserved exactly. Breaks become
meaningful instead of arbitrary. That is readability, not phrase completeness, and it
becomes load-bearing rather than optional once chunk seams exist.

### Not wired into production, on purpose
- The gain F3 was chartered to deliver (complete sentences) is only reachable through the
  chunked path, which is unbuilt and unmeasured in production.
- `Pipeline` translates each segment independently and already documents that this
  "loses cross-sentence context". Nothing in this repository measures translation quality
  yet, so changing the unit size on the transcription pane could silently degrade the
  translated one. That trade needs measuring, not assuming.

### F4: large-v3 loses to turbo on both counts
`large-v3` reaches 0.899 speech coverage with **28.36 s** of speech in gaps, against
turbo's 0.979 and 5.69 s, and runs at 5.4x real-time against turbo's 58x. More
characters (3813 vs 3543) is not more content; it is more invention on a bed this hard.
Turbo stays, and the app needs no model change. The punctuation finding is model
agnostic: `large-v3` prompted on the excerpt gave 7 periods and 6 commas in 651 chars.

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
- **F2 done, and it refuted the plan's own hypothesis.** Every decode-option change
  made coverage worse and bought almost nothing in M1. Current options win.
- **The benchmark's own detector was wrong and was corrected.** The first F2 sweep looked
  like a catastrophic result until a per-segment probe showed it was arithmetic nonsense.
  See the recalibration note below; this was the most important fix in the whole exercise.
- **F3 built the sentence grouper and disproved the phase's own premise.** Reading the
  transcript for the first time showed zero punctuation in 3543 characters, so there are
  no sentences to find. The grouper is sound and is kept, but it improves readability, not
  phrase completeness, and it is not wired into production.
- **F4 compared engines and turbo won.** large-v3 is worse on coverage and 10x slower.
- Not delegating: subagent dispatch has failed three times in this client
  ("OpenCode's free tier can only be used from within OpenCode"), so these files were
  written inline rather than spending a fourth attempt on it.

## Verification evidence

### Detector recalibration (found during F2, commit 356dc16)
The first F2 sweep appeared to show that `word_timestamps=True` was catastrophic:
coverage fell 0.978 → 0.818 and gaps rose 4 → 10. Before writing that down, a
per-segment probe showed:

| | baseline | `word_timestamps=True` |
|---|---|---|
| segment span total | 455.66 s | 366.75 s |
| characters emitted | 3595 | 3543 |

88.9 s of "lost" audio against a 1.4 % change in text is 0.6 characters per second,
which is impossible. The text was never lost; the timestamps had been redistributed.
The detector was at fault, not the engine. It reported **744 speech regions averaging
0.435 s** — the music bed counted as a stutter — because `SPEECH_MARGIN_DB` was 6 dB
above a noise floor taken from a *loud* file. Every downstream metric inherited the
error and reported it as dropped text.

Fixes: margin 6 → **12 dB**, merge runs closer than **0.40 s**, drop runs shorter than
**0.20 s**. The reference file now yields **123 regions averaging 2.615 s**, and the
detector's own numbers are published in the report so it can be checked, not trusted.
The unused `sample_rate` arguments on `speech_regions` and `silence_and_repetition`
were removed rather than left lying about the frame length.

### F1 baseline, reference MKV, `large-v3-turbo`, app options `{beam_size:5, vad_filter:True}`
Signal: 468.95 s, noise floor **-69.3 dBFS**, 123 speech regions, speech_frac 0.691.

| Family | Result | Reading |
|--------|--------|---------|
| M1 | **0/45 complete (0 %)**, median 9.08 s, median 96 chars | no punctuation in the output at all |
| M2 | speech_covered 0.979, **4 gaps carrying 5.69 s of real speech** | 4 concrete omissions |
| M3 | chars_in_silence 24.4 %, **fully_in_silence 0**, partly 41, repeat 0.036 | no hallucination found |
| M4 | -219 chars (-6.1 %) | not frequency-masked |
| M5 | **58x real-time** | plenty of headroom |

**M1 is the defect.** 41 of 45 segments straddle a pause, which is the mechanical cause:
whisper emits fixed 30 s windows, so every window boundary lands mid-thought. No model
change touches this. F3 is the fix.

**M3 refuted our hypothesis.** We expected text invented over the music bed. It is not
there: **zero** segments sit entirely on silence, and the 4-gram repeat ratio is 0.036,
so there is no runaway loop either. The 24.4 % is segments each *partly* silent, which
is what continuous speech chopped into windows looks like. Recorded as a guard, not as a
fault. (`compression_ratio_threshold=2.4` is active by default and is the likely reason
the loop never starts.)

**M4 says the background voice is level-masked, not frequency-masked.** Restricting to
300-3400 Hz produced *less* text, not more. Combined with the earlier 15 dB level
measurement, a band-pass cannot recover this voice. This closes a line of enquiry: do
not spend time on EQ or filtering.

### F2 decode options, re-measured on the corrected detector
All four rows share model, audio and metrics. Only decode options differ.

| Config | M1 complete | M2 covered | gaps | speech in gaps | M3 silence |
|--------|-------------|------------|------|-----------------|------------|
| **baseline (current app)** | 0.000 | **0.979** | **4** | **5.69 s** | 0.244 |
| `condition_on_previous_text=False` | 0.111 | 0.872 | 8 | 33.82 s | 0.198 |
| `word_timestamps=True` | 0.000 | 0.851 | 10 | 42.31 s | 0.210 |
| both | **0.156** | 0.858 | 11 | 43.22 s | 0.209 |

**The plan's F2 hypothesis is refuted.** The best M1 comes from the worst coverage.
`condition_on_previous_text=False` buys 5 complete phrases and pays 28 s of untranscribed
speech; `word_timestamps=True` buys nothing at all and costs 37 s. The current options are
already the best of the four, so **production decode options are unchanged** — and the app
needs no `word_timestamps`, which keeps the clamp bug at `transcriber.py:99-103` out of
scope for now.

A test that cannot fail is worse than no test: had the detector not been checked, F2 would
have "proven" that turning on word timestamps destroys the transcription. The only reason
that did not happen is that the sweep looked implausible and got investigated.

## Next step
The evidence now points at one concrete implementation rather than a product choice:
decode in chunks with a fresh `initial_prompt` per chunk, overlap them by
`clipper.RANGE_WARMUP` to close the seams, and group the words into sentences to absorb
the joins. Every part of that already exists in this repository; none of it is wired
together, and the two known costs are seam artifacts and shorter translation units.
Measure that path against the current baseline before changing production.
