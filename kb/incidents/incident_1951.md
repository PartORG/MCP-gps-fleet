# Incident #1951: Fuel card refuel not visible in the tank

- Vehicle: FM-0204 (Volkswagen Transporter)
- Date: 2026-09-09
- Alert type: fuel_drop
- Severity: high
- Status: open

## Observed
The fuel card was used for 68 litres at a filling station in Bremen, but the
tank level rose by only 10 %. Two days later a fuel_drop alert was raised
because the level was lower than the driven distance explains.

## Investigation
The fuel sensor was checked and works correctly. The receipt shows a second
pump number. The investigation into possible fuel card misuse is ongoing.

## Root cause
Suspected fuel card fraud: fuel paid with the fleet card went into another
vehicle or canisters.

## Resolution
Fuel card blocked and replaced. Case handed to the fleet manager.
