// syscalls.c -- newlib-nano syscall stubs. (v)snprintf in he_dbg.c and
// bm_core's configuration.c pull in newlib's reentrancy layer, which
// references these. Nothing here is ever *usefully* called: there is no
// filesystem, no stdout and -- deliberately -- no libc heap (all dynamic
// memory is FreeRTOS heap_4 via bm_malloc). _sbrk traps loudly so any
// accidental libc-malloc path is caught, not silently serviced.

#include <stdint.h>
#include <sys/stat.h>

extern void bm_set_err(uint32_t err);   // startup.c, sticky

int _close(int fd) {
    (void)fd;
    return -1;
}

int _fstat(int fd, struct stat *st) {
    (void)fd;
    st->st_mode = S_IFCHR;
    return 0;
}

int _getpid(void) { return 1; }

int _isatty(int fd) {
    (void)fd;
    return 1;
}

int _kill(int pid, int sig) {
    (void)pid;
    (void)sig;
    return -1;
}

int _lseek(int fd, int off, int whence) {
    (void)fd;
    (void)off;
    (void)whence;
    return 0;
}

int _read(int fd, char *buf, int len) {
    (void)fd;
    (void)buf;
    (void)len;
    return 0;
}

int _write(int fd, const char *buf, int len) {
    (void)fd;
    (void)buf;
    return len;   // pretend-consume: nothing should printf to stdout, but
                  // if it does, losing it beats faulting
}

void *_sbrk(int incr) {
    (void)incr;
    bm_set_err(0x5B7Cu);   // 'sbrk': libc heap requested -- a bug, find it
    return (void *)-1;
}

void _exit(int code) {
    (void)code;
    bm_set_err(0xE817u);   // 'exit'
    for (;;) {
    }
}
