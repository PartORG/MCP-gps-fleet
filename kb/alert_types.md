# Alert Types Reference

The telematics platform raises an alert when a vehicle's telemetry breaks one of
the rules below. Every alert has a type, a severity (low, medium, high) and a
short machine-written description. An alert is a signal to investigate, not a
verdict: many alerts turn out to have harmless explanations.

## gps_jump

Raised when a vehicle's reported position moves 10 km or more within 30 seconds
and then returns close to the previous location. No road vehicle can travel that
far that fast, so the jumped fix is physically impossible. Severity is low below
20 km and medium above. The usual causes are signal reflections (multipath),
tunnel exits, a damaged antenna or cable, and deliberate GPS jamming. See
"GPS Troubleshooting Guide" for the investigation steps.

## speeding

Raised when a telemetry sample reports a speed above the fleet policy limit of
130 km/h. Severity is medium up to 160 km/h and high above. Heavy vehicles
(Mercedes-Benz Actros, MAN TGX) are legally limited to 80 km/h on German
motorways, so any speeding alert on a truck is serious. Before contacting the
driver, rule out a false speed caused by a GPS jump: a spurious position fix can
produce an impossible speed for a single sample.

## fuel_drop

Raised when the fuel level falls 15 % or more beyond what the distance driven
explains. Severity is always high because the most common real cause is fuel
theft. Other causes are leaks, faulty fuel level sensors and readings taken on a
slope. See "Fuel Anomalies Guide" for how to tell them apart.

## long_idle

Raised when the engine runs at 0 km/h for 45 minutes or longer. Severity is low.
Idling wastes about 2-3 litres of diesel per hour on a van and more on a truck,
increases engine wear and is restricted in many German city centres. Common
reasons are waiting at loading docks, heating or cooling the cab during breaks,
refrigeration units that need the engine, and faulty ignition sensors.

## Severity and response times

High severity alerts should be reviewed by the fleet coordinator within 2 hours.
Medium within one working day. Low severity alerts are reviewed in the weekly
report unless the same vehicle raises the same alert three or more times in a
week, in which case it is treated as medium.
