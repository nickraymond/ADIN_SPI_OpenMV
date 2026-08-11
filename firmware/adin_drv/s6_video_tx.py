# s6_video_tx.py -- Sprint S6 bite 1: camera -> MJPEG -> chunked raw
# Ethernet frames over the S5 TX path. Single-buffered, deliberately
# unpipelined: this run MEASURES the capture/encode/tx split so the
# bite-3 pipelining change is data-driven (one variable at a time).
#
# Duration and quality are per-run choices (Nick, bite 2) -- invoke main()
# explicitly. Pair with the bench counter or the S6 shim on nereus001, then
# from nereus000:
#   mpremote connect <dev> mount firmware/adin_drv \
#       exec "import s6_video_tx; s6_video_tx.main()"
#   ... s6_video_tx.main(duration_s=300, quality=70)   # long run / q ladder

import s6_video

DURATION_S = 65            # default: covers the counter's 60 s window
QUALITY = 50               # default per T1 (q35-50); ladder overrides at run
LOAD_BAUD = 20_000_000     # S5-demo-proven: 0% loss at 4.21 Mbps payload
WARMUP_MS = 2000           # auto-exposure settle (board_config default)
STATS_EVERY_MS = 5_000


def sensor_init():
    import sensor
    # Proven streaming setup (firmware/ae3_usb/capture_service.py:158-161).
    # Framebuffer count left at default -- S3 measured set_framebuffers(2)
    # making the legacy stream loop WORSE; revisit with bite-1 numbers.
    sensor.reset()
    sensor.set_pixformat(sensor.RGB565)
    sensor.set_framesize(sensor.QVGA)     # letterboxes to 320x200 (S0)
    sensor.skip_frames(time=WARMUP_MS)
    return sensor


def encode_jpeg(img, quality):
    """In-place JPEG encode; returns the byte buffer. fw >= 1.28 renamed
    compress() -> to_jpeg() (same dance as bench/ae3_video_bench.py)."""
    if hasattr(img, "to_jpeg"):
        img = img.to_jpeg(quality=quality, copy=False)
    else:
        img.compress(quality=quality)
    return img.bytearray()


def main(duration_s=DURATION_S, quality=QUALITY):
    import time
    from adin_hal_ae3 import Ae3Hal
    from adin_spi import AdinSpi, AdinError
    from adin_bringup import bring_up

    print("S6 video TX -- QVGA q%d, %d s, chunk payload %d B"
          % (quality, duration_s, s6_video.PAYLOAD_MAX))
    hal = Ae3Hal(baudrate=LOAD_BAUD)
    hal.reset_pulse()
    adin = AdinSpi(hal)
    bring_up(hal, adin)
    sensor = sensor_init()

    chunk_buf = bytearray(s6_video.PAYLOAD_OFF + s6_video.PAYLOAD_MAX)
    frame_seq = 0
    stalls = 0
    link_drops = 0
    jpeg_bytes = 0
    jpeg_min = jpeg_max = None
    cap_us = enc_us = tx_us = 0
    t0 = time.ticks_ms()
    last_stat_t, last_stat_seq = t0, 0
    while True:
        now = time.ticks_ms()
        if time.ticks_diff(now, t0) >= duration_s * 1000:
            break

        ta = time.ticks_us()
        img = sensor.snapshot()
        tb = time.ticks_us()
        data = encode_jpeg(img, quality)
        tc = time.ticks_us()
        mv = memoryview(data)
        count = s6_video.n_chunks(len(data))
        try:
            for idx in range(count):
                payload = mv[idx * s6_video.PAYLOAD_MAX:
                             (idx + 1) * s6_video.PAYLOAD_MAX]
                n = s6_video.fill_chunk(chunk_buf, frame_seq, idx, count,
                                        payload)
                stalls += adin.send_frame(memoryview(chunk_buf)[:n])
        except AdinError as exc:
            # Demo resilience (TRACKER S6): a pulled pair fills the TX FIFO.
            # Drop this frame, ride out the outage, resume with seq intact.
            # If SPI itself is dead, link_up()'s MDIO poll raises and we die
            # loudly instead of spinning -- that fault class should be fatal.
            link_drops += 1
            print("!! TX failed at frame %d (%s) -- waiting for link"
                  % (frame_seq, exc))
            t_down = time.ticks_ms()
            while not adin.link_up():
                time.sleep_ms(200)
            print("link back after %d ms -- resuming"
                  % time.ticks_diff(time.ticks_ms(), t_down))
            frame_seq += 1
            continue
        td = time.ticks_us()

        cap_us += time.ticks_diff(tb, ta)
        enc_us += time.ticks_diff(tc, tb)
        tx_us += time.ticks_diff(td, tc)
        jpeg_bytes += len(data)
        jpeg_min = len(data) if jpeg_min is None else min(jpeg_min, len(data))
        jpeg_max = len(data) if jpeg_max is None else max(jpeg_max, len(data))
        frame_seq += 1

        if time.ticks_diff(now, last_stat_t) >= STATS_EVERY_MS:
            dt = time.ticks_diff(now, last_stat_t)
            nf = frame_seq - last_stat_seq
            print("  t=%3ds  %5d frames  %5.1f fps  cap %4.1f  enc %4.1f  "
                  "tx %4.1f ms/f  stalls %d"
                  % (time.ticks_diff(now, t0) // 1000, frame_seq,
                     nf * 1000.0 / dt, cap_us / frame_seq / 1000.0,
                     enc_us / frame_seq / 1000.0, tx_us / frame_seq / 1000.0,
                     stalls))
            last_stat_t, last_stat_seq = now, frame_seq

    dt_ms = time.ticks_diff(time.ticks_ms(), t0)
    s0, s1, spi_err = adin.status_summary()
    print("sent %d video frames (%d chunks, %d B JPEG) in %.1f s"
          % (frame_seq, adin.tx_frames, jpeg_bytes, dt_ms / 1000.0))
    print("  %.1f fps, %.2f Mbps JPEG payload, %d FIFO stalls, "
          "%d frames dropped to link outages"
          % (frame_seq * 1000.0 / dt_ms, jpeg_bytes * 8.0 / dt_ms / 1000,
             stalls, link_drops))
    print("  per frame: cap %.1f / enc %.1f / tx %.1f ms, JPEG %d-%d B"
          % (cap_us / frame_seq / 1000.0, enc_us / frame_seq / 1000.0,
             tx_us / frame_seq / 1000.0, jpeg_min, jpeg_max))
    print("MAC status: STATUS0=0x%08X STATUS1=0x%08X SPI_ERR=%s"
          % (s0, s1, "YES -- results suspect" if spi_err else "no"))
    print("verdict lives on the Pi: s6_video_counter.py must show complete, "
          "openable JPEGs (last frame_seq sent: %d)" % (frame_seq - 1))
