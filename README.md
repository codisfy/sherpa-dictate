# Sherpa Dictation

Small offline voice-typing tool using the existing Nemotron 3.5 or Parakeet
TDT ONNX model. The models are referenced in place and are not copied into this
project.

## Commands

```bash
./dictate continuous   # pauses finalize/paste phrases; call again to end the session
./dictate toggle       # manual mode: first call records; second call transcribes/pastes
./dictate model        # show the selected and available models
./dictate model parakeet
./dictate model nemotron
./dictate start
./dictate stop
./dictate stop --no-paste
./dictate status
./dictate quit         # unload the model and stop the background daemon
./dictate transcribe /path/to/16-bit-pcm.wav
```

The first command starts a local background daemon automatically. The daemon
keeps the model loaded, so later dictations start quickly. Logs are written to
`dictate.log`.

## Choosing a model

Show the current selection:

```bash
./dictate model
```

Select either model:

```bash
./dictate model parakeet
./dictate model nemotron
./dictate model parakeet-en
./dictate model nemotron-en
```

Changing models stops a differently loaded daemon. The selected model loads
when dictation is next started, so the existing keyboard shortcut does not need
to change.

- `nemotron`: multilingual Nemotron 3.5, native streaming.
- `nemotron-en`: English-only Nemotron Speech, native streaming.
- `parakeet`: multilingual Parakeet TDT v3, offline phrase recognition.
- `parakeet-en`: English-only Parakeet Unified, offline phrase recognition.

For either Parakeet profile, continuous mode buffers one silence-delimited
phrase at a time, then recognizes and inserts it. From the user's perspective,
listening continues between phrases.

## GNOME global shortcut

Open **Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts**.
Create a shortcut named `Sherpa Dictation` with this command:

```text
/absolute/path/to/sherpa-dictate/dictate continuous
```

Press the shortcut once to begin continuous listening. Each time you finish a
phrase and pause, its text is inserted into the active window. Press
the shortcut again when you are finished with the dictation session.

The original manual start/stop behavior remains available through
`./dictate toggle`.

## Punctuation and pauses

A pause completes the current recognition phrase, but does not itself mean
punctuation. The selected model's own punctuation is preserved. You can also say one of
these commands as a separate phrase:

- `full stop`
- `comma`
- `question mark`
- `exclamation mark`
- `new line`
- `new paragraph`

For example, say a sentence, pause, say `full stop`, and pause again. The
command is inserted as `.` rather than as the words “full stop.”

## Wayland input behavior

By default, the tool uses `ydotool` to send virtual keyboard input. This works
in terminals and ordinary text fields without using the clipboard. Direct
typing uses the current keyboard layout.

Set `output_method = "clipboard"` to copy each phrase with
`wl-copy --sensitive`, send `Ctrl+Shift+V`, then clear the clipboard after
500 ms. The sensitive hint asks compatible clipboard-history managers not to
save the phrase, while `Ctrl+Shift+V` supports terminal applications.

The sensitive flag and subsequent clear are best-effort: a clipboard manager
that ignores the Wayland sensitive-content hint can still record the text.
`--paste-once` is intentionally not used because applications may request the
clipboard more than once, which can make pasting fail.

The expected ydotool socket is `/run/user/1000/.ydotool_socket`.

## Configuration

Edit `config.toml` to change:

- `active_model`: default selection when `.active-model` does not exist
- `num_threads`: CPU threads used by ONNX Runtime
- `audio_device`: empty for the system default, or a PortAudio device index/name
- `paste_delay_ms`: delay between updating the clipboard and sending `Ctrl+V`
- `output_method`: `clipboard` or `type` for virtual keystrokes
- `typing_key_delay_ms` / `typing_key_hold_ms`: direct-typing speed and key hold
- `clipboard_sensitive`: mark transcription clipboard contents as sensitive
- `clipboard_shortcut`: `ctrl_shift_v` for terminals, or `ctrl_v`
- `clipboard_clear_after_paste_ms`: delay before clearing; `-1` disables clearing
- `silence_ms`: pause length that completes a phrase (default: 900 ms)
- `pre_roll_ms`: audio retained before speech begins, avoiding clipped first words
- `min_speech_ms`: ignores clicks and other very short sounds
- `speech_threshold`: RMS level treated as speech; lower it if speech is missed,
  or raise it if background noise triggers phrases
- `max_phrase_seconds`: safety limit for speech without a pause
- `append_space_after_phrase`: separates successively inserted phrases
- `spoken_punctuation`: enables the exact phrase commands listed above

The `[models.*]` tables define each model's backend, directory, and language
settings. After manually editing configuration, run `./dictate quit`; the next
command reloads it.

## Recreate or remove the environment

Recreate:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Stop before removal:

```bash
./dictate quit
```

Deleting this project removes the runtime and script. It does not delete the
shared models under `~/.cache/openwhispr/parakeet-models/`.
