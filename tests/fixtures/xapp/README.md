# zmig cross-compile fixture

A minimal app for `nrf52840dk/nrf52840` (ARM Cortex-M4F), built with sysbuild
and MCUboot by the Zephyr SDK's `arm-zephyr-eabi` toolchain. It checks the paths the
native_sim fixture can't: SDK toolchain discovery, `CMAKE_NM` from the build, sysbuild
image layout, SPDX for a cross build, and ROM/RAM against `arm-zephyr-eabi-size`.

It is a real board rather than a `qemu_*` one: the only emulated ARM board with MCUboot
slots, `qemu_cortex_m0` (nRF51, 16 KB RAM), can't fit MCUboot's default crypto (the link
fails with `region 'RAM' overflowed`). Nothing here needs the hardware; it only builds.

The pair is v4.4.0 → v4.5.0-rc3 because SDK 1.0.x declares itself incompatible with
Zephyr releases that ask for a pre-1.0 SDK (everything before 4.4).

```sh
pip install 'zmig[zephyr,mcuboot]'
zmig scan --app tests/fixtures/xapp --board nrf52840dk/nrf52840 --sysbuild \
  --module mcuboot --module hal_nordic --module cmsis_6 --module mbedtls --module tf-psa-crypto \
  --from v4.4.0 --to v4.5.0-rc3
```
