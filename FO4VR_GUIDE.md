# Mantella for Fallout 4 VR - User Guide

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

With `custom_vision_model = True`, a separate cheaper model describes the screenshot as text before passing it to the main LLM. With `custom_vision_model = False`, the screenshot goes directly to the main LLM (requires a vision-capable model a vision-capable model).

## Per-NPC Model Overrides

You can assign different LLMs to different NPCs via `data/npc_model_overrides.json`:

```json
{
  "_example_Nick Valentine": "anthropic/claude-sonnet-4"
  
}
```

NPCs not listed use the global model from config.ini.

## Settings

Config file: `C:\Users\<user>\Documents\My Games\Mantella\config.ini`

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
