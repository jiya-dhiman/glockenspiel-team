#!/usr/bin/env python3
"""
sheet_to_solenoid.py - turn sheet music into timed solenoid strikes for the glockenspiel robot.

Input
  MusicXML  (.musicxml / .xml / .mxl)  - "Export > MusicXML" from MuseScore, Finale, Sibelius, Dorico...
  MIDI      (.mid / .midi)             - needs:  pip install mido

Output
  A JSON "strike list": one entry per hit, with time (seconds), note, GPIO pin and pulse length.

Commands
  info     score.musicxml                         list parts/tracks in the file
  convert  score.musicxml -o song.json            make the strike list
             [--map pins.json] [--part 1,2] [--transpose N] [--fold-octaves] [--tempo BPM]
  preview  song.json [--wav preview.wav]          print the schedule; optionally render audio to hear it
  play     song.json [--mock] [--fast] [--speed X] fire the solenoids (gpiozero on the Pi)

Only "convert", "preview" and "play --mock" are needed to test without a Pi.

What the MusicXML reader handles
  notes, rests, chords, multiple voices (backup/forward), ties (tied notes are struck once),
  tempo marks and tempo changes, simple repeats with 1st/2nd endings.
  Grace notes are skipped. D.C. / D.S. / Coda jumps are NOT followed (a warning is printed);
  exporting to MIDI from your notation program expands those for you.
"""
import argparse
import bisect
import json
import math
import re
import struct
import sys
import time
import wave
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from fractions import Fraction

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
STEP_TO_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
MIN_PULSE_MS, MAX_PULSE_MS = 5, 60          # same safety limits as one_note.py
DEFAULT_BPM = 120


# --------------------------------------------------------------------------- note names

def midi_to_name(m):
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


def name_to_midi(name):
    """'C6' -> 84, 'F#5' -> 78, 'Bb6' -> 94."""
    s = name.strip()
    step = s[0].upper()
    if step not in STEP_TO_SEMI:
        raise ValueError(f"bad note name: {name!r}")
    i, alter = 1, 0
    while i < len(s) and s[i] in "#b":
        alter += 1 if s[i] == "#" else -1
        i += 1
    return (int(s[i:]) + 1) * 12 + STEP_TO_SEMI[step] + alter


def clamp_pulse(ms):
    return max(MIN_PULSE_MS, min(MAX_PULSE_MS, int(ms)))


# --------------------------------------------------------------------------- pin map

def load_pin_map(path):
    with open(path) as f:
        cfg = json.load(f)
    default_pulse = clamp_pulse(cfg.get("pulse_ms", 20))
    notes, used_pins = {}, {}
    for name, v in cfg.get("notes", {}).items():
        if name.startswith("_"):
            continue
        if isinstance(v, int):
            pin, pulse = v, default_pulse
        else:
            pin, pulse = int(v["pin"]), clamp_pulse(v.get("pulse_ms", default_pulse))
        if pin in used_pins:
            sys.exit(f"pin map error: GPIO{pin} is used by both {used_pins[pin]} and {name}")
        used_pins[pin] = name
        notes[name_to_midi(name)] = (pin, pulse)
    if not notes:
        sys.exit(f"pin map error: no notes defined in {path}")
    return {
        "notes": notes,
        "min_gap_ms": cfg.get("min_gap_ms", 60),
        "max_simultaneous": cfg.get("max_simultaneous", 4),
    }


# --------------------------------------------------------------------------- MusicXML

def _read_musicxml_root(path):
    if path.lower().endswith(".mxl"):
        with zipfile.ZipFile(path) as z:
            try:
                container = _strip_ns(ET.fromstring(z.read("META-INF/container.xml")))
                inner = container.find(".//rootfile").get("full-path")
            except KeyError:
                inner = next(n for n in z.namelist()
                             if n.endswith((".xml", ".musicxml")) and not n.startswith("META-INF"))
            data = z.read(inner)
    else:
        with open(path, "rb") as f:
            data = f.read()
    root = _strip_ns(ET.fromstring(data))
    if root.tag == "score-timewise":
        sys.exit("score-timewise MusicXML isn't supported; re-export as normal (partwise) MusicXML.")
    return root


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _part_names(root):
    names = {}
    for sp in root.findall("part-list/score-part"):
        names[sp.get("id")] = (sp.findtext("part-name") or "").strip() or sp.get("id")
    return names


def _ending_numbers(text):
    return {int(x) for x in re.findall(r"\d+", text or "")}


def _barline_items(measure, child, attr, value):
    return [e for bl in measure.findall("barline") for e in bl.findall(child) if e.get(attr) == value]


def _measure_play_order(measures, warnings):
    """Expand simple repeats and numbered endings into a list of measure indexes."""
    order, n = [], len(measures)
    i, repeat_start, passno = 0, 0, 1
    jumps = Counter()
    guard = 0
    while i < n:
        guard += 1
        if guard > 50 * n + 100:
            warnings.append("repeat structure too complicated; stopped expanding repeats")
            break
        m = measures[i]
        starts = _barline_items(m, "ending", "type", "start")
        if starts:
            nums = _ending_numbers(starts[0].get("number"))
            if nums and passno not in nums:            # skip this ending on this pass
                j = i
                while j < n and not (_barline_items(measures[j], "ending", "type", "stop")
                                     or _barline_items(measures[j], "ending", "type", "discontinue")):
                    j += 1
                i = j + 1
                continue
        if _barline_items(m, "repeat", "direction", "forward"):
            repeat_start = i
        order.append(i)
        back = _barline_items(m, "repeat", "direction", "backward")
        if back:
            times = int(back[0].get("times", "2"))
            if jumps[i] < times - 1:
                jumps[i] += 1
                passno += 1
                i = repeat_start
                continue
            passno, repeat_start = 1, i + 1
        i += 1
    return order


_BEAT_UNIT = {"whole": 4, "half": 2, "quarter": 1, "eighth": 0.5, "16th": 0.25, "32nd": 0.125}


def _tempo_from(el):
    """Tempo in quarter notes per minute from a <direction> or <sound> element, or None."""
    snd = el if el.tag == "sound" else el.find("sound")
    if snd is not None and snd.get("tempo"):
        return float(snd.get("tempo"))
    met = el.find("direction-type/metronome")
    if met is not None:
        pm = re.search(r"[\d.]+", met.findtext("per-minute") or "")
        unit = _BEAT_UNIT.get(met.findtext("beat-unit") or "quarter")
        if pm and unit:
            factor = unit * (1.5 if met.find("beat-unit-dot") is not None else 1)
            return float(pm.group()) * factor
    return None


def _parse_part(part, warnings):
    measures = part.findall("measure")
    for m in measures:
        for snd in m.iter("sound"):
            if any(snd.get(a) for a in ("dacapo", "dalsegno", "tocoda", "fine")):
                warnings.append("score has D.C./D.S./Coda/Fine jumps; these are not followed "
                                "(export MIDI instead if you need them)")
                break
    order = _measure_play_order(measures, warnings)
    divisions = 1
    t = last_start = Fraction(0)
    notes, tempos, graces = [], [], 0
    for idx in order:
        for el in measures[idx]:
            tag = el.tag
            if tag == "attributes":
                d = el.findtext("divisions")
                if d:
                    divisions = int(d)
            elif tag in ("direction", "sound"):
                bpm = _tempo_from(el)
                if bpm:
                    tempos.append((t, bpm))
            elif tag == "backup":
                t -= Fraction(el.findtext("duration", "0")) / divisions
            elif tag == "forward":
                t += Fraction(el.findtext("duration", "0")) / divisions
            elif tag == "note":
                if el.find("grace") is not None:
                    graces += 1
                    continue
                if el.find("cue") is not None:
                    continue
                dur = Fraction(el.findtext("duration", "0")) / divisions
                if el.find("chord") is not None:
                    start = last_start
                else:
                    start = last_start = t
                    t += dur
                pitch = el.find("pitch")
                if el.find("rest") is not None or pitch is None:
                    continue
                if any(tie.get("type") == "stop" for tie in el.findall("tie")):
                    continue                            # continuation of a tied note: don't re-strike
                midi = ((int(pitch.findtext("octave")) + 1) * 12
                        + STEP_TO_SEMI[pitch.findtext("step").strip()]
                        + round(float(pitch.findtext("alter", "0"))))
                notes.append((start, midi))
    return notes, tempos, graces


def _beats_to_seconds(tempos, default_bpm):
    by_beat = {}
    for b, bpm in tempos:
        by_beat[b] = bpm
    tm = sorted(by_beat.items())
    if not tm or tm[0][0] > 0:
        tm.insert(0, (Fraction(0), default_bpm))
    beats = [b for b, _ in tm]
    cum = [0.0]
    for (b0, bpm0), (b1, _) in zip(tm, tm[1:]):
        cum.append(cum[-1] + float(b1 - b0) * 60.0 / bpm0)

    def convert(beat):
        k = max(0, bisect.bisect_right(beats, beat) - 1)
        b0, bpm = tm[k]
        return cum[k] + float(beat - b0) * 60.0 / bpm
    return convert


def _select(items, selector, kind):
    """items: list of (id, name). selector: None or '1,2' / 'P1' / part name."""
    if not selector:
        return list(range(len(items)))
    chosen = []
    for s in selector.split(","):
        s = s.strip()
        if s.isdigit() and 1 <= int(s) <= len(items):
            chosen.append(int(s) - 1)
            continue
        hits = [i for i, (pid, name) in enumerate(items) if s == pid or s.lower() in name.lower()]
        if not hits:
            sys.exit(f"no {kind} matches {s!r}; run 'info' to list them")
        chosen.extend(hits)
    return sorted(set(chosen))


def load_musicxml(path, part_selector=None, tempo_override=None):
    root = _read_musicxml_root(path)
    names = _part_names(root)
    parts = root.findall("part")
    items = [(p.get("id"), names.get(p.get("id"), p.get("id"))) for p in parts]
    chosen = _select(items, part_selector, "part")
    warnings, all_tempos, beat_notes, graces = [], [], [], 0
    for i, part in enumerate(parts):
        notes, tempos, g = _parse_part(part, warnings)
        all_tempos += tempos                            # tempo marks often live only in part 1
        if i in chosen:
            beat_notes += notes
            graces += g
    if graces:
        warnings.append(f"skipped {graces} grace note(s)")
    if tempo_override:
        to_sec = _beats_to_seconds([], tempo_override)
    else:
        if not all_tempos:
            warnings.append(f"no tempo marking found; using {DEFAULT_BPM} BPM (override with --tempo)")
        to_sec = _beats_to_seconds(all_tempos, DEFAULT_BPM)
    notes = [(to_sec(b), m) for b, m in beat_notes]
    return notes, [items[i][1] for i in chosen], sorted(set(warnings))


def list_musicxml(path):
    root = _read_musicxml_root(path)
    names = _part_names(root)
    for i, p in enumerate(root.findall("part"), 1):
        pid = p.get("id")
        print(f"  part {i}: {names.get(pid, pid)}  (id {pid}, {len(p.findall('measure'))} measures)")


# --------------------------------------------------------------------------- MIDI

def _mido():
    try:
        import mido
        return mido
    except ImportError:
        sys.exit("MIDI input needs the 'mido' package:  pip install mido")


def load_midi(path, track_selector=None, tempo_override=None):
    mido = _mido()
    mid = mido.MidiFile(path)
    items = [(str(i + 1), t.name or f"track {i + 1}") for i, t in enumerate(mid.tracks)]
    chosen = _select(items, track_selector, "track")
    note_tracks = set(chosen)       # track 1 is always read too, since it usually holds the tempo
    tagged = []
    for idx in sorted(set(chosen) | {0}):
        tagged.append([(msg, idx in note_tracks) for msg in mid.tracks[idx]])
    # merge by absolute tick, keeping track of which tracks contribute notes
    events = []
    for tr in tagged:
        tick = 0
        for msg, use_notes in tr:
            tick += msg.time
            events.append((tick, msg, use_notes))
    events.sort(key=lambda e: e[0])
    tempo = mido.bpm2tempo(tempo_override) if tempo_override else 500000
    notes, t, last_tick = [], 0.0, 0
    for tick, msg, use_notes in events:
        t += mido.tick2second(tick - last_tick, mid.ticks_per_beat, tempo)
        last_tick = tick
        if msg.type == "set_tempo" and not tempo_override:
            tempo = msg.tempo
        elif msg.type == "note_on" and msg.velocity > 0 and use_notes:
            if getattr(msg, "channel", None) == 9:
                continue                                # GM drum channel: not pitches
            notes.append((t, msg.note))
    return notes, [items[i][1] for i in chosen], []


def list_midi(path):
    mid = _mido().MidiFile(path)
    for i, t in enumerate(mid.tracks, 1):
        n = sum(1 for m in t if m.type == "note_on" and m.velocity > 0)
        print(f"  track {i}: {t.name or '(unnamed)'}  ({n} notes)")


def load_score(path, part=None, tempo=None):
    if path.lower().endswith((".mid", ".midi")):
        return load_midi(path, part, tempo)
    return load_musicxml(path, part, tempo)


# --------------------------------------------------------------------------- convert

def build_events(notes, pin_map, transpose=0, fold=False):
    mapped = pin_map["notes"]
    events, skipped, folded = [], Counter(), Counter()
    for t, m in notes:
        m += transpose
        if m not in mapped and fold:
            options = [m + 12 * k for k in range(-8, 9) if m + 12 * k in mapped]
            if options:
                new = min(options, key=lambda c: abs(c - m))
                folded[f"{midi_to_name(m)}->{midi_to_name(new)}"] += 1
                m = new
        if m not in mapped:
            skipped[midi_to_name(m)] += 1
            continue
        pin, pulse = mapped[m]
        events.append((round(t, 4), midi_to_name(m), pin, pulse))
    events = sorted(set(events), key=lambda e: (e[0], e[2]))   # same note twice at once -> once
    # dedupe same pin at same instant (can happen after folding)
    seen, unique = set(), []
    for e in events:
        if (e[0], e[2]) not in seen:
            seen.add((e[0], e[2]))
            unique.append(e)
    if unique:
        t0 = unique[0][0]                                 # trim leading silence
        unique = [(round(t - t0, 4), n, p, w) for t, n, p, w in unique]
    return unique, skipped, folded


def check_events(events, pin_map):
    warnings = []
    gap = pin_map["min_gap_ms"] / 1000.0
    last = {}
    fast = Counter()
    for t, note, pin, _ in events:
        if pin in last and t - last[pin] < gap:
            fast[note] += 1
        last[pin] = t
    for note, n in fast.items():
        warnings.append(f"{note}: {n} repeat(s) closer than {pin_map['min_gap_ms']} ms "
                        f"(solenoid may not reset in time; try play --speed 0.8)")
    per_instant = Counter(t for t, *_ in events)
    big = {t: c for t, c in per_instant.items() if c > pin_map["max_simultaneous"]}
    if big:
        warnings.append(f"{len(big)} chord(s) fire more than {pin_map['max_simultaneous']} "
                        f"solenoids at once (check your power supply); first at {min(big):.2f}s")
    return warnings


def cmd_convert(a):
    pin_map = load_pin_map(a.map)
    notes, parts, warnings = load_score(a.score, a.part, a.tempo)
    events, skipped, folded = build_events(notes, pin_map, a.transpose, a.fold_octaves)
    warnings += check_events(events, pin_map)
    out = {
        "source": a.score,
        "parts": parts,
        "transpose": a.transpose,
        "duration_s": events[-1][0] if events else 0,
        "events": [{"t": t, "note": n, "pin": p, "pulse_ms": w} for t, n, p, w in events],
        "skipped_notes": dict(skipped),
    }
    with open(a.output, "w") as f:
        json.dump(out, f, indent=1)

    total = len(notes)
    print(f"Read {total} note(s) from {', '.join(parts) or a.score}")
    print(f"Wrote {len(events)} strike(s), {out['duration_s']:.1f} s long -> {a.output}")
    if folded:
        print(f"Moved by octaves to fit: {sum(folded.values())} note(s) "
              f"({', '.join(f'{k} x{v}' for k, v in folded.most_common(6))})")
    if skipped:
        top = ", ".join(f"{k} x{v}" for k, v in skipped.most_common(8))
        print(f"No solenoid for {sum(skipped.values())} note(s): {top}"
              + ("" if a.fold_octaves else "   (try --fold-octaves or --transpose)"))
    for w in warnings:
        print(f"warning: {w}")


def cmd_info(a):
    print(a.score)
    if a.score.lower().endswith((".mid", ".midi")):
        list_midi(a.score)
    else:
        list_musicxml(a.score)


# --------------------------------------------------------------------------- preview

def load_song(path):
    with open(path) as f:
        song = json.load(f)
    for e in song["events"]:
        e["pulse_ms"] = clamp_pulse(e.get("pulse_ms", 20))
    return song


def render_wav(events, path, speed=1.0, rate=22050):
    """Bell-ish tone per strike so you can hear the timing without hardware."""
    if not events:
        return
    decay = 0.6
    length = int((events[-1]["t"] / speed + decay + 0.2) * rate)
    buf = [0.0] * length
    tones = {}
    for e in events:
        note = e["note"]
        if note not in tones:
            f = 440.0 * 2 ** ((name_to_midi(note) - 69) / 12)
            n = int(decay * rate)
            tones[note] = [
                math.exp(-6 * i / n) * (math.sin(2 * math.pi * f * i / rate)
                                        + 0.3 * math.sin(2 * math.pi * 2.76 * f * i / rate))
                for i in range(n)]
        start = int(e["t"] / speed * rate)
        for i, s in enumerate(tones[note]):
            if start + i < length:
                buf[start + i] += s
    peak = max(1e-9, max(abs(x) for x in buf))
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(32000 * x / peak)) for x in buf))


def cmd_preview(a):
    song = load_song(a.song)
    groups = defaultdict(list)
    for e in song["events"]:
        groups[e["t"]].append(e)
    print(f"{a.song}: {len(song['events'])} strikes, {song.get('duration_s', 0):.1f} s")
    print("   time   notes (GPIO)")
    for t in sorted(groups)[: a.limit or None]:
        hits = "  ".join(f"{e['note']}({e['pin']})" for e in groups[t])
        print(f"{t / a.speed:7.3f}s  {hits}")
    if a.limit and len(groups) > a.limit:
        print(f"   ... {len(groups) - a.limit} more")
    if a.wav:
        render_wav(song["events"], a.wav, a.speed)
        print(f"Audio preview -> {a.wav}")


# --------------------------------------------------------------------------- play

class MockPin:
    def __init__(self, pin):
        self.pin = pin

    def on(self):
        pass

    def off(self):
        pass

    def close(self):
        pass


def _wait_until(target):
    while True:
        remaining = target - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(remaining - 0.002 if remaining > 0.003 else 0)


def cmd_play(a):
    song = load_song(a.song)
    events = song["events"]
    if not events:
        sys.exit("nothing to play")

    actions = []                    # (time, 0=off/1=on, pin, label)
    for e in events:
        t = e["t"] / a.speed
        actions.append((t, 1, e["pin"], e["note"], e["pulse_ms"]))
        actions.append((t + e["pulse_ms"] / 1000.0, 0, e["pin"], e["note"], e["pulse_ms"]))
    actions.sort(key=lambda x: (x[0], x[1]))   # at the same instant, release before striking

    pins = sorted({e["pin"] for e in events})
    if a.mock:
        devices = {p: MockPin(p) for p in pins}
    else:
        import warnings
        warnings.filterwarnings("ignore", module="gpiozero")
        try:
            from gpiozero import OutputDevice
            devices = {p: OutputDevice(p, active_high=True, initial_value=False) for p in pins}
        except Exception as err:
            sys.exit(f"Can't use the GPIO pins on this computer ({type(err).__name__}).\n"
                     "Real playback only works on the Raspberry Pi. "
                     "To test here, add --mock:\n"
                     f"  python sheet_to_solenoid.py play {a.song} --mock")

    print(f"Playing {len(events)} strikes on GPIO {', '.join(map(str, pins))}"
          f"{' (mock)' if a.mock else ''}. Ctrl+C to stop.")
    start = time.perf_counter() + 1.0
    try:
        for t, kind, pin, note, pulse in actions:
            if not a.fast:
                _wait_until(start + t)
            if kind:
                devices[pin].on()
                if a.mock:
                    print(f"{t:8.3f}s  STRIKE {note:<4} GPIO{pin:<3} {pulse} ms")
            else:
                devices[pin].off()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        for d in devices.values():          # never leave a coil energised
            d.off()
            d.close()


# --------------------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="Sheet music -> glockenspiel solenoid strikes")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="list parts/tracks in a score")
    p.add_argument("score")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("convert", help="score -> strike list JSON")
    p.add_argument("score", help=".musicxml / .xml / .mxl / .mid")
    p.add_argument("-o", "--output", default="song.json")
    p.add_argument("--map", default="pins.json", help="note -> GPIO map (default pins.json)")
    p.add_argument("--part", help="which part(s)/track(s): number, id or name, comma-separated")
    p.add_argument("--transpose", type=int, default=0, help="semitones, e.g. 12 = up an octave")
    p.add_argument("--fold-octaves", action="store_true",
                   help="move notes we have no bar for up/down by octaves to the nearest bar we do have")
    p.add_argument("--tempo", type=float, help="force a tempo in quarter-notes per minute")
    p.set_defaults(func=cmd_convert)

    p = sub.add_parser("preview", help="print schedule, optionally render a WAV")
    p.add_argument("song")
    p.add_argument("--wav", help="write an audio preview to this .wav file")
    p.add_argument("--speed", type=float, default=1.0)
    p.add_argument("--limit", type=int, default=40, help="rows to print (0 = all)")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("play", help="fire the solenoids")
    p.add_argument("song")
    p.add_argument("--mock", action="store_true", help="print instead of using GPIO (no Pi needed)")
    p.add_argument("--fast", action="store_true", help="with --mock: don't wait in real time")
    p.add_argument("--speed", type=float, default=1.0, help="0.5 = half speed")
    p.set_defaults(func=cmd_play)

    a = ap.parse_args()
    if getattr(a, "speed", 1.0) <= 0:
        sys.exit("--speed must be > 0")
    if a.cmd == "play" and a.fast and not a.mock:
        sys.exit("--fast only makes sense with --mock")
    a.func(a)


if __name__ == "__main__":
    main()
