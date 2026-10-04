# Incident #1731: Position jump after the Elbe tunnel

- Vehicle: FM-0166 (Mercedes-Benz Vito)
- Date: 2026-08-02
- Alert type: gps_jump
- Severity: low
- Status: closed

## Observed
Directly after leaving the Elbe tunnel on the A7 in Hamburg the position jumped
11 km west and came back within one minute.

## Investigation
The vehicle had been without satellite reception for about 3 minutes in the
tunnel. The first fix after the tunnel exit was wrong; all following fixes were
correct.

## Root cause
Re-acquisition of the satellite signal after a tunnel. Known behaviour of the
tracker model.

## Resolution
No action required.
