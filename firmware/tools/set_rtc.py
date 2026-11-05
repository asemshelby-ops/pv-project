"""Set the DS3231 to Makkah local time (UTC+3) through the ds3231 test sketch.

    .venv/Scripts/python firmware/tools/set_rtc.py COM3

Uses UTC from the PC clock + 3 h, so the PC's own time zone setting does not
matter (keep the PC clock synced to internet time). Sends the command at the
start of a second so the RTC is within a few tens of milliseconds.
"""
import datetime as dt
import sys
import time

import serial

MAKKAH = dt.timezone(dt.timedelta(hours=3))   # Saudi Arabia: UTC+3, no daylight saving


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 115200, 2
    ser.dtr = ser.rts = False          # do not reset the board when opening the port
    ser.open()
    time.sleep(0.5)
    ser.reset_input_buffer()
    now = dt.datetime.now(MAKKAH)
    time.sleep(1 - now.microsecond / 1e6)          # wait for the next whole second
    t = dt.datetime.now(MAKKAH).replace(microsecond=0)
    ser.write(f"SET {t:%Y-%m-%d %H:%M:%S}\n".encode())
    deadline = time.time() + 3
    while time.time() < deadline:
        line = ser.readline().decode(errors="replace").strip()
        if line.startswith("SET"):
            print(line)
            break
    else:
        print("no answer: is the ds3231 test sketch running on", port, "?")
    ser.close()


if __name__ == "__main__":
    main()
