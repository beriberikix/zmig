Regression fixture for the scan's Kconfig usage index (DOGFOOD-5 follow-up, post-baseline).

`app/boards/arm/planck/planck_rev6_defconfig` is ZMK's file at `b8846cf6` (MIT,
zmkfirmware/zmk), unchanged: line 10 is `CONFIG_PINMUX=y`, which Zephyr removed and the
maintainers deleted in `802881b6`. The index read only `*.conf` files, so this line was
never reported. `boards/dz60rgb_unused.conf` is a constructed file the build doesn't read,
which must not be indexed. `prj.conf` stands in for ZMK's (no active assignments).
