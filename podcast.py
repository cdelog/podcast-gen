# /// script
# requires-python = "==3.12.*"
# dependencies = [
#   "qwen-tts==0.1.1", "torch==2.9.1", "torchaudio==2.9.1",
#   "numpy==2.5.4", "lameenc==1.9.0", "pypdf==6.1.3",
# ]
# [tool.uv.sources]
# torch = { index = "pytorch-cu128" }
# torchaudio = { index = "pytorch-cu128" }
# [[tool.uv.index]]
# name = "pytorch-cu128"
# url = "https://download.pytorch.org/whl/cu128"
# explicit = true
# ///
"""NotebookLM-style podcasts, locally: source material -> dialog script (LLM via OpenRouter) -> audio (Qwen3-TTS).

    uv run podcast.py make notes.pdf https://example.com/article [--speakers 3] [--minutes 8]
    uv run podcast.py draft notes.pdf -o episode.txt      # script only, edit it, then:
    uv run podcast.py render episode.txt [-o out.mp3]
    uv run podcast.py voices

Drafting sends the source text to OpenRouter (OPENROUTER_API_KEY). Rendering runs on the local GPU.
The script format and the writing rules are in README.md and prompt.md.
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import urllib.request
import wave
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
SR = 24000                      # Qwen3-TTS outputs 24 kHz mono
FRAME = SR // 50                # 20 ms analysis frames
MAX_SPEAKERS = 3
TTS_MODEL = os.environ.get("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
LLM_MODEL = os.environ.get("PODCAST_LLM", "anthropic/claude-sonnet-5.5")
CACHE = HERE / ".tts-cache"
BATCH = int(os.environ.get("QWEN_TTS_BATCH", "4"))

# Qwen3-TTS CustomVoice speakers (model card descriptions). Only Aiden and Ryan are native English.
VOICES = {
    "aiden": "sunny American male, clear midrange (native English)",
    "ryan": "dynamic male, strong rhythmic drive (native English)",
    "serena": "warm, gentle young female (native Chinese)",
    "vivian": "bright, slightly edgy young female (native Chinese)",
    "sohee": "warm Korean female, rich emotion (native Korean)",
    "ono_anna": "playful Japanese female, light and nimble (native Japanese)",
    "uncle_fu": "seasoned male, low mellow timbre (native Chinese)",
    "dylan": "youthful Beijing male, clear natural timbre (native Chinese)",
    "eric": "lively Chengdu male, slightly husky (native Chinese)",
}
DEFAULT_VOICES = ["serena", "aiden", "ryan"]
BASE_STYLE = "A relaxed, unscripted-sounding podcast conversation at a natural pace."

# Leading "(tag)" delivery cues -> (instruction for Qwen, gain dB). Any other leading tag is passed to Qwen as-is.
DELIVERY = {
    "fast": ("Speak a little faster than usual.", 0), "slow": ("Speak slowly and deliberately.", 0),
    "soft": ("Speak softly, close to the mic.", -4), "excited": ("Sound excited and energetic.", 1),
    "thoughtful": ("Sound thoughtful, working it out while speaking.", 0), "dry": ("Dry, deadpan delivery.", 0),
    "emphatic": ("Emphatic, leaning on the key words.", 1),
}
LAUGHS = {"laughs": "Ha ha!", "chuckles": "Heh."}
OVER = {"over", "overlapping"}
BACKCHANNEL_DB = -5             # a backchannel sits under the main speaker

NAME_RE = re.compile(r"^([A-Za-z][\w .'-]{0,24}):\s*(.*)$")
LEAD_RE = re.compile(r"^\(([^)]*)\)\s*")
CUE_RE = re.compile(r"\[(pause|laughs|chuckles)(?:\s+(\d+(?:\.\d+)?))?\]", re.I)
DASHES = ("--", "\u2014")


@dataclass
class Line:
    speaker: str
    voice: str
    style: str = ""             # speaker persona from the front matter
    cues: list = field(default_factory=list)    # per-line delivery instructions
    gain: float = 0.0           # dB, applied after loudness levelling
    over: bool = False          # backchannel: lands in a phrase gap of the previous turn
    latch: bool = False         # starts right on the previous turn's heels
    cut: bool = False           # gets interrupted: the next turn barges in before it ends
    parts: list = field(default_factory=list)   # ("say", text) | ("pause", seconds)

    @property
    def instruct(self):
        cues = self.cues + (["A quick, quiet listener reaction."] if self.over else [])
        return " ".join(s for s in [BASE_STYLE, self.style and f"Speaker: {self.style}.", *cues] if s)


# ---------------------------------------------------------------- parsing

def voice_spec(val, n):
    voice, _, style = (s.strip() for s in val.partition(","))
    voice = voice.lower()
    if voice and voice not in VOICES:
        raise ValueError(f"line {n}: unknown voice {voice!r}; choose from {', '.join(VOICES)}")
    return {"voice": voice, "style": style}


def add_say(line, text):
    text = " ".join(re.sub(r"\[[^\]]*\]", " ", text).split())   # unknown [cues] are dropped
    if re.search(r"\w", text):
        line.parts.append(("say", text))


def parse(src):
    """Script text -> (title, [Line]). Raises ValueError naming the line on bad input."""
    rows = src.splitlines()
    title, cast, start = "", {}, 0
    if rows and rows[0].strip() == "---":
        end = next((i for i in range(1, len(rows)) if rows[i].strip() == "---"), None)
        if end is None:
            raise ValueError("front matter has no closing '---'")
        section = None
        for n, raw in enumerate(rows[1:end], 2):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            key, _, val = (s.strip() for s in raw.partition(":"))
            if raw[0].isspace() and section == "speakers":
                cast[key.upper()] = voice_spec(val, n)
            else:
                section = key.lower()
                if section == "title":
                    title = val
        start = end + 1
    if len(cast) > MAX_SPEAKERS:
        raise ValueError(f"{len(cast)} speakers declared; the maximum is {MAX_SPEAKERS}")
    declared = bool(cast)

    def free_voice():
        used = {c["voice"] for c in cast.values()}
        return next(v for v in DEFAULT_VOICES + list(VOICES) if v not in used)

    for spec in cast.values():
        spec["voice"] = spec["voice"] or free_voice()

    lines = []
    for n, raw in enumerate(rows[start:], start + 1):
        s = raw.replace("*", "").strip()            # tolerate **NAME:** markdown from LLM drafts
        if not s or s.startswith("#"):
            continue
        m = NAME_RE.match(s)
        if not m or not m.group(2).strip():
            raise ValueError(f"line {n}: expected 'NAME: what they say', got {s[:50]!r}")
        name = m.group(1).strip().upper()
        if name not in cast:
            if declared:
                raise ValueError(f"line {n}: {name} is not in the front-matter speakers list")
            if len(cast) == MAX_SPEAKERS:
                raise ValueError(f"line {n}: {name} would be speaker {MAX_SPEAKERS + 1}; the maximum is {MAX_SPEAKERS}")
            cast[name] = {"voice": free_voice(), "style": ""}
        spec = cast[name]
        line = Line(name, spec["voice"], spec["style"])

        text = m.group(2).strip()
        while True:                                  # leading (tags) and a latching dash, in any order
            if lm := LEAD_RE.match(text):
                for tag in (t.strip() for t in lm.group(1).split(",")):
                    key = tag.lower()
                    if key in OVER:
                        line.over = True
                    elif key in LAUGHS:
                        line.parts.append(("say", LAUGHS[key]))
                    elif key in DELIVERY:
                        cue, g = DELIVERY[key]
                        line.cues.append(cue)
                        line.gain += g
                    elif tag:
                        line.cues.append(f"Delivery: {tag}.")   # free-form, e.g. (skeptical) or (amused, half-laughing)
                text = text[lm.end():]
            elif text.startswith(DASHES):
                line.latch, text = True, text.lstrip("-\u2014 ")
            else:
                break
        if text.endswith(DASHES):
            line.cut, text = True, text.rstrip("-\u2014 ")
        text = re.sub(r"\([^)]*\)", " ", text).replace("--", "\u2014")   # inline (...) = stage direction

        pos = 0
        for cm in CUE_RE.finditer(text):
            add_say(line, text[pos:cm.start()])
            kind = cm.group(1).lower()
            line.parts.append(("pause", float(cm.group(2) or 0.6)) if kind == "pause" else ("say", LAUGHS[kind]))
            pos = cm.end()
        add_say(line, text[pos:])
        if not any(k == "say" for k, _ in line.parts):
            raise ValueError(f"line {n}: nothing left to say after removing cues")
        lines.append(line)
    if not lines:
        raise ValueError("script has no dialog lines")
    return title, lines


# ---------------------------------------------------------------- audio

def frame_rms(x):
    n = len(x) // FRAME
    return np.sqrt(np.mean(x[:n * FRAME].reshape(n, FRAME) ** 2, axis=1)) if n else np.zeros(0)


def trim(x, thresh=0.01, pad=0.03):
    """Cut the TTS engine's own leading/trailing silence so the turn gaps are ours."""
    loud = np.flatnonzero(np.abs(x) > thresh)
    if not loud.size:
        return x[:0]
    p = int(pad * SR)
    return x[max(loud[0] - p, 0):loud[-1] + p]


def level(x, gain_db=0.0, target_db=-20.0):
    """Bring every turn to one speech loudness (RMS over voiced frames), then apply its gain."""
    fr = frame_rms(x)
    voiced = fr[fr > 0.01]
    if not voiced.size:
        return x
    return x * (10 ** ((target_db + gain_db) / 20) / np.sqrt(np.mean(voiced ** 2)))


def phrase_gaps(x, min_gap=0.12):
    """Start times (s) of quiet stretches inside a turn (commas, breaths, [pause]s): where a backchannel lands."""
    fr = frame_rms(x)
    if not fr.size:
        return []
    gaps, run = [], 0
    for i, quiet in enumerate(np.append(fr < 0.15 * fr.max(), False)):
        if quiet:
            run += 1
        else:
            if run * FRAME / SR >= min_gap:
                gaps.append((i - run) * FRAME / SR)
            run = 0
    return gaps


def ends_with_question(line):
    return [t for k, t in line.parts if k == "say"][-1].endswith("?")


def clip_key(line, text):
    return (line.voice, line.instruct, text)


def render_line(line, clips):
    out, last = [], None
    for kind, val in line.parts:
        if kind == "pause":
            out.append(np.zeros(int(val * SR), np.float32))
        else:
            if last == "say":
                out.append(np.zeros(int(0.08 * SR), np.float32))
            out.append(clips[clip_key(line, val)])
        last = kind
    x = level(np.concatenate(out), line.gain + (BACKCHANNEL_DB if line.over else 0))
    if line.cut:                                     # interrupted speakers trail off, not stop dead
        n = min(int(0.08 * SR), len(x))
        x[len(x) - n:] *= np.linspace(1, 0, n, dtype=np.float32)
    return x


def timeline(lines, clips, rng):
    """Start time (s) of every line: human turn-taking instead of fixed gaps."""
    starts, cursor, prev = [], 0.4, None             # prev = index of the last main (non-backchannel) turn
    bc_tail = False                                  # a backchannel outlasted the turn it sat under
    for i, (line, clip) in enumerate(zip(lines, clips)):
        dur = len(clip) / SR
        if line.over and prev is not None and lines[prev].speaker != line.speaker:
            pdur = len(clips[prev]) / SR
            aim = rng.uniform(0.45, 0.75) * pdur
            gaps = [g for g in phrase_gaps(clips[prev]) if 0.3 * pdur <= g <= pdur - 0.35]
            off = min(gaps, key=lambda g: abs(g - aim)) + 0.05 if gaps else pdur - 0.15
            starts.append(starts[prev] + max(off, 0.0))
            bc_tail = starts[-1] + dur > cursor
            cursor = max(cursor, starts[-1] + dur)
            continue
        gap = 0.0
        if prev is not None:
            p = lines[prev]
            if p.cut:
                gap = -rng.uniform(0.12, 0.25)       # barge in over the tail
            elif line.latch:
                gap = rng.uniform(0.0, 0.05)
            elif p.speaker == line.speaker and not bc_tail:
                gap = rng.uniform(0.35, 0.65)        # same speaker, new thought
            elif ends_with_question(p):
                gap = rng.uniform(0.10, 0.28)        # answers come fast
            else:
                gap = rng.uniform(0.15, 0.40)
        starts.append(max(cursor + gap, 0.0))
        cursor, prev, bc_tail = starts[-1] + dur, i, False
    return starts


def mixdown(starts, clips, tail=0.8):
    # ponytail: the whole episode is mixed in RAM (~58 MB per 10 min at 24 kHz float32); stream if episodes run to hours.
    end = max(s + len(c) / SR for s, c in zip(starts, clips))
    mix = np.zeros(int((end + tail) * SR) + 1, np.float32)
    for s, c in zip(starts, clips):
        i = int(s * SR)
        mix[i:i + len(c)] += c
    peak = float(np.abs(mix).max())
    return mix * (0.95 / peak) if peak > 0.95 else mix


def write_audio(path, mix):
    pcm = (np.clip(mix, -1, 1) * 32767).astype("<i2").tobytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm)
        return
    import lameenc
    enc = lameenc.Encoder()
    enc.set_bit_rate(96)
    enc.set_in_sample_rate(SR)
    enc.set_channels(1)
    enc.set_quality(2)
    path.write_bytes(enc.encode(pcm) + enc.flush())


# ---------------------------------------------------------------- TTS (Qwen3-TTS, local GPU)

def max_seconds(text):
    """Longest plausible clip for this text: 80 words/min plus slack. Longer means the model ran away."""
    return 2.0 + 0.75 * len(text.split())


def synthesize(jobs):
    """{(voice, instruct, text)} -> {job: float32 clip}. Disk-cached, so a re-render only pays for edited lines."""
    CACHE.mkdir(exist_ok=True)
    paths = {j: CACHE / (hashlib.sha1(repr((TTS_MODEL, j)).encode()).hexdigest() + ".npy") for j in jobs}
    todo = sorted((j for j, p in paths.items() if not p.exists()), key=lambda j: len(j[2]))   # similar lengths batch well
    print(f"{len(jobs)} clips, {len(jobs) - len(todo)} cached, synthesizing {len(todo)}...", flush=True)
    if todo:
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        import torch
        from qwen_tts import Qwen3TTSModel
        if not torch.cuda.is_available():
            raise RuntimeError("Qwen3-TTS needs an NVIDIA GPU with CUDA; torch sees none")
        # float16, not the model card's bfloat16: Turing cards (RTX 20xx) have no native bf16.
        dtype = torch.bfloat16 if torch.cuda.get_device_capability()[0] >= 8 else torch.float16
        model = Qwen3TTSModel.from_pretrained(TTS_MODEL, device_map="cuda:0", dtype=dtype, attn_implementation="sdpa")

        def generate(batch):
            wavs, sr = model.generate_custom_voice(
                text=[t for _, _, t in batch], language=["English"] * len(batch),
                speaker=[v for v, _, _ in batch], instruct=[s for _, s, _ in batch])
            out = []
            for job, w in zip(batch, wavs):
                x = np.asarray(w, dtype=np.float32).reshape(-1)
                if sr != SR:
                    x = np.interp(np.arange(0, len(x), sr / SR), np.arange(len(x)), x).astype(np.float32)
                if not np.isfinite(x).all():
                    raise RuntimeError(f"TTS produced NaN audio for {job[2][:60]!r}")
                out.append(trim(x))
            return out

        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            for job, x in zip(batch, generate(batch)):
                limit = max_seconds(job[2])
                for _ in range(2):                   # runaway generation (e.g. laughing for 30 s): resample
                    if len(x) <= limit * SR:
                        break
                    x = generate([job])[0]
                if len(x) > limit * SR:
                    print(f"  trimmed a runaway clip to {limit:.1f}s: {job[2][:50]!r}", flush=True)
                    x = x[:int(limit * SR)].copy()
                    x[-int(0.1 * SR):] *= np.linspace(1, 0, int(0.1 * SR), dtype=np.float32)
                np.save(paths[job], x)
            print(f"  {min(i + BATCH, len(todo))}/{len(todo)}", flush=True)
        del model
        torch.cuda.empty_cache()
    return {j: np.load(p) for j, p in paths.items()}


def render(script, out, seed):
    title, lines = parse(script.read_text(encoding="utf-8-sig"))
    clips = synthesize({clip_key(l, t) for l in lines for k, t in l.parts if k == "say"})
    line_clips = [render_line(l, clips) for l in lines]
    mix = mixdown(timeline(lines, line_clips, random.Random(seed)), line_clips)
    out = out or script.with_suffix(".mp3")
    write_audio(out, mix)
    cast = ", ".join(sorted({f"{l.speaker}={l.voice}" for l in lines}))
    print(f"wrote {out}: {title or script.stem}, {len(mix) / SR / 60:.1f} min, {len(lines)} turns ({cast})")


# ---------------------------------------------------------------- drafting (any content -> script, via OpenRouter)

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form"}

    def __init__(self):
        super().__init__()
        self.out, self.depth = [], 0

    def handle_starttag(self, tag, attrs):
        self.depth += tag in self.SKIP

    def handle_endtag(self, tag):
        self.depth -= tag in self.SKIP and self.depth > 0

    def handle_data(self, data):
        if not self.depth and data.strip():
            self.out.append(data.strip())


def read_source(src):
    """A URL, PDF or any text file -> plain text."""
    if re.match(r"https?://", src):
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0 podcast-gen"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
        if "html" not in r.headers.get_content_type():
            return body
        p = _Text()
        p.feed(body)
        return "\n".join(p.out)
    path = Path(src)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(encoding="utf-8", errors="replace")


def chat(messages, model):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("set OPENROUTER_API_KEY (https://openrouter.ai/keys) to draft scripts")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps({"model": model, "messages": messages}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "X-Title": "podcast-gen"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenRouter {e.code}: {e.read().decode(errors='replace')[:300]}") from e
    if "error" in resp:
        raise RuntimeError(f"OpenRouter: {resp['error']}")
    return resp["choices"][0]["message"]["content"]


def draft(sources, speakers, minutes, focus, model):
    rules = (HERE / "prompt.md").read_text(encoding="utf-8")
    voices = "\n".join(f"- {v}: {d}" for v, d in VOICES.items())
    docs = "\n\n".join(f"<source name={s!r}>\n{read_source(s).strip()}\n</source>" for s in sources)
    ask = (f"Write a {speakers}-speaker episode of about {minutes} minutes (roughly {minutes * 150} spoken words)."
           + (f" Focus: {focus}." if focus else "") + f"\n\nVoices:\n{voices}\n\n{docs}")
    messages = [{"role": "system", "content": rules}, {"role": "user", "content": ask}]
    print(f"drafting with {model} from {len(sources)} source(s), {len(docs):,} chars...", flush=True)
    for attempt in range(2):
        script = re.sub(r"^```\w*\n|\n```$", "", chat(messages, model).strip())   # models sometimes fence it
        try:
            _, lines = parse(script)
            print(f"draft: {len(lines)} turns, {len(script.split()):,} words")
            return script
        except ValueError as e:
            if attempt:
                raise RuntimeError(f"draft still invalid after a retry ({e}); fix it by hand:\n\n{script}") from e
            messages += [{"role": "assistant", "content": script},
                         {"role": "user", "content": f"That script fails the format check: {e}. Return the full corrected script only."}]


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(description="NotebookLM-style podcasts: sources -> script (OpenRouter) -> audio (Qwen3-TTS).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = argparse.ArgumentParser(add_help=False)
    d.add_argument("sources", nargs="+", help="files (.txt .md .pdf ...) and/or URLs")
    d.add_argument("--speakers", type=int, default=2, choices=range(1, MAX_SPEAKERS + 1))
    d.add_argument("--minutes", type=int, default=8)
    d.add_argument("--focus", help="angle or audience, e.g. 'for plant managers, skip the history'")
    d.add_argument("--model", default=LLM_MODEL, help=f"OpenRouter model id (default {LLM_MODEL})")
    r = argparse.ArgumentParser(add_help=False)
    r.add_argument("--seed", type=int, default=7, help="timing randomness; same seed, same mix")
    sub.add_parser("draft", parents=[d], help="write a script from sources").add_argument("-o", "--out", type=Path)
    m = sub.add_parser("make", parents=[d, r], help="draft a script, then render it")
    m.add_argument("-o", "--out", type=Path, help="output .mp3/.wav; the script is saved next to it as .txt")
    rp = sub.add_parser("render", parents=[r], help="turn a script into audio")
    rp.add_argument("script", type=Path)
    rp.add_argument("-o", "--out", type=Path, help="output .mp3 or .wav (default: next to the script)")
    sub.add_parser("voices", help="list voices")
    a = ap.parse_args()
    try:
        if a.cmd == "voices":
            for v, desc in VOICES.items():
                print(f"{v:<10}{desc}")
        elif a.cmd == "render":
            render(a.script, a.out, a.seed)
        else:
            script = draft(a.sources, a.speakers, a.minutes, a.focus, a.model)
            first = Path(a.sources[0]).stem if not re.match(r"https?://", a.sources[0]) else "episode"
            path = a.out.with_suffix(".txt") if a.out else Path(f"{first}-podcast.txt")
            path.write_text(script + "\n", encoding="utf-8")
            print(f"wrote {path}")
            if a.cmd == "make":
                render(path, a.out, a.seed)
    except (ValueError, RuntimeError, OSError) as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
