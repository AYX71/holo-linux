# Holo for Linux

Turn the desk around your laptop into tap-sensitive buttons. Tap near the
laptop, and a classifier listens through the built-in microphone, works out
which of four zones you tapped, and runs a command you assigned to that zone.

> **Status: experimental prototype.** It works well for one zone on the
> author's desk and less reliably for the others. See [Results](#results).
> No accuracy is promised on your hardware: calibrate and run `eval` yourself.

This is an independent Linux port of the idea behind
[JustinGamer191/Holo](https://github.com/JustinGamer191/Holo), a macOS app.
It is not affiliated with that project, and it does not contain its code. It
was written from that project's public description of the pipeline.

## What it does

- Four zones: `left_rear`, `right_rear`, `left_front`, `right_front`
- Runs entirely locally: no network, no cloud, audio is never saved
- One Python file (`holo.py`), two dependencies (`numpy`, `sounddevice`)
- Each zone can run any shell command: screenshots, notifications, opening
  apps, copying text, and so on

## How it works

```
mic stream -> onset detector -> 90 ms window -> clip / sustained-sound gate
           -> features -> ridge classifier + novelty check -> action
```

1. **Onset detection.** An adaptive noise floor is learned from the room. A
   block that is clearly louder than the floor starts a capture, with a short
   refractory period afterwards.
2. **Gating.** Clipped windows and sustained sounds (like speech) are rejected.
3. **Features.** 32 log-spaced spectral bands (100 Hz to 16 kHz, with the
   overall loudness removed) plus a 12-bin temporal envelope.
4. **Classifier.** A closed-form ridge regression over the four zones. A tap is
   rejected if the top score is too low, if the top two zones are too close,
   or if it is too far from any calibration tap.
5. **Action.** The zone's command from `actions.json` runs, with a per-zone
   cooldown.

Rejecting uncertain taps is deliberate: a wrong zone fires the wrong action,
while a rejection just does nothing.

## Results

Measured on one laptop on one desk, using a built-in microphone through
PipeWire, with 10-15 taps per zone per run:

| Run | Overall | Notes |
| --- | --- | --- |
| Eval 1 (60 taps) | 70% | Mostly rejections, not wrong zones |
| Eval 2 (60 taps) | 82% | After recalibrating with more varied taps |
| Eval 3 (60 taps) | 82% | No wrong zones, 11 rejections |
| Eval 4 (60 taps) | 75% | Lower threshold let wrong zones through; reverted |
| Eval 5 (40 taps) | 57% | One zone failed completely in that run |

`right_front` was consistently reliable (87-100%, scores around 0.7-1.3 in live
use). The other zones, especially on the left, were less consistent between
runs. Results vary a lot between runs, so don't read much into any single one.

Known gaps:

- The leave-one-out score printed during calibration is always about 100% and
  does not predict real accuracy. Only `eval` is a real test.
- False triggers from typing, talking, or touching the laptop have not been
  measured. There are no negative examples in the model yet.
- Single microphone channel only.

## Install (Arch Linux)

`python-sounddevice` is not in the official repos, so use a virtual
environment:

```bash
sudo pacman -S python python-numpy python-cffi portaudio pipewire-alsa
git clone https://github.com/AYX71/holo-linux.git
cd holo-linux
python -m venv --system-site-packages ~/.venv
source ~/.venv/bin/activate
pip install sounddevice
```

On other distros, install PortAudio and run `pip install numpy sounddevice`.

## Usage

```bash
python holo.py devices                 # list audio devices
python holo.py calibrate --device N    # tap each zone 15 times
python holo.py eval      --device N    # 40 fresh taps, shows accuracy
python holo.py run       --device N    # listen and run actions
```

Use a PipeWire or `default` input device (not a raw `hw:` device, which is
often busy) and check that it points at the built-in microphone with
`wpctl status`. Turn off any mic gain boost or auto-gain.

**Calibration tips**

- Put the laptop where it will stay. Moving it, or changing desks, means you
  need to recalibrate.
- Spread your taps over each zone's whole area and vary position and force a
  little. Ten identical taps make a profile that rejects real ones.
- Tap firmly and consistently, with a fingertip or knuckle, and pause between
  taps.

## Actions

Create `~/.config/holo/actions.json`. Zones without an entry do nothing.

```json
{
  "right_front": "$HOME/.local/bin/holo-shot.sh"
}
```

Commands run through the shell, so only put commands you trust there.

**Example: screenshot with a notification (KDE/Spectacle)**

`~/.local/bin/holo-shot.sh`:

```sh
#!/bin/sh
f="$HOME/Pictures/Screenshots/holo-$(date +%s%N).png"
if spectacle -f -b -n -o "$f"; then
    notify-send -i camera-photo "Holo" "Screenshot saved: $(basename "$f")"
else
    notify-send -u critical "Holo" "Screenshot failed"
fi
```

Make it executable with `chmod +x`, and `mkdir -p ~/Pictures/Screenshots`.
Other tools: `grim` (Wayland), `gnome-screenshot`, `maim` or `scrot` (X11).

**Optional launcher** (`~/.local/bin/holo`), so you don't need to activate the
virtual environment by hand:

```sh
#!/bin/sh
cd "$HOME/holo-linux" || exit 1
exec "$HOME/.venv/bin/python" holo.py "${@:-run}" --device 11
```

## Tuning

Constants at the top of `holo.py`:

| Constant | Effect |
| --- | --- |
| `MIN_SCORE` | Minimum classifier score. Lowering it lets uncertain taps through, and in testing those were mostly wrong |
| `MIN_MARGIN` | Required gap between best and second-best zone |
| `NOVELTY_FACTOR` | How far from a calibration tap is still accepted |
| `ONSET_RATIO` | How much louder than the room a sound must be to count |
| `SUSTAIN_RATIO` | Rejects speech-like, ringing sounds |
| `COOLDOWN` | Seconds before the same zone can fire again |

## Troubleshooting

- **`ModuleNotFoundError: No module named 'sounddevice'`**: the virtual
  environment is not active. Run `source ~/.venv/bin/activate`.
- **Nothing is detected**: check the default input with `wpctl status`, and
  that the mic is not muted.
- **Many `rejected: low confidence` lines**: recalibrate with more varied taps.
- **No screenshot appears**: run your action script by hand first. On KDE
  Wayland, approve the screen-capture permission prompt once.

## Roadmap

- A "none" class trained on typing, speech, and touches, to reduce false
  triggers
- Stereo features if the microphone is stereo
- The active ultrasonic probe from the original project
- A systemd user service

## Credits and license

Concept and pipeline description from
[JustinGamer191/Holo](https://github.com/JustinGamer191/Holo). Add a license
file of your choice (for example MIT) before publishing.
