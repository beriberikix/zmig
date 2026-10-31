#!/bin/bash
# §2.4: target movement on a real pair. Golioth B, baseline at v4.2.0, run at v4.3.0.
set -u
D=~/.cache/zmig/dogfood/d5; S=$D/move; cd ~/code/zmig; source .venv/bin/activate
SDK=~/.cache/zmig/sdks/zephyr-sdk-0.16.8
T=$S/gm/golioth-firmware-sdk
w(){ local name=$1; shift; echo "### $name"; /usr/bin/time -f "%e s" env -u PYTHONPATH zmig watch "$@" --out $S/out/$name > $S/out/$name.md 2> $S/out/$name.err; echo "exit $?  ($(tail -1 $S/out/$name.err))"; }
g(){ echo --app $T/examples/zephyr/hello --board nucleo_h563zi/stm32h563xx --sdk $SDK \
  --module hal_stm32 --module cmsis --module cmsis_6 --module mbedtls --module zcbor \
  --cmake-arg=-DZEPHYR_EXTRA_MODULES=$T --baseline $T/.zmig/baseline.json --from v4.1.0 --shallow "$@"; }
if [ "${1:-}" != rerun ]; then
rm -rf $S; mkdir -p $S/out $S/gm; cp -a $D/gx/golioth-firmware-sdk $S/gm/
w init $(g --to v4.2.0 --init --reason baseline-at-v4.2.0)
fi
if [ "${1:-}" = rerun ]; then
w changed-moved      $(g --to v4.3.0)
w changed-moved-risky $(g --to v4.3.0 --fail-on risky)
w changed-moved-warn  $(g --to v4.3.0 --fail-on risky --on-target-movement warn)
else
w moved $(g --to v4.3.0)
fi
