"""Host-side USB frame source: AE3 framed-JPEG stream -> Python frames (S3 bite 1).

Speaks the vendored wire protocol (``firmware/ae3_usb/command_protocol.py`` — imported
by file path so board and host share one source of truth): send ``start_stream`` with
settings, then read a repeating ``frame`` JSON header line followed by exactly
``size_bytes`` of JPEG, until a ``completed``/``failed`` control line ends the stream.

The parsing lives in ``StreamParser``, a pure incremental state machine with no I/O so
it unit-tests on any machine (``test_usb_frame_source.py``). ``UsbFrameSource`` wraps
it around a pyserial port and is the piece S3's sender service builds on:

    with UsbFrameSource(port, framesize="VGA", jpeg_quality=50) as src:
        for frame in src.frames():
            ...  # frame.seq, frame.width, frame.height, frame.data (JPEG bytes)

Stop semantics (from the firmware): ANY inbound byte ends the stream. A bare newline
is the safe stop/reset byte — a streaming board stops on it, an idle command loop
skips the blank line without a response.
"""

import glob
import importlib.util
import time
from collections import namedtuple
from pathlib import Path

_CP_PATH = Path(__file__).resolve().parents[2] / "firmware" / "ae3_usb" / "command_protocol.py"


def _load_command_protocol():
    """Load the vendored protocol module by file path (no sys.path pollution)."""
    spec = importlib.util.spec_from_file_location("ae3_command_protocol", _CP_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cp = _load_command_protocol()

#: by-id glob for any OpenMV camera. On nereus000 /dev/ttyACM0 is the N6 — always
#: resolve through by-id (S0 lesson, DEV_LOG 2026-08-09).
OPENMV_PORT_GLOB = "/dev/serial/by-id/usb-OpenMV_OpenMV_Camera_*-if00"

FrameRecord = namedtuple("FrameRecord", "seq width height data")


def find_openmv_port(pattern=OPENMV_PORT_GLOB):
    """Return the first OpenMV serial port, or raise with a usable hint."""
    ports = sorted(glob.glob(pattern))
    if not ports:
        raise FileNotFoundError(
            "no OpenMV camera found (glob %r) — is the AE3 plugged in?" % pattern
        )
    return ports[0]


def looks_like_jpeg(data):
    """True if ``data`` starts with the JPEG SOI marker (FF D8)."""
    return len(data) >= 4 and data[0] == 0xFF and data[1] == 0xD8


def has_jpeg_eoi(data):
    """True if ``data`` ends with the JPEG EOI marker (FF D9)."""
    return len(data) >= 4 and data[-2] == 0xFF and data[-1] == 0xD9


class StreamParser:
    """Incremental parser for the framed-JPEG stream. Pure: feed bytes, get events.

    ``feed(data)`` returns a list of ``(kind, message, payload)`` tuples:

    - ``("frame", header_dict, jpeg_bytes)`` — one complete frame
    - ``("control", message_dict, None)`` — completed / failed / any non-frame JSON
    - ``("junk", raw_line_bytes, None)`` — a non-JSON line (REPL banner, traceback…);
      surfaced, never swallowed, so the caller can fail loudly if the board is unwell
    """

    def __init__(self):
        self._buf = bytearray()
        self._pending = None  # frame header awaiting its binary payload

    def feed(self, data):
        events = []
        self._buf += data
        while True:
            if self._pending is not None:
                need = self._pending["frame"]["size_bytes"]
                if len(self._buf) < need:
                    break
                payload = bytes(self._buf[:need])
                del self._buf[:need]
                events.append(("frame", self._pending, payload))
                self._pending = None
                continue
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(self._buf[:nl])
            del self._buf[: nl + 1]
            if not line.strip():
                continue
            try:
                msg = cp.decode_message(line)
            except cp.ProtocolError:
                events.append(("junk", line, None))
                continue
            if msg.get("status") == "frame":
                self._pending = msg
            else:
                events.append(("control", msg, None))
        return events


class UsbFrameSource:
    """Own the AE3 serial port and yield JPEG frames from a ``start_stream`` session."""

    #: sensor.reset + skip_frames + first encode can take a few seconds at HD
    FIRST_FRAME_TIMEOUT_S = 10.0
    FRAME_TIMEOUT_S = 5.0

    def __init__(self, port, framesize="VGA", jpeg_quality=50, max_seconds=3600):
        self.port = port
        self.settings = {
            "framesize": framesize,
            "jpeg_quality": int(jpeg_quality),
            "max_seconds": int(max_seconds),
        }
        self.junk_lines = []  # surfaced parser junk, for loud error reports
        self._ser = None
        self._parser = StreamParser()

    # -- lifecycle ----------------------------------------------------------
    def start(self):
        try:
            import serial  # lazy: parser unit tests must not need pyserial
        except ImportError:
            raise ImportError("pyserial missing — sudo apt install python3-serial")
        self._ser = serial.Serial(self.port, baudrate=115200, timeout=0.2)
        # Reset any stale stream from a dead host: newline stops it, and is a
        # no-op for an idle command loop. Then drop whatever the board flushed.
        self._ser.write(b"\n")
        time.sleep(0.3)
        self._ser.reset_input_buffer()
        request = cp.make_request(
            "start_stream", "s3-usb-%d" % int(time.time()), self.settings
        )
        self._ser.write(cp.encode_message(request))
        return self

    def stop(self):
        if self._ser is None:
            return
        try:
            self._ser.write(b"\n")  # any byte stops the stream
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if not self._ser.read(4096):  # drain tail until quiet
                    break
        finally:
            self._ser.close()
            self._ser = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    # -- the stream ---------------------------------------------------------
    def frames(self):
        """Yield ``FrameRecord`` until the board ends the stream.

        Raises TimeoutError if no frame arrives in time and RuntimeError on a
        ``failed`` control line — always naming the port (fail loudly, usefully).
        """
        deadline = time.monotonic() + self.FIRST_FRAME_TIMEOUT_S
        while True:
            chunk = self._ser.read(4096)
            if chunk:
                for kind, msg, payload in self._parser.feed(chunk):
                    if kind == "frame":
                        f = msg["frame"]
                        deadline = time.monotonic() + self.FRAME_TIMEOUT_S
                        yield FrameRecord(f["seq"], f["width"], f["height"], payload)
                    elif kind == "control":
                        if msg.get("status") == "completed":
                            return
                        raise RuntimeError(
                            "AE3 %s: stream failed: %s" % (self.port, msg.get("error"))
                        )
                    else:
                        self.junk_lines.append(msg)
            if time.monotonic() > deadline:
                raise TimeoutError(
                    "AE3 %s: no frame within timeout (settings %r). Is the capture "
                    "service deployed? (firmware/ae3_usb/deploy.sh) Junk seen: %r"
                    % (self.port, self.settings, self.junk_lines[-3:])
                )
