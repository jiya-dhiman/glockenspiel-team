#!/usr/bin/env python3
"""
conductor_bridge.py - send conducting beats from the LAPTOP to the glockenspiel Pi.

Every beat becomes one small network (UDP) message. conducted_play.py on the Pi
receives it and plays the next beat of the song, so the robot follows the conductor's tempo.

Beat sources
  conductor   Kevin Liu's mini-robotic-concert hand-gesture conductor (camera).
              Needs that repo and its own Python environment (Python 3.10 + MediaPipe);
              the easiest way to start it is conduct.cmd. Anything after "--" goes to the
              conductor app, e.g.  -- --demo   or   -- --config ..\\mini-robotic-concert\\config-responsive.json
  metronome   steady beats at --bpm (no camera; for testing)
  tap         press SPACE for each beat, q to quit (no camera; for testing)

Examples
  python conductor_bridge.py metronome --bpm 90                     # player on this same computer
  python conductor_bridge.py tap --pi 172.20.10.3                   # player on the Pi
  conduct.cmd --pi 172.20.10.3 -- --demo                            # Kevin's synthetic demo, no camera
  conduct.cmd --pi 172.20.10.3                                      # real camera conducting

Only the Python standard library is used here.
"""
import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path

DEFAULT_PORT = 5005
MAX_AGE_S = 0.5          # ignore implausible detection delays


class BeatSender:
    """Sends {"type": "beat", ...} messages to conducted_play.py over UDP."""

    def __init__(self, host, port, source):
        self.addr = (host, port)
        self.source = source
        self.seq = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.addr = (socket.gethostbyname(host), port)
        except OSError:
            sys.exit(f"Can't find '{host}' on the network. Use the Pi's IP address instead "
                     f"(run 'hostname -I' on the Pi), e.g. --pi 172.20.10.3")

    def _send(self, msg):
        msg.update(v=1, source=self.source, seq=self.seq)
        self.seq += 1
        try:
            self.sock.sendto(json.dumps(msg).encode(), self.addr)
        except OSError as err:
            print(f"  (couldn't send to {self.addr[0]}: {err})", flush=True)

    def beat(self, bpm=None, age_s=0.0, count=None):
        """age_s = how long ago the beat actually happened (detection delay)."""
        age_s = min(max(float(age_s), 0.0), MAX_AGE_S)
        self._send({"type": "beat", "bpm": round(bpm, 2) if bpm else None,
                    "age_s": round(age_s, 4), "count": count})
        shown = f"{bpm:5.1f} BPM" if bpm else "  tempo not measured yet"
        print(f"BEAT {self.seq:4d}  {shown}  -> {self.addr[0]}:{self.addr[1]}", flush=True)

    def stop(self):
        self._send({"type": "stop"})
        print("sent stop", flush=True)


# --------------------------------------------------------------------------- beat sources

def run_metronome(sender, bpm, beats):
    period = 60.0 / bpm
    print(f"Metronome {bpm} BPM. Ctrl+C to stop.")
    nxt = time.perf_counter() + 0.5
    n = 0
    try:
        while not beats or n < beats:
            while time.perf_counter() < nxt:
                time.sleep(min(0.005, max(0.0, nxt - time.perf_counter())))
            sender.beat(bpm=bpm, count=n + 1)
            n += 1
            nxt += period
    except KeyboardInterrupt:
        pass
    finally:
        sender.stop()


def get_key():
    """One keypress without Enter (Windows, Mac, Linux)."""
    try:
        import msvcrt
        return msvcrt.getwch()
    except ImportError:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def run_tap(sender):
    print("Press SPACE on every beat, q to quit. (Tempo comes from the gaps between taps.)")
    try:
        while True:
            k = get_key()
            if k == " ":
                sender.beat()
            elif k in ("q", "Q", "\x03", "\x1b"):
                break
    finally:
        sender.stop()


def find_concert_dir(given):
    here = Path(__file__).resolve().parent
    options = [Path(given)] if given else []
    if os.environ.get("CONCERT_DIR"):
        options.append(Path(os.environ["CONCERT_DIR"]))
    options += [here.parent / "mini-robotic-concert", here / "mini-robotic-concert"]
    for p in options:
        if (p / "conductor" / "app.py").is_file():
            return p.resolve()
    sys.exit("Can't find the mini-robotic-concert folder. Clone it next to this repo:\n"
             "  git clone https://github.com/Kevin-Liu-PU/mini-robotic-concert.git\n"
             "or pass --concert-dir PATH")


def run_conductor(sender, concert_dir, conductor_args):
    """Start Kevin's conductor app unchanged, and forward every beat it detects."""
    concert_dir = find_concert_dir(concert_dir)
    sys.path.insert(0, str(concert_dir))
    os.chdir(concert_dir)
    try:
        from conductor import multitrack
        from conductor import app
    except ImportError as err:
        sys.exit(f"Can't load the conductor ({err}).\nRun it with that repo's Python, "
                 f"after its setup.cmd:\n  {concert_dir}\\.venv\\Scripts\\python.exe "
                 f"conductor_bridge.py conductor ...   (or just use conduct.cmd)")

    original_update = multitrack.ConductorSession.update

    def update_and_forward(self, *args, **kwargs):
        beat = original_update(self, *args, **kwargs)
        if beat is not None:                  # a detected downward contact = one quarter-note beat
            sender.beat(bpm=self.engine.output_bpm,
                        age_s=time.perf_counter() - beat.t,
                        count=beat.count)
        return beat

    # Every frame (camera and --demo) goes through ConductorSession.update,
    # so wrapping it here forwards beats without editing Kevin's code.
    multitrack.ConductorSession.update = update_and_forward
    sys.argv = ["conductor"] + conductor_args
    print(f"Conductor from {concert_dir}; beats -> {sender.addr[0]}:{sender.addr[1]}", flush=True)
    try:
        return app.main()
    except (ValueError, FileNotFoundError, ImportError) as exc:
        print(f"Setup/configuration error: {exc}", file=sys.stderr)
        return 1
    finally:
        sender.stop()


def main():
    argv = sys.argv[1:]
    passthrough = []
    if "--" in argv:
        i = argv.index("--")
        argv, passthrough = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(description="Send conducting beats to the glockenspiel Pi")
    ap.add_argument("source", choices=["conductor", "metronome", "tap"])
    ap.add_argument("--pi", default="127.0.0.1",
                    help="Pi's IP address or hostname (default: this computer, for mock tests)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--bpm", type=float, default=90, help="metronome tempo")
    ap.add_argument("--beats", type=int, default=0, help="metronome: stop after N beats (0 = forever)")
    ap.add_argument("--concert-dir", help="path to the mini-robotic-concert repo")
    a = ap.parse_args(argv)
    if not 20 <= a.bpm <= 240:
        sys.exit("--bpm must be between 20 and 240")
    if passthrough and a.source != "conductor":
        sys.exit("arguments after -- are only for the conductor source")

    sender = BeatSender(a.pi, a.port, a.source)
    if a.source == "metronome":
        run_metronome(sender, a.bpm, a.beats)
    elif a.source == "tap":
        run_tap(sender)
    else:
        sys.exit(run_conductor(sender, a.concert_dir, passthrough))


if __name__ == "__main__":
    main()
