# GPS Troubleshooting Guide

This guide explains how to investigate a gps_jump alert or any other position
data that looks wrong. Work through the steps in order; most cases are resolved
by step 2.

## Step 1: Look at the fixes around the alert

Retrieve the vehicle's telemetry for at least 30 minutes before and after the
alert. Note the position before the jump, the jumped position and the position
after it. If the vehicle returns to its original route within one or two
samples, the jumped fix is almost certainly a measurement error, not real
movement. Calculate the implied speed: a 15 km jump in 30 seconds means
1800 km/h, which is impossible for a road vehicle.

## Step 2: Check the location for known trouble spots

Signal problems cluster at the same places. Tunnel exits (for example the Elbe
tunnel in Hamburg), dense city centres with tall buildings (Frankfurt banking
district), large multi-storey car parks and loading docks under steel roofs all
produce bad fixes. A single jump at such a place needs no further action. Record
the location if it is new.

## Step 3: Look for a repeating pattern

Search the vehicle's alert history. One jump in a month is normal. Several jumps
per week on the same vehicle at different places point to a hardware problem: a
loose antenna connector, a damaged antenna cable or water in the connector.
Several vehicles jumping at the same time at different places point to an
external cause such as a satellite problem or a solar storm.

## Step 4: Consider deliberate interference

GPS jammers ("privacy devices") are illegal in Germany but are sometimes used by
drivers who do not want to be tracked. Signs of jamming are positions that
freeze or jump while the vehicle is clearly moving, problems that start and stop
with one particular driver, and jumps that cluster around unscheduled stops.
Other vehicles near a jammer can be affected too. Suspected jamming is a
disciplinary matter: involve the fleet manager, not only the workshop.

## Step 5: Hardware check

If steps 1-4 do not explain the problem, book the vehicle for a tracker check.
The workshop inspects the antenna, the cable and the connector, verifies the
tracker firmware version and performs a test drive with a reference receiver.
