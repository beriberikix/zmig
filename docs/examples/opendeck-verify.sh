#!/bin/bash
# OpenDeck verification builds (--analyzers none), patched Zephyr checkouts, OpenDeck's own
# build arguments for TARGET=nucleo_h563zi (zenv gen_build_config.sh, BUILD_TYPE=release).
# Only zlibs is a Zephyr module; u8g2 and vl53* are read as $ZEPHYR_WS/modules/<name>.
set -u
echo "verify.sh pid $$"
D=~/.cache/zmig/dogfood/d6; X=$D/x; cd ~/code/zmig; source .venv/bin/activate
SDK=~/.cache/zmig/sdks/zephyr-sdk-1.0.1
# Local checkouts: upstream SHA + OpenDeck's source-commit patch set (patched.sh).
PRE=$D/z/src; POST=$D/z/tgt
v(){ name=$1; side=$2; ref=$3; shift 3; T=$X/$side/OpenDeck; M=$X/$side/modules; rm -rf $D/verify/$name; mkdir -p $D/verify; echo "start $name $(date +%T)"; s=$(date +%s)
  TARGET=nucleo_h563zi ZENV_PROJECT_ROOT=$T ZEPHYR_WS=$X/$side env -u PYTHONPATH zmig scan --from $ref --to $ref --analyzers none --shallow --sdk $SDK \
    --app $T/app --board nucleo_h563zi/stm32h563xx --sysbuild \
    --cmake-arg=-DZENV_PRESET_NAME=default --cmake-arg=-DZENV_BUILD_TYPE=release \
    "--cmake-arg=-DCONF_FILE=$T/app/common.conf;$T/app/release.conf" \
    "--cmake-arg=-DZEPHYR_EXTRA_MODULES=$M/zlibs" \
    "$@" --out $D/verify/$name > $D/verify/$name.log 2>&1
  echo "$name exit $? $(( $(date +%s)-s ))s $(python3 -c "import json;c=json.load(open('$D/verify/$name/report.json'))['contexts'];print([(x['status'],x['key'],x.get('modules_added')) for x in c])" 2>&1 | tail -1)"; }
v source pre $PRE --module cmsis_6 --module hal_stm32 --module mcuboot --module mbedtls --module tf-psa-crypto
[ "${1:-}" = source-only ] && exit 0
v target post $POST --module cmsis_6 --module hal_stm32 --module mcuboot --module mbedtls --module tf-psa-crypto
