#ifndef BM_SPIKE_HAL_MOCK_H
#define BM_SPIKE_HAL_MOCK_H

#include <stdint.h>

void hal_mock_set_phyid(uint32_t phyid);
int  hal_mock_phyid_reads(void);
void hal_mock_reset_counts(void);
void hal_mock_corrupt_protection(int enable);

#endif
