#!/bin/bash
# Cold shallow module fetch vs restoring a cached workspace tarball from local disk.
set -u
M=~/.cache/zmig/dogfood/d5/meas; Z=~/code/zmig/.venv/bin/python
MIRROR=~/.cache/zmig/mirrors/github.com_zephyrproject-rtos_zephyr.git
SHA=30de5e8a2ceef99177c6c96785a8888b978154f0
CACHED=~/.cache/zmig/workspaces/$SHA
evict(){ $Z -c "import os,sys
for p in sys.argv[1:]:
  fd=os.open(p,os.O_RDONLY); os.posix_fadvise(fd,0,0,os.POSIX_FADV_DONTNEED); os.close(fd)" "$@"; }
t(){ local s=$(date +%s.%N); "$@"; local rc=$?; echo "$(echo "$(date +%s.%N)-$s"|bc) s (exit $rc)"; }
rm -rf $M/ws $M/restore-* $M/*.tar.zst; mkdir -p $M/ws/zephyr $M/ws/.west
echo "== zephyr tree export from the local mirror (git archive)"; t bash -c "git --git-dir=$MIRROR archive $SHA | tar -x -C $M/ws/zephyr"
printf '[manifest]\npath = zephyr\nfile = west.yml\nproject-filter = -.*,+cmsis,+cmsis_6,+hal_stm32,+mbedtls,+zcbor\n[zephyr]\nbase = zephyr\n' > $M/ws/.west/config
cd $M/ws
for m in cmsis cmsis_6 hal_stm32 mbedtls zcbor; do
  echo "== shallow cold fetch: $m"; t $Z -m west update --narrow -o=--depth=1 $m >$M/$m.out 2>$M/$m.err; tail -1 $M/$m.out
done
echo "== sizes (shallow): checkout+.git, then .git only"; du -sh $M/ws/zephyr $M/ws/modules/*/* | sed "s|$M/||"; du -sh $M/ws/modules/*/*/.git | sed "s|$M/||"
echo "== same modules in zmig's full-clone workspace: .git only"; du -sh $CACHED/modules/*/*/.git | sed "s|$HOME|~|"
cd $M
echo "== tar+zstd of the shallow modules"; t tar -C ws -I 'zstd -3 -T0' -cf shallow.tar.zst modules/hal/cmsis modules/hal/cmsis_6 modules/hal/stm32; ls -l shallow.tar.zst | awk '{printf "%.0f MB\n",$5/1e6}'
echo "== tar+zstd of zmig's full-clone cached modules (cmsis cmsis_6 hal_stm32)"; t tar -C $CACHED -I 'zstd -3 -T0' -cf full.tar.zst modules; ls -l full.tar.zst | awk '{printf "%.0f MB\n",$5/1e6}'
du -sh $CACHED/modules
for f in shallow full; do
  evict $f.tar.zst; sync; mkdir restore-$f
  echo "== restore $f.tar.zst from disk (page cache evicted)"; t bash -c "tar -C restore-$f -I 'zstd -d -T0' -xf $f.tar.zst && sync -f restore-$f"
done
