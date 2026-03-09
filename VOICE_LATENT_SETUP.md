# Voice Latent Setup for XTTS

This guide explains how to create voice latents (speaker reference files) for the XTTS TTS server used by Mantella.

## Overview

XTTS clones voices from short audio samples. Each NPC voice needs a **speaker reference WAV file** placed in the XTTS server's `speakers/` directory. The server generates latents (voice embeddings) from these files on first use and caches them.

## Directory Structure

On your XTTS server, place WAV files in:

```
xtts-api-server/speakers/
├── cait.wav              # Named NPC voices
├── codsworth.wav
├── nick_valentine.wav
├── piper.wav
├── preston_garvey.wav
├── ...
├── rand_f01.wav          # Generic settler voices (female)
├── rand_f02.wav
├── ...
├── rand_f25.wav
├── rand_m01.wav          # Generic settler voices (male)
├── rand_m02.wav
├── ...
├── rand_m25.wav
```

### Naming Convention

- **Named NPCs**: lowercase, underscores for spaces — e.g. `nick_valentine.wav`, `preston_garvey.wav`
- **Generic settler voices**: `rand_f01` through `rand_f25` (female), `rand_m01` through `rand_m25` (male)
- The filename (without `.wav`) is the speaker name used by Mantella's TTS calls

## Getting Source Audio

You need clean voice samples — ideally 6-15 seconds of a single speaker with no background music or sound effects.

### Option 1: Game audio rips

Extract voice lines from the game's `.ba2` archives using [Archive2](https://www.nexusmods.com/fallout4/mods/78) or [BAE](https://www.nexusmods.com/fallout4/mods/78). Voice files are in `Sound/Voice/Fallout4.esm/`. The `.fuz` files contain both lip sync and audio — extract the `.xwm` audio, then convert.

### Option 2: YouTube / online clips

Download character voice compilations or gameplay clips:

```bash
# Download audio from a YouTube video (requires yt-dlp)
yt-dlp -x --audio-format wav -o "raw_sample.wav" "https://youtube.com/watch?v=VIDEOID"
```

Then trim to a clean segment (see Processing below).

### Option 3: AI voice samples

Sites like [Weights.gg](https://weights.gg) or [Uberduck](https://uberduck.ai) host community voice models. Download short samples of the voice you want.

### Option 4: Any clean audio

For generic settler voices (`rand_*`), you can use any voice actor samples, audiobook clips, or voice recordings. Variety is the goal — each `rand_*` voice should sound distinct.

## Processing with ffmpeg

XTTS requires specific audio format. Convert all source files:

```bash
ffmpeg -i input.wav -ac 1 -ar 22050 -sample_fmt s16 -t 15 output.wav
```

What each flag does:
- `-ac 1` — mono (single channel)
- `-ar 22050` — 22050 Hz sample rate (XTTS native rate)
- `-sample_fmt s16` — 16-bit signed integer samples
- `-t 15` — trim to 15 seconds max (optional, but longer isn't better)

### Batch conversion

Convert all files in a folder:

```bash
mkdir -p processed
for f in raw/*.wav; do
  name=$(basename "$f")
  ffmpeg -i "$f" -ac 1 -ar 22050 -sample_fmt s16 -t 15 "processed/$name"
done
```

On Windows (PowerShell):

```powershell
mkdir processed -Force
Get-ChildItem raw\*.wav | ForEach-Object {
    ffmpeg -i $_.FullName -ac 1 -ar 22050 -sample_fmt s16 -t 15 "processed\$($_.Name)"
}
```

### Trimming a specific segment

If you need to extract a clean section from a longer file:

```bash
# Extract 10 seconds starting at 1:23
ffmpeg -i input.wav -ss 83 -t 10 -ac 1 -ar 22050 -sample_fmt s16 output.wav
```

## Tips for Good Voice Cloning

- **6-15 seconds** of audio is the sweet spot. Longer doesn't help much.
- **Clean speech only** — no music, sound effects, or other speakers
- **Consistent tone** — pick a segment where the speaker sounds natural, not shouting or whispering
- **No silence padding** — trim leading/trailing silence
- **One speaker per file** — if multiple people are talking, cut to just the target voice

## Verifying Setup

After placing WAV files in `speakers/`, restart the XTTS server. You can test a voice with:

```bash
curl -X POST http://YOUR_XTTS_SERVER:8020/tts_to_audio/ \
  -H "Content-Type: application/json" \
  -d '{"text": "Testing one two three.", "speaker_wav": "cait", "language": "en"}'
```

The first request for each speaker will be slow as latents are generated and cached. Subsequent requests are fast.

## How Many Voices?

- **Named NPCs**: One voice per NPC you want to sound distinct. At minimum, set up the companions (Cait, Codsworth, Curie, Danse, Deacon, Hancock, MacCready, Nick Valentine, Piper, Preston Garvey, Strong, X6-88).
- **Generic settlers**: The system supports up to 50 (`rand_f01`–`rand_f25`, `rand_m01`–`rand_m25`). More voices = more variety among your settlers. Even 10 total (5 female + 5 male) gives decent variety.
