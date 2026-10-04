# Incident #1933: False idling caused by an ignition wiring fault

- Vehicle: FM-0699 (Iveco Daily)
- Date: 2026-09-05
- Alert type: long_idle
- Severity: low
- Status: closed

## Observed
The tracker reported ignition on at 0 km/h for 6 hours overnight at the Dresden
depot, but the depot manager confirmed the engine was off and the keys were in
the office.

## Investigation
The ignition signal wire of the tracker had been damaged during a dashboard
repair and was picking up voltage from another circuit.

## Root cause
Faulty ignition wiring giving a false "ignition on" signal.

## Resolution
Wire repaired by the workshop. The false idle alerts were removed from the driver
score.
