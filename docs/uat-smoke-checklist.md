# Soren UAT Smoke Checklist

Use this checklist before declaring the project ready for user testing.

## 1) Run one full execution cycle

- `soren cycle run`
- Expectation: cycle completes without crash and emits a report in `data/reports`.

## 2) Confirm history/report visibility

- `soren history show --limit 5`
- Expectation: recent cycles table renders and includes the latest cycle result.

## 3) Confirm encrypted credentials are available

- `soren credentials list`
- Expectation: required `credential_ref` entries are present for enabled task sources.

## 4) Reset browser state baseline

- `soren vm reset`
- Expectation: browser reset reports success and returns a clean window state.

## Pass Criteria

- All four checks succeed with no blocking errors.
- Latest cycle report includes task result and telemetry fields.
- No plaintext credentials are printed in command output.
