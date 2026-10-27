# Cross-image mismatch fixture

Like `xapp`, but the images are configured inconsistently on purpose:

- `app.overlay` applies to the app image only and moves `slot1_partition` from
  0x82000+0x76000 (the board's layout) to 0x84000+0x74000;
- `sysbuild/mcuboot.overlay` applies to MCUboot only and changes something else.

MCUboot therefore swaps into a different secondary slot than the one the app's DFU
code writes to. `zmig scan` must report the partition disagreement (crossimage
analyzer), citing both images' zephyr.dts lines and `app.overlay:3`. It is the
guaranteed true positive for later checks.
