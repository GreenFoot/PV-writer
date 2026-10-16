"""Audio chunking and Mistral Voxtral transcription pipeline.

Vendored from dnd_logger@bc2bdf1 src/transcriber.py — keep in sync manually.
Changes: French is forced, segment timestamps are requested and turned into absolute
``[hh:mm:ss]`` line prefixes (chunk and file offsets added), the request falls back
gracefully if the API refuses a parameter combination, and requests are paced by
``rate_limit`` (tokens per minute, requests per second, server ``Retry-After``).
"""

import logging
import os
import re

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QThread, Signal

from .i18n import tr
from .mistral_compat import mistral_class
from .rate_limit import backoff_delay, is_rate_limit_error, transcription_limiter

log = logging.getLogger("pvwriter.transcriber")

TRANSCRIPTION_LANGUAGE = "fr"

# Request variants tried in order: some API versions refuse ``language`` together with
# ``timestamp_granularities``. The first variant that works is remembered for the session.
_REQUEST_VARIANTS = (("language", "timestamps"), ("timestamps",), ("language",))
_working_variant: tuple | None = None  # pylint: disable=invalid-name

_MAX_CONTEXT_BIAS = 100

# First guess of the audio tokens billed per second, refined from the reported usage
_audio_tokens_per_second = 12.5  # pylint: disable=invalid-name


def _audio_seconds(path: str) -> float:
    try:
        return float(sf.info(path).duration)
    except Exception:
        return 0.0


def format_timestamp(seconds: float) -> str:
    """Format a number of seconds as ``hh:mm:ss``."""
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def flac_stem(audio_path: str) -> str:
    """Return a filesystem-safe stem used to name the FLAC files derived from an audio file.

    Several source files can live in the same session directory, so chunk names are
    namespaced per source instead of using fixed ``full_audio.flac`` / ``chunk_NNN.flac``.

    Args:
        audio_path: Path of the source audio file.

    Returns:
        A sanitized stem, never empty.
    """
    stem = os.path.splitext(os.path.basename(audio_path))[0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")
    return stem or "audio"


class AudioChunker:
    """Splits a WAV file into chunks if it exceeds max duration."""

    OVERLAP_SECONDS = 10

    @staticmethod
    def chunk_audio(wav_path: str, max_minutes: int = 150) -> list[str]:
        """Split WAV into chunks with 10s overlap. Returns list of file paths."""
        return [path for path, _start in AudioChunker.chunk_audio_with_offsets(wav_path, max_minutes)]

    @staticmethod
    def chunk_offsets(total_seconds: float, max_minutes: int) -> list[float]:
        """Return the start time (seconds) of every chunk ``chunk_audio_with_offsets`` produces.

        Args:
            total_seconds: Duration of the source audio.
            max_minutes: Maximum chunk length.

        Returns:
            Chunk start offsets in seconds, ``[0.0]`` when no split is needed.
        """
        max_seconds = max_minutes * 60
        if total_seconds <= max_seconds:
            return [0.0]
        offsets = []
        start = 0.0
        while True:
            offsets.append(start)
            end = min(start + max_seconds, total_seconds)
            if end >= total_seconds:
                break
            start = end - AudioChunker.OVERLAP_SECONDS
        return offsets

    @staticmethod
    def chunk_audio_with_offsets(wav_path: str, max_minutes: int = 150) -> list[tuple[str, float]]:
        """Split audio into FLAC chunks with 10s overlap.

        Returns:
            ``(chunk_path, start_seconds)`` pairs, in order.
        """
        info = sf.info(wav_path)
        total_seconds = info.duration
        max_seconds = max_minutes * 60

        sr = info.samplerate
        chunk_dir = os.path.dirname(wav_path)
        stem = flac_stem(wav_path)

        if total_seconds <= max_seconds:
            # Already a FLAC that fits in one request — upload it as-is.
            if os.path.splitext(wav_path)[1].lower() == ".flac":
                return [(wav_path, 0.0)]
            flac_path = os.path.join(chunk_dir, f"{stem}__full.flac")
            data, _ = sf.read(wav_path, dtype="int16")
            sf.write(flac_path, data, sr, format="FLAC")
            return [(flac_path, 0.0)]

        overlap_samples = int(AudioChunker.OVERLAP_SECONDS * sr)
        chunk_samples = int(max_seconds * sr)

        chunks = []
        data, _ = sf.read(wav_path, dtype="int16")

        total_samples = len(data)
        start = 0
        idx = 0

        while start < total_samples:
            end = min(start + chunk_samples, total_samples)
            chunk_data = data[start:end]

            chunk_path = os.path.join(chunk_dir, f"{stem}__chunk_{idx:03d}.flac")
            sf.write(chunk_path, chunk_data, sr, format="FLAC")
            chunks.append((chunk_path, start / float(sr)))

            idx += 1
            start = end - overlap_samples
            if end >= total_samples:
                break

        return chunks


def _strip_stt_artifacts(text: str, min_repeats: int = 5, min_words: int = 2, max_words: int = 15) -> str:
    """Collapse consecutive repetitions of a phrase that Voxtral sometimes emits in loops.

    A phrase of N words (min_words <= N <= max_words) repeated min_repeats times or more in
    immediate succession is reduced to two occurrences (kept rather than one to preserve
    legitimate doublings like "non, non").
    """
    for nwords in range(min_words, max_words + 1):
        pattern = re.compile(r"((?:\b\S+\b[^\w]*){" + str(nwords) + r"})(\1){" + str(min_repeats - 1) + r",}")
        text = pattern.sub(r"\1\1", text)
    return text


def build_context_bias(raw_bias) -> list[str]:
    """Turn glossary entries into the token form the Voxtral ``context_bias`` accepts.

    Args:
        raw_bias: Iterable of glossary terms (names, acronyms, places).

    Returns:
        At most 100 de-duplicated entries made of letters, digits, ``_`` and ``-``.
    """
    valid = re.compile(r"^[\w-]+$")
    context_bias = []
    for b in raw_bias or []:
        entry = b.strip().replace(" ", "_").replace("&", "").replace("'", "_").replace("’", "_")
        if entry and valid.match(entry) and entry not in context_bias:
            context_bias.append(entry)
    return context_bias[:_MAX_CONTEXT_BIAS]


def _format_segments(segments, offset: float) -> str:
    """Render API segments as ``[hh:mm:ss] [speaker] text`` lines, with absolute times."""
    lines = []
    for seg in segments:
        text = _strip_stt_artifacts(str(getattr(seg, "text", "") or "").strip())
        if not text:
            continue
        start = getattr(seg, "start", None)
        prefix = f"[{format_timestamp(offset + float(start))}] " if start is not None else ""
        speaker = getattr(seg, "speaker", None) or getattr(seg, "speaker_id", None)
        if speaker is not None:
            prefix += f"[{speaker}] "
        lines.append(prefix + text)
    return "\n".join(lines)


def _is_parameter_error(err_str: str) -> bool:
    """Return True when an API error looks like a rejected parameter combination."""
    return any(code in err_str for code in ("400", "422")) or "not compatible" in err_str.lower()


def _transcribe_file(client, chunk_path: str, config: dict, offset: float = 0.0, retries: int = 5) -> str:
    """Transcribe a single audio file using Mistral Voxtral API with retry logic.

    Args:
        client: Open Mistral client.
        chunk_path: Audio file to upload.
        config: App configuration (model, glossary, diarization).
        offset: Start of this file within the meeting recording, in seconds.
        retries: Attempts on rate limiting (429).

    Returns:
        Timestamped lines when the API returns segments, else plain text.
    """
    global _working_variant, _audio_tokens_per_second  # pylint: disable=global-statement
    context_bias = build_context_bias(config.get("context_bias", []))
    model = config.get("transcription_model", "voxtral-mini-latest")
    diarize = config.get("diarize", False)
    limiter = transcription_limiter(config, model)
    audio_seconds = _audio_seconds(chunk_path)

    variants = [_working_variant] if _working_variant else list(_REQUEST_VARIANTS)
    for v_index, variant in enumerate(variants):
        for attempt in range(retries):
            reservation = limiter.acquire(int(audio_seconds * _audio_tokens_per_second))
            try:
                with open(chunk_path, "rb") as f:
                    kwargs = dict(
                        model=model,
                        file={"file_name": os.path.basename(chunk_path), "content": f},
                        context_bias=context_bias or None,
                        diarize=diarize,
                    )
                    if "language" in variant:
                        kwargs["language"] = TRANSCRIPTION_LANGUAGE
                    if "timestamps" in variant:
                        kwargs["timestamp_granularities"] = ["segment"]
                    result = client.audio.transcriptions.complete(**kwargs)

                usage = getattr(result, "usage", None)
                used = getattr(usage, "total_tokens", None) or getattr(usage, "prompt_tokens", None)
                limiter.record(reservation, used)
                log.info("Transcribed %.0f s of audio, usage: %s", audio_seconds, usage)
                if used and audio_seconds >= 30:
                    _audio_tokens_per_second = used / audio_seconds

                if _working_variant is None:
                    _working_variant = variant
                    log.info("Transcription request variant in use: %s", "+".join(variant))

                segments = getattr(result, "segments", None)
                if segments:
                    return _format_segments(segments, offset)
                text = result.text if hasattr(result, "text") else str(result)
                return _strip_stt_artifacts(text)

            except Exception as e:
                err_str = str(e)
                if "401" in err_str:
                    raise RuntimeError(tr("transcriber.error.invalid_key"))
                if is_rate_limit_error(e) and attempt < retries - 1:
                    delay = backoff_delay(e, attempt)
                    log.warning("Voxtral 429, retrying in %.0f s", delay)
                    limiter.penalize(delay)
                    continue
                if _is_parameter_error(err_str) and v_index < len(variants) - 1:
                    log.warning("Variant %s refused (%s), trying the next one", "+".join(variant), err_str[:200])
                    break
                raise
    return ""


TRANSCRIPT_EXTENSIONS = {".txt", ".md", ".text"}


def is_transcript_file(path: str) -> bool:
    """Return True if path is an already-written transcript rather than audio."""
    return os.path.splitext(path)[1].lower() in TRANSCRIPT_EXTENSIONS


def read_transcript_file(path: str) -> str:
    """Read an existing transcript file, tolerating non-UTF-8 encodings.

    Transcripts written by other tools on Windows are often cp1252 rather than UTF-8,
    so fall back through the usual suspects instead of producing replacement chars.

    Args:
        path: Path of the transcript file.

    Returns:
        The file contents.
    """
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    # latin-1 maps every byte, so this last attempt cannot fail
    with open(path, "r", encoding="latin-1") as f:
        return f.read()


class TranscriptionWorker(QObject):
    """Runs transcription in a QThread via Mistral Voxtral API."""

    progress = Signal(int, int)  # current, total
    file_started = Signal(int, int, str)  # file index (1-based), file count, file name
    chunk_completed = Signal(int, str)  # index, text
    completed = Signal(str)  # full transcript
    error = Signal(str)

    def __init__(self, wav_paths, config: dict):
        """Initialize the worker.

        Args:
            wav_paths: A single file path, or a list of paths processed in the given
                order (one session recorded across several files). Audio files are
                transcribed; existing transcript files (.txt/.md) are inserted as-is,
                so audio and transcripts can be mixed freely in one batch.
            config: Campaign configuration dict.
        """
        super().__init__()
        self._wav_paths = [wav_paths] if isinstance(wav_paths, str) else list(wav_paths)
        self._config = config

    def run(self):
        """Build the session transcript from every input file, in order."""
        client = None
        try:
            max_minutes = self._config.get("chunk_duration_minutes", 150)
            paths = self._wav_paths

            # The API is only needed when at least one input is audio.
            if any(not is_transcript_file(path) for path in paths):
                api_key = self._config.get("api_key", "")
                if not api_key:
                    self.error.emit(tr("transcriber.error.no_api_key"))
                    return
                client = mistral_class()(api_key=api_key)

            # Chunk every audio file up front so progress covers the whole batch.
            per_file_chunks = [
                [] if is_transcript_file(path) else AudioChunker.chunk_audio_with_offsets(path, max_minutes)
                for path in paths
            ]
            # Absolute times: every audio file starts where the previous ones ended
            file_offsets = []
            elapsed = 0.0
            for path in paths:
                file_offsets.append(elapsed)
                if not is_transcript_file(path):
                    elapsed += sf.info(path).duration
            total = sum(len(chunks) for chunks in per_file_chunks)
            file_count = len(paths)
            full_text_parts = []
            done = 0

            log.info("Transcription batch: %d file(s), %d audio chunk(s)", file_count, total)

            for file_index, (path, chunks, file_offset) in enumerate(zip(paths, per_file_chunks, file_offsets)):
                log.info(
                    "File %d/%d: %s (%d chunk(s))", file_index + 1, file_count, os.path.basename(path), len(chunks)
                )
                if file_count > 1:
                    self.file_started.emit(file_index + 1, file_count, os.path.basename(path))

                if is_transcript_file(path):
                    # Already transcribed by someone else — take it verbatim, and in
                    # particular do not run the STT artifact cleanup over it.
                    text = read_transcript_file(path).strip()
                    log.info("  read existing transcript, %d chars", len(text))
                    full_text_parts.append(text)
                    # No chunk_completed here: an imported transcript can be hundreds of
                    # KB, and completed() sets the full text in the display anyway, so
                    # appending it first would only build the same document twice.
                    continue

                file_parts = []
                for chunk_path, chunk_start in chunks:
                    done += 1
                    self.progress.emit(done, total)
                    text = _transcribe_file(client, chunk_path, self._config, offset=file_offset + chunk_start)
                    log.info("  chunk %d/%d transcribed, %d chars", done, total, len(text))
                    file_parts.append(text)
                    self.chunk_completed.emit(done - 1, text)
                # Artifact cleanup already ran per segment / per chunk in _transcribe_file
                full_text_parts.append("\n".join(file_parts))

            full_text = "\n\n".join(part for part in full_text_parts if part.strip())

            # Save transcript next to the first input file (all files share a session dir)
            session_dir = os.path.dirname(paths[0])
            transcript_path = os.path.join(session_dir, "transcript.txt")
            with open(transcript_path, "w", encoding="utf-8") as f:
                f.write(full_text)

            log.info("Transcript written to %s (%d chars)", transcript_path, len(full_text))
            self.completed.emit(full_text)

        except Exception as e:
            log.exception("Transcription failed")
            self.error.emit(tr("transcriber.error.transcription", error=e))

        finally:
            # Release the connection pool and event loop instead of leaving them
            # to the garbage collector, which tears them down in bursts.
            if client is not None:
                client.__exit__(None, None, None)


class LiveTranscriptionWorker(QObject):
    """Transcribes a single audio chunk for live/incremental transcription."""

    completed = Signal(str)  # transcribed text
    error = Signal(str)

    def __init__(self, source, config: dict):
        """Initialize the worker.

        Args:
            source: Either the path of an existing FLAC chunk, or a
                ``(blocks, samplerate, flac_path, start_seconds)`` tuple handed over by
                :meth:`AudioRecorder.take_pending_audio`, in which case the FLAC is
                encoded here rather than on the GUI thread.
            config: Campaign configuration dict.
        """
        super().__init__()
        self._source = source
        self._config = config
        self._offset = 0.0 if isinstance(source, str) else float(source[3])

    def _encode_source(self) -> str:
        """Return the FLAC path to upload, encoding the pending buffer if needed."""
        if isinstance(self._source, str):
            return self._source
        blocks, samplerate, flac_path, _start = self._source
        sf.write(flac_path, np.concatenate(blocks), samplerate, format="FLAC")
        return flac_path

    def run(self):
        """Transcribe a single audio chunk and emit the result."""
        flac_path = None
        try:
            mistral_cls = mistral_class()
            api_key = self._config.get("api_key", "")
            if not api_key:
                self.error.emit(tr("transcriber.error.no_api_key_short"))
                return

            flac_path = self._encode_source()
            # Closing the client releases its connection pool and event loop. Left to
            # the garbage collector they pile up and are torn down in bursts instead.
            with mistral_cls(api_key=api_key) as client:
                text = _transcribe_file(client, flac_path, self._config, offset=self._offset)

            self.completed.emit(text)

        except Exception as e:
            self.error.emit(tr("transcriber.error.live_transcription", error=e))

        finally:
            # Clean up the temp FLAC chunk whether or not the upload succeeded
            if flac_path:
                try:
                    os.remove(flac_path)
                except OSError:
                    pass


def start_transcription(wav_paths, config: dict) -> tuple[QThread, TranscriptionWorker]:
    """Create a transcription worker in a new thread.

    Args:
        wav_paths: A single path, or an ordered list of audio and/or transcript paths.
        config: Campaign configuration dict.

    Returns:
        The (thread, worker) pair; the caller starts the thread.
    """
    thread = QThread()
    worker = TranscriptionWorker(wav_paths, config)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.completed.connect(thread.quit)
    worker.error.connect(thread.quit)
    return thread, worker


def start_live_transcription(source, config: dict) -> tuple[QThread, LiveTranscriptionWorker]:
    """Create a live transcription worker in a new thread.

    Args:
        source: A FLAC path, or the tuple returned by ``take_pending_audio()``.
        config: Campaign configuration dict.

    Returns:
        The (thread, worker) pair; the caller starts the thread.
    """
    thread = QThread()
    worker = LiveTranscriptionWorker(source, config)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.completed.connect(thread.quit)
    worker.error.connect(thread.quit)
    return thread, worker
