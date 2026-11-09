# AIM Glockenspiel Robot — Hardware & Control

An autonomous mechatronic instrument performance platform developed as part of the **AI4Musicians (AIM)** team under Purdue University's **Vertically Integrated Projects (VIP)** program.

This repository houses the embedded firmware, solenoid driver logic, and control interfaces for driving a robotic glockenspiel.


# Project Overview
The goal of this project is to develop a mechatronic glockenspiel performance setup capable of controlled timing, strike velocity, and millisecond-accurate musical execution.

Microcontroller: Raspberry Pi Pico 2 / RP2350

Actuation Strategy: Linear Push/Pull Solenoids with Transistor/MOSFET Driver Circuits

Primary Focus: Pulse duration timing (preventing double-strikes/buzzing), velocity control, and host trigger integration.

# Tech Stack & Hardware Components
Language: MicroPython / C++ (Pico SDK)

Microcontroller: Raspberry Pi RP2350

Peripherals: GPIO (Digital Output, PWM pulse control), Flyback Diodes, Transistor/MOSFET Driver Array

Host Communication: USB / Serial (MIDI Event Triggers


# Sheet music -> solenoid strikes (`sheet_to_solenoid.py`)

Converts a score into a timed list of solenoid strikes (`song.json`) and plays it on the Pi.

**Input:** MusicXML (`.musicxml`, `.xml`, `.mxl`; in MuseScore use *File > Export > MusicXML*) or MIDI (`.mid`, needs `mido`).

**Pin map:** `pins.json` says which note each solenoid hits. It currently has one solenoid (C6 on GPIO17, both placeholders). `pins_full_example.json` is a 17-bar layout for later.

## Test without hardware (laptop or Pi)

```
pip install -r requirements.txt
python sheet_to_solenoid.py info examples/test_score.musicxml
python sheet_to_solenoid.py convert examples/test_score.musicxml --map pins_full_example.json --fold-octaves -o song.json
python sheet_to_solenoid.py preview song.json --wav preview.wav
python sheet_to_solenoid.py play song.json --mock
```

`preview.wav` lets you hear the timing; `play --mock` prints each strike in real time.

## On the Raspberry Pi

```
python3 sheet_to_solenoid.py convert my_song.musicxml -o song.json
python3 sheet_to_solenoid.py play song.json --speed 0.5
```

Useful options for `convert`: `--part N` (pick one instrument), `--transpose N` (semitones), `--fold-octaves` (move notes with no bar to the same note in another octave), `--tempo BPM`.

Pulses are limited to 5-60 ms and every coil is switched off on exit or Ctrl+C.

**Not supported yet:** D.C./D.S./Coda jumps (export MIDI instead), grace notes, scanned PDFs (convert them to MusicXML first with MuseScore or Audiveris).


# Playing with a live conductor (`conductor_bridge.py` + `conducted_play.py`)

Connects Kevin Liu's hand-gesture conductor ([mini-robotic-concert](https://github.com/Kevin-Liu-PU/mini-robotic-concert)) to the robot. The laptop's camera sees the conductor's hand; every downward beat is sent over Wi-Fi to the Pi, and the Pi plays the song one beat at a time. Conduct faster and the robot speeds up; stop and it pauses.

```
laptop: camera -> conductor (mini-robotic-concert) -> conductor_bridge.py --UDP port 5005--> Pi: conducted_play.py -> solenoids
```

- Kevin's code is **not modified**. `conductor_bridge.py` starts it and forwards each detected beat.
- The first 2 beats are a count-in that sets the tempo; the song starts on the 3rd.
- By default the Pi **predicts**: when a beat arrives it schedules the next beat's notes one beat-length later, so they land with the conductor's next beat instead of late (`--mode follow` plays each beat's notes when it arrives instead).
- No beats for about 2.5 beat-lengths = pause. Conduct 2 beats to carry on from the same spot.
- `song.json` now stores each strike's position in beats. **Re-run `convert` for any older `song.json`.**

## 1. Test everything on the laptop (no Pi, no camera)

Two terminals in this folder:

```
python sheet_to_solenoid.py convert hot-cross-buns.mxl --map pins_full_example.json --fold-octaves -o song.json
python conducted_play.py song.json --mock                 # terminal 1: the "robot"
python conductor_bridge.py metronome --bpm 90             # terminal 2: steady beats
python conductor_bridge.py tap                            # or: press SPACE on each beat
```

## 2. Set up the conductor (laptop, Windows, once)

Needs **Python 3.10** (`py -3.10 --version`); MediaPipe doesn't support newer versions yet. Clone it **next to** this folder:

```
cd ..
git clone https://github.com/Kevin-Liu-PU/mini-robotic-concert.git
cd mini-robotic-concert
.\setup.cmd
.\.venv\Scripts\python.exe download_model.py
cd ..\glockenspiel-team
```

Then, with `conducted_play.py song.json --mock` running in another terminal:

```
.\conduct.cmd -- --demo        # Kevin's synthetic hand: 60 BPM, then 120 BPM, then a pause
.\conduct.cmd                  # real camera; show one hand, tap your index finger down through the line
```

Other conductor options go after `--`, e.g. `.\conduct.cmd -- --config ..\mini-robotic-concert\config-responsive.json --camera 1`.

## 3. With the Pi

On the Pi (laptop and Pi on the same Wi-Fi/hotspot):

```
python3 sheet_to_solenoid.py convert hot-cross-buns.mxl --fold-octaves -o song.json
python3 conducted_play.py song.json
```

On the laptop, use the Pi's IP address (`hostname -I` on the Pi):

```
.\conduct.cmd --pi 172.20.10.3
python conductor_bridge.py metronome --bpm 90 --pi 172.20.10.3      # no-camera test
```

Useful `conducted_play.py` options: `--count-in N`, `--mode follow`, `--latency-ms 50` (play earlier if it sounds late), `--loop`, `--port`.

**Limits:** beats only (no dynamics or cues yet); camera detection adds some delay, which predict mode hides only while the tempo is steady; a sudden big tempo change takes a beat to catch up.
