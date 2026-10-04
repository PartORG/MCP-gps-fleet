# Incident #1842: GPS jump in Frankfurt city centre

- Vehicle: FM-0977 (Iveco Daily)
- Date: 2026-09-14
- Alert type: gps_jump
- Severity: medium
- Status: closed

## Observed
The vehicle reported a position 14 km north-east of its route within 30 seconds
while delivering in the Frankfurt banking district, then returned to the
previous location 30 seconds later.

## Investigation
The fixes before and after the jump were consistent with the planned route. The
implied speed of the jump was about 1700 km/h. No other alerts on this vehicle
that week.

## Root cause
Temporary GPS signal degradation caused by multipath between high-rise buildings.

## Resolution
No action required. Location added to the list of known trouble spots.
