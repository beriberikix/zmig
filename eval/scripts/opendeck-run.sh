#!/bin/bash
# DOGFOOD-5: zmig scan (pre-bump tree, patched source -> patched target) and zmig plan
# (fork triage of the 31 patch commits against upstream 70be2ff).
set -u
echo "run.sh pid $$  zmig $(git -C ~/code/zmig rev-parse --short HEAD)"
D=~/.cache/zmig/dogfood/d6; X=$D/x; T=$X/pre/OpenDeck; M=$X/pre/modules; cd ~/code/zmig; source .venv/bin/activate
SDK=~/.cache/zmig/sdks/zephyr-sdk-1.0.1
rm -rf $D/scan $D/plan; s=$(date +%s)
TARGET=nucleo_h563zi ZENV_PROJECT_ROOT=$T ZEPHYR_WS=$X/pre env -u PYTHONPATH zmig scan --shallow --sdk $SDK \
  --app $T/app --board nucleo_h563zi/stm32h563xx --sysbuild \
  --module cmsis_6 --module hal_stm32 --module mcuboot --module mbedtls --module tf-psa-crypto \
  --cmake-arg=-DZENV_PRESET_NAME=default --cmake-arg=-DZENV_BUILD_TYPE=release \
  "--cmake-arg=-DCONF_FILE=$T/app/common.conf;$T/app/release.conf" \
  "--cmake-arg=-DZEPHYR_EXTRA_MODULES=$M/zlibs" \
  --from $D/z/src --to $D/z/tgt --out $D/scan > $D/scan.human 2> $D/scan.err
echo "scan exit $? $(( $(date +%s)-s )) s"
# A west workspace whose zephyr is the patched source checkout (for fork triage).
mkdir -p $D/z/.west; printf '[manifest]\npath = src\nfile = west.yml\n\n[zephyr]\nbase = src\n' > $D/z/.west/config
s=$(date +%s)
env -u PYTHONPATH zmig plan --workspace $D/z --from 769c4f2846db675bc5c584b6d35551ad239c72da --to 70be2ff0b565a3313128f5577f51cfeb3ebcf602 --out $D/plan > $D/plan.human 2> $D/plan.err
echo "plan exit $? $(( $(date +%s)-s )) s"
echo "run.sh done"
