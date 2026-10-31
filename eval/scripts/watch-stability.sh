#!/bin/bash
# Fingerprint stability (DESIGN-WATCH.md §2.4, retired; https://github.com/beriberikix/zmig/blob/c07a0bf/docs/DESIGN-WATCH.md):
# real `zmig watch` runs, no --report.
# Each scenario is a copy of the project with the baseline committed inside it, so the
# config (app path, -DZEPHYR_EXTRA_MODULES) is the same in every copy.
set -u
D=~/.cache/zmig/dogfood/d5; S=$D/stab; cd ~/code/zmig; source .venv/bin/activate
SDK=~/.cache/zmig/sdks/zephyr-sdk-0.16.8
V410=7823374e872145b5bd018bfe447839eb36042611; B42=30de5e8a2ceef99177c6c96785a8888b978154f0
w(){ local name=$1; shift; echo "### $name"; /usr/bin/time -f "%e s" env -u PYTHONPATH zmig watch "$@" --out $S/out/$name > $S/out/$name.md 2> $S/out/$name.err; echo "exit $?  ($(tail -1 $S/out/$name.err))"; }
copy(){ rm -rf $S/$2; mkdir -p $S/$2; cp -a $1 $S/$2/; }
mkdir -p $S/out
X=$D/../d4/x/v1.1.0/cannectivity
cann(){ local T=$S/$1/cannectivity; shift; echo --app $T/app --board usb2canfdv1 --module cmsis --module hal_stm32 --sdk $SDK \
  --cmake-arg=-DZEPHYR_EXTRA_MODULES=$T --cmake-arg=-DFILE_SUFFIX=usbd_next --baseline $T/.zmig/baseline.json --from $V410 "$@"; }
G=$D/gx/golioth-firmware-sdk
gol(){ local T=$S/$1/golioth-firmware-sdk; shift; echo --app $T/examples/zephyr/hello --board nucleo_h563zi/stm32h563xx --sdk $SDK \
  --module hal_stm32 --module cmsis --module cmsis_6 --module mbedtls --module zcbor \
  --cmake-arg=-DZEPHYR_EXTRA_MODULES=$T --baseline $T/.zmig/baseline.json --from v4.1.0 "$@"; }

if [ "${1:-all}" != golioth ]; then
copy $X c-orig; copy $X c-shift; copy $X c-rename
w c-init $(cann c-orig --to $B42 --init --reason stability-baseline)
for c in c-shift c-rename; do mkdir -p $S/$c/cannectivity/.zmig; cp $S/c-orig/cannectivity/.zmig/baseline.json $S/$c/cannectivity/.zmig/; done
# Unrelated line shifts: 5 comment lines at the top of every file with a finding, plus main.c.
for f in led.c usb.c main.c; do sed -i '1i /* unrelated edit 1 */\n/* 2 */\n/* 3 */\n/* 4 */\n/* 5 */' $S/c-shift/cannectivity/app/src/$f; done
# Rename: src/led.c -> src/led_ctrl.c (the only source list mentioning it updated).
mv $S/c-rename/cannectivity/app/src/led.c $S/c-rename/cannectivity/app/src/led_ctrl.c
grep -rl "src/led.c" $S/c-rename/cannectivity/app/CMakeLists.txt | xargs sed -i 's|src/led.c|src/led_ctrl.c|'
w c-same   $(cann c-orig)
w c-target $(cann c-orig --to v4.2.0)
w c-shift  $(cann c-shift)
w c-rename $(cann c-rename)
fi
if [ "${1:-all}" != cann ]; then
copy $G g-orig; copy $G g-shift
w g-init $(gol g-orig --to v4.2.0 --init --reason stability-baseline)
mkdir -p $S/g-shift/golioth-firmware-sdk/.zmig; cp $S/g-orig/golioth-firmware-sdk/.zmig/baseline.json $S/g-shift/golioth-firmware-sdk/.zmig/
for f in examples/zephyr/common/net_connect.c examples/zephyr/hello/src/main.c; do sed -i '1i /* unrelated edit 1 */\n/* 2 */\n/* 3 */' $S/g-shift/golioth-firmware-sdk/$f; done
w g-same   $(gol g-orig)
w g-target $(gol g-orig --to $B42)
w g-shift  $(gol g-shift)
fi
