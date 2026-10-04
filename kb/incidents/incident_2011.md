# Incident #2011: Speeding alert and position jump at the same moment

- Vehicle: FM-0166 (Mercedes-Benz Vito)
- Date: 2026-09-20
- Alert type: speeding
- Severity: high
- Status: closed

## Observed
A speeding alert of 162 km/h and a gps_jump alert were raised for the same
minute on the A2 near Hanover.

## Investigation
Neighbouring samples showed 95-100 km/h. This is the third position problem on
FM-0166 within two months (see incidents #1731 and #1905), so the workshop
checked the hardware even though firmware was current.

## Root cause
GPS antenna partly covered by a phone holder mounted on the windscreen, causing
poor reception and occasional bad fixes.

## Resolution
Phone holder moved. Speeding alert dismissed as a measurement error.
