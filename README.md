<a id="readme-top"></a>

[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]

<br />
<div align="center">
  <h3 align="center">podcast-gen</h3>

  <p align="center">
    NotebookLM-style podcasts, made locally: hand it documents or URLs, get back a two- or three-host episode.
    <br />
    <br />
    <a href="#usage">Usage</a>
    &middot;
    <a href="https://github.com/cdelog/podcast-gen/issues/new?labels=bug">Report Bug</a>
    &middot;
    <a href="https://github.com/cdelog/podcast-gen/issues/new?labels=enhancement">Request Feature</a>
  </p>
</div>

<details>
  <summary>Table of Contents</summary>
  <ol>
    <li>
      <a href="#about-the-project">About The Project</a>
      <ul>
        <li><a href="#built-with">Built With</a></li>
        <li><a href="#pipeline">Pipeline</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
      </ul>
    </li>
    <li>
      <a href="#usage">Usage</a>
      <ul>
        <li><a href="#script-format">Script format</a></li>
        <li><a href="#examples">Examples</a></li>
      </ul>
    </li>
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>

## About The Project

podcast-gen is a local take on NotebookLM's Audio Overviews. An LLM on
OpenRouter reads your sources and writes a conversation script, then
Qwen3-TTS performs it on your own GPU. The mixer adds the things that make two
voices sound like a conversation: varied gaps between turns, barge-ins,
latched replies, quiet "mm-hmm"s under the other speaker, and a delivery
instruction on every line.

The source text leaves your machine only for the drafting step. Audio is
generated locally.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Built With

* [![Python][Python-shield]][Python-url]
* [![PyTorch][PyTorch-shield]][PyTorch-url]
* [Qwen3-TTS 1.7B CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)
* [OpenRouter](https://openrouter.ai)
* [uv](https://docs.astral.sh/uv/)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Pipeline

1. **Read.** `.pdf` (text layer only, no OCR), URLs (HTML reduced to text),
   any other file as plain text. Several sources can go into one episode.
2. **Draft.** The sources and `prompt.md`, the writing rules, go to an
   OpenRouter model (default `anthropic/claude-sonnet-5.5`; change it with
   `--model` or `PODCAST_LLM`). The reply must pass the script parser; if it
   doesn't, the model gets the error and one retry. A 10-page source costs a
   few cents.
3. **Render.** Qwen3-TTS, 4 lines per batch. Each line gets an instruction
   built from the speaker's persona and the line's tags. A clip that runs far
   longer than its text (the model sometimes laughs for 30 s) is regenerated,
   then cut.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Getting Started

### Prerequisites

* An NVIDIA GPU with about 4 GB of free VRAM. On an RTX 2060 Super, a
  7-minute episode takes about 4.5 minutes to render.
* [uv](https://docs.astral.sh/uv/getting-started/installation/). It installs
  Python 3.12 and every package on first run, so there is no separate install
  step.
* An [OpenRouter API key](https://openrouter.ai/keys), for drafting only.
  Rendering an existing script needs no key.

### Installation

1. Clone the repo
   ```sh
   git clone https://github.com/cdelog/podcast-gen.git
   cd podcast-gen
   ```
2. Set your OpenRouter key
   ```powershell
   $env:OPENROUTER_API_KEY = "sk-or-..."
   ```
3. Run the offline self-check
   ```sh
   uv run test_podcast.py
   ```

The first render downloads CUDA PyTorch and qwen-tts (about 3 GB) and the
model into the Hugging Face cache (about 4 GB). "SoX could not be found!" at
startup is harmless; nothing here uses SoX.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Usage

```powershell
uv run podcast.py make notes.pdf https://example.com/post --speakers 3 --minutes 8 -o out\ep1.mp3
uv run podcast.py draft notes.pdf -o ep1.txt              # script only; edit it, then
uv run podcast.py render ep1.txt                          # -> ep1.mp3 (.wav via -o)
uv run podcast.py voices
```

`make` saves the script next to the audio, so you can fix a line and
`render` again. Every clip is cached in `.tts-cache/`, so only edited lines
are regenerated. `--focus` sets an angle or audience, for example
`--focus "for plant managers, skip the history"`.

### Script format

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

### Examples

`examples/` has three industrial-automation episode scripts: the PLC, air
gaps, and OPC UA vs MQTT. Audio isn't in the repo; render one with
`uv run podcast.py render examples/plc-refuses-to-die.txt`.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Roadmap

- [x] Script format with up to 3 speakers, backchannels, barge-ins and pauses
- [x] Local rendering with Qwen3-TTS
- [x] Drafting from PDFs, URLs and text through OpenRouter
- [ ] Native-English female voices (Qwen VoiceDesign + Base voice cloning)
- [ ] OCR for scanned PDFs

Known limits:

- Each line is generated on its own, so delivery doesn't carry across turns
  the way it does in a model that generates the whole conversation at once.
- Laughs are spoken "Ha ha!", not recorded laughter. Keep them rare.
- Pages that build their text with JavaScript come out empty; save them as
  text or PDF.

See the [open issues](https://github.com/cdelog/podcast-gen/issues) for more.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Contributing

Pull requests are welcome. For anything large, open an issue first.

1. Fork the project
2. Create your feature branch (`git checkout -b feature/my-change`)
3. Run `uv run test_podcast.py`
4. Commit your changes and push the branch
5. Open a pull request

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## License

Distributed under the MIT License. See `LICENSE` for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Contact

Open an issue: [github.com/cdelog/podcast-gen/issues](https://github.com/cdelog/podcast-gen/issues)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

## Acknowledgments

* [Google NotebookLM](https://notebooklm.google) Audio Overviews, the inspiration
* [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) by the Qwen team
* [Best-README-Template](https://github.com/othneildrew/Best-README-Template)
* [Shields.io](https://shields.io)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

[contributors-shield]: https://img.shields.io/github/contributors/cdelog/podcast-gen.svg?style=for-the-badge
[contributors-url]: https://github.com/cdelog/podcast-gen/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/cdelog/podcast-gen.svg?style=for-the-badge
[forks-url]: https://github.com/cdelog/podcast-gen/network/members
[stars-shield]: https://img.shields.io/github/stars/cdelog/podcast-gen.svg?style=for-the-badge
[stars-url]: https://github.com/cdelog/podcast-gen/stargazers
[issues-shield]: https://img.shields.io/github/issues/cdelog/podcast-gen.svg?style=for-the-badge
[issues-url]: https://github.com/cdelog/podcast-gen/issues
[license-shield]: https://img.shields.io/github/license/cdelog/podcast-gen.svg?style=for-the-badge
[license-url]: https://github.com/cdelog/podcast-gen/blob/main/LICENSE
[Python-shield]: https://img.shields.io/badge/Python_3.12-3776AB?style=for-the-badge&logo=python&logoColor=white
[Python-url]: https://www.python.org/
[PyTorch-shield]: https://img.shields.io/badge/PyTorch-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white
[PyTorch-url]: https://pytorch.org/
