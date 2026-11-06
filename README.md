# podcast-gen

A local take on NotebookLM's Audio Overviews. Give it documents or URLs and it
writes a two- or three-person podcast script with an LLM through OpenRouter,
then performs it on your own GPU with Qwen3-TTS: varied turn gaps, barge-ins,
latched replies, quiet "mm-hmm"s under the other speaker, and per-line
delivery instructions.

```powershell
$env:OPENROUTER_API_KEY = "sk-or-..."                      # https://openrouter.ai/keys
uv run podcast.py make notes.pdf https://example.com/post --speakers 3 --minutes 8 -o out\ep1.mp3
uv run podcast.py draft notes.pdf -o ep1.txt              # script only; edit it, then
uv run podcast.py render ep1.txt                          # -> ep1.mp3 (.wav via -o)
uv run podcast.py voices
uv run test_podcast.py                                    # offline self-check
```

`make` saves the script next to the audio, so you can fix a line and
`render` again. Rendering caches every clip in `.tts-cache\`, so only edited
lines are regenerated.

## Pipeline

1. **Read.** `.pdf` (text layer only, no OCR), URLs (HTML reduced to text),
   any other file as plain text. Several sources can go into one episode.
2. **Draft.** The sources and `prompt.md`, the writing rules, go to an
   OpenRouter model (default `anthropic/claude-sonnet-5.5`; change it with
   `--model` or `PODCAST_LLM`). The reply must pass the script parser; if it
   doesn't, the model gets the error and one retry. `--focus` sets an angle
   or audience. A 10-page source costs a few cents.
3. **Render.** Qwen3-TTS 1.7B CustomVoice, 4 lines per batch. Each line gets
   an instruction built from the speaker's persona and the line's tags. A
   clip that runs far longer than its text (the model sometimes laughs for
   30 s) is regenerated, then cut.

The source text leaves the machine only in step 2. Audio is generated locally.

## Setup

- An NVIDIA GPU with about 4 GB free VRAM. On an RTX 2060 Super, a 7-minute
  episode takes about 4.5 minutes to render.
- First run: `uv` installs Python 3.12, CUDA PyTorch and qwen-tts (about 3 GB),
  and the model downloads to the Hugging Face cache (about 4 GB).
- "SoX could not be found!" at startup is harmless; nothing here uses SoX.

## Script format

```
---
title: Why the PLC Refuses to Die
speakers:
  MAYA: serena, curious and quick to react
  THEO: aiden, calm explainer with a dry sense of humor
---
MAYA: So I have a confession to make.
THEO: (over) Oh no.
MAYA: (thoughtful) I thought ladder logic was a museum piece. [pause] I was wrong.
THEO: [laughs] Most people are, at first. It reads inputs, solves the logic--
MAYA: --and writes the outputs. Every few milliseconds?
```

- One turn per line: `NAME: text`. Blank lines and `# comments` are skipped.
- Front matter is optional. Each speaker is `NAME: voice, persona`. The
  persona is a short description of how they sound, and Qwen reads it as a
  delivery instruction on every line. Without front matter, the first three
  names get Serena, Aiden and Ryan. A 4th speaker is an error.
- Voices (`voices` lists them): only **aiden** and **ryan** are native English
  speakers. The female voices (serena, vivian, sohee, ono_anna) are native
  Chinese, Korean or Japanese speakers and may carry an accent.
- `(...)` mid-line is a silent stage direction. At the start of a line it sets
  the delivery, and tags combine as `(over, soft)`:

| Tag | Effect |
|---|---|
| `(over)` | Backchannel: plays under the previous speaker, in their nearest phrase gap, a bit quieter |
| `(thoughtful)` `(dry)` `(excited)` `(soft)` `(emphatic)` `(fast)` `(slow)` | A tuned instruction to Qwen, plus a small level change for soft, excited and emphatic |
| anything else, e.g. `(skeptical)` | Passed to Qwen as "Delivery: skeptical." |
| `(laughs)` `(chuckles)` | Opens the line with a laugh |

| Inline | Effect |
|---|---|
| `[pause]` / `[pause 1.2]` | Silence inside the turn (default 0.6 s) |
| `[laughs]` / `[chuckles]` | A laugh at that point |
| line ends with `--` or `—` | Cut off: the next speaker barges in over the tail |
| line starts with `--` or `—` | Latched: picks up instantly, finishing the thought |

Other `[cues]` are dropped. `prompt.md` holds the rules for writing scripts
that sound like a conversation; edit it to change the show's style.

## Examples

`examples\` has three industrial-automation episode scripts: the PLC, air
gaps, and OPC UA vs MQTT. Audio isn't in the repo; render one with
`uv run podcast.py render examples\plc-refuses-to-die.txt`.

## Limits

- Each line is generated on its own, so delivery doesn't carry across turns
  the way it does in a model that generates the whole conversation at once.
- Laughs are spoken "Ha ha!", not recorded laughter. Keep them rare.
- Scanned PDFs need OCR first. Pages that build their text with JavaScript
  come out empty; save them as text or PDF.
