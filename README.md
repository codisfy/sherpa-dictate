# Sherpa

Sherpa is a private, local Linux desktop app for dictation and text to speech.
It runs the speech models on your computer and keeps the existing command-line
tools available for global keyboard shortcuts and automation.

## Install a release

Sherpa currently supports 64-bit Linux. On Debian, Ubuntu, and related
distributions, download the latest `.deb` from the
[Releases page](https://github.com/codisfy/sherpa-dictate/releases/latest),
then install it with:

```bash
sudo apt install ./sherpa_0.1.2_amd64.deb
```

Open **Sherpa** from the application menu. Speech models are not bundled; the
Models page lets each user download only the models they want and choose where
to store them.

Other Linux distributions can run Sherpa from source using the instructions
below. Windows and macOS input adapters are not implemented yet.

## Run from source

Create the environment and launch the app:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./sherpa
```

The app includes:

- a system-tray menu for starting/stopping dictation and selected-text reading
- live status for both background services
- a model manager with verified official Sherpa-ONNX downloads
- a configurable model storage location
- controls for pause detection, microphone threshold, text insertion, voice,
  and TTS speed
- per-user preferences stored outside the project directory
- automatic global-shortcut registration through the Wayland desktop portal
- a Shortcuts page with native configuration and copyable fallback commands

Install an application-menu launcher for the current checkout:

```bash
./install-desktop.sh
```

To also launch Sherpa in the system tray when you sign in:

```bash
./install-desktop.sh --autostart
```

The current engine integration is Linux-first because global selected-text
capture and typing use Wayland/X11 tools. The PySide6 interface, settings, and
model manager are platform-neutral foundations for future Windows and macOS
input adapters.

## Keyboard shortcuts after installation

On a supported Wayland desktop, opening Sherpa creates a shortcut session and
shows the desktop's native permission/configuration dialog the first time. The
desktop remembers approved bindings. Sherpa restores them on later launches and
keeps them active while it is running in the status bar. Portal version 2
desktops can change them later with **Shortcuts → Configure shortcuts**; older
desktops expose the same bindings in their global shortcut settings.

The Debian package installs the required application identity automatically.
When running from a source checkout, run `./install-desktop.sh` first so the
portal can associate shortcut permissions with Sherpa instead of the terminal.

Suggested defaults are `Ctrl+Alt+D` for continuous dictation,
`Ctrl+Alt+M` for manual dictation, and `Ctrl+Alt+R` for reading selected text.
The desktop dialog has the final say and can replace or leave any binding
unassigned.

Packaged releases also install a stable `sherpa` command. On X11 or a Wayland
desktop without the GlobalShortcuts portal, assign key combinations manually in
the desktop's settings to these fallback commands:

```bash
sherpa action dictation-toggle
sherpa action dictation-start
sherpa action dictation-stop
sherpa action dictation-manual-toggle
sherpa action read-selection
sherpa action tts-stop
```

The Shortcuts tab shows the portal status, opens the native configuration UI,
and provides a Copy button for each fallback command.

## Building a Linux release

```bash
./.venv/bin/pip install -r requirements-dev.txt
./build-release.sh
./build-deb.sh 0.1.2
```

This produces a self-contained application directory and a Debian package in
`dist/`. Models are deliberately excluded and remain optional in-app downloads.

## Command-line tools

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
./dictate reload-settings # apply runtime settings without unloading the model
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

## Read selected text aloud

Sherpa can also keep a small KittenTTS model loaded and read selected text. The
reader has its own daemon and audio output, so it can run at the same time as
microphone dictation:

```bash
./read selection             # read the desktop selection; call again to stop
./read speak "Text to read"  # read text supplied on the command line
printf '%s' "Text" | ./read speak
./read stop
./read status
./read quit                  # unload the TTS model
```

The selection command first tries the Wayland/X11 primary selection. If an
application does not expose it, the command waits for the shortcut keys to be
released, sends `Ctrl+C` with `ydotool` or `xdotool`, and reads the clipboard.
It removes zero-width characters, soft hyphens, document line-end hyphenation,
and layout line breaks before synthesis.

The configured model is the English KittenTTS nano v0.8 INT8 export. Its Sherpa
model directory must contain `model.int8.onnx`, `voices.bin`, `tokens.txt`, and
`espeak-ng-data`.

Install the prepackaged Sherpa model once with:

```bash
mkdir -p ~/.cache/sherpa-onnx/tts
curl -fL -O https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kitten-nano-en-v0_8-int8.tar.bz2
tar -xjf kitten-nano-en-v0_8-int8.tar.bz2 -C ~/.cache/sherpa-onnx/tts
```

For either Parakeet profile, continuous mode buffers one silence-delimited
phrase at a time, then recognizes and inserts it. From the user's perspective,
listening continues between phrases.

## Manual GNOME shortcut fallback

Use this only when Sherpa reports that the GlobalShortcuts portal is unavailable.
Open **Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts**.
Create a shortcut named `Sherpa Dictation` with this command:

```text
sherpa action dictation-toggle
```

Press the shortcut once to begin continuous listening. Each time you finish a
phrase and pause, its text is inserted into the active window. Press
the shortcut again when you are finished with the dictation session.

The original manual start/stop behavior remains available through
`./dictate toggle`.

To read selected text, add another custom shortcut named `Sherpa Reader` with:

```text
sherpa action read-selection
```

Press it once to start reading the current selection. Press it again while
audio is playing to stop. This does not start, stop, or otherwise change an
active dictation session.

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

The expected ydotool socket is `$XDG_RUNTIME_DIR/.ydotool_socket` (normally
`/run/user/<your user ID>/.ydotool_socket`).

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
- `warm_up_model`: run a silent inference when the daemon loads
- `warm_up_output`: connect to `ydotool` before microphone capture begins
- `silence_ms`: pause length that completes a phrase (default: 1500 ms)
- `pre_roll_ms`: audio retained before speech begins, avoiding clipped first words
- `first_phrase_pre_roll_ms`: longer pre-roll used until the first valid phrase
- `min_speech_ms`: ignores clicks and other very short sounds
- `speech_threshold`: RMS level treated as speech; lower it if speech is missed,
  or raise it if background noise triggers phrases
- `max_phrase_seconds`: safety limit for speech without a pause
- `audio_buffer_seconds`: raw microphone buffer used if processing briefly falls behind
- `append_space_after_phrase`: separates successively inserted phrases
- `spoken_punctuation`: enables the exact phrase commands listed above

The `[tts]` table configures the independent reader daemon:

- `model_dir` and the model asset filenames select the KittenTTS export
- `num_threads`: CPU threads used for synthesis
- `speaker_id`: voice number from `0` through `7`
- `speed`: speaking-speed multiplier
- `output_device`: empty for the system default, or a PortAudio device
- `max_text_characters`: safety limit for one selection
- `audio_queue_chunks`: bounded synthesis/playback buffer

The app reloads voice, speed, and audio-output changes for the next reading
without rebuilding the TTS model.

The `[models.*]` tables define each model's backend, directory, and language
settings. The app applies text-insertion changes to a running dictation service
immediately and safely restarts an idle recognizer when model-construction
settings change. If dictation is active, that restart is deferred until its
buffered work has drained. After manually editing configuration, run
`./dictate reload-settings` for runtime dictation settings. Model construction
settings still require `./dictate quit` or `./read quit`; the next command
reloads the affected service.

## Recreate or remove the environment

Recreate:

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Stop before removal:

```bash
./dictate quit
./read quit
```

Deleting this project removes the runtime and script. It does not delete the
shared ASR models under `~/.cache/openwhispr/parakeet-models/` or the TTS model
under `~/.cache/sherpa-onnx/tts/`.

## License

Sherpa's original code is available under the [MIT License](LICENSE).
Third-party components retain their own licenses; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
