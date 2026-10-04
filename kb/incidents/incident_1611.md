# Incident #1611: False speeding caused by a GPS jump

- Vehicle: FM-0567 (Mercedes-Benz Sprinter)
- Date: 2026-07-07
- Alert type: speeding
- Severity: high
- Status: closed

## Observed
A single sample reported 171 km/h in the Cologne city centre where traffic
allows at most 50 km/h.

## Investigation
The sample coincided with a position jump of 12 km. The speed had been calculated
from the jumped position. The samples before and after showed 35-45 km/h.

## Root cause
Measurement error: spurious speed derived from a bad GPS fix (multipath).

## Resolution
Alert dismissed, no driver contact. Speed calculation of the platform now ignores
samples flagged as position jumps.
