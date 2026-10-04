# GPS Accuracy Explained

The trackers in our vehicles use satellite positioning (GPS and Galileo). This
document explains how accurate positions are and why they sometimes are not.

## Normal accuracy

Under open sky a tracker position is typically accurate to 3-5 metres. The
reported speed is calculated from the change of position and the Doppler shift
of the satellite signals and is accurate to about 1 km/h when moving. Heading is
unreliable when the vehicle is standing still.

## Multipath and urban canyons

In city centres signals bounce off buildings before reaching the antenna. The
receiver then measures a longer distance to the satellite and computes a wrong
position. Errors are usually tens of metres, but with few satellites in view a
single fix can be off by kilometres. This is called multipath; streets lined by
tall buildings are called urban canyons.

## Tunnels and covered areas

In tunnels no satellites are visible. Some trackers keep reporting the last known
position, others estimate the position from the last speed and heading (dead
reckoning). When the vehicle leaves the tunnel the receiver needs a few seconds
to compute a fresh position, and the first fix after the exit can be far off.

## Cold start

After the tracker was without power for a long time it has to download satellite
orbit data again. During the first one to two minutes after a cold start,
positions can be wrong by several kilometres. Jumps immediately after ignition
on, after a long stop, are therefore usually harmless.

## Space weather

Strong solar storms disturb the ionosphere and reduce accuracy for all receivers
in a region at the same time. They are rare, last hours to a day, and affect the
whole fleet, so they show up as many simultaneous alerts on unrelated vehicles.

## Jamming and spoofing

A jammer is a small transmitter that drowns the satellite signals in noise; the
receiver then reports no position or a frozen one. Spoofing sends fake satellite
signals and can move the reported position anywhere. Both are illegal. Jamming
devices are cheap and plug into the cigarette lighter socket.
