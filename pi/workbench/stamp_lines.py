#!/usr/bin/env python3
"""Prefix every stdin line with host unix time. Pipe a board probe through
this so its PWR_MARK lines land on the same clock as power_log.py rows:

    mpremote ... run probe.py | python3 stamp_lines.py | tee probe_run.log
"""
import sys
import time

for line in sys.stdin:
    sys.stdout.write("%.3f %s" % (time.time(), line))
    sys.stdout.flush()
