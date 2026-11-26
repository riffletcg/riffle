"""Read, retain, and collect MTGO event decklists.

Event pages render client-side. The data is one JSON object assigned to
`window.MTGO.decklists.data` in a script tag, so we lift that out instead of
parsing HTML. The monthly index (/decklists/YYYY/MM) links every event as
/decklist/<slug>, where the slug ends in the date and event id:

    modern-challenge-32-2026-04-1812839681   -> 2026-04-18, event 12839681

Each event is fetched once and stored, normalized, as
<data_dir>/mtgo/<year>/<month>/<slug>.json, with the page's whole data object gzipped
in mtgo/raw/. Published lists don't change, so nothing stored is fetched again.

Fetching is a trickle: a job asks for a few pages every 10 minutes, all day, and
keeps a list of every event an index listed that isn't stored yet. Throttled,
mtgo.com doesn't refuse: it answers with stripped pages. How a run tells those
apart from missing data is described in riffle.ingest.mtgo.trickle.
"""
