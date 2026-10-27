# zmig fixture app

A small native_sim application that touches real v3.6.0 -> v3.7.0 changes. Each
reference below is chosen from upstream history, not invented:

| Reference | v3.6.0 | v3.7.0 |
|---|---|---|
| `-b native_sim_64` | board | deprecated alias of `native_sim/native/64` (boards/deprecated.cmake) |
| `CONFIG_CPLUSPLUS=y` (prj.conf) | deprecated alias, selects CPP | removed; configure aborts |
| `bus-speed` on `&can0` (overlay) | required property | deprecated |
| `DT_PATH(gpio_800)` (main.c) | node `/gpio@800` | renamed `/gpio_emul`; DT_NODE_EXISTS() turns false |
| `DT_PROP(can0, sample_point)` (main.c) | 875 in board DTS | no longer set; compile error |
| `CONFIG_BOARD` (main.c) | "native_sim_64" | "native_sim" |
| `CONFIG_CAN_AUTO_BUS_OFF_RECOVERY=n` (prj.conf) | disables automatic recovery | removed; renamed and inverted to `CONFIG_CAN_MANUAL_RECOVERY_MODE` (migration guide 3.7) |
| `can_recover()` (main.c) | available because automatic recovery is off | needs `CONFIG_CAN_MANUAL_RECOVERY_MODE`, else -ENOSYS (migration guide 3.7) |
| `#include <version.h>` (main.c) | generated header | moved to `<zephyr/version.h>`; old path kept by a deprecated compatibility option (migration guide 3.7) |

Run: `zmig scan --app tests/fixtures/app --board native_sim_64 --from v3.6.0 --to v3.7.0`
