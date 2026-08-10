# Out-of-tree build of the mainline ADIN1110 MAC-PHY + ADIN1100 T1L PHY
# drivers against the running kernel's installed headers.
#
# adin1110.c / adin1100.c are UNMODIFIED vendored copies — see README.md
# for the exact source commit. Do not edit them here; re-vendor instead.

obj-m += adin1110.o
obj-m += adin1100.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean

install:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules_install
	depmod -a
