# Makefile -- he_spike.elf/.bin cross-build. Runs INSIDE the D23 docker
# container (firmware-builder image) via build_he_spike.sh; needs:
#   CROSS      -- arm-none-eabi- prefix (SDK gcc)
#   OPENMV_DIR -- openmv clone (Alif DFP driver headers/sources only)
# Host tests build separately (host_test/run_tests.sh, plain clang).

CROSS      ?= arm-none-eabi-
OPENMV_DIR ?= $(HOME)/openmv-dev/openmv
BUILD      ?= build

CC      = $(CROSS)gcc
OBJCOPY = $(CROSS)objcopy
SIZE    = $(CROSS)size

FR   = vendor/freertos
PORT = $(FR)/portable/GCC/ARM_CM55_NTZ/non_secure
ALIF = $(OPENMV_DIR)/lib/alif

SRCS = \
    src/startup.c \
    src/main.c \
    src/mhu.c \
    src/rpmsg_remote.c \
    src/bench.c \
    src/spi_probe.c \
    $(FR)/tasks.c \
    $(FR)/list.c \
    $(FR)/portable/MemMang/heap_4.c \
    $(PORT)/port.c \
    $(PORT)/portasm.c \
    $(ALIF)/drivers/source/spi.c \
    $(ALIF)/drivers/source/pinconf.c

INC = -Isrc -I$(FR)/include -I$(PORT) \
      -I$(ALIF)/drivers/include \
      -I$(ALIF)/Device/common/include

CFLAGS = -mcpu=cortex-m55 -mthumb -mfloat-abi=hard \
         -O2 -g -ffunction-sections -fdata-sections \
         -Wall -Wextra -Werror -Wno-unused-parameter \
         -std=gnu11 $(INC)

LDFLAGS = -mcpu=cortex-m55 -mthumb -mfloat-abi=hard \
          -T src/he_spike.ld -nostartfiles \
          -Wl,--gc-sections -Wl,-Map=$(BUILD)/he_spike.map \
          --specs=nano.specs

OBJS = $(patsubst %.c,$(BUILD)/%.o,$(notdir $(SRCS)))
VPATH = $(sort $(dir $(SRCS)))

# Vendor code (Alif DFP, FreeRTOS) is not ours to -Werror.
VENDOR_OBJS = spi.o pinconf.o tasks.o list.o heap_4.o port.o portasm.o
$(addprefix $(BUILD)/,$(VENDOR_OBJS)): CFLAGS := $(filter-out -Werror,$(CFLAGS))

all: $(BUILD)/he_spike.elf $(BUILD)/he_spike.bin
	$(SIZE) $(BUILD)/he_spike.elf

$(BUILD)/%.o: %.c | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/he_spike.elf: $(OBJS) src/he_spike.ld
	$(CC) $(LDFLAGS) $(OBJS) -o $@

$(BUILD)/he_spike.bin: $(BUILD)/he_spike.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD):
	mkdir -p $(BUILD)

clean:
	rm -rf $(BUILD)

.PHONY: all clean
