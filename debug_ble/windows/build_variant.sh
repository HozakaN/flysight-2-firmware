#!/bin/bash
# usage: build_variant.sh <git ref> <name> [option...]
#
# Builds one firmware for the bench without STM32CubeIDE: exports <ref> of this repository into
# a fresh tree, applies the options in the order given, sets the debugger flag the SWD bench
# needs (CFG_DEBUGGER_SUPPORTED), and builds $FS_BUILD/<name>/build/<name>.elf with
# arm-none-eabi-gcc and make. The include paths are taken from .cproject.
#
#   patch         the three fixes of the report (bugs 1 to 3), from the commits of this branch
#   trace         the trace of Scripts/cli/ble_trace.py (it needs the branch, or develop with patch)
#   file:<path>   a patch file, applied with patch -p1
#   commit:<ref>  what one commit of this repository changed, for a fix alone on master or develop
#
# The four builds of the report, the branch, and the branch with the trace:
#   ./build_variant.sh 9ec7186 master-unmodified
#   ./build_variant.sh 9ec7186 master-patched patch
#   ./build_variant.sh e67ee9f develop-unmodified
#   ./build_variant.sh e67ee9f develop-patched patch
#   ./build_variant.sh HEAD branch
#   ./build_variant.sh HEAD branch-trace trace
#
# 9ec7186 and e67ee9f are master and develop of https://github.com/flysight/flysight-2-firmware
# (git fetch --no-tags <that url> master develop). It runs on Linux, and on Windows in WSL:
#   sudo apt-get install gcc-arm-none-eabi make patch
# In WSL the repository of Windows is under /mnt/c, and git needs to be told it may read it,
# which this script does. FS_BUILD (default ~/fs-fw) is where the trees are built.
set -e
REF=$1; NAME=$2; shift 2
REPO=${FS_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}
BUILD=${FS_BUILD:-$HOME/fs-fw}
GIT="git -c safe.directory=$REPO -C $REPO"
T=$BUILD/$NAME
rm -rf "$T"; mkdir -p "$T"
$GIT archive "$REF" | tar -x -C "$T"
cd "$T"
for OPTION in "$@"; do
  case "$OPTION" in
    patch)
      $GIT diff 8720e69^ 8720e69 -- STM32_WPAN/App/app_ble.c | patch -p1 --no-backup-if-mismatch -s
      $GIT diff bddd707^ bddd707 -- STM32_WPAN/App/app_ble.c | patch -p1 --no-backup-if-mismatch -s
      $GIT diff 47e458f^ 47e458f -- FlySight/state.c | patch -p1 --no-backup-if-mismatch -s ;;
    trace)
      python3 "$REPO/Scripts/cli/ble_trace.py" instrument --root "$T" > /dev/null ;;
    file:*)
      patch -p1 --no-backup-if-mismatch -s < "${OPTION#file:}" ;;
    commit:*)
      $GIT diff "${OPTION#commit:}^" "${OPTION#commit:}" | patch -p1 --no-backup-if-mismatch -s ;;
    *)
      echo "unknown option $OPTION" >&2; exit 2 ;;
  esac
done
sed -i 's/^#define CFG_DEBUGGER_SUPPORTED    0$/#define CFG_DEBUGGER_SUPPORTED    1/' Core/Inc/app_conf.h
grep -q "^#define CFG_DEBUGGER_SUPPORTED    1" Core/Inc/app_conf.h
printf '#ifndef VERSION_H\n#define VERSION_H\n\n#define GIT_TAG "%s"\n\n#endif // VERSION_H\n' "$NAME" > FlySight/version.h
INC=$(python3 - <<'PY'
import re
s = open('.cproject').read()
incs = []
for m in re.finditer(r'<listOptionValue builtIn="false" value="(\.\./[^"]+)"/>', s):
    v = m.group(1)[3:]
    if v not in incs:
        incs.append(v)
print(' '.join('-I' + i for i in incs))
PY
)
cat > Makefile <<MK
CC := arm-none-eabi-gcc
SRC := \$(shell find Core Drivers FATFS FlySight Middlewares STM32_WPAN USB_Device Utilities -name '*.c' ! -name '*template*.c')
OBJ := \$(SRC:%.c=build/%.o) build/startup.o
CFLAGS := -mcpu=cortex-m4 -std=gnu11 -g3 -DDEBUG -DUSE_HAL_DRIVER -DSTM32WB5Mxx $INC -O0 -ffunction-sections -fdata-sections -Wall --specs=nano.specs -mfpu=fpv4-sp-d16 -mfloat-abi=hard -mthumb -MMD -MP
all: build/$NAME.elf
build/%.o: %.c
	@mkdir -p \$(dir \$@)
	@\$(CC) \$< \$(CFLAGS) -c -o \$@
build/startup.o: Core/Startup/startup_stm32wb5mmghx.s
	@mkdir -p build
	@\$(CC) -mcpu=cortex-m4 -g3 -x assembler-with-cpp -mfpu=fpv4-sp-d16 -mfloat-abi=hard -mthumb -c \$< -o \$@
build/$NAME.elf: \$(OBJ)
	\$(CC) -o \$@ \$(OBJ) -mcpu=cortex-m4 -T"STM32WB5MMGHX_FLASH.ld" --specs=nosys.specs -Wl,-Map=build/$NAME.map -Wl,--gc-sections -static --specs=nano.specs -mfpu=fpv4-sp-d16 -mfloat-abi=hard -mthumb -u _printf_float -Wl,--start-group -lc -lm -Wl,--end-group
MK
make -j"$(nproc)" > build.log 2>&1 || { grep -E " error|\*\*\*|undefined reference" build.log | head -12; exit 1; }
printf '%-22s warnings=%s  ' "$NAME" "$(grep -c ' warning:' build.log || true)"
arm-none-eabi-size "build/$NAME.elf" | tail -1
