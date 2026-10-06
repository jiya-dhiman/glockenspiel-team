"""
one_note.py - strike ONE glockenspiel bar with ONE solenoid from the keyboard.

Controls:
    SPACE  strike the bar
    +      longer pulse (harder hit)
    -      shorter pulse (softer hit)
    q      quit

Run on the Raspberry Pi:   python3 one_note.py
Test with an LED first:    python3 one_note.py --led
Run on a laptop (no Pi):   python3 one_note.py --mock

LED wiring (Pi 3 header):
    physical pin 11 (GPIO17) -> 330 ohm resistor -> LED long leg (+)
    LED short leg (-)        -> physical pin 9 (GND)
"""

import sys
import time
import argparse

from gpiozero import Device, DigitalOutputDevice

# ---- settings (change these to match the hardware) -------------------------
SOLENOID_PIN = 17        # BCM GPIO number wired to the driver circuit's input
PULSE_MS = 20            # how long the solenoid is powered per strike
MIN_PULSE_MS = 5
MAX_PULSE_MS = 60        # hard limit so the solenoid can't be left on
# ----------------------------------------------------------------------------


def get_key():
    """Read ONE keypress without needing Enter (works on Pi, Mac, Windows)."""
    try:
        import msvcrt                       # Windows
        return msvcrt.getwch()
    except ImportError:
        import termios, tty                 # Linux / Mac / Raspberry Pi
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def strike(solenoid, pulse_ms):
    solenoid.on()                 # power the solenoid -> plunger hits the bar
    time.sleep(pulse_ms / 1000)
    solenoid.off()                # release -> plunger springs back, bar rings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="fake pins, no Pi needed")
    parser.add_argument("--led", action="store_true",
                        help="LED test mode: longer flash so you can see it")
    args = parser.parse_args()

    if args.mock:
        from gpiozero.pins.mock import MockFactory
        Device.pin_factory = MockFactory()

    global MAX_PULSE_MS
    pulse_ms = PULSE_MS
    if args.led:
        # 20 ms is too quick to see on an LED, so flash for 300 ms instead.
        # (Safe for an LED; NOT for a solenoid, so remove --led once it's connected.)
        pulse_ms, MAX_PULSE_MS = 300, 1000
    solenoid = DigitalOutputDevice(SOLENOID_PIN, initial_value=False)  # starts OFF

    mode = " (MOCK)" if args.mock else ""
    mode += " (LED TEST)" if args.led else ""
    print(f"Output on GPIO{SOLENOID_PIN}{mode}")
    print("SPACE = strike   + / - = pulse length   q = quit\n")

    try:
        while True:
            key = get_key()
            if key == " ":
                strike(solenoid, pulse_ms)
                print(f"DING!  ({pulse_ms} ms)\r")
            elif key in "+=":
                pulse_ms = min(MAX_PULSE_MS, pulse_ms + 5)
                print(f"pulse = {pulse_ms} ms\r")
            elif key in "-_":
                pulse_ms = max(MIN_PULSE_MS, pulse_ms - 5)
                print(f"pulse = {pulse_ms} ms\r")
            elif key in ("q", "Q", "\x03"):   # q or Ctrl+C
                break
    finally:
        solenoid.off()            # safety: never leave it powered
        solenoid.close()
        print("Bye.\r")


if __name__ == "__main__":
    main()
