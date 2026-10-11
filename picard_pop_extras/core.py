#!/usr/bin/env python3
"""Pure Pop Extras projection shared by Picard and command-line validation.

This first stage is deliberately read-only with respect to audio files. It consumes an explorer
output directory (or SEMANTIC_MAP.json), optionally enriches track IDs from raw/release.json, and
writes a deterministic POP_EXTRAS.json review artifact. The Picard package also
uses :class:`PopExtrasProjector` to project one release track at a time.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


FORMAT = "MusicBrainz Pop Extras Map"
VERSION = "0.1.1"
SEMANTIC_SCHEMA = 1
DISPLAY_SEPARATOR = " - "

PERFORMANCE_TYPES = {"instrument", "vocal", "performer"}
INTRINSIC_RECORDING_SOURCE_TYPES = {"samples material", "mashes up"}

FIELD_PROGRAMMING = "MUSICBRAINZ_PROGRAMMING"
FIELD_INSTRUMENT_ARRANGER = "MUSICBRAINZ_INSTRUMENT_ARRANGER"
FIELD_VOCAL_ARRANGER = "MUSICBRAINZ_VOCAL_ARRANGER"
FIELD_QUALIFIED_PRODUCER = "MUSICBRAINZ_QUALIFIED_PRODUCER"
FIELD_QUALIFIED_ENGINEER = "MUSICBRAINZ_QUALIFIED_ENGINEER"
FIELD_PERFORMANCE = "MUSICBRAINZ_PERFORMANCE_CREDITS"
FIELD_PRODUCTION = "MUSICBRAINZ_PRODUCTION_CREDITS"
FIELD_RECORDED_AT = "MUSICBRAINZ_RECORDED_AT"
FIELD_MIXED_AT = "MUSICBRAINZ_MIXED_AT"
FIELD_MASTERED_AT = "MUSICBRAINZ_MASTERED_AT"
FIELD_RECORDING_DATE = "MUSICBRAINZ_RECORDING_DATE"
FIELD_WORK_CREDITS = "MUSICBRAINZ_WORK_CREDITS"
FIELD_PUBLISHERS = "MUSICBRAINZ_PUBLISHERS"
FIELD_ISWC = "MUSICBRAINZ_ISWC"
FIELD_WORK_LANGUAGE = "MUSICBRAINZ_WORK_LANGUAGE"
FIELD_WORK_IDENTIFIERS = "MUSICBRAINZ_WORK_IDENTIFIERS"
FIELD_SOURCE_MATERIAL = "MUSICBRAINZ_SOURCE_MATERIAL"
FIELD_WORK_GENEALOGY = "MUSICBRAINZ_WORK_GENEALOGY"
FIELD_SEMANTIC_JSON = "MUSICBRAINZ_SEMANTIC_JSON"

MAPPER_FIELDS = frozenset({
    FIELD_PROGRAMMING,
    FIELD_INSTRUMENT_ARRANGER,
    FIELD_VOCAL_ARRANGER,
    FIELD_QUALIFIED_PRODUCER,
    FIELD_QUALIFIED_ENGINEER,
    FIELD_PERFORMANCE,
    FIELD_PRODUCTION,
    FIELD_RECORDED_AT,
    FIELD_MIXED_AT,
    FIELD_MASTERED_AT,
    FIELD_RECORDING_DATE,
    FIELD_WORK_CREDITS,
    FIELD_PUBLISHERS,
    FIELD_ISWC,
    FIELD_WORK_LANGUAGE,
    FIELD_WORK_IDENTIFIERS,
    FIELD_SOURCE_MATERIAL,
    FIELD_WORK_GENEALOGY,
    FIELD_SEMANTIC_JSON,
})

SESSION_FIELDS = {
    "recorded at": FIELD_RECORDED_AT,
    "mixed at": FIELD_MIXED_AT,
    "mastered at": FIELD_MASTERED_AT,
}


def stable_json(value: Any, *, pretty: bool = False) -> str:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return json.dumps(value, **options)


def _normalized(value: Any) -> Any:
    """Recursively normalize dictionaries and sets while preserving list semantics."""
    if isinstance(value, dict):
        return {key: _normalized(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, tuple):
        return [_normalized(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalized(item) for item in value)
    return value


def _entity(entity: dict[str, Any] | None, default_type: str | None = None) -> dict[str, Any]:
    entity = entity or {}
    result: dict[str, Any] = {
        "type": entity.get("type") or entity.get("entity_type") or default_type,
        "id": entity.get("id"),
        "name": entity.get("name") or entity.get("title"),
    }
    if entity.get("musicbrainz_type"):
        result["musicbrainz_type"] = entity["musicbrainz_type"]
    if entity.get("musicbrainz_type_id"):
        result["musicbrainz_type_id"] = entity["musicbrainz_type_id"]
    if entity.get("disambiguation"):
        result["disambiguation"] = entity["disambiguation"]
    return result


def _relationship(
    entry: dict[str, Any],
    *,
    scope: str,
    provenance: str,
) -> dict[str, Any]:
    rel = entry.get("relationship") or {}
    result: dict[str, Any] = {
        "scope": scope,
        "source": _entity(entry.get("source"), scope),
        "type": rel.get("type"),
        "type_id": rel.get("type_id"),
        "direction": rel.get("direction"),
        "target": _entity(entry.get("target")),
        "attributes": sorted(set(rel.get("attributes") or [])),
        "attribute_values": _normalized(rel.get("attribute_values") or {}),
        "attribute_ids": _normalized(rel.get("attribute_ids") or {}),
        "attribute_credits": _normalized(rel.get("attribute_credits") or {}),
        "begin": rel.get("begin"),
        "end": rel.get("end"),
        "ended": bool(rel.get("ended", False)),
        "ordering_key": rel.get("ordering_key"),
        "source_credit": rel.get("source_credit") or "",
        "target_credit": rel.get("target_credit") or "",
        "provenance": provenance,
    }
    if entry.get("found_at"):
        result["found_at"] = sorted(set(entry["found_at"]))
    if entry.get("classification"):
        result["classification"] = _normalized(entry["classification"])
    return result


def _dedupe_relations(relations: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed = {stable_json(relation): relation for relation in relations}
    return [keyed[key] for key in sorted(keyed)]


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})


def _date_text(rel: dict[str, Any]) -> str | None:
    begin = rel.get("begin")
    end = rel.get("end")
    if begin and end and begin != end:
        return f"{begin}–{end}"
    return begin or end


def _detail_parts(rel: dict[str, Any], *, include_date: bool = True) -> list[str]:
    details = list(sorted(set(rel.get("attributes") or [])))
    for key, value in sorted((rel.get("attribute_values") or {}).items()):
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
        else:
            rendered = str(value)
        details.append(f"{key}={rendered}")
    if rel.get("ordering_key") is not None:
        details.append(f"order={rel['ordering_key']}")
    if include_date and (date := _date_text(rel)):
        details.append(f"date={date}")
    return details


def _credit_text(entry: dict[str, Any]) -> str:
    rel = entry.get("relationship") or {}
    target = entry.get("target") or {}
    name = target.get("name") or target.get("title") or target.get("id") or "unknown"
    role = rel.get("type") or "relationship"
    details = _detail_parts(rel)
    result = f"{name}{DISPLAY_SEPARATOR}{role}"
    if details:
        result += f" ({'; '.join(details)})"
    credited = rel.get("target_credit")
    if credited and credited != name:
        result += f" [credited as: {credited}]"
    return result


def _target_text(entry: dict[str, Any]) -> str:
    rel = entry.get("relationship") or {}
    target = entry.get("target") or {}
    name = target.get("name") or target.get("title") or target.get("id") or "unknown"
    role = rel.get("type") or "relationship"
    result = f"{name}{DISPLAY_SEPARATOR}{role}"
    details = _detail_parts(rel)
    if details:
        result += f" ({'; '.join(details)})"
    credited = rel.get("target_credit")
    if credited and credited != name:
        result += f" [credited as: {credited}]"
    if target.get("id"):
        result += f" [{target['id']}]"
    return result


def _has_qualifier(entry: dict[str, Any]) -> bool:
    rel = entry.get("relationship") or {}
    return bool(
        rel.get("attributes")
        or rel.get("attribute_values")
        or rel.get("begin")
        or rel.get("end")
        or rel.get("source_credit")
        or rel.get("target_credit")
    )


def _index_by_source(entries: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        source_id = (entry.get("source") or {}).get("id")
        if source_id:
            result[source_id].append(entry)
    return result


def _flatten_groups(groups: dict[str, Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for entries in (groups or {}).values():
        if isinstance(entries, list):
            result.extend(entries)
    return result


def _raw_track_index(raw_release: dict[str, Any] | None) -> dict[tuple[int, int], dict[str, Any]]:
    result: dict[tuple[int, int], dict[str, Any]] = {}
    for medium in (raw_release or {}).get("media") or []:
        medium_position = medium.get("position")
        for track in medium.get("tracks") or []:
            track_position = track.get("position")
            if isinstance(medium_position, int) and isinstance(track_position, int):
                result[(medium_position, track_position)] = track
    return result


def _work_envelope(identity: dict[str, Any]) -> dict[str, Any]:
    identifiers = [
        {
            "type": attribute.get("type"),
            "type_id": attribute.get("type-id") or attribute.get("type_id"),
            "value": attribute.get("value"),
        }
        for attribute in identity.get("attributes") or []
    ]
    identifiers.sort(key=stable_json)
    return {
        "id": identity.get("id"),
        "title": identity.get("title"),
        "role": "performance",
        "type": identity.get("type"),
        "type_id": identity.get("type-id") or identity.get("type_id"),
        "language": identity.get("language"),
        "languages": sorted(set(identity.get("languages") or [])),
        "iswcs": sorted(set(identity.get("iswcs") or [])),
        "identifiers": identifiers,
    }


def _add_field(fields: dict[str, list[str]], name: str, values: Iterable[str]) -> None:
    clean = _dedupe_strings(values)
    if clean:
        fields[name] = clean


def map_semantic(
    semantic: dict[str, Any],
    raw_release: dict[str, Any] | None = None,
) -> dict[str, Any]:
    release_identity = (semantic.get("release") or {}).get("identity") or {}
    release_id = release_identity.get("id")
    media_by_position = {
        medium.get("position"): medium
        for medium in (semantic.get("release") or {}).get("media") or []
    }
    raw_tracks = _raw_track_index(raw_release)

    work_by_id = {
        (work.get("identity") or {}).get("id"): work.get("identity") or {}
        for work in semantic.get("works") or []
        if (work.get("identity") or {}).get("id")
    }
    composition_by_work = _index_by_source(semantic.get("composition_metadata") or [])
    rights_by_source = _index_by_source(semantic.get("rights") or [])
    source_by_source = _index_by_source(semantic.get("source_material") or [])
    genealogy_by_source = _index_by_source(_flatten_groups(semantic.get("work_genealogy")))
    recording_source_by_source = _index_by_source(
        entry
        for entry in semantic.get("other_unclassified") or []
        if (entry.get("source") or {}).get("type") == "recording"
        and (entry.get("relationship") or {}).get("type") in INTRINSIC_RECORDING_SOURCE_TYPES
        and (entry.get("relationship") or {}).get("direction") == "forward"
    )

    output_tracks: list[dict[str, Any]] = []
    recording_count = 0
    no_work_count = 0
    missing_track_id_count = 0

    for recording in semantic.get("recordings") or []:
        recording_identity = recording.get("identity") or {}
        recording_id = recording_identity.get("id")
        recording_count += 1

        performed_entries = recording.get("performed_works") or []
        performed_work_ids = sorted({
            (entry.get("target") or {}).get("id")
            for entry in performed_entries
            if (entry.get("target") or {}).get("id")
        })
        if not performed_work_ids:
            no_work_count += 1

        work_identities = [work_by_id[work_id] for work_id in performed_work_ids if work_id in work_by_id]
        work_envelopes = [_work_envelope(identity) for identity in work_identities]

        direct_credit_entries = recording.get("credits") or []
        direct_session_entries = recording.get("session") or []
        direct_rights_entries = recording.get("rights") or []
        work_credit_entries = [
            entry
            for work_id in performed_work_ids
            for entry in composition_by_work.get(work_id, [])
        ]
        work_rights_entries = [
            entry
            for work_id in performed_work_ids
            for entry in rights_by_source.get(work_id, [])
        ]
        source_entries = list(source_by_source.get(recording_id, []))
        source_entries.extend(recording_source_by_source.get(recording_id, []))
        genealogy_entries: list[dict[str, Any]] = []
        for work_id in performed_work_ids:
            source_entries.extend(source_by_source.get(work_id, []))
            genealogy_entries.extend(genealogy_by_source.get(work_id, []))

        envelope_relations: list[dict[str, Any]] = []
        envelope_relations.extend(
            _relationship(entry, scope="recording", provenance="direct_recording")
            for entry in direct_credit_entries + direct_session_entries + direct_rights_entries
        )
        envelope_relations.extend(
            _relationship(entry, scope="recording", provenance="performed_work")
            for entry in performed_entries
        )
        envelope_relations.extend(
            _relationship(entry, scope="work", provenance="performed_work_metadata")
            for entry in work_credit_entries
        )
        envelope_relations.extend(
            _relationship(entry, scope="work", provenance="performed_work_rights")
            for entry in work_rights_entries
        )
        envelope_relations.extend(
            _relationship(
                entry,
                scope=(entry.get("source") or {}).get("type") or "work",
                provenance="source_material",
            )
            for entry in source_entries
        )
        envelope_relations.extend(
            _relationship(entry, scope="work", provenance="work_genealogy")
            for entry in genealogy_entries
        )
        envelope_relations = _dedupe_relations(envelope_relations)

        friendly: dict[str, list[str]] = {}
        _add_field(
            friendly,
            FIELD_PROGRAMMING,
            (_credit_text(entry) for entry in direct_credit_entries if (entry.get("relationship") or {}).get("type") == "programming"),
        )
        _add_field(
            friendly,
            FIELD_INSTRUMENT_ARRANGER,
            (_credit_text(entry) for entry in direct_credit_entries if (entry.get("relationship") or {}).get("type") == "instrument arranger"),
        )
        _add_field(
            friendly,
            FIELD_VOCAL_ARRANGER,
            (_credit_text(entry) for entry in direct_credit_entries if (entry.get("relationship") or {}).get("type") == "vocal arranger"),
        )
        _add_field(
            friendly,
            FIELD_QUALIFIED_PRODUCER,
            (
                _credit_text(entry)
                for entry in direct_credit_entries
                if (entry.get("relationship") or {}).get("type") == "producer" and _has_qualifier(entry)
            ),
        )
        _add_field(
            friendly,
            FIELD_QUALIFIED_ENGINEER,
            (
                _credit_text(entry)
                for entry in direct_credit_entries
                if (entry.get("relationship") or {}).get("type") == "engineer" and _has_qualifier(entry)
            ),
        )
        _add_field(
            friendly,
            FIELD_PERFORMANCE,
            (
                _credit_text(entry)
                for entry in direct_credit_entries
                if (entry.get("relationship") or {}).get("type") in PERFORMANCE_TYPES
            ),
        )
        _add_field(
            friendly,
            FIELD_PRODUCTION,
            (
                _credit_text(entry)
                for entry in direct_credit_entries
                if (entry.get("relationship") or {}).get("type") not in PERFORMANCE_TYPES
            ),
        )

        for relationship_type, field_name in SESSION_FIELDS.items():
            _add_field(
                friendly,
                field_name,
                (
                    _target_text(entry)
                    for entry in direct_session_entries
                    if (entry.get("relationship") or {}).get("type") == relationship_type
                ),
            )

        _add_field(
            friendly,
            FIELD_RECORDING_DATE,
            (
                f"{_date_text(entry.get('relationship') or {})}{DISPLAY_SEPARATOR}{(entry.get('relationship') or {}).get('type')}"
                + (
                    f"{DISPLAY_SEPARATOR}{(entry.get('target') or {}).get('name')}"
                    if (entry.get("target") or {}).get("name")
                    else ""
                )
                for entry in direct_session_entries
                if _date_text(entry.get("relationship") or {})
            ),
        )
        _add_field(friendly, FIELD_WORK_CREDITS, (_credit_text(entry) for entry in work_credit_entries))
        _add_field(
            friendly,
            FIELD_PUBLISHERS,
            (
                _target_text(entry)
                for entry in work_rights_entries
                if (entry.get("relationship") or {}).get("type") == "publishing"
            ),
        )
        _add_field(
            friendly,
            FIELD_ISWC,
            (
                f"{identity.get('title')}{DISPLAY_SEPARATOR}{iswc} [{identity.get('id')}]"
                for identity in work_identities
                for iswc in identity.get("iswcs") or []
            ),
        )
        _add_field(
            friendly,
            FIELD_WORK_LANGUAGE,
            (
                f"{identity.get('title')}{DISPLAY_SEPARATOR}{language} [{identity.get('id')}]"
                for identity in work_identities
                for language in (identity.get("languages") or ([identity.get("language")] if identity.get("language") else []))
            ),
        )
        _add_field(
            friendly,
            FIELD_WORK_IDENTIFIERS,
            (
                f"{identity.get('title')}{DISPLAY_SEPARATOR}{attribute.get('type')}={attribute.get('value')} [{identity.get('id')}]"
                for identity in work_identities
                for attribute in identity.get("attributes") or []
                if attribute.get("type") and attribute.get("value")
            ),
        )
        _add_field(friendly, FIELD_SOURCE_MATERIAL, (_target_text(entry) for entry in source_entries))
        _add_field(friendly, FIELD_WORK_GENEALOGY, (_target_text(entry) for entry in genealogy_entries))

        for track in recording.get("tracks") or []:
            medium_position = track.get("medium_position")
            track_position = track.get("track_position")
            medium_source = media_by_position.get(medium_position) or {}
            raw_track = raw_tracks.get((medium_position, track_position), {})
            track_id = raw_track.get("id")
            if not track_id:
                missing_track_id_count += 1

            medium = {
                "position": medium_position,
                "format": medium_source.get("format"),
                "format_id": medium_source.get("format-id") or medium_source.get("format_id"),
                "title": medium_source.get("title"),
                "track_count": medium_source.get("track-count") or medium_source.get("track_count"),
            }
            track_identity = {
                "id": track_id,
                "position": track_position,
                "number": track.get("track_number"),
                "title": track.get("track_title"),
                "length": track.get("track_length"),
            }
            envelope = {
                "schema": SEMANTIC_SCHEMA,
                "release": {"id": release_id},
                "medium": medium,
                "track": track_identity,
                "recording": {
                    "id": recording_id,
                    "title": recording_identity.get("title"),
                },
                "works": work_envelopes,
                "relationships": envelope_relations,
            }
            fields = {key: list(value) for key, value in sorted(friendly.items())}
            fields[FIELD_SEMANTIC_JSON] = [stable_json(envelope)]
            output_tracks.append({
                "medium": medium,
                "track": track_identity,
                "recording": {
                    "id": recording_id,
                    "title": recording_identity.get("title"),
                },
                "fields": fields,
            })

    output_tracks.sort(key=lambda item: (
        item["medium"].get("position") or 0,
        item["track"].get("position") or 0,
        item["recording"].get("id") or "",
    ))

    return {
        "format": FORMAT,
        "version": VERSION,
        "source_semantic_version": semantic.get("version"),
        "release": {
            "id": release_id,
            "title": release_identity.get("title"),
        },
        "counts": {
            "recordings": recording_count,
            "tracks": len(output_tracks),
            "recordings_without_performed_work": no_work_count,
            "tracks_without_release_track_id": missing_track_id_count,
        },
        "tracks": output_tracks,
    }


def load_inputs(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None, Path]:
    path = path.resolve()
    if path.is_dir():
        semantic_path = path / "SEMANTIC_MAP.json"
        base = path
    else:
        semantic_path = path
        base = path.parent
    if not semantic_path.is_file():
        raise FileNotFoundError(f"SEMANTIC_MAP.json not found: {semantic_path}")
    semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
    raw_path = base / "raw" / "release.json"
    raw_release = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else None
    return semantic, raw_release, base


def write_output(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json(data, pretty=True) + "\n", encoding="utf-8", newline="\n")


class PopExtrasProjector:
    """Index one semantic map for exact Picard track projection."""

    def __init__(
        self,
        semantic: dict[str, Any],
        raw_release: dict[str, Any] | None = None,
    ) -> None:
        self.output = map_semantic(semantic, raw_release)
        self.release_id = (self.output.get("release") or {}).get("id")
        self.by_release_track: dict[str, dict[str, Any]] = {}
        by_recording: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for track in self.output.get("tracks") or []:
            track_id = (track.get("track") or {}).get("id")
            recording_id = (track.get("recording") or {}).get("id")
            if track_id:
                self.by_release_track[track_id] = track
            if recording_id:
                by_recording[recording_id].append(track)
        self.by_unique_recording = {
            recording_id: tracks[0]
            for recording_id, tracks in by_recording.items()
            if len(tracks) == 1
        }
        self.ambiguous_recordings = {
            recording_id
            for recording_id, tracks in by_recording.items()
            if len(tracks) > 1
        }

    def project_track(
        self,
        release_track_id: str | None,
        recording_id: str | None,
    ) -> tuple[dict[str, list[str]], str]:
        """Return ``(fields, status)`` without guessing by title or position."""
        if release_track_id and release_track_id in self.by_release_track:
            track = self.by_release_track[release_track_id]
            return dict(track.get("fields") or {}), "release_track"
        if recording_id in self.ambiguous_recordings:
            return {}, "ambiguous_recording"
        if recording_id and recording_id in self.by_unique_recording:
            track = self.by_unique_recording[recording_id]
            return dict(track.get("fields") or {}), "unique_recording"
        return {}, "not_found"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build deterministic per-track MusicBrainz Pop Extras fields."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Explorer output directory or SEMANTIC_MAP.json path.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path (default: POP_EXTRAS.json beside SEMANTIC_MAP.json).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print JSON instead of writing a file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    semantic, raw_release, base = load_inputs(args.input)
    output = map_semantic(semantic, raw_release)
    if args.stdout:
        print(stable_json(output, pretty=True))
    else:
        output_path = (args.output or (base / "POP_EXTRAS.json")).resolve()
        write_output(output, output_path)
        counts = output["counts"]
        print(f"Wrote {output_path}")
        print(
            f"Tracks: {counts['tracks']} | recordings: {counts['recordings']} | "
            f"without work: {counts['recordings_without_performed_work']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
