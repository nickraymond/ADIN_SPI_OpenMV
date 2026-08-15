# main_s17.py -- S17 bite-0 boot launcher. Deployed AS /flash/main.py for
# capture-relay bench sessions ONLY; the fixture's real main.py
# (firmware/ae3_usb) is restored afterwards -- see
# firmware/bm_bridge/README.md §Restore.
#
# Crash rule (BENCHSPEC BUILD-2b): while the Pi owns the VCP, a printed
# traceback lands in the counter's decoder as COBS garbage and the text
# is lost -- so persist EVERY exit cause (including KeyboardInterrupt:
# the firmware injects one at raw-REPL attach, and a plain host-side
# port-open can look like that) to /flash/s14_crash.txt BEFORE it hits
# the console. (Same crash file as the S14 bench -- one bench era, one
# forensics trail.)

import sys


def _log(msg, exc=None):
    try:
        with open("/flash/s14_crash.txt", "a") as f:
            f.write(msg + "\n")
            if exc is not None:
                sys.print_exception(exc, f)
    except Exception:
        pass


def _run():
    import s17_capture_pump
    s17_capture_pump.main()


_log("boot: s17 launcher start")
try:
    _run()
    _log("exit: main() returned cleanly")
except KeyboardInterrupt as exc:
    _log("exit: KeyboardInterrupt (host attach or ctrl-C)", exc)
    raise
except BaseException as exc:
    _log("exit: %s" % type(exc).__name__, exc)
    raise
