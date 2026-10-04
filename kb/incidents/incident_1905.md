# Incident #1905: GPS jumps after a missed firmware update

- Vehicle: FM-0166 (Mercedes-Benz Vito)
- Date: 2026-09-01
- Alert type: gps_jump
- Severity: low
- Status: closed

## Observed
Several short position jumps per day, mostly right after the ignition was
switched on in the morning.

## Investigation
The tracker was running firmware 3.1, while the fleet had been updated to 3.4.
Firmware 3.1 reports positions before the receiver has a valid fix after a cold
start.

## Root cause
Outdated tracker firmware reporting invalid fixes after cold start.

## Resolution
Firmware updated over the air. The vehicle had missed the update because it was
parked in an underground garage without mobile coverage.
