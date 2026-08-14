# main_s14.py -- S14 bench boot launcher. Deployed AS /flash/main.py for
# relay-bench sessions ONLY; the fixture's real main.py (firmware/ae3_usb)
# is restored afterwards -- see firmware/bm_bridge/README.md §Restore.
#
# Crash rule (BENCHSPEC BUILD-2b, adopted early): while the Pi owns the
# VCP, a printed traceback lands in the counter's decoder as COBS garbage
# and the text is lost -- so persist EVERY exit cause (including
# KeyboardInterrupt: the firmware injects one at raw-REPL attach, and a
# plain host-side port-open can look like that) to /flash/s14_crash.txt
# BEFORE it hits the console.

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
    import s14_relay_pump
    s14_relay_pump.main()


_log("boot: launcher start")
try:
    _run()
    _log("exit: main() returned cleanly")
except KeyboardInterrupt as exc:
    _log("exit: KeyboardInterrupt (host attach or ctrl-C)", exc)
    raise
except BaseException as exc:
    _log("exit: %s" % type(exc).__name__, exc)
    raise
