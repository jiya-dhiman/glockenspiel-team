#!/usr/bin/env python3
"""
conducted_play.py - play a song on the glockenspiel at the tempo of a live conductor.

Runs on the PI. It waits for beat messages from conductor_bridge.py (on the laptop) and
plays the song one beat at a time, so speeding up or slowing down the conducting changes
the robot's tempo, and stopping the conducting pauses the robot.

  1. Make the strike list (needs the "beat" positions, so re-run convert if song.json is old):
       python3 sheet_to_solenoid.py convert hot-cross-buns.mxl -o song.json
  2. Start the player on the Pi:
       python3 conducted_play.py song.json
  3. Start beats on the laptop (see conductor_bridge.py), e.g.
       python conductor_bridge.py metronome --bpm 90 --pi <PI-IP>

How it follows
  count-in   the first --count-in beats (default 2) only set the tempo.
  predict    (default) when beat N arrives, the notes of the NEXT beat are scheduled one beat
             period later, so they land together with the conductor's next beat. This hides the
             camera/network delay, like a player who watches the conductor and anticipates.
  follow     the notes of a beat are played when that beat arrives (simpler, always a bit late).
  pause      no beat for ~2.5 beat lengths -> the song pauses where it is; conduct 2 beats to resume.

Test on one computer without a Pi:
  python conducted_play.py song.json --mock          (terminal 1)
  python conductor_bridge.py metronome --bpm 90      (terminal 2)
"""
import argparse
import heapq
import json
import math
import select
import socket
import sys
import time
from collections import defaultdict

from sheet_to_solenoid import MockPin, load_song

DEFAULT_PORT = 5005
MIN_BPM, MAX_BPM = 25.0, 200.0


def bpm_ok(bpm):
    return bpm is not None and MIN_BPM <= bpm <= MAX_BPM


class ConductedPlayer:
    def __init__(self, song, devices, a):
        self.devices = devices
        self.a = a
        self.min_gap = a.min_gap_ms / 1000.0
        self.windows = defaultdict(list)            # beat index -> [(offset in beats, event)]
        missing = 0
        for e in song["events"]:
            beat = e.get("beat")
            if beat is None:
                missing += 1
                beat = e["t"] * a.score_bpm / 60.0
            k = int(math.floor(beat + 1e-6))
            self.windows[k].append((beat - k, e))
        if missing:
            print(f"warning: {missing} strike(s) had no beat position; assumed the song was converted "
                  f"at {a.score_bpm:g} BPM. Re-run 'sheet_to_solenoid.py convert' to fix this.")
        self.last_window = max(self.windows) if self.windows else -1
        self.k = 0                       # next song beat to schedule
        self.waiting = a.count_in        # beats still needed before (re)starting
        self.started = False             # count-in done and song running
        self.period = None               # seconds per beat
        self.last_beat_t = None
        self.actions = []                # heap: (time, order, kind, pin, note, pulse, window, offset)
        self.order = 0
        self.prev = None                 # (window index, start time) of the last scheduled beat
        self.last_fire = {}
        self.finished = False

    # ---- timing -------------------------------------------------------------
    def on_message(self, msg, arrival):
        kind = msg.get("type")
        if kind == "stop":
            if self.started:
                self.pause("conductor stopped")
            return
        if kind != "beat":
            return
        age = min(max(float(msg.get("age_s") or 0.0), 0.0), 0.5)
        t = arrival - age - self.a.latency_ms / 1000.0
        if self.last_beat_t is not None:
            interval = t - self.last_beat_t
            if bpm_ok(msg.get("bpm")):
                self.period = 60.0 / msg["bpm"]
            elif bpm_ok(60.0 / interval if interval > 0 else None):
                self.period = interval
        elif bpm_ok(msg.get("bpm")):
            self.period = 60.0 / msg["bpm"]
        self.last_beat_t = t
        tempo = f"{60 / self.period:5.1f} BPM" if self.period else "tempo ?"

        if self.waiting > 0:
            self.waiting -= 1
            if self.waiting == 0 and self.period is None:
                self.waiting = 1                     # need one more beat to know the tempo
            if self.waiting > 0:
                print(f"  count-in ({tempo}) ... {self.waiting} more", flush=True)
                return
            self.started = True
            print(f"  count-in done ({tempo}) - {'starting' if self.k == 0 else 'resuming at beat ' + str(self.k + 1)}",
                  flush=True)
            if self.a.mode == "follow":
                return                               # first notes play on the next beat
        if self.finished:
            return
        start = t + (self.period if self.a.mode == "predict" else 0.0)
        self.schedule_window(self.k, start, tempo)
        self.k += 1
        if self.k > self.last_window:
            if self.a.loop:
                self.k = 0
                print("  -- looping --", flush=True)
            else:
                self.finished = True

    def push(self, at, kind, pin, note, pulse, window=None, offset=0.0):
        self.order += 1
        heapq.heappush(self.actions, (at, self.order, kind, pin, note, pulse, window, offset))

    def schedule_window(self, k, start, tempo):
        now = time.perf_counter()
        start = max(now, start)
        if self.prev is not None:
            # Now we know when the previous beat really ends (= this beat's start), so squeeze or
            # stretch its not-yet-played notes (e.g. the second eighth note) to fit before it.
            pk, pstart = self.prev
            span = start - pstart
            self.actions = [
                (max(now, pstart + off * span) if (w == pk and kind == 1 and span > 0) else at,
                 o, kind, pin, note, pulse, w, off)
                for at, o, kind, pin, note, pulse, w, off in self.actions]
            heapq.heapify(self.actions)
        self.prev = (k, start)
        bar, beat_in_bar = divmod(k, self.a.beats_per_bar)
        label = f"beat {k + 1:3d} (bar {bar + 1}, beat {beat_in_bar + 1})  {tempo}"
        self.push(start, 2, None, label, 0, k)       # printed when the beat actually starts
        for offset, e in self.windows.get(k, []):
            self.push(start + offset * self.period, 1, e["pin"], e["note"], e["pulse_ms"], k, offset)

    def pause(self, why):
        self.started = False
        self.prev = None
        self.waiting = self.a.resume_beats
        self.last_beat_t = None
        print(f"  || paused before beat {self.k + 1} ({why}). Conduct {self.waiting} beats to resume.",
              flush=True)

    def check_timeout(self, now):
        if self.started and not self.finished and self.period and self.last_beat_t is not None:
            if now - self.last_beat_t > max(2.5 * self.period, 1.5):
                self.pause("no beats")

    # ---- solenoids ----------------------------------------------------------
    def fire_due(self, now):
        while self.actions and self.actions[0][0] <= now:
            at, _, kind, pin, note, pulse, _, _ = heapq.heappop(self.actions)
            if kind == 2:
                print(note, flush=True)
                continue
            dev = self.devices[pin]
            if kind == 1:
                if now - self.last_fire.get(pin, -1.0) < self.min_gap:
                    print(f"  skipped {note}: solenoid hasn't reset yet", flush=True)
                    continue
                self.last_fire[pin] = now
                dev.on()
                self.push(now + pulse / 1000.0, 0, pin, note, pulse)
                if self.a.mock:
                    print(f"      STRIKE {note:<4} GPIO{pin:<3} {pulse} ms", flush=True)
            else:
                dev.off()

    def next_due(self):
        return self.actions[0][0] if self.actions else None

    def done(self):
        return self.finished and not self.actions


def open_devices(pins, mock):
    if mock:
        return {p: MockPin(p) for p in pins}
    import warnings
    warnings.filterwarnings("ignore", module="gpiozero")
    try:
        from gpiozero import OutputDevice
        return {p: OutputDevice(p, active_high=True, initial_value=False) for p in pins}
    except Exception as err:
        sys.exit(f"Can't use the GPIO pins on this computer ({type(err).__name__}). "
                 f"On a laptop, add --mock.")


def main():
    ap = argparse.ArgumentParser(description="Play a song following a live conductor's beats")
    ap.add_argument("song", help="strike list from sheet_to_solenoid.py convert")
    ap.add_argument("--mock", action="store_true", help="print instead of using GPIO (no Pi needed)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--mode", choices=["predict", "follow"], default="predict")
    ap.add_argument("--count-in", type=int, default=2, help="beats before the song starts (default 2)")
    ap.add_argument("--resume-beats", type=int, default=2, help="beats needed to resume after a pause")
    ap.add_argument("--beats-per-bar", type=int, default=4, help="only for the printed bar numbers")
    ap.add_argument("--latency-ms", type=float, default=0.0,
                    help="extra delay to subtract (camera/network), tune by ear")
    ap.add_argument("--min-gap-ms", type=float, default=60.0, help="minimum time between hits on one bar")
    ap.add_argument("--score-bpm", type=float, default=120.0,
                    help="only for old song.json files without beat positions")
    ap.add_argument("--loop", action="store_true", help="start again at the end")
    a = ap.parse_args()
    if a.count_in < 1 or a.resume_beats < 1:
        sys.exit("--count-in and --resume-beats must be at least 1")

    song = load_song(a.song)
    if not song["events"]:
        sys.exit("nothing to play")
    pins = sorted({e["pin"] for e in song["events"]})
    devices = open_devices(pins, a.mock)
    player = ConductedPlayer(song, devices, a)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", a.port))
    sock.setblocking(False)
    print(f"{a.song}: {len(song['events'])} strikes over {player.last_window + 1} beats, "
          f"GPIO {', '.join(map(str, pins))}{' (mock)' if a.mock else ''}")
    print(f"Listening for beats on UDP port {a.port} ({a.mode} mode, count-in {a.count_in}). Ctrl+C to stop.",
          flush=True)
    try:
        while not player.done():
            now = time.perf_counter()
            player.fire_due(now)
            player.check_timeout(now)
            due = player.next_due()
            wait = 0.05 if due is None else min(0.05, max(0.0, due - time.perf_counter()))
            ready, _, _ = select.select([sock], [], [], wait)
            if ready:
                while True:
                    try:
                        data = sock.recv(2048)
                    except (BlockingIOError, InterruptedError):
                        break
                    arrival = time.perf_counter()
                    try:
                        msg = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(msg, dict):
                        player.on_message(msg, arrival)
        print("Song finished.")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        for d in devices.values():               # never leave a coil energised
            d.off()
            d.close()
        sock.close()


if __name__ == "__main__":
    main()
