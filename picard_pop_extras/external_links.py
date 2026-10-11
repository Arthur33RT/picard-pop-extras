"""Pure MusicBrainz external-link bridge for Discogs and AllMusic."""

from __future__ import annotations

import re
from typing import Any, Iterable


TAG_DISCOGS_RELEASE_ID = "discogs_release_id"
TAG_DISCOGS_RELEASE_URL = "discogs_release_url"
TAG_DISCOGS_MASTER_ID = "discogs_master_id"
TAG_DISCOGS_MASTER_URL = "discogs_master_url"
TAG_ALLMUSIC_RELEASE_URL = "allmusic_release_url"
TAG_ALLMUSIC_RELEASEGROUP_URL = "allmusic_releasegroup_url"

OWNED_TAGS = frozenset({
    TAG_DISCOGS_RELEASE_ID,
    TAG_DISCOGS_RELEASE_URL,
    TAG_DISCOGS_MASTER_ID,
    TAG_DISCOGS_MASTER_URL,
    TAG_ALLMUSIC_RELEASE_URL,
    TAG_ALLMUSIC_RELEASEGROUP_URL,
})

RE_DISCOGS_RELEASE = re.compile(
    r"^https?://(?:www\.)?discogs\.com/(?:[a-z]{2}(?:_[A-Z]{2})?/)?release/(\d+)",
    re.IGNORECASE,
)
RE_DISCOGS_MASTER = re.compile(
    r"^https?://(?:www\.)?discogs\.com/(?:[a-z]{2}(?:_[A-Z]{2})?/)?master/(?:view/)?(\d+)",
    re.IGNORECASE,
)
RE_ALLMUSIC = re.compile(
    r"^https?://(?:www\.)?allmusic\.com/album/", re.IGNORECASE
)


def _relation_urls(entity: dict[str, Any] | None, relation_type: str) -> Iterable[str]:
    for relation in (entity or {}).get("relations") or []:
        if relation.get("target-type") != "url" or relation.get("type") != relation_type:
            continue
        resource = (relation.get("url") or {}).get("resource")
        if resource:
            yield str(resource)


def _first(values: Iterable[str]) -> str | None:
    ordered = sorted(set(values), key=lambda value: (value.casefold(), value))
    return ordered[0] if ordered else None


def project_external_links(
    release: dict[str, Any] | None,
    release_groups: Iterable[dict[str, Any]],
) -> dict[str, str]:
    """Project deterministic scalar bridge fields from MusicBrainz URL relations.

    Release relationships take precedence for release-level links. Release-group
    relationships take precedence for master/release-group links. Cross-attached
    Discogs relationships remain valid fallbacks, matching the standalone plugin.
    """
    release = release or {}
    groups = [group for group in release_groups if isinstance(group, dict)]

    release_discogs = list(_relation_urls(release, "discogs"))
    group_discogs = [url for group in groups for url in _relation_urls(group, "discogs")]
    release_allmusic = list(_relation_urls(release, "allmusic"))
    group_allmusic = [url for group in groups for url in _relation_urls(group, "allmusic")]

    result: dict[str, str] = {}
    release_url = _first(
        url for url in release_discogs if RE_DISCOGS_RELEASE.match(url)
    ) or _first(
        url for url in group_discogs if RE_DISCOGS_RELEASE.match(url)
    )
    if release_url:
        result[TAG_DISCOGS_RELEASE_URL] = release_url
        result[TAG_DISCOGS_RELEASE_ID] = RE_DISCOGS_RELEASE.match(release_url).group(1)
    master_url = _first(
        url for url in group_discogs if RE_DISCOGS_MASTER.match(url)
    ) or _first(
        url for url in release_discogs if RE_DISCOGS_MASTER.match(url)
    )
    if master_url:
        result[TAG_DISCOGS_MASTER_URL] = master_url
        result[TAG_DISCOGS_MASTER_ID] = RE_DISCOGS_MASTER.match(master_url).group(1)
    allmusic_release = _first(url for url in release_allmusic if RE_ALLMUSIC.match(url))
    if allmusic_release:
        result[TAG_ALLMUSIC_RELEASE_URL] = allmusic_release
    allmusic_group = _first(url for url in group_allmusic if RE_ALLMUSIC.match(url))
    if allmusic_group:
        result[TAG_ALLMUSIC_RELEASEGROUP_URL] = allmusic_group
    return result
