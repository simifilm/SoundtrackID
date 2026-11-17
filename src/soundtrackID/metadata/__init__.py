"""Film + composer metadata enrichment."""

from soundtrackID import __version__

# How the lookups introduce themselves to MusicBrainz and Wikidata, which ask
# every program for a contact: the project's repository.
USER_AGENT = f"SoundtrackID/{__version__} (https://github.com/simifilm/SoundtrackID)"


def escape_sparql_string(s: str) -> str:
    """`s` to put between the double quotes of a Wikidata (SPARQL) query."""
    return s.replace("\\", "\\\\").replace('"', '\\"')
