# MCTP Test Environment

A pytest-based test suite, run from a host PC, for exercising an
[OpenBIC](https://github.com/facebook/OpenBIC) controller's MCTP
(Management Component Transport Protocol) endpoint over SMBus/I2C.
Sibling project to
[openbic-test-environment](https://github.com/wrouwet/openbic-test-environment)
(which tests the same kind of controller over IPMB) -- same test style
and philosophy, different transport/protocol.

**Scope is deliberately MCTP-only**: MCTP transport framing (DSP0236)
and the MCTP Control Protocol. No PLDM or other higher-layer message
type is tested here; that would be a different project built on top of
this one's foundation, not an extension of it.

## Current status: built, not yet hardware-verified

**Read this before trusting any test result from this repo.** Every
other test-development effort in this project's family (see the sibling
openbic-test-environment repo) was built by writing code, then
immediately running it against real, connected hardware, adjusting
based on what actually happened. This repo is different: it was built
*before* the physical I2C bus it needs was wired up, at the user's
explicit direction, specifically so the test harness would be ready the
moment the hardware connection exists.

What that means concretely:

- The MCTP transport header, message-type byte, and MCTP Control header
  layouts (`mctp.py`) are confirmed against the actual OpenBIC source
  this project targets (peer-provided struct definitions, not just the
  DSP0236 spec from memory) -- see that file's docstrings for exactly
  what's confirmed.
- The SMBus PEC (CRC-8) engine is verified against the published
  CRC-8/SMBUS standard check value, independent of any hardware --
  see `mctp.py`'s PEC functions.
- The request/response round-trip logic, framing build/parse functions,
  and instance-ID-matching logic have been unit-verified in isolation
  (build a frame, parse it back, confirm the fields match) -- but NOT
  against a real MCTP endpoint responding over a real bus.
- Target facts (EID 0x09, I2C address 0x10 on `flexcomm3_lpi2c3`,
  IPMB-shaped response addressing) are confirmed with the team
  developing the OpenBIC port, not guessed.
- What's genuinely unverified: whether OpenBIC's actual MCTP stack
  behaves the way this suite assumes under real timing/bus conditions,
  whether any of the response body formats this suite is conservative
  about (see individual test docstrings) match reality, and whether the
  bridge firmware's SMBus PEC support (also newly built, see the
  firmware repo) actually interoperates correctly with a real
  SMBus-PEC-aware device -- none of which has been possible to check
  without the second I2C bus physically connected.

Once that wiring exists, running this suite for the first time should be
treated as genuine, first-contact integration testing -- expect to find
and fix real issues, the same way the sibling IPMB project did, not to
see a clean pass on the first try.

## What you need before you start

**Hardware:**

1. A FRDM-MCXA153 bridge board flashed with the firmware from
   [frdm-mcxa153-usb-i2c-hub](https://github.com/wrouwet/frdm-mcxa153-usb-i2c-hub)
   (the same one the sibling IPMB project uses) -- specifically a build
   including its SMBus (`WS`/`RS`/`XS`) command support, which this
   project depends on.
2. **A second, separate physical I2C bus connection** to the OpenBIC
   target's MCTP endpoint -- this is NOT the same wiring as the sibling
   IPMB project uses. The bridge chip (MCXA153) has only one hardware
   I2C peripheral, already committed to IPMB if that's also wired up, so
   this requires either a second bridge board dedicated to this bus, or
   a deliberate rewiring plan -- see this project's originating
   conversation for the options considered. MCTP lives on the target's
   `flexcomm3_lpi2c3`, I2C address `0x10`.
3. The OpenBIC target powered on.

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

Until the hardware from above is connected, expect every test to fail
at the `bridge` fixture with a clear "no bridge found" or "endpoint not
found on bus" error -- that's expected, not a code bug (see "Current
status" above).

### Reading the output

Same conventions as the sibling project: `-v -s` output by default, with
verbose wire-level `print()` diagnostics from `bridge.py` on every
command. `xfailed` (via `mctp_helpers.not_implemented()`) means a
confirmed, currently-real gap on the OpenBIC side, not a problem with
this suite -- see the sibling project's README for the full explanation
of why that's tracked as a permanent, visible test rather than skipped.

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
