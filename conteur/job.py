"""One naming job: read the WAV, decide, rename. No Qt."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from conteur.naming import (
    NO_SPEECH, ORIGIN_NO_SPEECH, ORIGIN_SILENCE, ORIGIN_TIMECODE,
    ORIGIN_TRANSCRIPT, SILENCE,
    choose_name, slugify,
)
from conteur.orphans import is_placeholder
from conteur.paths import build_name, unique_path
from conteur.signal import (
    CAPTURE_RATE, looks_like_timecode, rms_dbfs, to_whisper_input,
)
from conteur.wavread import read_samples
from conteur.titler import make_title_tracked
from conteur.transcribe import transcribe


# Above this RMS level a file is audible: if it holds no speech, it is not
# silence either.
AUDIBLE_DBFS = -55.0

# Naming a file only requires listening to its beginning: transcribing an hour
# of audio to produce three words is a waste. Below this cap the take is
# transcribed in full, which gives better keyword statistics and more context
# for titling. At most two windows are examined — the second one only serves
# takes whose beginning is silent.
NAMING_SAMPLE_S = 300.0
NAMING_WINDOWS = 2


@dataclass(frozen=True)
class NameResult:
    path: Path
    slug: str
    origin: str


def _read_wav(path: Path) -> np.ndarray:
    # Not `wave`: it refuses format 3 (IEEE float), which is what the RØDE
    # onboard recordings use. Without this the application could only name its
    # own files.
    #
    # The read is bounded to what naming can consult: beyond that we would
    # convert hundreds of MB only to never look at them.
    budget = int(NAMING_SAMPLE_S * NAMING_WINDOWS * CAPTURE_RATE)
    return read_samples(path, max_frames=budget)


def _target_name(path: Path, when: datetime, slug: str,
                 replace_named: bool = False) -> str:
    """The file name a take takes on when it is given `slug`.

    Everything before the last `__` is the name the card carried and, for a
    part, its rank. Decision 8 keeps both deliberately: the card name will not
    be reproducible — the take counter restarts at 00001 after an erase — and
    the rank is the only thing linking a part to its siblings, which rejoining
    them depends on. A take recorded here has no such segment and is rebuilt
    from its timestamp.

    `replace_named` is what separates the two callers. Automatic naming only
    ever replaces a placeholder, so it leaves an already-named file alone.
    Renaming by hand replaces whatever tail is there — the user is choosing
    the title — but must keep the prefix all the same, or correcting one
    part's title would silently break the group.
    """
    stem = path.stem
    if "__" in stem:
        head, tail = stem.rsplit("__", 1)
        if replace_named or is_placeholder(tail):
            return f"{head}__{slug}{path.suffix}"
    return build_name(when, slug)


def _renamed(wav_path: Path, when: datetime, slug: str, origin: str) -> NameResult:
    target = unique_path(wav_path.parent, _target_name(wav_path, when, slug))
    wav_path.rename(target)
    return NameResult(path=target, slug=slug, origin=origin)


def _transcribe_sample(model, samples) -> tuple[str, float]:
    """Transcribe the beginning, and a second window only if it is silent.

    Naming does not require hearing the whole file: a story is characterised by
    how it opens. A take whose first minutes are silent still gets its chance
    with a following window.
    """
    window = int(NAMING_SAMPLE_S * CAPTURE_RATE)
    for index in range(NAMING_WINDOWS):
        start = index * window
        if start >= samples.size:
            break
        chunk = samples[start:start + window]
        text, speech_s = transcribe(model, to_whisper_input(chunk))
        if text.strip():
            return text, speech_s
    return "", 0.0


def decide_name(samples, model, title_fn=make_title_tracked,
                threshold_s: float = 12.0) -> tuple[str, str]:
    """Decide (slug, origin) for an already-read signal. No I/O, no renaming.

    Shared with the command-line tools, so that recognition there is the same
    as in the application rather than a copy that would drift.
    """
    if samples.size == 0:
        # Empty take (receiver gone before the first block): that is silence,
        # not a failure.
        return SILENCE, ORIGIN_SILENCE

    if looks_like_timecode(samples):
        return ORIGIN_TIMECODE, ORIGIN_TIMECODE

    text, speech_s = _transcribe_sample(model, samples)

    # `title_fn` returns (title, origin); we capture the origin on the way past
    # to tell a model-written title from a fallback on keywords.
    seen_origin = ORIGIN_TRANSCRIPT

    def make_title(t: str) -> str:
        nonlocal seen_origin
        title, seen_origin = title_fn(t)
        return title

    slug = choose_name(text, speech_s, make_title, threshold_s=threshold_s)
    origin = ORIGIN_SILENCE if slug == SILENCE else seen_origin

    if slug == SILENCE and rms_dbfs(samples) > AUDIBLE_DBFS:
        # Audible but with no recognised speech: calling it "silence" would be
        # wrong.
        slug, origin = NO_SPEECH, ORIGIN_NO_SPEECH
    return slug, origin


def name_recording(
    wav_path: Path,
    when: datetime,
    model,
    title_fn=make_title_tracked,
    threshold_s: float = 12.0,
) -> NameResult:
    """Name a recording already on disk. Never loses the file."""
    slug, origin = decide_name(_read_wav(wav_path), model, title_fn, threshold_s)
    # A timecode take is renamed too: otherwise orphan recovery would detect it
    # again at every launch, forever.
    return _renamed(wav_path, when, slug, origin)


def rename_take(path: Path, new_text: str, when: datetime) -> Path:
    """Rename a take on request. An empty name leaves the file untouched."""
    slug = slugify(new_text)
    if not slug:
        return path
    target = unique_path(path.parent,
                         _target_name(path, when, slug, replace_named=True))
    path.rename(target)
    return target
