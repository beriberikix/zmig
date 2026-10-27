# Sysbuild salvage fixture

Like `xapp`, with one assignment per image that is valid at v4.4.0 and gone at
v4.5.0-rc3, so each image's target configure fails:

- `prj.conf:2` (app image): `CONFIG_BUILD_NO_GAP_FILL`, removed upstream in f4ea8b9
  ("kconfig: build: remove deprecated CONFIG_BUILD_NO_GAP_FILL");
- `sysbuild/mcuboot.conf:1` (MCUboot image): `CONFIG_BOOT_USB_DFU_NO`, defined in
  MCUboot's Kconfig at the v4.4.0 pin (ee39e2d) and gone at the v4.5.0-rc3 pin (725d63c).

`zmig scan` must salvage both (disable each line in a copy of the app), attribute each
removed-symbol finding to its image, and compare the rest.
