"""Dispatch scheduled jobs to launchd or systemd.

The scheduled jobs: `riffle sync` at set times each day, the MTGO trickle every
10 minutes, and a watch for each store every 5 minutes. launchd runs them on macOS
(riffle.ops.schedule.launchd); systemd user timers on Linux (riffle.ops.schedule.systemd).
Elsewhere there's no scheduler, and the schedule commands say so (`unsupported`) before
they write anything.

A store's watch is its own job because neither launchd nor systemd starts a second run
of a job while one is going (launchd skips the firing; systemd holds it until the run ends).
One job for every store would stop checking them all while any one of them fetched.

One label per job, under the project's domain: installing a job again replaces it,
it never adds a second one. Times are 24-hour HH:MM, in this machine's zone.
Everything that touches launchd goes through `run` so the logic is testable without it.
"""

from riffle.ops.schedule.dispatch import backend as backend
from riffle.ops.schedule.dispatch import install as install
from riffle.ops.schedule.dispatch import remove as remove
from riffle.ops.schedule.dispatch import status as status
from riffle.ops.schedule.dispatch import supported as supported
from riffle.ops.schedule.dispatch import unsupported as unsupported
from riffle.ops.schedule.jobs import LABEL as LABEL
from riffle.ops.schedule.jobs import PREFIX as PREFIX
from riffle.ops.schedule.jobs import SYNC as SYNC
from riffle.ops.schedule.jobs import TRICKLE as TRICKLE
from riffle.ops.schedule.jobs import Job as Job
from riffle.ops.schedule.jobs import Status as Status
from riffle.ops.schedule.jobs import fmt as fmt
from riffle.ops.schedule.jobs import log_path as log_path
from riffle.ops.schedule.jobs import next_run as next_run
from riffle.ops.schedule.jobs import parse_time as parse_time
from riffle.ops.schedule.jobs import parse_times as parse_times
from riffle.ops.schedule.jobs import watch_job as watch_job
from riffle.ops.schedule.launchd import build as build
from riffle.ops.schedule.launchd import parse_print as parse_print
from riffle.ops.schedule.launchd import plist_path as plist_path
from riffle.ops.schedule.launchd import read_times as read_times
