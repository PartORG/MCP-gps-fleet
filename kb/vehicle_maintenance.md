# Vehicle Maintenance

Regular maintenance keeps vehicles safe and avoids expensive breakdowns. The
workshop also maintains the telematics hardware.

## Service intervals

Vans (Ford Transit, Volkswagen Crafter and Transporter, Mercedes-Benz Sprinter
and Vito, Iveco Daily, MAN TGE) are serviced every 30,000 km or once a year.
Trucks (Mercedes-Benz Actros, MAN TGX) are serviced every 60,000 km and have a
mandatory safety inspection every six months. The odometer value in the fleet
database is used to plan services.

## Taking a vehicle out of service

When a vehicle goes to the workshop the coordinator sets its status to
maintenance, so it is excluded from route planning. When the workshop releases
the vehicle the status must be set back to active. Forgotten status changes are
a common reason for vehicles that appear idle or unavailable without reason.

## Tracker hardware

The tracker is connected to the vehicle battery, the ignition signal, the fuel
level sensor and an external GPS antenna, usually behind the windscreen or on
the roof. Typical faults are loose or corroded antenna connectors (causing GPS
jumps), a disconnected ignition wire (causing wrong ignition on/off reports and
false idling alerts) and outdated firmware. The firmware is updated over the air;
vehicles that missed updates are updated during their next service.

## Fuel system

The fuel level sensor is checked at every service. Cracked fuel lines, loose tank
caps and damaged seals are the most common causes of leaks. A vehicle with a
suspected leak must not be driven to the workshop if diesel is dripping; it is
towed instead.

## Refrigerated vans

Some Sprinter vans carry a refrigeration unit powered by the engine. These
vehicles have to idle during deliveries to keep the cargo cold, so long idle
alerts on them are expected. Their refrigeration unit is serviced every six
months.
