#!/bin/bash
# §2.3: cold scan (empty zmig cache except a hardlinked Zephyr mirror), CANnectivity C1,
# --shallow vs full module fetch.
set -u
D=~/.cache/zmig/dogfood/d5; X=$D/../d4/x/v1.1.0/cannectivity; cd ~/code/zmig; source .venv/bin/activate
run(){ local name=$1; shift; local C=~/.cache/zmig/cold-$name; rm -rf $C; mkdir -p $C/mirrors
  cp -al ~/.cache/zmig/mirrors/github.com_zephyrproject-rtos_zephyr.git $C/mirrors/
  local s=$(date +%s)
  env -u PYTHONPATH zmig scan --cache-dir $C --app $X/app --board usb2canfdv1 --module cmsis --module hal_stm32 \
    --cmake-arg=-DZEPHYR_EXTRA_MODULES=$X --cmake-arg=-DFILE_SUFFIX=usbd_next --sdk ~/.cache/zmig/sdks/zephyr-sdk-0.16.8 \
    --from 7823374e872145b5bd018bfe447839eb36042611 --to 30de5e8a2ceef99177c6c96785a8888b978154f0 "$@" --out $D/cold/$name > $D/cold/$name.human 2> $D/cold/$name.err
  echo "== $name exit $? wall $(( $(date +%s) - s )) s"
  grep -E "updating modules.*done|: (ok|build-failed)" $D/cold/$name.err
  du -sh $C/workspaces/*/modules/*/*/.git 2>/dev/null | sed "s|$HOME|~|"
  python3 -c "import json;r=json.load(open('$D/cold/$name/report.json'));print([n['area'] for n in r['not_checked']])"
  head -3 $D/cold/$name.human | tail -2; }
mkdir -p $D/cold
run shallow --shallow
run full
