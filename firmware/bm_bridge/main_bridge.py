# main_bridge.py -- S16 bridge boot launcher. Deployed AS /flash/main.py
# for bridge sessions ONLY; the fixture's real main.py (firmware/ae3_usb)
# is restored afterwards -- see firmware/bm_bridge/README.md §Restore.
#
# Crash rule (BENCHSPEC BUILD-2b): while bm_sbc owns the VCP, a printed
# traceback lands in its uart_l2 decoder as COBS garbage and the text is
# lost -- so persist EVERY exit cause (including KeyboardInterrupt: the
# firmware injects one at raw-REPL attach, and a plain host-side
# port-open can look like that) to /flash/bridge_crash.txt BEFORE it
# hits the console. Failure sequence for the Pi operator: link death ->
# stop bm_sbc -> mpremote attach -> read /flash/bridge_crash.txt +
# /flash/bridge_trace.txt (includes the HE debug ring dump).

import sys


def _log(msg, exc=None):
    try:
        with open("/flash/bridge_crash.txt", "a") as f:
            f.write(msg + "\n")
            if exc is not None:
                sys.print_exception(exc, f)
    except Exception:
        pass


def _run():
    import bm_bridge
    bm_bridge.main()


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
