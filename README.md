# MusicBrainz Pop Extras for Picard

MusicBrainz Pop Extras is a Picard plugin that retrieves detailed MusicBrainz
recording and work relationships and writes 19 documented Pop Extras fields.

The plugin can optionally write six Discogs and AllMusic bridge fields derived
from MusicBrainz URL relationships. It does not contact the Discogs or AllMusic
APIs or write genres, styles, companies, format details, notes, or external
service credits.

## Download

[Download the latest installable plugin ZIP](../../releases/latest/download/picard_pop_extras.zip)

Download `picard_pop_extras.zip`. Do not use GitHub's automatically generated
"Source code" archives as Picard installation files.

## Installation

1. Download `picard_pop_extras.zip` from the latest release.
2. In Picard, open **Options → Plugins**.
3. Install the ZIP without extracting it.
4. Restart Picard if prompted.
5. Enable **MusicBrainz Pop Extras** and confirm the expected version is shown.

## Documentation

- [Release history](CHANGELOG.md)

## Compatibility

| Plugin series | Picard | Plugin API |
| --- | --- | --- |
| 0.1.x | 2.x | 2.0 |

## Normal workflow

1. Install and enable **MusicBrainz Pop Extras** in Picard.
2. Leave **Write Pop Extras fields to files** enabled for normal use.
3. Enable the external-link option if you want the six Discogs and AllMusic
   bridge fields.
4. Avoid enabling another plugin that writes the same bridge fields.
5. Load, process, and save a release normally in Picard.
6. If you use a separate music library application, refresh its metadata from
   the saved files.

## Pop Extras fields

- `MUSICBRAINZ_PROGRAMMING`
- `MUSICBRAINZ_INSTRUMENT_ARRANGER`
- `MUSICBRAINZ_VOCAL_ARRANGER`
- `MUSICBRAINZ_QUALIFIED_PRODUCER`
- `MUSICBRAINZ_QUALIFIED_ENGINEER`
- `MUSICBRAINZ_PERFORMANCE_CREDITS`
- `MUSICBRAINZ_PRODUCTION_CREDITS`
- `MUSICBRAINZ_RECORDED_AT`
- `MUSICBRAINZ_MIXED_AT`
- `MUSICBRAINZ_MASTERED_AT`
- `MUSICBRAINZ_RECORDING_DATE`
- `MUSICBRAINZ_WORK_CREDITS`
- `MUSICBRAINZ_PUBLISHERS`
- `MUSICBRAINZ_ISWC`
- `MUSICBRAINZ_WORK_LANGUAGE`
- `MUSICBRAINZ_WORK_IDENTIFIERS`
- `MUSICBRAINZ_SOURCE_MATERIAL`
- `MUSICBRAINZ_WORK_GENEALOGY`
- `MUSICBRAINZ_SEMANTIC_JSON`


## Optional bridge fields

- `DISCOGS_RELEASE_ID`
- `DISCOGS_RELEASE_URL`
- `DISCOGS_MASTER_ID`
- `DISCOGS_MASTER_URL`
- `ALLMUSIC_RELEASE_URL`
- `ALLMUSIC_RELEASEGROUP_URL`

Release-level relationships supply release links; release-group relationships supply master/group
links. Cross-attached Discogs relationships are accepted as fallbacks. When several eligible URLs
exist, selection is deterministic.

## Retrieval and safety

- Requests use Picard's configured MusicBrainz server and work with a local mirror.
- A seven-day, server-isolated entity cache is stored beside Picard's plugin directory.
- Complete recording objects embedded in the release response are reused.
- Identical in-flight requests are coalesced and concurrency is bounded.
- The optional acceleration profile applies only to loopback servers.
- Tracks match by release-track MBID, with recording MBID accepted only when unique in the release.
- No title, filename, disc/track-position, or familiar-artist guessing is used.
- Owned stale tags are removed only after complete successful retrieval and an exact track match.
- Failed or partial retrieval leaves existing saved values untouched.
- Unknown, generic Picard, rating, lyrics, ReplayGain, and artwork fields are never
  cleaned by this plugin.

## Safe first use

Test the plugin on copied audio files before applying it to a large collection.
After saving the files in Picard, inspect the resulting Pop Extras fields and
confirm that unrelated metadata remains unchanged.

A second Picard refresh and save should produce the same values. If you use a
separate music library application, refresh its metadata from the saved files
and verify that multivalue fields remain independently selectable.


## License

This project is licensed under the GNU General Public License, version 2 or
(at your option) any later version. See [LICENSE](LICENSE).
