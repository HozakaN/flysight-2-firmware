#!/bin/bash
# usage: write_time.sh <elf> [count]   Time spent in FS_State_Write() at start-up, in ms of the firmware's own tick.
E=$1; N=${2:-1}
W=$(arm-none-eabi-nm $E | awk '$3=="FS_State_Write"{print $1}'); T=$(arm-none-eabi-nm $E | awk '$3=="uwTick"{print $1}')
R=$(arm-none-eabi-objdump -d --no-show-raw-insn $E | awk '/<FS_State_Init>:/{p=1} p&&/^[0-9a-f]+ <FS_State_Update>:/{exit} p' | grep -A1 "bl.*<FS_State_Write>" | tail -1 | awk '{print $1}' | tr -d ':')
for n in $(seq 1 $N); do
  OUT=$(timeout 400 openocd -f interface/stlink.cfg -c "transport select hla_swd" -f target/stm32wbx.cfg -c "stm32wbx.cpu configure -event examine-end {}" -c init -c "reset halt" -c "bp 0x$W 2 hw" -c "bp 0x$R 2 hw" -c resume -c "wait_halt 20000" -c "echo T1=[read_memory 0x$T 32 1]" -c resume -c "wait_halt 300000" -c "echo T2=[read_memory 0x$T 32 1]" -c "rbp 0x$W" -c "rbp 0x$R" -c resume -c exit 2>&1)
  A=$(echo "$OUT" | grep "^T1=" | cut -d= -f2); B=$(echo "$OUT" | grep "^T2=" | cut -d= -f2)
  if [ -n "$A" ] && [ -n "$B" ]; then echo "FS_State_Write() at start-up: $(( B - A )) ms"; else echo "no measurement: $(echo "$OUT" | grep -i -E "timed out|rror" | head -1)"; fi
  sleep 3
done
