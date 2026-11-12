"""Set the DS3231 to UTC through the ds3231 test sketch, and log how far it had drifted.

    .venv/Scripts/python firmware/tools/set_rtc.py COM3

Uses UTC from the PC clock, so the PC's own time zone setting does not
matter (keep the PC clock synced to internet time). Sends the command at the
start of a second so the RTC is within a few tens of milliseconds.

Before setting, the script asks the sketch for the RTC time (command NOW: the sketch
answers at the moment the RTC second changes) and compares it with the PC clock. Each run
appends one line to firmware/tools/rtc_set_log.csv:

    set_utc, port, rtc_before_utc, offset_s, days_since_last_set, drift_s_per_day, drift_ppm, note

offset_s = RTC minus PC just before setting (positive: the RTC was ahead). The drift is
that offset divided by the time since the previous line of the log, so the first run
gives an offset but no drift. Accuracy of the offset: the PC clock's own error plus the
USB-serial delay, a few tens of ms [EST]; over a week that resolves about 0.1 ppm.
An offset above MAX_DRIFT_OFFSET_S is not a drift (clock never set, or on local time).
Keep the log file: commit it with the campaign data.
"""
import csv
import datetime as dt
import pathlib
import sys
import time

LOG = pathlib.Path(__file__).resolve().parent / "rtc_set_log.csv"
COLUMNS = ["set_utc", "port", "rtc_before_utc", "offset_s", "days_since_last_set",
           "drift_s_per_day", "drift_ppm", "note"]
MAX_DRIFT_OFFSET_S = 600.0   # [ASSUMPTION] 10 min; the datasheet drift is below 0.3 s/day
FMT = "%Y-%m-%d %H:%M:%S"
UTC = dt.timezone.utc


def drift(offset_s, last_set, now):
    """(days since the last setting, drift in s/day, drift in ppm, note) for one log line.
    offset_s: RTC minus PC before setting, or None if it could not be measured.
    last_set: time of the previous setting (aware datetime) or None."""
    if offset_s is None:
        return "", "", "", "offset not measured (flash the current tests/ds3231 sketch)"
    if abs(offset_s) > MAX_DRIFT_OFFSET_S:
        return "", "", "", "offset too large to be drift: clock was not set, or not on UTC"
    if last_set is None:
        return "", "", "", "first entry: no earlier setting to measure drift from"
    days = (now - last_set).total_seconds() / 86400.0
    if days < 0.5:
        return round(days, 4), "", "", "less than 12 h since the last setting: drift not resolved"
    per_day = offset_s / days
    return round(days, 4), round(per_day, 4), round(per_day / 86400.0 * 1e6, 3), ""


def last_set_time(log=LOG):
    if not log.exists():
        return None
    with open(log, newline="") as f:
        rows = list(csv.DictReader(f))
    return dt.datetime.strptime(rows[-1]["set_utc"], FMT).replace(tzinfo=UTC) if rows else None


def append_log(row, log=LOG):
    new = not log.exists()
    with open(log, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)


def read_offset(ser):
    """RTC minus PC in seconds, and the RTC time read; (None, None) if the sketch does not
    answer NOW. The sketch replies as its RTC second starts, so the reply's arrival time on
    the PC is the PC time of that RTC second."""
    ser.reset_input_buffer()
    ser.write(b"NOW\n")
    deadline = time.time() + 3
    while time.time() < deadline:
        line = ser.readline().decode(errors="replace").strip()
        arrived = dt.datetime.now(UTC)
        if line.startswith("NOW 2"):
            rtc = dt.datetime.strptime(line[4:23], FMT).replace(tzinfo=UTC)
            return (rtc - arrived).total_seconds(), rtc
        if line.startswith("NOW failed"):
            print(line)
            break
    return None, None


def main():
    import serial

    port = sys.argv[1] if len(sys.argv) > 1 else "COM3"
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 115200, 2
    ser.dtr = ser.rts = False          # do not reset the board when opening the port
    ser.open()
    time.sleep(0.5)
    offset, rtc_before = read_offset(ser)
    ser.reset_input_buffer()
    now = dt.datetime.now(UTC)
    time.sleep(1 - now.microsecond / 1e6)          # wait for the next whole second
    t = dt.datetime.now(UTC).replace(microsecond=0)
    ser.write(f"SET {t:%Y-%m-%d %H:%M:%S}\n".encode())
    deadline = time.time() + 3
    done = False
    while time.time() < deadline:
        line = ser.readline().decode(errors="replace").strip()
        if line.startswith("SET"):
            print(line)
            done = line.startswith("SET ok")
            break
    else:
        print("no answer: is the ds3231 test sketch running on", port, "?")
    ser.close()
    if not done:
        return
    days, per_day, ppm, note = drift(offset, last_set_time(), t)
    append_log({"set_utc": f"{t:{FMT}}", "port": port,
                "rtc_before_utc": f"{rtc_before:{FMT}}" if rtc_before else "",
                "offset_s": "" if offset is None else round(offset, 3),
                "days_since_last_set": days, "drift_s_per_day": per_day, "drift_ppm": ppm,
                "note": note})
    if offset is not None:
        print(f"RTC was {offset:+.3f} s from the PC clock before setting")
    if per_day != "":
        print(f"drift since the last setting ({days} days): {per_day:+.4f} s/day = {ppm:+.3f} ppm")
    elif note:
        print(note)
    print("logged in", LOG)


if __name__ == "__main__":
    main()
