# booking

Reservation backend for shared resources (meeting rooms, lab benches, ...).

Status: the Gel project is wired up but empty — the schema module has no types
yet and there is no application code.

Intended data model (not implemented yet):

* bookable resources, identified by a short unique code, with a display name and
  a capacity;
* reservations attached to a resource, each covering one temporal interval plus
  the name of whoever booked it, exposing the interval's start, end and length
  in minutes;
* an interval is half-open: it covers its start instant and stops just before
  its end instant, so back-to-back reservations are fine while any shared
  instant is a conflict. Malformed intervals (empty, reversed, open-ended) must
  never reach storage.

The local database is started with `/usr/local/bin/gel-start.sh`; connection
settings come from the environment.
