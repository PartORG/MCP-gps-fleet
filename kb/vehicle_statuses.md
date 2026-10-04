# Vehicle Statuses

Every vehicle in the fleet has exactly one status. The status is set by the
fleet coordinator, not by the tracker, so it can be out of date. Always compare
the status with the vehicle's last telemetry.

## active

The vehicle is in normal service and expected to be driven on working days. An
active vehicle that has sent no telemetry for more than 24 hours on a working
day needs a check: either it is unused (and the status should become idle), or
the tracker has lost power or connectivity.

## idle

The vehicle is in working order but not currently assigned to routes, for example
because its driver is on leave or demand is low. Idle vehicles should be parked
at a depot. An idle vehicle that suddenly reports movement is unusual and should
be confirmed with the depot manager, because unauthorised use is possible.

## maintenance

The vehicle is in the workshop or waiting for repair. It should not move except
for test drives by the workshop. Telemetry from a vehicle in maintenance is
expected to stop for several days. The status must be set back to active after
the workshop releases the vehicle, otherwise the vehicle appears unavailable in
route planning.

## offline

The tracker is not reporting and the reason is known: the vehicle is
deregistered, waiting to be sold, or the tracker was removed. Offline vehicles
are excluded from driver scoring and most alert reviews.

## Silent vehicles

A vehicle can stop sending telemetry for reasons unrelated to its status: an
expired SIM card, a disconnected tracker, a flat vehicle battery or poor mobile
coverage at the parking place. If an active vehicle is silent, first check when
and where it last reported. A last position at a depot usually means a parked
vehicle; a last position on a motorway at speed suggests a tracker or power
problem and should be checked the same day.
