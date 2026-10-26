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
