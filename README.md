# MCTP Test Environment

A pytest-based test suite, run from a host PC, for exercising an
[OpenBIC](https://github.com/facebook/OpenBIC) controller's MCTP
(Management Component Transport Protocol) endpoint over SMBus/I2C.
Sibling project to
[ipmi-test-environment](https://github.com/wrouwet/ipmi-test-environment)
(which tests the same kind of controller over IPMB) -- same test style
and philosophy, different transport/protocol.

**Scope is deliberately MCTP-only**: MCTP transport framing (DSP0236)
and the MCTP Control Protocol. No PLDM or other higher-layer message
type is tested here; that would be a different project built on top of
this one's foundation, not an extension of it.

## Current status: hardware-verified, 18 passed / 3 skipped (2026-08-28)

This repo's first draft was built *before* the I2C bus existed, at the
user's direction -- see git history. That phase is long over. First
contact found the missing DSP0237 3-byte SMBus block-write wrapper
(`mctp.build_smbus_block_wrapper()`), which OpenBIC's `mctp_smbus_read()`
silently required; once added, real bidirectional MCTP traffic worked
and the suite has grown into a solid green baseline.

**Bus:** since the 2026-08-27 consolidation the MCTP endpoint is at
address `0x10` on **`flexcomm2_lpi2c2`, the same physical bus as IPMB**
(`0x20`) -- one FRDM-MCXA153 bridge drives IPMI + MCTP + PLDM + SPDM
with no rewiring. (Earlier drafts of this README described a separate
`flexcomm3_lpi2c3` bus; that's gone.)

**Verified on the wire:**
- Get Endpoint ID: EID `0x09`, endpoint-type byte `0x11` (STATIC /
  BRIDGE) -- decoded and asserted, not just printed.
- Get MCTP Version Support: MCTP Base 1.3 (`01 f1 f3 ff 00`).
- Get Message Type Support: `{0x00 Control, 0x01 PLDM, 0x05 SPDM,
  0x7E Vendor-PCI}` -- exact-set assertion, so it fails loudly the next
  time the set changes (it already caught PLDM→+Vendor-PCI→+SPDM).
- Get Endpoint UUID + Resolve Endpoint ID: both implemented now (were
  `not_implemented()` gaps; the strict-xfail mechanism flipped them to
  loud XPASS when the peer landed them, and they're real tests since).
- Set Endpoint ID: idempotent round trip, **and** a real EID change
  (`0x09` → `0x0A`) confirmed to survive a cold reboot via NVS
  (`test_set_endpoint_id_persists_across_reboot`, `MCTP_INTERACTIVE=1`).
- Fragmentation / reassembly, both directions, incl. a `pkt_seq`
  wraparound (`test_fragmentation.py`, `test_reverse_fragmentation.py`).
- PEC-corruption framing, and the 1-deep-TX-queue back-to-back probe
  (`test_back_to_back_requests_queue_depth` -- documents a genuine bus
  race and is mildly timing-flaky; passes on retry).

The `not_implemented()` (`xfail(strict=True)`) mechanism is still how
new gaps get tracked -- see "Tests that document unimplemented
features" below -- there just aren't any open right now.

## What you need before you start

**Hardware:**

1. A FRDM-MCXA153 bridge board flashed with the firmware from
   [frdm-mcxa153-usb-i2c-hub](https://github.com/wrouwet/frdm-mcxa153-usb-i2c-hub)
   (the same one the sibling IPMB project uses) -- specifically a build
   including its SMBus (`WS`/`RS`/`XS`) command support, which this
   project depends on.
2. The OpenBIC target wired to that bridge's I2C pins and powered on.
   Since the 2026-08-27 bus consolidation the MCTP endpoint is a second
   I2C target address (`0x10`) on the **same** physical bus as IPMB
   (`0x20`) -- target `flexcomm2_lpi2c2`, one dual-address LPI2C driver
   patch on the firmware side. No second bridge or separate wiring; the
   sibling `ipmi-test-environment` hardware setup is exactly this bus.

**Software**, on the host PC: same as the sibling project -- Linux,
Python 3.9+ with `venv`, and `dialout` group membership (see the sibling
project's README for the full explanation of that gotcha, identical
here).

## Quick start

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests/
```

To also save a copy of the full run to `test_report.txt` (same
convention as the sibling ipmi-test-environment project), use
`./run_tests.sh` instead of the raw `pytest` command above.

Without the hardware connected, expect every test to fail at the
`bridge` fixture with a clear "no bridge found" error -- that's
expected, not a code bug. With it connected, expect **18 passed /
3 skipped** (the 3 skips are interactive: `MCTP_INTERACTIVE=1` plus a
person able to cold-reboot the board). See "Current status" above.

### Reading the output

Same conventions as the sibling project: `-v -s` output by default, with
verbose wire-level `print()` diagnostics from `bridge.py` on every
command. `xfailed` (via `mctp_helpers.not_implemented()`) would mean a
confirmed, currently-real gap on the OpenBIC side -- there are none open
right now, but the mechanism stays; see the sibling project's README
for why gaps are tracked as permanent visible tests rather than skipped.

## Layout

```
bridge.py                Python client for the bridge's text command protocol
                          (near-identical copy of the sibling project's,
                          plus smbus_write()/smbus_read()/smbus_write_read())
mctp.py                  MCTP transport + Control Protocol framing
                          (build/parse), and the SMBus PEC engine
conftest.py               pytest fixture that connects the bridge once per session
tests/config.py           shared constants (target EID/address, our own)
tests/mctp_helpers.py     shared request/response round-trip logic,
                          PEC verification for slave-mode-captured
                          responses, and the not_implemented() marker
tests/test_bus.py         bus presence (everything else assumes this passes)
tests/test_mctp_control.py
                          MCTP Control Protocol commands
tests/test_framing_edge_cases.py
                          checksum/PEC corruption handling
```

## Tests that document unimplemented features

Same mechanism as the sibling project: `mctp_helpers.not_implemented()`
is `pytest.mark.xfail(strict=True)`. A gap gets written as a real test
that's expected to fail, with a `reason` explaining what's missing and
how it's known -- and the moment real support lands and the test starts
passing, the run fails loudly (XPASS) instead of quietly staying green.
See the sibling project's README for the full rationale; it applies here
unchanged.

## Adding tests

Follow the sibling project's conventions: one file per protocol area,
`mctp_helpers.send_mctp_control_command()` for the request/response
round trip, prefer confirming behavior against source (via whoever's
developing the OpenBIC port) or live hardware observation over guessing
at spec-typical behavior -- especially important here given how much of
this repo was necessarily built without hardware access at all (see
"Current status" above). The first real hardware run of any new test
here should be treated with extra scrutiny, not assumed correct just
because it was carefully written.
