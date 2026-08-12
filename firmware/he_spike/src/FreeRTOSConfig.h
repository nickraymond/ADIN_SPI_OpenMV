// FreeRTOSConfig.h -- HE spike. Kernel: vendored FreeRTOS-Kernel V11.3.0,
// port GCC/ARM_CM55_NTZ/non_secure (no TrustZone switching; the SE boots
// HE apps in the secure state, so FreeRTOS runs secure-only).
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#define configUSE_PREEMPTION                    1
#define configUSE_TICKLESS_IDLE                 0
#define configCPU_CLOCK_HZ                      160000000UL // M55_HE_CPU_FREQ_HZ,
                                                            // boards/OPENMV_AE3/board_config.mk:16
#define configTICK_RATE_HZ                      1000
#define configMAX_PRIORITIES                    4
#define configMINIMAL_STACK_SIZE                256
#define configMAX_TASK_NAME_LEN                 8
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1

#define configUSE_MUTEXES                       0
#define configUSE_RECURSIVE_MUTEXES             0
#define configUSE_COUNTING_SEMAPHORES           0
#define configQUEUE_REGISTRY_SIZE               0
#define configUSE_QUEUE_SETS                    0
#define configUSE_TIME_SLICING                  1
#define configUSE_NEWLIB_REENTRANT              0
#define configENABLE_BACKWARD_COMPATIBILITY     0
#define configUSE_TASK_NOTIFICATIONS            1

#define configSUPPORT_STATIC_ALLOCATION         0
#define configSUPPORT_DYNAMIC_ALLOCATION        1
#define configTOTAL_HEAP_SIZE                   (24 * 1024)
#define configAPPLICATION_ALLOCATED_HEAP        0

#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configCHECK_FOR_STACK_OVERFLOW          0
#define configUSE_MALLOC_FAILED_HOOK            0

#define configGENERATE_RUN_TIME_STATS           0
#define configUSE_TRACE_FACILITY                0
#define configUSE_STATS_FORMATTING_FUNCTIONS    0

#define configUSE_TIMERS                        0
#define configUSE_CO_ROUTINES                   0

#define INCLUDE_vTaskDelay                      1
#define INCLUDE_xTaskGetSchedulerState          1
#define INCLUDE_vTaskDelete                     0
#define INCLUDE_vTaskSuspend                    0

// ARMv8-M port specifics (ARM_CM55_NTZ/non_secure).
#define configENABLE_TRUSTZONE                  0
#define configRUN_FREERTOS_SECURE_ONLY          1
#define configENABLE_MPU                        0
#define configENABLE_FPU                        1
#define configENABLE_MVE                        1

// 8 NVIC priority bits (M55_HE.h:677 __NVIC_PRIO_BITS).
#define configPRIO_BITS                         8
#define configKERNEL_INTERRUPT_PRIORITY         (0xFF)
#define configMAX_SYSCALL_INTERRUPT_PRIORITY    (0x80)

extern void vAssertCalled(const char *file, int line);
#define configASSERT(x) \
    do { if (!(x)) { vAssertCalled(__FILE__, __LINE__); } } while (0)

#endif // FREERTOS_CONFIG_H
