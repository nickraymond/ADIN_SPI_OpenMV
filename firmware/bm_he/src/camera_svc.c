// camera_svc.c -- "camera/control" service + camera/stream publish path.
//
// Threading: the bm_service handler runs on the middleware/pubsub RX
// path; the wire task consumes the pending mailbox and calls
// camera_svc_publish from its own loop. The mailbox is a single-slot
// last-wins volatile pair (writer: handler; reader: wire task) -- no
// queue, no blocking in the handler (bm_service handlers must return
// promptly; capture is asynchronous by design).

#include "camera_svc.h"

#include <string.h>

#include "bm_service.h"
#include "pubsub.h"

static volatile bool s_pending_valid;
static wire_capture_t s_pending;

static volatile uint8_t s_mode_active;
static volatile uint32_t s_cmds;
static volatile uint32_t s_pub_ok;
static volatile uint32_t s_pub_errs;
static volatile uint32_t s_pub_bytes;

// Defaults applied by the BRIDGE (0 = "bridge default") -- the service
// passes zeros through untouched so the defaults live in exactly one
// place (bm_bridge.py).

bool camera_svc_handle(const uint8_t *req_data, uint32_t req_len,
                       camera_rep_t *rep) {
    memset(rep, 0, sizeof(*rep));
    rep->magic = CAMERA_REQ_MAGIC;

    camera_req_t req;
    if (req_len != sizeof(req)) {
        return false;               // not ours; no reply
    }
    memcpy(&req, req_data, sizeof(req));   // may be unaligned
    if (req.magic != CAMERA_REQ_MAGIC) {
        return false;
    }
    if (req.payload_max > CAMERA_MAX_PAYLOAD) {
        req.payload_max = CAMERA_MAX_PAYLOAD;
    }

    bool ok = true;
    switch (req.cmd) {
    case CAMERA_CMD_CAPTURE:
    case CAMERA_CMD_STREAM: {
        wire_capture_t cap = {
            .mode = req.cmd == CAMERA_CMD_STREAM ? CAMERA_MODE_STREAM
                                                 : CAMERA_MODE_SINGLE,
            .quality = req.quality,
            .fps_x10 = req.fps_x10,
            .rate_bps = req.rate_bps,
            .secs = req.secs,
            .payload_max = req.payload_max,
        };
        s_pending = cap;
        s_pending_valid = true;     // written AFTER the payload (reader
                                    // copies then clears; last-wins)
        s_mode_active = cap.mode;
        break;
    }
    case CAMERA_CMD_STOP: {
        wire_capture_t cap = {.mode = CAMERA_MODE_STOP};
        s_pending = cap;
        s_pending_valid = true;
        s_mode_active = CAMERA_MODE_STOP;
        break;
    }
    case CAMERA_CMD_STATUS:
        break;                      // counters only
    default:
        ok = false;                 // answered, but not accepted
        break;
    }
    if (ok && req.cmd != CAMERA_CMD_STATUS) {
        s_cmds = s_cmds + 1;
    }

    rep->ok = ok ? 1 : 0;
    rep->mode_active = s_mode_active;
    rep->cmds = s_cmds;
    rep->pub_ok = s_pub_ok;
    rep->pub_errs = s_pub_errs;
    rep->pub_bytes = s_pub_bytes;
    return true;
}

bool camera_svc_take_pending(wire_capture_t *out) {
    if (!s_pending_valid) {
        return false;
    }
    *out = s_pending;
    s_pending_valid = false;
    return true;
}

void camera_svc_publish(const uint8_t *payload, uint16_t len) {
    if (len == 0 || len > CAMERA_MAX_PAYLOAD) {
        s_pub_errs = s_pub_errs + 1;
        return;
    }
    if (bm_pub(CAMERA_STREAM_TOPIC, payload, len, 0,
               BM_COMMON_PUB_SUB_VERSION) == BmOK) {
        s_pub_ok = s_pub_ok + 1;
        s_pub_bytes = s_pub_bytes + len;
    } else {
        s_pub_errs = s_pub_errs + 1;
    }
}

// bm_service glue: reply buffer is bm_service's (MAX_BM_SERVICE_DATA_SIZE
// minus its header); camera_rep_t is 24 B -- always fits.
static bool camera_service_cb(size_t service_strlen, const char *service,
                              size_t req_data_len, uint8_t *req_data,
                              size_t *buffer_len, uint8_t *reply_data) {
    (void)service_strlen;
    (void)service;
    camera_rep_t rep;
    if (!camera_svc_handle(req_data, (uint32_t)req_data_len, &rep)) {
        return false;
    }
    if (*buffer_len < sizeof(rep)) {
        return false;
    }
    memcpy(reply_data, &rep, sizeof(rep));
    *buffer_len = sizeof(rep);
    return true;
}

BmErr camera_svc_init(void) {
    return bm_service_register(strlen(CAMERA_SERVICE), CAMERA_SERVICE,
                               camera_service_cb)
               ? BmOK
               : BmENOMEM;
}
