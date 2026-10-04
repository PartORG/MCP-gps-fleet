# Fuel Anomalies Guide

Fuel is the largest running cost of the fleet after salaries. This guide
explains how to investigate a fuel_drop alert and how to tell theft from
technical causes.

## How the fuel level is measured

Each vehicle has a float sensor in the tank that reports the fill level in
percent. The reading is affected by the tank's shape, by sloshing while driving
and by the vehicle's inclination. The platform smooths readings while driving;
single samples can still be off by a few percent.

## Signature: theft

Theft shows as a sudden drop of 15 % or more while the vehicle is parked with
the ignition off, typically at night, at motorway service areas, truck stops or
unfenced depots. The level stays low afterwards. Theft is a crime: secure the
data, report it to the police within 24 hours and inform the insurer. Prevention
measures are locking fuel caps, anti-siphon devices and parking in fenced,
lit areas.

## Signature: leak

A leak shows as a consumption that is steadily higher than normal over several
samples, also while driving, rather than one sudden drop. Drivers may notice a
diesel smell or stains under the vehicle. A leaking vehicle is a fire and
environmental risk: take it out of service and send it to the workshop the
same day.

## Signature: faulty sensor

A faulty or stuck float sensor produces drops that recover again without a
refuel: the level falls by 20 % and an hour later is back where it was. Jumps in
both directions and readings that do not change over long trips are typical. The
fix is a sensor replacement or recalibration in the workshop.

## Signature: slope and parking position

A vehicle parked on a steep slope can show a lower (or higher) level because the
fuel moves away from the sensor. The reading returns to normal as soon as the
vehicle drives on level ground. No action is needed, but the same parking place
will keep producing alerts.

## Fuel card mismatch

Compare fuel card transactions with the tank level. A refuel paid with the
fleet card that does not appear as a rise in the tank level can mean the fuel
went into another vehicle or a canister. This is investigated like theft.
