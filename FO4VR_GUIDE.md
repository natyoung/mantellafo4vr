# Mantella for Fallout 4 VR - User Guide

This is a fork of [Mantella](https://github.com/art-from-the-machine/Mantella) (via [slavkovsky77's FO4VR branch](https://github.com/slavkovsky77/Mantella)) with 100+ commits of Fallout 4 VR improvements. It is not affiliated with the upstream project.

## What This Fork Adds

### Stability & Threading (core fixes)
- Fixed SentenceQueue deadlock between `clear()` and `get_next_sentence()`
- Fixed stale generation threads preventing new LLM responses
- Fixed race conditions in `__update_context`, `load_character()`, `player_input()`
- Fixed concurrent `start_conversation` requests stacking and corrupting state
- Fixed server deadlock when remote TTS is unreachable on startup
- Added timeouts to all external calls to prevent permanent hangs
- Added `asyncio.to_thread()` for blocking route handlers to unblock the event loop
- Fixed topicID race condition causing duplicate audio in multi-NPC conversations
- Debounced `start_conversation` to prevent rapid-fire restarts from Papyrus

### FO4VR-Specific Fixes
- Fixed voice delivery pipeline (FUZ generation and file copy)
- Fixed stale F4SE_HTTP queue killing new conversations
- Fixed stale STT/TTS responses leaking between conversations
- Forced topicID alternation to prevent FO4's aggressive audio caching
- Skip action-only responses (Papyrus can't handle them in FO4VR)
- Removed `fo4_` voice prefix that caused XTTS 500 errors
- Guarded `onnxruntime` import against DLL load failures (VR environment)

### Generic NPC Identity System (new feature)
- Settlers, Residents, Scavengers, etc. get persistent unique names, voices, and bios
- ~250 lore-appropriate Wasteland names, 50 dedicated TTS voices (`rand_f01`–`rand_f25`, `rand_m01`–`rand_m25`)
- Bios assembled from personality traits, occupations, and backstory fragments
- Identities persist across sessions via JSON registry keyed by reference ID
- Original game name preserved for Papyrus communication; assigned name used for LLM

### Quest Awareness (new feature)
- Full pipeline: Papyrus sends quest FormIDs → game checks stages → Mantella enriches from wiki database → LLM receives quest context
- NPCs know which quests they're involved in, current status, and stage details
- Wiki database with 889 characters, 299 quests, 44,892 pages from Fallout wiki

### Vision System (enhanced)
- Enabled vision action for Fallout 4 (ON_DEMAND mode — not every turn)
- Speech-triggered vision: say "look at this", "check this out", etc.
- Vision fires on silence timeout so NPCs comment on surroundings
- Improved vision prompt to ignore HUD elements (power armor dials, compass, notifications)
- Fixed crash when vision LLM call fails (`UnboundLocalError` on `async_client`)

### Conversation Memory (enhanced)
- SQLite-based conversation storage (messages saved on the fly, not just at conversation end)
- Removed redundant JSON file writes — DB is the single source of truth
- First-person NPC memory prompts ("I remember..." not "The assistant summarized...")
- "Days since last spoke" injected into NPC context (only after 7+ in-game days)
- Summary recall: player says "summary" or "recap" to hear past interactions
- Orphaned conversation recovery (auto-summarizes if game crashed mid-conversation)
- Memory paraphrasing instruction so NPCs don't read summaries back verbatim

### Silence Auto-Response (enhanced)
- Fixed race condition where events refresh prevented timeout from ever firing
- Changed prompt from literal `*says nothing*` to natural NPC dialogue prompt
- Coupled with vision system — NPC describes surroundings when player is silent

### LLM Improvements
- Per-NPC model overrides via `data/npc_model_overrides.json`
- Truncation detection: appends "...should I go on?" when response hits max tokens
- Better event filtering: debounce location spam, filter sit/stand, equip spam, mod spell hits
- Disabled loot/deposit/workshop events that derailed conversations
- Angle brackets as narration indicators for LLM emote stripping
- Whisper STT prompt includes all NPC names (not just first) + companion name corrections

### Documentation & Tooling
- FO4VR user guide, architecture docs, improvement roadmap
- Startup/shutdown convenience scripts
- LLM debug logging per conversation folder

## Quick Start

1. **Launch Mantella** before starting FO4VR:
   ```
   cd <your Mantella folder>
   .venv\Scripts\activate
   python main.py
   ```
2. **Launch Fallout 4 VR** (with MantellaMod installed via your mod manager)
3. **In-game**: Look at an NPC and press the **grip trigger** to start talking

## Starting a Conversation

Look at an NPC and press the **grip trigger** on your VR controller to start a Mantella conversation. (The **A button** opens the normal game dialogue instead.) A notification appears: *"Starting conversation with [NPC Name]"*

The NPC will greet you automatically — just wait for them to speak.

## Talking to NPCs

Once a conversation starts, a loop begins:

1. **NPC speaks** — you hear their voice (synthesized via your TTS engine)
2. **"Listening..."** appears — speak into your microphone
3. **Your speech is transcribed** and sent to the AI
4. **"Thinking..."** appears — the AI generates the NPC's response
5. Back to step 1

Just talk naturally. The mic listens automatically and detects when you stop speaking.

## Ending a Conversation

Say **"goodbye"** (or "bye", "good bye", etc.) and the NPC will respond with a farewell and the conversation ends.

You can also add custom end keywords in your config (see Settings below).

## Controls

- **Start conversation**: Grip trigger on NPC (A button = normal game dialogue)
- **Talk**: Just speak — mic listens automatically
- **End conversation**: Say "goodbye"

No buttons needed during conversation — it's fully voice-driven in VR.

## Vision

When `vision_enabled = True` in config, NPCs can "see" what's on screen via a screenshot sent to the LLM.

**Trigger phrases** — say any of these to make the NPC look:
- "look at this" / "look at that"
- "check this out"
- "see this" / "see that"
- "what do you see" / "what can you see"
- "take a look" / "have a look"

Vision also fires automatically if you stay silent for 2 minutes (silence auto-response). The NPC will comment on their surroundings.

With `custom_vision_model = True`, a separate cheaper model describes the screenshot as text before passing it to the main LLM. This works with any NPC model, even ones that don't support images. With `custom_vision_model = False`, the screenshot goes directly to the main LLM (requires a vision-capable model).

## Quest Awareness

NPCs are aware of quests they're involved in. When you talk to an NPC, Mantella checks which quests are associated with them and their current status (running, completed, etc.).

### How it works

1. **Papyrus** sends quest FormIDs for each NPC in the conversation
2. **The game** checks each quest's current stage and sends back status info
3. **Mantella** enriches this with detailed quest descriptions from a wiki database (walkthrough text, stage descriptions, locations)
4. **The LLM** receives this as context, so NPCs can reference quests naturally in conversation

For example, if you've completed "Benign Intervention" with Cait, she knows about her recovery and can reference it. If you're mid-way through "The First Step" with Preston, he knows what stage you're at.

### Wiki database

Quest details come from `data/Fallout4/wiki.db` — a SQLite database with 889 characters, 299 quests, and 44,892 wiki pages scraped from the Fallout wiki. This ships with the mod and doesn't need any setup.

To regenerate the database (only needed if you want to update it):
```
pip install mediawiki-dump wikitextparser
python -m src.wiki.dump_parser --full
```

## Generic NPC Identity System

In vanilla Fallout 4, settlement NPCs all share generic names like "Settler" or "Resident". Mantella gives each one a unique persistent identity:

- **Unique name** — drawn from a pool of ~250 lore-appropriate Wasteland names (male and female)
- **Unique voice** — assigned from 50 dedicated TTS voices (`rand_f01`–`rand_f25` for female, `rand_m01`–`rand_m25` for male)
- **Unique bio** — assembled from personality traits, occupations, and backstory fragments (e.g. *"Eloise Kraft is a quiet and observant human wastelander. She spends most days teaching younger settlers. Eloise Kraft left Diamond City because she couldn't afford to stay."*)

Identities are **persistent** — the same settler always has the same name, voice, and personality across sessions. The registry is stored in `Documents\My Games\Mantella\generic_npc_registry.json`, keyed by the NPC's unique reference ID.

### Which NPCs get identities?

Any NPC with one of these generic game names: Settler, Resident, Scavenger, Provisioner, Caravan Guard, Trader, Merchant, Farmer, Guard, Minuteman, Militia, Refugee, Wastelander, Drifter, Traveler.

Named NPCs (Cait, Preston, etc.) are unaffected — they keep their original names and use their configured voice.

### Voice setup

The 50 `rand_*` voices need to be set up on your TTS server with voice latents/samples. If a voice isn't available, the TTS will fall back to a default. The more voices you set up, the more variety your settlers will have.

## Per-NPC Model Overrides

You can assign different LLMs to different NPCs via `data/npc_model_overrides.json`:

```json
{
  "_example_Nick Valentine": "anthropic/claude-sonnet-4"
  
}
```

NPCs not listed use the global model from config.ini.

## Settings

Config file: `Documents\My Games\Mantella\config.ini`

You can also access settings in-game via the **Mantella Settings Holotape** in your inventory.

### Settings You Might Want to Change

| Setting | Default | What it does |
|---------|---------|-------------|
| `audio_threshold` | 0.4 | Mic sensitivity (0-1). Raise if picking up background noise |
| `allow_interruption` | True | Whether you can interrupt NPCs mid-sentence |
| `silence_auto_response_enabled` | False | If True, NPC talks again if you're silent too long |
| `silence_auto_response_timeout` | 30.0 | Seconds of silence before auto-response triggers |
| `vision_enabled` | False | If True, NPCs can see screenshots when triggered |
| `custom_vision_model` | False | Use a separate vision LLM instead of the main one |
| `end_conversation_keyword` | goodbye, bye... | Words that end the conversation |
| `goodbye_npc_response` | Safe travels | What the NPC says when you leave |

## Troubleshooting

**NPC just stares / no response:**
- Check the Mantella terminal window for errors
- Make sure your TTS server is running
- Restart Mantella and try again

**NPC says the same line on repeat:**
- Your `fallout4vr_mod_folder` path in config.ini is wrong
- It must point to the MantellaMod folder containing a `Sound` subfolder

**Mic not picking up speech:**
- Check Windows sound settings — make sure your VR headset mic is the default
- Try lowering `audio_threshold` (e.g. 0.2)
- Set `save_mic_input = True` in config to save recordings for debugging

**Conversation hangs on "Thinking...":**
- The LLM service might be slow — wait a moment
- Check your internet connection
- Check the Mantella terminal for timeout errors

**No audio from NPC:**
- Verify your TTS server is running and reachable
- Check FO4VR game volume isn't muted
