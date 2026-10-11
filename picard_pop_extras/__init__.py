"""MusicBrainz Pop Extras plugin for MusicBrainz Picard 2."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from PyQt5.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from picard import config, log
from picard.config import BoolOption, IntOption
from picard.const import USER_PLUGIN_DIR
from picard.metadata import register_album_metadata_processor, register_track_metadata_processor
from picard.plugin import PluginPriority
from picard.ui.options import OptionsPage, register_options_page

from .core import MAPPER_FIELDS, PopExtrasProjector
from .external_links import OWNED_TAGS as BRIDGE_TAGS
from .external_links import project_external_links
from .retrieval import EntityBroker, INCLUDES, ReleaseCrawl


PLUGIN_NAME = "MusicBrainz Pop Extras"
PLUGIN_AUTHOR = "MusicBrainz Pop Extras Project"
PLUGIN_DESCRIPTION = (
    "Retrieves detailed MusicBrainz recording/work relationships and writes "
    "qualified musicbrainz_* tags. Optionally writes Discogs and AllMusic "
    "bridge links published as MusicBrainz URL relationships."
)
PLUGIN_VERSION = "0.1.0"
PLUGIN_API_VERSIONS = ["2.0"]
PLUGIN_LICENSE = "GPL-2.0-or-later"
PLUGIN_LICENSE_URL = "https://www.gnu.org/licenses/gpl-2.0.html"


OPT_ENABLED = "pop_extras_enabled"
OPT_PERSIST = "pop_extras_persist_tags"
OPT_EXTERNAL_LINKS = "pop_extras_external_links"
OPT_LOCAL_ACCEL = "pop_extras_local_mirror_acceleration"
OPT_LOCAL_CONCURRENCY = "pop_extras_local_mirror_concurrency"
OPT_LOCAL_DELAY_MS = "pop_extras_local_mirror_delay_ms"

DEFAULT_CONCURRENCY = 4
BROKERS = {}
_RATECONTROL_ORIGINALS = {}

# Picard normalizes Vorbis names internally. Lowercase keys avoid duplicate
# FLAC comments when a file already contains uppercase on-disk fields.
POP_TAGS = frozenset(name.casefold() for name in MAPPER_FIELDS)


def _bounded_setting(name, default, minimum, maximum):
    try:
        value = int(config.setting[name])
    except (KeyError, TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _loopback_hostkey(server):
    try:
        parsed = urlsplit(server)
        host = (parsed.hostname or "").rstrip(".").casefold()
        ipv4 = host.split(".")
        ipv4_loopback = (
            len(ipv4) == 4
            and ipv4[0] == "127"
            and all(part.isdigit() and 0 <= int(part) <= 255 for part in ipv4)
        )
        if host not in {"localhost", "::1", "0:0:0:0:0:0:0:1"} and not ipv4_loopback:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port
    except (ValueError, AttributeError):
        return None


def _restore_ratecontrol(hostkey, ratecontrol):
    original = _RATECONTROL_ORIGINALS.pop(hostkey, None)
    if original is None:
        return
    minimum, delay, window = original
    ratecontrol.REQUEST_DELAY_MINIMUM[hostkey] = minimum
    ratecontrol.REQUEST_DELAY[hostkey] = delay
    ratecontrol.CONGESTION_WINDOW_SIZE[hostkey] = window


def _network_profile(server):
    concurrency = _bounded_setting(OPT_LOCAL_CONCURRENCY, 8, 1, 32)
    delay_ms = _bounded_setting(OPT_LOCAL_DELAY_MS, 10, 0, 1000)
    hostkey = _loopback_hostkey(server)
    requested = bool(config.setting[OPT_LOCAL_ACCEL])
    if not hostkey:
        if requested:
            log.warning(
                "%s: local-mirror acceleration ignored for non-loopback server %r",
                PLUGIN_NAME,
                server,
            )
        return False, DEFAULT_CONCURRENCY, None
    if not requested and hostkey not in _RATECONTROL_ORIGINALS:
        return False, DEFAULT_CONCURRENCY, None
    try:
        from picard.webservice import ratecontrol

        required = (
            "REQUEST_DELAY_MINIMUM",
            "REQUEST_DELAY",
            "CONGESTION_WINDOW_SIZE",
            "set_minimum_delay",
        )
        if not all(hasattr(ratecontrol, name) for name in required):
            raise AttributeError("Picard rate-control API is incomplete")
        if not requested:
            _restore_ratecontrol(hostkey, ratecontrol)
            return False, DEFAULT_CONCURRENCY, None
        if hostkey not in _RATECONTROL_ORIGINALS:
            _RATECONTROL_ORIGINALS[hostkey] = (
                ratecontrol.REQUEST_DELAY_MINIMUM.get(hostkey, 1000),
                ratecontrol.REQUEST_DELAY.get(hostkey, 1000),
                ratecontrol.CONGESTION_WINDOW_SIZE.get(hostkey, 1.0),
            )
        ratecontrol.set_minimum_delay(hostkey, delay_ms)
        ratecontrol.REQUEST_DELAY[hostkey] = delay_ms
        ratecontrol.CONGESTION_WINDOW_SIZE[hostkey] = float(concurrency)
        return True, concurrency, delay_ms
    except (ImportError, AttributeError, TypeError, ValueError) as error:
        log.warning("%s: unable to apply local-mirror acceleration: %s", PLUGIN_NAME, error)
        return False, DEFAULT_CONCURRENCY, None


def _first(metadata, *keys):
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value:
            return value
    return None


def _node_get(node, *keys):
    current = node
    for key in keys:
        if current is None:
            return None
        try:
            current = current.get(key)
        except (AttributeError, TypeError):
            try:
                current = current[key]
            except (KeyError, TypeError):
                return None
    return current


def _delete(metadata, name):
    try:
        del metadata[name]
    except KeyError:
        pass


def _write_owned(metadata, values, owned, *, replace):
    """Write one owned family; prune only after a complete safe projection."""
    persistent = bool(config.setting[OPT_PERSIST])
    normalized = {str(name).casefold(): value for name, value in values.items()}
    for name in owned:
        hidden = "~" + name
        if hidden in metadata:
            del metadata[hidden]
        if persistent and replace and name not in normalized:
            _delete(metadata, name)
    for name, value in normalized.items():
        output_name = name if persistent else "~" + name
        if isinstance(value, list):
            metadata[output_name] = list(value)
        elif value is not None:
            metadata[output_name] = str(value)


def _set_status(metadata, value):
    metadata["~pop_extras_plugin_status"] = value


def process_album(album, metadata, release_node):
    """Hold finalization until one shared focused crawl completes."""
    album._pop_extras_projector = None
    album._pop_extras_external_links = {}
    album._pop_extras_errors = []
    album._pop_extras_retrieval_status = "not_started"
    if not config.setting[OPT_ENABLED]:
        return

    api = album.tagger.mb_api
    server = api.base_url.toString()
    accelerated, concurrency, delay_ms = _network_profile(server)
    cache_folder = Path(USER_PLUGIN_DIR).parent / "pop_extras_cache" / "v0.1"
    key = server, str(cache_folder), id(api), concurrency
    if key not in BROKERS:
        def fetch(entity, mbid, callback):
            def received(document, http, error):
                message = http.errorString() if error and http else str(error) if error else None
                callback(document, message)

            api.get(
                "/" + entity + "/" + mbid,
                received,
                unencoded_queryargs={"inc": "+".join(INCLUDES[entity]), "fmt": "json"},
                parse_response_type="json",
                mblogin=False,
                priority=False,
            )

        BROKERS[key] = EntityBroker(server, cache_folder, fetch, concurrency=concurrency)
    broker = BROKERS[key]

    album._requests += 1
    released = False

    def is_current():
        return album.tagger.albums.get(album.id) is album

    def completed(semantic, errors):
        nonlocal released
        if released:
            return
        released = True
        try:
            if is_current():
                album._pop_extras_errors = list(errors)
                album._pop_extras_retrieval_status = "partial" if errors else "complete"
                if semantic and not errors:
                    album._pop_extras_projector = PopExtrasProjector(semantic, crawl.release)
                    if config.setting[OPT_EXTERNAL_LINKS]:
                        album._pop_extras_external_links = project_external_links(
                            crawl.release,
                            crawl.groups.values(),
                        )
                elif not semantic:
                    album._pop_extras_retrieval_status = "failed"
                diagnostics = crawl.diagnostics()
                diagnostics.update({
                    "local_mirror_acceleration": accelerated,
                    "request_delay_ms": delay_ms,
                })
                log.info(
                    "%s: %s retrieval %s (%d errors) diagnostics %s",
                    PLUGIN_NAME,
                    album.id,
                    album._pop_extras_retrieval_status,
                    len(errors),
                    json.dumps(diagnostics, sort_keys=True),
                )
                for error in errors:
                    log.warning("%s: %s", PLUGIN_NAME, error)
        except Exception:
            album._pop_extras_retrieval_status = "failed"
            log.exception("%s: unable to finalize retrieval", PLUGIN_NAME)
        finally:
            album._requests -= 1
            if is_current() and not album.loaded:
                album._finalize_loading(None)

    crawl = ReleaseCrawl(album.id, broker, completed, concurrency=concurrency)
    album._pop_extras_crawl = crawl
    try:
        crawl.start()
    except Exception as error:
        completed(None, [{"entity": "release", "id": album.id, "error": str(error)}])


def process_track(album, metadata, track_node, release_node):
    _set_status(metadata, "processor_called")
    if not config.setting[OPT_ENABLED]:
        _set_status(metadata, "disabled")
        return
    status = getattr(album, "_pop_extras_retrieval_status", "failed")
    projector = getattr(album, "_pop_extras_projector", None)
    if status != "complete" or projector is None:
        _set_status(metadata, "retrieval:" + status)
        return

    release_id = (
        _first(metadata, "musicbrainz_albumid")
        or _node_get(release_node, "id")
        or getattr(album, "id", None)
    )
    if not release_id or release_id != projector.release_id:
        _set_status(metadata, "release_mismatch")
        return
    release_track_id = (
        _first(metadata, "musicbrainz_releasetrackid")
        or _node_get(track_node, "id")
    )
    recording_id = (
        _first(metadata, "musicbrainz_recordingid", "musicbrainz_trackid")
        or _node_get(track_node, "recording", "id")
    )
    fields, match_status = projector.project_track(release_track_id, recording_id)
    if not fields:
        _set_status(metadata, "track:" + match_status)
        return

    _write_owned(metadata, fields, POP_TAGS, replace=True)
    if config.setting[OPT_EXTERNAL_LINKS]:
        links = getattr(album, "_pop_extras_external_links", {})
        _write_owned(metadata, links, BRIDGE_TAGS, replace=True)
    _set_status(metadata, "projected:" + match_status)


class PopExtrasOptionsPage(OptionsPage):
    NAME = "musicbrainz_pop_extras"
    TITLE = "MusicBrainz Pop Extras"
    PARENT = "plugins"
    options = [
        BoolOption("setting", OPT_ENABLED, True),
        BoolOption("setting", OPT_PERSIST, True),
        BoolOption("setting", OPT_EXTERNAL_LINKS, True),
        BoolOption("setting", OPT_LOCAL_ACCEL, False),
        IntOption("setting", OPT_LOCAL_CONCURRENCY, 8),
        IntOption("setting", OPT_LOCAL_DELAY_MS, 10),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Enable MusicBrainz Pop Extras retrieval")
        layout.addWidget(self.enabled)
        self.persist = QCheckBox("Write Pop Extras fields to files")
        layout.addWidget(self.persist)
        self.external_links = QCheckBox(
            "Write Discogs and AllMusic bridge links from MusicBrainz"
        )
        layout.addWidget(self.external_links)
        self.local_accel = QCheckBox(
            "Accelerate retrieval for a loopback MusicBrainz mirror"
        )
        layout.addWidget(self.local_accel)
        row = QHBoxLayout()
        row.addWidget(QLabel("Local concurrency:"))
        self.local_concurrency = QSpinBox()
        self.local_concurrency.setRange(1, 32)
        row.addWidget(self.local_concurrency)
        row.addWidget(QLabel("Delay between requests (ms):"))
        self.local_delay = QSpinBox()
        self.local_delay.setRange(0, 1000)
        row.addWidget(self.local_delay)
        layout.addLayout(row)
        layout.addWidget(QLabel(
            "Local acceleration applies only to localhost/loopback servers. "
            "Entity cache lifetime: 7 days. Disable the standalone Discogs & "
            "AllMusic Links plugin when this bridge option is enabled."
        ))
        layout.addStretch(1)

    def load(self):
        self.enabled.setChecked(config.setting[OPT_ENABLED])
        self.persist.setChecked(config.setting[OPT_PERSIST])
        self.external_links.setChecked(config.setting[OPT_EXTERNAL_LINKS])
        self.local_accel.setChecked(config.setting[OPT_LOCAL_ACCEL])
        self.local_concurrency.setValue(config.setting[OPT_LOCAL_CONCURRENCY])
        self.local_delay.setValue(config.setting[OPT_LOCAL_DELAY_MS])

    def save(self):
        config.setting[OPT_ENABLED] = self.enabled.isChecked()
        config.setting[OPT_PERSIST] = self.persist.isChecked()
        config.setting[OPT_EXTERNAL_LINKS] = self.external_links.isChecked()
        config.setting[OPT_LOCAL_ACCEL] = self.local_accel.isChecked()
        config.setting[OPT_LOCAL_CONCURRENCY] = self.local_concurrency.value()
        config.setting[OPT_LOCAL_DELAY_MS] = self.local_delay.value()


register_album_metadata_processor(process_album, priority=PluginPriority.LOW)
register_track_metadata_processor(process_track, priority=PluginPriority.LOW)
register_options_page(PopExtrasOptionsPage)
