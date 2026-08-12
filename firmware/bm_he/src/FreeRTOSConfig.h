// FreeRTOSConfig.h -- bm_he (S10 INTERIM 2). Same kernel + port as the
// bite-1 spike (vendored V11.3.0, GCC/ARM_CM55_NTZ/non_secure) but grown
// for bm_core: mutexes, counting semaphores, software timers, stream
// buffers, and priorities up to DFU's 11 (bm_dfu_generic.h).
#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#define configUSE_PREEMPTION                    1
#define configUSE_TICKLESS_IDLE                 0
#define configCPU_CLOCK_HZ                      160000000UL // M55_HE_CPU_FREQ_HZ,
                                                            // boards/OPENMV_AE3/board_config.mk:16
#define configTICK_RATE_HZ                      1000
#define configMAX_PRIORITIES                    16  // bm_core uses up to 11 (DFU)
#define configMINIMAL_STACK_SIZE                256
#define configMAX_TASK_NAME_LEN                 16  // "timer_cb_handler"
#define configUSE_16_BIT_TICKS                  0
#define configIDLE_SHOULD_YIELD                 1

#define configUSE_MUTEXES                       1   // bm_mutex_create
#define configUSE_RECURSIVE_MUTEXES             1   // contrib sys_arch mutex
#define configUSE_COUNTING_SEMAPHORES           1   // bm_semaphore_create
#define configQUEUE_REGISTRY_SIZE               0
#define configUSE_QUEUE_SETS                    0
#define configUSE_TIME_SLICING                  1
#define configUSE_NEWLIB_REENTRANT              0
#define configENABLE_BACKWARD_COMPATIBILITY     1   // contrib sys_arch uses
                                                    // portTICK_RATE_MS et al.
#define configUSE_TASK_NOTIFICATIONS            1

#define configSUPPORT_STATIC_ALLOCATION         0
#define configSUPPORT_DYNAMIC_ALLOCATION        1
// Serves task stacks (BCMP 4K, L2 8K, DFU 4K, timer_cb 4K, tcpip 4K,
// wire task 4K, FreeRTOS timer svc 2K ~= 30K), queues, timers, and every
// bm_malloc in the stack. 64K leaves the 256 KB region room for code;
// heap_min on the wire status reports the real watermark.
#define configTOTAL_HEAP_SIZE                   (64 * 1024)
#define configAPPLICATION_ALLOCATED_HEAP        0

#define configUSE_IDLE_HOOK                     0
#define configUSE_TICK_HOOK                     0
#define configCHECK_FOR_STACK_OVERFLOW          2   // cheap; catches sizing
                                                    // mistakes in a new stack
#define configUSE_MALLOC_FAILED_HOOK            1

#define configGENERATE_RUN_TIME_STATS           0
#define configUSE_TRACE_FACILITY                0
#define configUSE_STATS_FORMATTING_FUNCTIONS    0

#define configUSE_TIMERS                        1   // bm_timer_* (heartbeat!)
#define configTIMER_TASK_PRIORITY               (configMAX_PRIORITIES - 1)
#define configTIMER_QUEUE_LENGTH                16
#define configTIMER_TASK_STACK_DEPTH            512 // words
#define configUSE_CO_ROUTINES                   0

#define INCLUDE_vTaskDelay                      1
#define INCLUDE_xTaskGetSchedulerState          1
#define INCLUDE_vTaskDelete                     1   // bm_task_delete
#define INCLUDE_vTaskSuspend                    1   // portMAX_DELAY blocking
#define INCLUDE_xTimerPendFunctionCall          1   // stream buffers / timers

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
