#!/usr/bin/env python3
"""Minimal LCD renderer for the HIL playback state (pygame on KMS/DRM).

Replaces the chromium kiosk on nereus000's HDMI screen, because the
browser stack reproducibly kills active board USB sessions during NPU
predicts (A/B/A-confirmed 2026-08-25: n6_stage1_probe crashes with the
chromium kiosk up — as pi AND as a no-group user — and runs clean
without it; mechanism unattributed, see SPEC §Open questions). This
client is deliberately tiny: no browser, no compositor, one HTTP poll
and one blit per state change.

Renders the SAME semantics as the playback page:
  step   the canonical still, letterboxed in a 16:9 content box
  calib  the 4 white markers at the server's marker fractions
  black  black screen
  loop   a 2 s slideshow over the stills (the LCD stand-in for video —
         real video loop stays a browser-on-laptop job for now)

Runs under systemd (hil-lcd.service, user kiosk) with SDL's kmsdrm
driver; needs no X, no Wayland.
"""
import io
import json
import os
import time
import urllib.request

os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
import pygame  # noqa: E402

BASE = os.environ.get("HIL_PLAYBACK", "http://localhost:8091")


def get_state():
    with urllib.request.urlopen(BASE + "/api/state", timeout=3) as r:
        return json.loads(r.read())


def get_media(rel):
    with urllib.request.urlopen(BASE + "/media/" + rel, timeout=10) as r:
        return r.read()


def content_box(sw, sh):
    a = 16 / 9
    w, h = sw, sw / a
    if h > sh:
        h, w = sh, sh * a
    return int((sw - w) / 2), int((sh - h) / 2), int(w), int(h)


def main():
    pygame.display.init()
    info = pygame.display.Info()
    screen = pygame.display.set_mode((info.current_w, info.current_h),
                                     pygame.FULLSCREEN)
    pygame.mouse.set_visible(False)
    sw, sh = screen.get_size()
    bx, by, bw, bh = content_box(sw, sh)
    print(f"hil-lcd: {sw}x{sh}, content {bw}x{bh}+{bx}+{by}", flush=True)

    cache = {}

    def blit_still(rel):
        if rel not in cache:
            if len(cache) > 12:
                cache.clear()
            img = pygame.image.load(io.BytesIO(get_media(rel)))
            cache[rel] = pygame.transform.smoothscale(img, (bw, bh))
        screen.fill((0, 0, 0))
        screen.blit(cache[rel], (bx, by))

    shown = None
    slide_i, slide_t = 0, 0.0
    while True:
        try:
            st = get_state()
        except Exception:
            screen.fill((0, 0, 0))
            pygame.display.flip()
            time.sleep(1.0)
            shown = None
            continue
        mode = st["mode"]
        key = (st["seq"], mode)
        now = time.monotonic()
        if mode == "loop" and st["stills"] and now - slide_t > 2.0:
            slide_t = now
            slide_i = (slide_i + 1) % len(st["stills"])
            key = ("slide", slide_i)
        if key != shown:
            shown = key
            if mode == "black":
                screen.fill((0, 0, 0))
            elif mode == "calib":
                screen.fill((0, 0, 0))
                mw = int(st["marker_w"] * bw)
                for fx, fy in st["markers"]:
                    pygame.draw.rect(
                        screen, (255, 255, 255),
                        (bx + int(fx * bw) - mw // 2,
                         by + int(fy * bh) - mw // 2, mw, mw))
            elif mode == "step" and st["stills"]:
                blit_still(st["stills"][st["still"]])
            elif mode == "loop" and st["stills"]:
                blit_still(st["stills"][slide_i])
            else:
                screen.fill((0, 0, 0))
            pygame.display.flip()
        time.sleep(0.25)


if __name__ == "__main__":
    main()
