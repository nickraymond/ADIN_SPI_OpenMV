#!/bin/bash
# run_host_tests.sh -- compile the UNMODIFIED vendored adin2111 driver +
# spike verdicts against the host HAL mock, run the tests.
#
# The vendored ADI files are compiled at default warning levels (they are
# not ours to clean up); OUR files get -Wall -Wextra -Werror.

set -euo pipefail
cd "$(dirname "$0")"

BUILD=build
VENDOR=../vendor/adin2111
SRC=../src
mkdir -p "${BUILD}"

CC="${CC:-cc}"
INC="-I${VENDOR} -I${SRC} -I."

# ENABLE_TESTING is NOT defined: we want the real retry loops.
for f in adi_spi_oa adi_mac adi_phy adi_fcs adin2111; do
    "${CC}" -c ${INC} -O1 -o "${BUILD}/${f}.o" "${VENDOR}/${f}.c"
done
"${CC}" -c ${INC} -O1 -Wall -Wextra -Werror -o "${BUILD}/bm_spike_verify.o" "${SRC}/bm_spike_verify.c"
"${CC}" -c ${INC} -O1 -Wall -Wextra -Werror -o "${BUILD}/hal_mock.o" hal_mock.c
"${CC}" -c ${INC} -O1 -Wall -Wextra -Werror -o "${BUILD}/test_verify.o" test_verify.c

"${CC}" -o "${BUILD}/test_verify" "${BUILD}"/*.o -lm

"${BUILD}/test_verify"
