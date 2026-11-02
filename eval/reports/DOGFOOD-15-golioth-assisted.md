# Dogfood 15: an assisted migration of golioth, v4.1.0 → v4.2.0

Date: 2026-11-02. This is the second assisted run of the `ASSISTANT.md` loop, after
[DOGFOOD-14](DOGFOOD-14-ardep-assisted.md) (ardep). It checks whether the loop holds on a
project of a different shape:
- both builds succeed from the start;
- the project's own module is the SDK itself, not a board;
- the work is a C API change, not Kconfig or board files.

This is an **assisted run**, so it is not scored ([ORACLE-RULES.md](../ORACLE-RULES.md)).

## Setup

- **App:** a copy of the Golioth firmware SDK at `1fc16f4afec5`
  ([configs.toml](../configs.toml) `golioth-B`): `examples/zephyr/hello` on
  `nucleo_h563zi/stm32h563xx`, with SDK 0.16.8. The edits went on a branch of the copy.
- **Loop:** as in DOGFOOD-14. The edited app was scanned with `--pin-source
  <original>/report.json`, then compared against the original. The scan command never
  changed.
- **zmig:** PR #29's branch for the first pass. Both scans were re-run on `c9afd7f`, which
  holds the two fixes below.

## The run

**The original scan** found 2 breaking and 5 risky findings, and both builds were `ok`.
All but the two module repins come from one Zephyr v4.2 change: network-management event
masks became 64-bit. In `examples/zephyr/common/net_connect.c`, that means:
- `NET_EVENT_IPV4_ADDR_ADD` is stored in a `uint32_t` and truncates to 1 (breaking);
- `net_mgmt_event_handler_t`'s event parameter widened, so the handler no longer matches
  (breaking);
- the `event_mask` field widened (risky);
- the target build has two new compiler warnings for the same lines (risky).

**Batch 1 (api)** was one approved edit, the assistant's own (`30a883e`): the handler's
`mgmt_event` and `wait_for_net_event`'s `event` go from `uint32_t` to `uint64_t`.

| `compare` | zmig | Result |
|---|---|---|
| **0** | #29 | nothing new; 2 fixed (the warnings); typedef "updated (2 -> 1 sites)", still breaking; the macro "unchanged", though AFTER graded it risky |
| **0** | `c9afd7f` | nothing new; 2 fixed; **2 de-escalated** (macro and typedef, "was breaking", now risky); 3 unchanged (`event_mask`, 2 module repins) |

**End state:**
- no breaking findings;
- 3 risky findings remain: the changed `event_mask` field the code now uses correctly, and
  two module repins;
- nothing unseen and nothing revealed; `unseen_source_build_broke` 0;
- nothing was run on hardware.

## What the run found in zmig

| # | Problem | Status |
|---|---|---|
| 1 | `compare` had no state for a finding that became less severe. The macro went from breaking to risky when the narrowing store went away, yet read "unchanged"; a drop to info would have read "fixed" | fixed in `c9afd7f` ("de-escalated", not gating) |
| 2 | A function-pointer typedef change was breaking whenever the type changed, even after the project's callback matched the new type | fixed in `c9afd7f`: risky when every using file parsed cleanly at the target and no callback mismatches |
| 3 | "Your code uses X, and X changed" findings stay open after a correct edit (`event_mask`, risky). zmig never takes an assistant's word that a use is handled | by design. The user acknowledges them; worth a design note if it becomes friction |

Both fixes leave the eval unchanged: all 17 configurations match their baselines. Every
function-pointer typedef finding in the unedited eval projects still has a mismatched
callback.

## Compared with DOGFOOD-14

- **One batch, no stops.** ardep needed five batches, and `compare` stopped on three of
  them; golioth needed one, and `compare` never stopped.
- **Pinning was used but never tested:** the scans were pinned, so whether the edited app
  still builds at v4.1.0 wasn't checked; nothing depended on it.
- **The gaps were different.** ardep's were about seeing anything at all while the target
  configure was failing. golioth's were about crediting a correct fix, which a grading
  based only on "you use what changed" can't do.
