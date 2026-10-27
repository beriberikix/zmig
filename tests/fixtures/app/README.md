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

Run: `zmig scan --app tests/fixtures/app --board native_sim_64 --from v3.6.0 --to v3.7.0`
