"""Pure semantic rules mechanically copied from Release X-Ray V2.5.
Regenerate with work/sync_semantic_engine.py; no HTTP or discovery globals.
"""

import json
import re
import calendar
from datetime import datetime

MB_BASE = "http://localhost:5000/ws/2"

VERSION = "2.5"

MAX_PARENT_WORK_DEPTH = 8

MAX_CANONICAL_SOURCE_DEPTH = 2

MAX_CONTEXTUAL_SOURCE_DEPTH = 1

MAX_PERFORMED_COMPONENT_DEPTH = 1

CANONICAL_SOURCE_RELATIONS = {
    "arrangement", "orchestration", "revision", "revision of", "other version",
    "adaptation", "adapted from",
}

CONTEXTUAL_SOURCE_RELATIONS = {"based on"}

RELATIONSHIP_INCLUDES = [
    "area-rels",
    "artist-rels",
    "event-rels",
    "genre-rels",
    "instrument-rels",
    "label-rels",
    "place-rels",
    "recording-rels",
    "release-rels",
    "release-group-rels",
    "series-rels",
    "url-rels",
    "work-rels",
]

RELEASE_INCLUDES = [
    "artist-credits",
    "labels",
    "recordings",
    "release-groups",
    "media",
    "discids",
    "isrcs",
    "annotation",
    "tags",
    "genres",
    *RELATIONSHIP_INCLUDES,
    "recording-level-rels",
    "release-group-level-rels",
    "work-level-rels",
]

RECORDING_INCLUDES = [
    "artist-credits",
    "isrcs",
    "releases",
    "release-groups",
    "annotation",
    "tags",
    "genres",
    "ratings",
    *RELATIONSHIP_INCLUDES,
    "work-level-rels",
]

WORK_INCLUDES = [
    "aliases",
    "annotation",
    "tags",
    "genres",
    "ratings",
    *RELATIONSHIP_INCLUDES,
]

RELEASE_GROUP_INCLUDES = [
    "artist-credits",
    "releases",
    "annotation",
    "tags",
    "genres",
    "ratings",
    *RELATIONSHIP_INCLUDES,
]

def relation_target(rel):
    possible = [
        "area", "artist", "event", "genre", "instrument", "label",
        "place", "recording", "release", "release-group", "series",
        "url", "work",
    ]
    for key in possible:
        if key in rel:
            return key, rel[key]
    return None, None

def is_upward_parent_relation(rel):
    """True only for a work-to-work edge pointing from child to parent."""
    target_type, target = relation_target(rel)
    if target_type != "work" or not isinstance(target, dict) or not target.get("id"):
        return False
    relation_type = str(rel.get("type") or "").strip().casefold()
    direction = str(rel.get("direction") or "").strip().casefold()
    if relation_type == "part of":
        return direction != "forward"
    if relation_type == "parts":
        return direction == "backward"
    return False

def is_downward_child_relation(rel):
    """True only for an ordered work-to-work edge from parent to child."""
    target_type, target = relation_target(rel)
    if target_type != "work" or not isinstance(target, dict) or not target.get("id"):
        return False
    if rel.get("ordering-key") is None:
        return False
    rel_type = str(rel.get("type") or "").strip().casefold()
    direction = str(rel.get("direction") or "").strip().casefold()
    if rel_type == "parts":
        return direction != "backward"
    if rel_type == "part of":
        return direction == "forward"
    return False

def source_relation_kind(rel):
    """Classify only backward work edges which point to source material."""
    target_type, target = relation_target(rel)
    if target_type != "work" or not isinstance(target, dict) or not target.get("id"):
        return None
    direction = str(rel.get("direction") or "").strip().casefold()
    if direction != "backward":
        return None
    rel_type = str(rel.get("type") or "").strip().casefold()
    if rel_type in CANONICAL_SOURCE_RELATIONS or any(
        marker in rel_type
        for marker in ("arrangement", "orchestration", "revision", "adaptation")
    ):
        return "canonical_source"
    if rel_type in CONTEXTUAL_SOURCE_RELATIONS:
        return "contextual_source"
    return None

def normalized_title_tokens(value):
    """Small title fingerprint used as supporting evidence, never as identity."""
    return {
        token for token in re.findall(r"[\w]+", str(value or "").casefold())
        if len(token) > 2
    }

def assess_source_relation(work_data, rel):
    """Score a backward source edge and identify strong semantic contradictions.

    MusicBrainz relationship direction is normally authoritative.  A small
    number of links are nevertheless entered backwards.  We only quarantine
    an edge when its own disambiguations provide affirmative contradictory
    evidence; weak title similarity alone never rejects a source.
    """
    target = rel.get("work") or {}
    rel_type = str(rel.get("type") or "").strip().casefold()
    source_disambiguation = str(work_data.get("disambiguation") or "").casefold()
    target_disambiguation = str(target.get("disambiguation") or "").casefold()
    source_title = work_data.get("title") or work_data.get("name")
    target_title = target.get("title") or target.get("name")
    source_original = bool(re.search(r"\boriginal\b", source_disambiguation))
    target_derivative = bool(re.search(
        r"\b(?:arr(?:anged|angement)?|orchestrated|adapted|version by)\b",
        target_disambiguation,
    ))
    source_derivative = bool(re.search(
        r"\b(?:arr(?:anged|angement)?|orchestrated|adapted|version by)\b",
        source_disambiguation,
    ))
    target_original = bool(re.search(r"\boriginal\b", target_disambiguation))
    shared_tokens = sorted(
        normalized_title_tokens(source_title) & normalized_title_tokens(target_title)
    )
    evidence = []
    if source_derivative:
        evidence.append("source is labelled as a derivative")
    if target_original:
        evidence.append("target is labelled as an original")
    if shared_tokens:
        evidence.append("titles share: " + ", ".join(shared_tokens[:8]))
    if rel_type == "orchestration":
        evidence.append("relationship explicitly identifies orchestration")

    if source_original and target_derivative:
        return {
            "confidence": "low",
            "decision": "quarantine",
            "reasons": [
                "backward source edge contradicts disambiguations: source is "
                "labelled original while target is labelled derivative"
            ],
            "supporting_evidence": evidence,
        }
    confidence = "high" if source_derivative or target_original or rel_type == "orchestration" else "medium"
    return {
        "confidence": confidence,
        "decision": "traverse",
        "reasons": [],
        "supporting_evidence": evidence,
    }

SINGULAR_ENTITY_KEYS = {
    "area", "artist", "event", "genre", "instrument", "label", "place",
    "recording", "release", "release-group", "series", "url", "work",
}

PLURAL_ENTITY_KEYS = {
    "areas": "area", "artists": "artist", "events": "event",
    "genres": "genre", "instruments": "instrument", "labels": "label",
    "places": "place", "recordings": "recording", "releases": "release",
    "release-groups": "release-group", "series": "series", "urls": "url",
    "works": "work",
}

def target_summary(target_type, target):
    if not isinstance(target, dict):
        return {"entity_type": target_type, "value": target}
    result = {
        # Keep the relationship target's entity kind separate from the
        # entity's own MusicBrainz subtype. A work can, for example, have
        # entity_type="work" and musicbrainz_type="Sonata"; a series can
        # have entity_type="series" and musicbrainz_type="Catalogue".
        "entity_type": target_type,
        "id": target.get("id"),
        "name": target.get("name") or target.get("title"),
    }
    if target_type == "url":
        result["resource"] = target.get("resource")
    if target.get("type") is not None:
        result["musicbrainz_type"] = target.get("type")
    if target.get("type-id") is not None:
        result["musicbrainz_type_id"] = target.get("type-id")
    for key in (
        "disambiguation", "address", "language", "languages", "iswcs",
        "attributes",
    ):
        if target.get(key) is not None:
            result[key] = target.get(key)
    return result

def semantic_relation_entry(rel, location, source_type, source_id, source_name=None):
    target_type, target = relation_target(rel)
    return {
        "source": {"type": source_type, "id": source_id, "name": source_name},
        "relationship": {
            "type": rel.get("type"),
            "type_id": rel.get("type-id"),
            "direction": rel.get("direction"),
            "attributes": rel.get("attributes", []),
            "attribute_values": rel.get("attribute-values", {}),
            "attribute_ids": rel.get("attribute-ids", {}),
            "attribute_credits": rel.get("attribute-credits", {}),
            "begin": rel.get("begin"),
            "end": rel.get("end"),
            "ended": rel.get("ended"),
            "ordering_key": rel.get("ordering-key"),
            "source_credit": rel.get("source-credit"),
            "target_credit": rel.get("target-credit"),
        },
        "target": target_summary(target_type, target),
        "found_at": [location],
        "raw_relation": rel,
    }

def collect_entity_aware_relations(value, location, source_type, source_id, source_name, results):
    """Walk JSON while carrying the identity of the entity owning each relation."""
    if isinstance(value, dict):
        relations_value = value.get("relations")
        if isinstance(relations_value, list):
            for rel in relations_value:
                if isinstance(rel, dict):
                    results.append(semantic_relation_entry(
                        rel, location, source_type, source_id, source_name
                    ))

        for key, child in value.items():
            if key == "relations":
                continue
            child_location = f"{location}.{key}"
            if key in SINGULAR_ENTITY_KEYS and isinstance(child, dict):
                child_name = child.get("name") or child.get("title") or child.get("resource")
                collect_entity_aware_relations(
                    child, child_location, key, child.get("id") or child.get("resource"),
                    child_name, results
                )
            elif key in PLURAL_ENTITY_KEYS and isinstance(child, list):
                child_type = PLURAL_ENTITY_KEYS[key]
                for index, item in enumerate(child):
                    if not isinstance(item, dict):
                        continue
                    child_name = item.get("name") or item.get("title") or item.get("resource")
                    collect_entity_aware_relations(
                        item, f"{child_location}[{index}]", child_type,
                        item.get("id") or item.get("resource"), child_name, results
                    )
            else:
                collect_entity_aware_relations(
                    child, child_location, source_type, source_id, source_name, results
                )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            collect_entity_aware_relations(
                child, f"{location}[{index}]", source_type, source_id, source_name, results
            )

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def semantic_relation_key(entry):
    rel = entry["relationship"]
    target = entry["target"]
    attributes = sorted(rel.get("attributes") or [])
    return (
        entry["source"].get("type"),
        entry["source"].get("id"),
        rel.get("type_id") or rel.get("type"),
        rel.get("direction"),
        target.get("entity_type"),
        target.get("id") or target.get("resource") or target.get("value"),
        canonical(attributes),
        canonical(rel.get("attribute_values") or {}),
        canonical(rel.get("attribute_ids") or {}),
        canonical(rel.get("attribute_credits") or {}),
        rel.get("begin"),
        rel.get("end"),
        rel.get("ended"),
        rel.get("ordering_key"),
        rel.get("source_credit"),
        rel.get("target_credit"),
    )

def deduplicate_relations(entries):
    unique = {}
    for entry in entries:
        key = semantic_relation_key(entry)
        if key not in unique:
            unique[key] = entry
        else:
            locations = unique[key]["found_at"]
            for location in entry["found_at"]:
                if location not in locations:
                    locations.append(location)
    return list(unique.values())

def selected_fields(data, keys):
    return {key: data.get(key) for key in keys if data.get(key) is not None}

def summarize_media(media):
    """Keep release structure useful without embedding raw recording payloads."""
    result = []
    for medium in media or []:
        if not isinstance(medium, dict):
            continue
        item = selected_fields(medium, [
            "position", "title", "format", "format-id", "track-count",
        ])
        item["disc_ids"] = [
            disc.get("id") for disc in medium.get("discs", [])
            if isinstance(disc, dict) and disc.get("id")
        ]
        result.append(item)
    return result

def relation_type(entry):
    return str(entry["relationship"].get("type") or "").strip().casefold()

def has_number_attribute(entry):
    rel = entry["relationship"]
    keys = set(str(key).casefold() for key in (rel.get("attribute_values") or {}))
    attributes = set(str(value).casefold() for value in (rel.get("attributes") or []))
    return "number" in keys or "number" in attributes

def catalog_kind(entry):
    """Classify the authority independently of the identifier it contains."""
    target = entry["target"]
    name = str(target.get("name") or "").casefold()
    musicbrainz_type = str(target.get("musicbrainz_type") or "").casefold()

    ranked_markers = ("classic 100", "top 100", "greatest", "best of", "ranked")
    if musicbrainz_type == "work series" and any(marker in name for marker in ranked_markers):
        return "ranked_lists"
    if musicbrainz_type == "catalogue":
        return "scholarly_catalogues"
    if "opus" in name or "op. number" in name:
        return "opus_catalogues"
    if musicbrainz_type == "work series":
        return "work_series"
    return "other_numbered_series"

def catalog_number_values(entry):
    values = entry["relationship"].get("attribute_values") or {}
    result = []
    for key, value in values.items():
        if str(key).casefold() == "number":
            if isinstance(value, list):
                result.extend(str(item) for item in value)
            elif value is not None:
                result.append(str(value))
    return result

def catalog_identifier_kind(entry):
    values = catalog_number_values(entry)
    combined = " ".join(values).strip()
    authority = catalog_kind(entry)
    if authority == "ranked_lists":
        return "rank_position"
    source_name = str(entry["source"].get("name") or "")
    if re.search(
        r"(?:^|\b)(?:op(?:us)?\.?|woO)\s*\d", combined + " " + source_name,
        re.IGNORECASE,
    ):
        return "opus_number"
    if combined:
        return "catalogue_number"
    return "other_number"

def work_genealogy_kind(rel_type):
    if any(word in rel_type for word in (
        "arrangement", "arranger", "orchestration", "orchestrator",
    )):
        return "arrangements_orchestrations"
    if any(word in rel_type for word in (
        "revision", "revised", "version", "adaptation", "adapted",
    )):
        return "revisions_versions"
    return None

def hierarchy_facets(entry):
    """A hierarchy edge may simultaneously carry order and collection meaning."""
    target_type = str(entry["target"].get("musicbrainz_type") or "").casefold()
    target_name = str(entry["target"].get("name") or "").casefold()
    attributes = {
        str(value).casefold()
        for value in (entry["relationship"].get("attributes") or [])
    }
    facets = []
    if entry["relationship"].get("ordering_key") is not None:
        facets.append("ordered_sections")
    if "number" in attributes:
        facets.append("ordered_numbers")
    if attributes.intersection({"movement", "part", "number"}) or any(
        word in target_type for word in ("sonata", "symphony", "concerto", "suite")
    ):
        facets.append("movement_parts")
    if attributes.intersection({"act", "scene"}) or any(
        word in target_type for word in ("opera", "ballet", "theatre", "dramatic")
    ):
        facets.append("dramatic_parts")
    if "collection" in target_type or any("collection" in value for value in attributes):
        facets.append("collection_membership")
    if "excerpt" in attributes or "excerpt" in target_name:
        facets.append("excerpts")
    return list(dict.fromkeys(facets)) or ["other_hierarchy"]

def source_material_kind(entry):
    if entry["target"].get("entity_type") != "work":
        return None
    rel_type = relation_type(entry)
    target = entry["target"]
    target_type = str(target.get("musicbrainz_type") or "").casefold()
    target_name = str(target.get("name") or "").casefold()
    target_disambiguation = str(target.get("disambiguation") or "").casefold()
    description = " ".join((target_name, target_disambiguation, target_type))
    if "cadenza" in rel_type or re.search(r"\bcadenza\b", description):
        return "cadenzas"
    if any(word in rel_type for word in ("quotation", "quote")):
        return "quotations"
    if "medley" in rel_type or re.search(r"\bmedley\b", description):
        return "medleys"
    literary_types = {
        "poem", "novel", "play", "libretto", "literary work", "prose",
    }
    if target_type in literary_types or re.search(
        r"\b(?:poem|novel|play|libretto|literary)\b", target_disambiguation
    ):
        return "literary_sources"
    if rel_type in {"based on", "adaptation", "adapted from"}:
        return "adaptations_sources"
    if any(word in rel_type for word in ("included", "compilation")):
        return "included_works_compilations"
    return None

def is_rights_relation(rel_type):
    return any(word in rel_type for word in (
        "copyright", "publisher", "publishing", "published by", "rights society",
    ))

def assertion_key(entry):
    rel = entry["relationship"]
    target = entry["target"]
    return (
        entry["source"].get("id"), rel.get("type_id") or rel.get("type"),
        rel.get("direction"), target.get("entity_type"),
        target.get("id") or target.get("resource") or target.get("value"),
    )

def date_precision(value):
    if not value:
        return 0
    return len(str(value).split("-"))

def partial_date_bounds(value):
    """Convert YYYY[-MM[-DD]] to an inclusive earliest/latest date tuple."""
    if not value:
        return None
    try:
        pieces = [int(piece) for piece in str(value).split("-")]
        year = pieces[0]
        month = pieces[1] if len(pieces) > 1 else 1
        day = pieces[2] if len(pieces) > 2 else 1
        low = (year, month, day)
        high_month = pieces[1] if len(pieces) > 1 else 12
        high_day = pieces[2] if len(pieces) > 2 else calendar.monthrange(year, high_month)[1]
        return low, (year, high_month, high_day)
    except (TypeError, ValueError, IndexError):
        return None

def assertion_interval(entry):
    rel = entry["relationship"]
    begin = partial_date_bounds(rel.get("begin"))
    end = partial_date_bounds(rel.get("end"))
    if not begin and not end:
        return None
    return (begin or end)[0], (end or begin)[1]

def reconcile_relations(entries):
    """Collapse repeated assertions while retaining every asserted variant."""
    groups = {}
    for entry in entries:
        groups.setdefault(assertion_key(entry), []).append(entry)
    facts = []
    for assertions in groups.values():
        preferred = max(assertions, key=lambda item: (
            date_precision(item["relationship"].get("begin"))
            + date_precision(item["relationship"].get("end")),
            len(item["relationship"].get("attributes") or []),
        ))
        intervals = [assertion_interval(item) for item in assertions]
        known = [item for item in intervals if item]
        compatible = not known or max(item[0] for item in known) <= min(
            item[1] for item in known
        )
        attributes = sorted({
            value for item in assertions
            for value in (item["relationship"].get("attributes") or [])
        }, key=str.casefold)
        facts.append({
            "source": preferred["source"],
            "relationship_type": preferred["relationship"].get("type"),
            "relationship_type_id": preferred["relationship"].get("type_id"),
            "direction": preferred["relationship"].get("direction"),
            "target": preferred["target"],
            "attributes_union": attributes,
            "status": (
                "single" if len(assertions) == 1
                else "compatible" if compatible else "conflicting"
            ),
            "preferred_assertion": preferred,
            "assertions": assertions,
        })
    return facts

def make_track_work_diagnostics(recording_items):
    diagnostics = {
        "recordings_without_performed_work": [],
        "recordings_with_multiple_performed_works": [],
        "works_spanning_multiple_recordings": [],
        "partial_performance_assertions": [],
    }
    work_recordings = {}
    for recording in recording_items:
        recording_id = recording["identity"].get("id")
        recording_title = recording["identity"].get("title")
        facts = recording["reconciled"].get("performed_works", [])
        work_ids = {
            fact["target"].get("id") for fact in facts if fact["target"].get("id")
        }
        summary = {
            "recording_id": recording_id,
            "recording_title": recording_title,
            "tracks": recording["tracks"],
        }
        if not work_ids:
            diagnostics["recordings_without_performed_work"].append(summary)
        if len(work_ids) > 1:
            diagnostics["recordings_with_multiple_performed_works"].append({
                **summary, "work_ids": sorted(work_ids),
            })
        for work_id in work_ids:
            work_recordings.setdefault(work_id, []).append(summary)
        for fact in facts:
            attributes = {value.casefold() for value in fact["attributes_union"]}
            if "partial" in attributes:
                diagnostics["partial_performance_assertions"].append({
                    **summary, "fact": fact,
                })
    for work_id, recording_summaries in work_recordings.items():
        if len(recording_summaries) > 1:
            diagnostics["works_spanning_multiple_recordings"].append({
                "work_id": work_id, "recordings": recording_summaries,
            })
    return diagnostics

def classify_semantic_relation(entry, semantic, recordings_by_id):
    source_type = entry["source"].get("type")
    source_id = entry["source"].get("id")
    target_type = entry["target"].get("entity_type")
    rel_type = relation_type(entry)

    if target_type == "url":
        semantic["external_references"].append(entry)
        return

    if source_type in {"release", "release-group"}:
        if is_rights_relation(rel_type):
            semantic["release"]["rights"].append(entry)
            semantic["rights"].append(entry)
        else:
            semantic["release"]["relationships"].append(entry)
        return

    if source_type == "recording" and source_id in recordings_by_id:
        recording = recordings_by_id[source_id]
        if target_type == "work" and rel_type in {"performance", "performance of"}:
            recording["performed_works"].append(entry)
        elif is_rights_relation(rel_type):
            recording["rights"].append(entry)
            semantic["rights"].append(entry)
        elif target_type in {"place", "area"} or rel_type in {
            "recorded at", "mixed at", "mastered at", "engineered at",
        }:
            recording["session"].append(entry)
        elif target_type in {"artist", "instrument"} or rel_type in {
            "producer", "editor", "balance", "engineer", "mix", "mastering",
            "instrument", "performer", "vocal", "conductor", "orchestra",
        }:
            recording["credits"].append(entry)
        else:
            semantic["other_unclassified"].append(entry)
        return

    if source_type == "work":
        assessment = entry.get("source_assessment")
        if assessment and assessment.get("decision") == "quarantine":
            semantic["suspicious_source_relationships"].append(entry)

        if target_type == "recording" and rel_type in {"performance", "performance of"}:
            entry["classification"] = {
                "section": "related_recordings",
                "recording_scope": (
                    "release_recordings"
                    if entry["target"].get("id") in recordings_by_id
                    else "other_recordings"
                ),
            }
            semantic["related_recordings"].append(entry)
            semantic["related_recording_groups"][
                entry["classification"]["recording_scope"]
            ].append(entry)
        elif target_type == "work" and rel_type in {"parts", "part of"}:
            semantic["work_hierarchy"].append(entry)
            facets = hierarchy_facets(entry)
            entry["classification"] = {
                "section": "work_hierarchy",
                "hierarchy_facets": facets,
            }
            for facet in facets:
                semantic["work_hierarchy_groups"][facet].append(entry)
        elif (
            target_type == "series" and has_number_attribute(entry)
        ) or "catalog" in rel_type or "catalogue" in rel_type:
            semantic["catalogs"].append(entry)
            kind = catalog_kind(entry)
            identifier_kind = catalog_identifier_kind(entry)
            entry["classification"] = {
                "section": "catalogs",
                "authority_kind": kind,
                "identifier_kind": identifier_kind,
            }
            semantic["catalog_groups"][kind].append(entry)
            semantic["catalog_identifier_groups"][identifier_kind].append(entry)
        elif rel_type in {
            "composer", "composed in", "writer", "lyricist", "librettist",
            "translator", "dedication", "commissioned", "premiere",
            "instrument", "instrumentation",
        }:
            semantic["composition_metadata"].append(entry)
        elif is_rights_relation(rel_type):
            semantic["rights"].append(entry)
        elif source_material_kind(entry):
            kind = source_material_kind(entry)
            semantic["source_material"].append(entry)
            entry["classification"] = {
                "section": "source_material", "source_material_kind": kind,
            }
            semantic["source_material_groups"][kind].append(entry)
        elif work_genealogy_kind(rel_type):
            semantic["arrangements_revisions"].append(entry)
            kind = work_genealogy_kind(rel_type)
            entry["classification"] = {
                "section": "work_genealogy",
                "genealogy_kind": kind,
            }
            semantic["work_genealogy"][kind].append(entry)
        else:
            semantic["other_unclassified"].append(entry)
        return

    semantic["other_unclassified"].append(entry)

def make_semantic_map(
    release_data, release_group_data, recording_data, work_data,
    track_rows, work_depths, source_depths, work_provenance, work_discoveries,
    all_entity_data, raw_relation_count, crawl_mode, embedded_work_count,
    source_traversal, source_edge_assessments
):
    captured = []
    for source_type, source_id, label, data in all_entity_data:
        collect_entity_aware_relations(
            data, label, source_type, source_id,
            data.get("title") or data.get("name"), captured
        )
    unique = deduplicate_relations(captured)

    tracks_by_recording = {}
    for track in track_rows:
        tracks_by_recording.setdefault(track["recording_id"], []).append(track)

    semantic_recordings = []
    recordings_by_id = {}
    for mbid, data in recording_data.items():
        item = {
            "identity": selected_fields(data, [
                "id", "title", "disambiguation", "length", "video", "isrcs",
                "artist-credit", "first-release-date",
            ]),
            "tracks": tracks_by_recording.get(mbid, []),
            "session": [],
            "credits": [],
            "performed_works": [],
            "rights": [],
            "reconciled": {},
        }
        semantic_recordings.append(item)
        recordings_by_id[mbid] = item

    semantic_works = []
    for mbid, data in work_data.items():
        semantic_works.append({
            "identity": selected_fields(data, [
                "id", "title", "disambiguation", "type", "type-id", "language",
                "languages", "iswcs", "attributes", "aliases", "annotation",
                "genres", "tags", "rating",
            ]),
            "parent_traversal_depth": work_depths.get(mbid),
            "source_traversal_depth": source_depths.get(mbid),
            "provenance": sorted(work_provenance.get(mbid, {"unknown"})),
            "discovered_by": work_discoveries.get(mbid, []),
        })

    semantic = {
        "format": "MusicBrainz Release X-Ray Semantic Map",
        "version": VERSION,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "server": MB_BASE,
        "scope": {
            "core": (
                "Release, track recordings, sessions, credits, directly performed "
                "works, upward work hierarchy, catalogs, and composition metadata."
            ),
            "optional_work_genealogy": (
                "Arrangements, orchestrations, revisions, and alternate versions "
                "encountered within the selected crawl mode."
            ),
        },
        "crawl": {
            "mode": crawl_mode,
            "max_parent_work_depth": MAX_PARENT_WORK_DEPTH,
            "source_traversal": source_traversal,
            "max_canonical_source_depth": MAX_CANONICAL_SOURCE_DEPTH,
            "max_contextual_source_depth": MAX_CONTEXTUAL_SOURCE_DEPTH,
            "max_performed_component_depth": MAX_PERFORMED_COMPONENT_DEPTH,
            "recording_response_works_observed": embedded_work_count,
            "fetched_work_count": len(work_data),
        },
        "release": {
            "identity": selected_fields(release_data, [
                "id", "title", "status", "quality", "date", "country", "barcode",
                "asin", "packaging", "text-representation", "artist-credit",
                "label-info", "cover-art-archive",
            ]),
            "media": summarize_media(release_data.get("media")),
            "release_groups": [selected_fields(data, [
                "id", "title", "primary-type", "primary-type-id",
                "secondary-types", "secondary-type-ids", "first-release-date",
                "artist-credit", "disambiguation",
            ]) for data in release_group_data.values()],
            "relationships": [],
            "rights": [],
        },
        "recordings": semantic_recordings,
        "works": semantic_works,
        "work_hierarchy": [],
        "work_hierarchy_groups": {
            "ordered_sections": [],
            "ordered_numbers": [],
            "movement_parts": [],
            "dramatic_parts": [],
            "collection_membership": [],
            "excerpts": [],
            "other_hierarchy": [],
        },
        "catalogs": [],
        "catalog_groups": {
            "scholarly_catalogues": [],
            "opus_catalogues": [],
            "ranked_lists": [],
            "work_series": [],
            "other_numbered_series": [],
        },
        "catalog_identifier_groups": {
            "opus_number": [],
            "catalogue_number": [],
            "rank_position": [],
            "other_number": [],
        },
        "composition_metadata": [],
        "arrangements_revisions": [],
        "work_genealogy": {
            "arrangements_orchestrations": [],
            "revisions_versions": [],
        },
        "source_material": [],
        "source_material_groups": {
            "literary_sources": [],
            "cadenzas": [],
            "quotations": [],
            "medleys": [],
            "adaptations_sources": [],
            "included_works_compilations": [],
        },
        "rights": [],
        "related_recordings": [],
        "related_recording_groups": {
            "release_recordings": [],
            "other_recordings": [],
        },
        "suspicious_source_relationships": [],
        "external_references": [],
        "related_unfetched_works": [],
        "other_unclassified": [],
    }

    source_assessment_lookup = {
        (
            item.get("source_work_id"),
            item.get("relationship_type_id") or item.get("relationship_type"),
            item.get("direction"),
            item.get("target_work_id"),
        ): item.get("assessment")
        for item in source_edge_assessments
    }
    for entry in unique:
        if (
            entry.get("source", {}).get("type") == "work"
            and entry.get("target", {}).get("entity_type") == "work"
        ):
            rel = entry.get("relationship", {})
            assessment = source_assessment_lookup.get((
                entry["source"].get("id"),
                rel.get("type_id") or rel.get("type"),
                rel.get("direction"),
                entry["target"].get("id"),
            ))
            if assessment:
                entry["source_assessment"] = assessment
        classify_semantic_relation(entry, semantic, recordings_by_id)

    reconciled_facts = []
    for recording in semantic_recordings:
        for category in ("performed_works", "credits", "session", "rights"):
            facts = reconcile_relations(recording[category])
            recording["reconciled"][category] = facts
            reconciled_facts.extend(facts)

    # Give downstream consumers a compact inventory of work targets which
    # remain visible in relationships but were intentionally not fetched.
    fetched_work_ids = set(work_data)
    related = {}
    for entry in unique:
        target = entry.get("target", {})
        target_id = target.get("id")
        if target.get("entity_type") != "work" or not target_id:
            continue
        if target_id in fetched_work_ids:
            continue
        if target_id not in related:
            related[target_id] = {
                "identity": target,
                "discovered_by": [],
            }
        discovery = {
            "source": entry.get("source"),
            "relationship_type": entry.get("relationship", {}).get("type"),
            "relationship_type_id": entry.get("relationship", {}).get("type_id"),
            "direction": entry.get("relationship", {}).get("direction"),
        }
        if discovery not in related[target_id]["discovered_by"]:
            related[target_id]["discovered_by"].append(discovery)
    semantic["related_unfetched_works"] = sorted(
        related.values(),
        key=lambda item: (
            str(item["identity"].get("name") or "").casefold(),
            str(item["identity"].get("id") or ""),
        ),
    )

    classified_count = len(unique) - len(semantic["other_unclassified"])
    semantic["counts"] = {
        "raw_relationship_instances": raw_relation_count,
        "entity_aware_relationship_instances_seen": len(captured),
        "unique_relationships": len(unique),
        "duplicate_relationship_instances_removed": len(captured) - len(unique),
        "classified_unique_relationships": classified_count,
        "other_unclassified_unique_relationships": len(semantic["other_unclassified"]),
        "recordings": len(semantic_recordings),
        "works": len(semantic_works),
        "parent_works": sum(1 for tags in work_provenance.values() if any(
            tag in tags for tag in (
                "performed_ancestor", "source_ancestor", "derivative_ancestor"
            )
        ) and "performed" not in tags),
        "performed_ancestor_works": sum(
            1 for tags in work_provenance.values() if "performed_ancestor" in tags
        ),
        "performed_component_works": sum(
            1 for tags in work_provenance.values() if "performed_component" in tags
        ),
        "canonical_source_works": sum(
            1 for tags in work_provenance.values() if "canonical_source" in tags
        ),
        "contextual_source_works": sum(
            1 for tags in work_provenance.values() if "contextual_source" in tags
        ),
        "source_ancestor_works": sum(
            1 for tags in work_provenance.values() if "source_ancestor" in tags
        ),
        "derivative_ancestor_works": sum(
            1 for tags in work_provenance.values() if "derivative_ancestor" in tags
        ),
        "related_unfetched_works": len(semantic["related_unfetched_works"]),
        "ordered_work_relationships": sum(
            1 for entry in semantic["work_hierarchy"]
            if entry["relationship"].get("ordering_key") is not None
        ),
        "catalog_relationships_by_kind": {
            key: len(entries) for key, entries in semantic["catalog_groups"].items()
        },
        "catalog_identifiers_by_kind": {
            key: len(entries)
            for key, entries in semantic["catalog_identifier_groups"].items()
        },
        "work_genealogy_relationships": sum(
            len(entries) for entries in semantic["work_genealogy"].values()
        ),
        "source_material_relationships": len(semantic["source_material"]),
        "rights_relationships": len(semantic["rights"]),
        "related_recording_relationships": len(semantic["related_recordings"]),
        "related_recordings_outside_release": len(
            semantic["related_recording_groups"]["other_recordings"]
        ),
        "suspicious_source_relationships": len(
            semantic["suspicious_source_relationships"]
        ),
        "recording_semantic_assertions": sum(
            len(recording[category]) for recording in semantic_recordings
            for category in ("performed_works", "credits", "session", "rights")
        ),
        "recording_reconciled_facts": len(reconciled_facts),
        "recording_assertions_collapsed": sum(
            max(0, len(fact["assertions"]) - 1) for fact in reconciled_facts
        ),
        "recording_fact_conflicts": sum(
            fact["status"] == "conflicting" for fact in reconciled_facts
        ),
    }
    semantic["track_work_diagnostics"] = make_track_work_diagnostics(
        semantic_recordings
    )
    semantic["counts"]["track_work_diagnostic_counts"] = {
        key: len(value) for key, value in semantic["track_work_diagnostics"].items()
    }
    return semantic
