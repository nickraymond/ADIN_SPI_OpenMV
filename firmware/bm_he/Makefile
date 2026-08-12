# Makefile -- bm_he.elf/.bin cross-build (S10 INTERIM 2). Runs INSIDE the
# D23 docker container (firmware-builder image) via build_bm_he.sh; needs:
#   CROSS      -- arm-none-eabi- prefix (SDK gcc)
#   OPENMV_DIR -- openmv clone (lwIP 2.2.1 lives at
#                 lib/micropython/lib/lwip -- referenced, not vendored,
#                 same pattern as the Alif DFP sources in he_spike)
# Host tests build separately (host_test/run_tests.sh, plain clang).
#
# Shared-with-bite-1 sources (mhu.c, rpmsg_remote.c and the FreeRTOS
# kernel vendored under he_spike) are referenced from ../he_spike -- one
# copy, no divergence; he_spike.elf remains independently buildable.

CROSS      ?= arm-none-eabi-
OPENMV_DIR ?= $(HOME)/openmv-dev/openmv
BUILD      ?= build

CC      = $(CROSS)gcc
OBJCOPY = $(CROSS)objcopy
SIZE    = $(CROSS)size

SPIKE  = ../he_spike
FR     = $(SPIKE)/vendor/freertos
PORT   = $(FR)/portable/GCC/ARM_CM55_NTZ/non_secure
BM     = vendor/bm_core
LWIP   = $(OPENMV_DIR)/lib/micropython/lib/lwip/src

APP_SRCS = \
    src/startup.c \
    src/main.c \
    src/he_dbg.c \
    src/syscalls.c \
    src/bm_stubs.c \
    src/bm_net_mock.c \
    $(SPIKE)/src/mhu.c \
    $(SPIKE)/src/rpmsg_remote.c

FR_SRCS = \
    $(FR)/tasks.c \
    $(FR)/list.c \
    $(FR)/queue.c \
    $(FR)/timers.c \
    $(FR)/stream_buffer.c \
    $(FR)/portable/MemMang/heap_4.c \
    $(PORT)/port.c \
    $(PORT)/portasm.c

BM_SRCS = \
    $(BM)/common/bm_freertos.c \
    $(BM)/common/util.c \
    $(BM)/common/ll.c \
    $(BM)/common/device.c \
    $(BM)/common/timer_callback_handler.c \
    $(BM)/common/lib_state_machine.c \
    $(BM)/network/l2.c \
    $(BM)/network/l2_policy.c \
    $(BM)/network/bm_lwip.c \
    $(BM)/bcmp/bcmp.c \
    $(BM)/bcmp/packet.c \
    $(BM)/bcmp/heartbeat.c \
    $(BM)/bcmp/neighbors.c \
    $(BM)/bcmp/ping.c \
    $(BM)/bcmp/info.c \
    $(BM)/bcmp/time.c \
    $(BM)/bcmp/config.c \
    $(BM)/bcmp/configuration.c \
    $(BM)/bcmp/resource_discovery.c \
    $(BM)/bcmp/dfu_core.c \
    $(BM)/bcmp/dfu_client.c \
    $(BM)/bcmp/dfu_host.c \
    $(BM)/third_party/crc/crc16.c \
    $(BM)/third_party/crc/crc32.c \
    $(BM)/third_party/tinycbor/src/cborencoder.c \
    $(BM)/third_party/tinycbor/src/cborencoder_close_container_checked.c \
    $(BM)/third_party/tinycbor/src/cborencoder_float.c \
    $(BM)/third_party/tinycbor/src/cborerrorstrings.c \
    $(BM)/third_party/tinycbor/src/cborparser.c \
    $(BM)/third_party/tinycbor/src/cborparser_dup_string.c \
    $(BM)/third_party/tinycbor/src/cborparser_float.c \
    $(BM)/third_party/tinycbor/src/cborvalidation.c

LWIP_SRCS = \
    vendor/lwip_contrib/sys_arch.c \
    $(LWIP)/core/init.c \
    $(LWIP)/core/def.c \
    $(LWIP)/core/inet_chksum.c \
    $(LWIP)/core/ip.c \
    $(LWIP)/core/mem.c \
    $(LWIP)/core/memp.c \
    $(LWIP)/core/netif.c \
    $(LWIP)/core/pbuf.c \
    $(LWIP)/core/raw.c \
    $(LWIP)/core/stats.c \
    $(LWIP)/core/sys.c \
    $(LWIP)/core/timeouts.c \
    $(LWIP)/core/udp.c \
    $(LWIP)/core/ipv6/dhcp6.c \
    $(LWIP)/core/ipv6/ethip6.c \
    $(LWIP)/core/ipv6/icmp6.c \
    $(LWIP)/core/ipv6/inet6.c \
    $(LWIP)/core/ipv6/ip6.c \
    $(LWIP)/core/ipv6/ip6_addr.c \
    $(LWIP)/core/ipv6/ip6_frag.c \
    $(LWIP)/core/ipv6/mld6.c \
    $(LWIP)/core/ipv6/nd6.c \
    $(LWIP)/api/tcpip.c \
    $(LWIP)/netif/ethernet.c

SRCS = $(APP_SRCS) $(FR_SRCS) $(BM_SRCS) $(LWIP_SRCS)

# Our headers FIRST: bm_he's FreeRTOSConfig.h must beat he_spike's.
INC = -Isrc \
      -I$(SPIKE)/src \
      -I$(FR)/include -I$(PORT) \
      -I$(BM)/bcmp -I$(BM)/common -I$(BM)/network \
      -I$(BM)/third_party -I$(BM)/third_party/crc \
      -I$(BM)/third_party/tinycbor/src \
      -I$(LWIP)/include \
      -Ivendor/lwip_contrib

CFLAGS = -mcpu=cortex-m55 -mthumb -mfloat-abi=hard \
         -Os -g -ffunction-sections -fdata-sections \
         -Wall -Wextra -Werror -Wno-unused-parameter \
         -std=gnu11 $(INC) \
         -DCBOR_CUSTOM_ALLOC_INCLUDE='"tinycbor_alloc.h"' \
         -DCBOR_PARSER_MAX_RECURSIONS=10

LDFLAGS = -mcpu=cortex-m55 -mthumb -mfloat-abi=hard \
          -T src/bm_he.ld -nostartfiles \
          -Wl,--gc-sections -Wl,-Map=$(BUILD)/bm_he.map \
          --specs=nano.specs

OBJS = $(patsubst %.c,$(BUILD)/%.o,$(notdir $(SRCS)))
# src FIRST: he_spike/src also holds a startup.c and main.c -- ours must
# win the VPATH search for those basenames.
VPATH = src $(sort $(dir $(filter-out src/%,$(SRCS))))

# Vendor code (bm_core, lwIP, FreeRTOS, contrib) is not ours to -Werror.
APP_OBJS = startup.o main.o he_dbg.o bm_stubs.o bm_net_mock.o \
           mhu.o rpmsg_remote.o syscalls.o
VENDOR_OBJS = $(filter-out $(APP_OBJS),$(notdir $(OBJS)))
$(addprefix $(BUILD)/,$(VENDOR_OBJS)): CFLAGS := $(filter-out -Werror,$(CFLAGS))

all: $(BUILD)/bm_he.elf $(BUILD)/bm_he.bin
	$(SIZE) $(BUILD)/bm_he.elf

$(BUILD)/%.o: %.c | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/bm_he.elf: $(OBJS) src/bm_he.ld
	$(CC) $(LDFLAGS) $(OBJS) -o $@

$(BUILD)/bm_he.bin: $(BUILD)/bm_he.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)

.PHONY: all clean
