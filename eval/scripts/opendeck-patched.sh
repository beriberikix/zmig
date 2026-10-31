#!/bin/bash
# Local Zephyr checkouts for OpenDeck (DOGFOOD-5): upstream SHA + OpenDeck's SOURCE-commit
# patch set (zephyr/patch/zephyr/*.patch at ab8f6af^), applied like zenv's patch.sh
# (git apply, in sorted order, one commit per patch), on both sides. A patch that fails
# is recorded and skipped (never hand-fixed). OpenDeck's tree is only read (git show).
set -u
echo "patched.sh pid $$"
D=~/.cache/zmig/dogfood/d6; Z=$D/z; OD=$D/OpenDeck; MIRROR=~/.cache/zmig/mirrors/github.com_zephyrproject-rtos_zephyr.git
SRC_REF=ab8f6af^; PSRC=$(git -C $OD rev-parse $SRC_REF)
rm -rf $Z $D/patches-src; mkdir -p $Z $D/patches-src
for p in $(git -C $OD ls-tree --name-only $SRC_REF zephyr/patch/zephyr/ | LC_ALL=C sort); do git -C $OD show $SRC_REF:$p > $D/patches-src/$(basename $p); done
ls $D/patches-src | wc -l
for side in src:769c4f2846db675bc5c584b6d35551ad239c72da tgt:70be2ff0b565a3313128f5577f51cfeb3ebcf602; do
  name=${side%%:*}; sha=${side##*:}; R=$Z/$name
  git clone -q --shared --no-checkout $MIRROR $R && git -C $R checkout -q -b opendeck-patched $sha
  git -C $R config user.name "patch.sh"; git -C $R config user.email "patch.sh@zenv.github.com"
  echo -e "patch\tresult\tdetail" > $Z/$name.tsv
  for f in $(ls $D/patches-src/*.patch | LC_ALL=C sort); do
    b=$(basename $f); h=$(sha256sum $f | cut -d' ' -f1)
    if git -C $R apply --check $f 2>/dev/null; then
      out=$(git -C $R apply -v $f 2>&1); off=$(echo "$out" | grep -o "offset -\?[0-9]* lines\?" | sort -u | tr '\n' ' ')
      git -C $R add -A && git -C $R commit -q -m "$b" -m "OpenDeck patch zephyr/patch/zephyr/$b at OpenDeck $PSRC
sha256: $h"
      if [ -n "$off" ]; then res=offset; else res=clean; fi; echo -e "$b\t$res\t$off" >> $Z/$name.tsv
    elif git -C $R apply --reverse --check $f 2>/dev/null; then
      echo -e "$b\talready-applied\treverse-applies: upstream has it (zenv skips it)" >> $Z/$name.tsv
    else
      err=$(git -C $R apply --check $f 2>&1 | head -2 | tr '\n' ' ' | sed "s|$Z/$name/||g")
      fz=$(cd $R && patch -p1 --dry-run -s < $f 2>&1 | grep -E "fuzz|FAILED|offset" | head -3 | tr '\n' ' ')
      echo -e "$b\tfails\t$err | GNU patch --dry-run: ${fz:-applies}" >> $Z/$name.tsv
    fi
  done
  echo "== $name: $(git -C $R log --oneline $sha..HEAD | wc -l) patch commits on $sha, HEAD $(git -C $R rev-parse --short HEAD)"
  cut -f2 $Z/$name.tsv | tail -n +2 | sort | uniq -c
done
echo "patched.sh done"
