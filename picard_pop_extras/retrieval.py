"""Callback-driven focused crawl and server-isolated shared entity cache.

No Picard/Qt imports: injected fetch uses callback(data, error). Only actual
release recordings seed works; reverse performances never become requests.
"""
from collections import deque
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from . import semantic as rules

INCLUDES = {
    'release': rules.RELEASE_INCLUDES,
    'release-group': rules.RELEASE_GROUP_INCLUDES,
    'recording': rules.RECORDING_INCLUDES,
    'work': rules.WORK_INCLUDES,
}
CACHE_TTL = 7 * 24 * 3600


class EntityBroker:
    """Coalesce in-flight requests, limit concurrency, persist successful JSON."""
    def __init__(self, server, folder, fetch, concurrency=4):
        self.server, self.fetch = server, fetch
        self.folder = Path(folder) if folder else None
        self.concurrency = concurrency
        self.memory, self.waiters = {}, {}
        self.queue = deque()
        self.active = 0
        self.pumping = False
        self.hits = self.requests = 0
        self.cache_errors = []

    def _path(self, entity, mbid):
        fingerprint = json.dumps([self.server, entity, mbid, INCLUDES[entity], 1])
        return self.folder / (hashlib.sha256(fingerprint.encode()).hexdigest() + '.json')

    def get(self, entity, mbid, callback, metrics=None):
        def count(name):
            if metrics is not None:
                metrics[name] = metrics.get(name, 0) + 1
        key = entity, mbid
        cached = self.memory.get(key)
        if cached is None and self.folder:
            try:
                envelope = json.loads(self._path(*key).read_text(encoding='utf-8'))
                age = time.time() - envelope['time']
                if 0 <= age < CACHE_TTL and envelope['data'].get('id') == mbid:
                    cached = envelope['time'], envelope['data']
                    self.memory[key] = cached
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
        if cached is not None and 0 <= time.time() - cached[0] < CACHE_TTL:
            count('cache_hits')
            self.hits += 1
            callback(deepcopy(cached[1]), None)
            return
        if key in self.waiters:
            count('coalesced_lookups')
            self.waiters[key].append(callback)
            return
        self.waiters[key] = [callback]
        count('network_requests')
        self.queue.append(key)
        self._pump()

    def _pump(self):
        if self.pumping:
            return
        self.pumping = True
        try:
            while self.queue and self.active < self.concurrency:
                key = self.queue.popleft()
                self.active += 1
                self.requests += 1
                def received(data, error, key=key):
                    if key not in self.waiters:
                        return  # ignore a duplicate completion
                    if not error and (not isinstance(data, dict) or data.get('id') != key[1]):
                        error = 'invalid_or_redirected_entity_response'
                    if not error:
                        self.memory[key] = time.time(), deepcopy(data)
                        if len(self.memory) > 4096:
                            self.memory.pop(next(iter(self.memory)))
                        self._persist(key, data)
                    callbacks = self.waiters.pop(key)
                    self.active -= 1
                    for listener in callbacks:
                        listener(deepcopy(data) if not error else None, error)
                    self._pump()
                try:
                    self.fetch(*key, received)
                except Exception as error:
                    received(None, str(error))
        finally:
            self.pumping = False

    def _persist(self, key, data):
        if not self.folder:
            return
        temporary = None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.folder, delete=False) as stream:
                temporary = stream.name
                json.dump({'time': time.time(), 'data': data}, stream, ensure_ascii=False)
            os.replace(temporary, self._path(*key))
        except OSError as error:
            self.cache_errors.append(str(error))
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


class ReleaseCrawl:
    MAX_WORKS = 2000
    MAX_ROUTES = 10000

    def __init__(self, release_id, broker, completed, progress=None, concurrency=None):
        self.release_id, self.broker, self.completed = release_id, broker, completed
        self.progress = progress or (lambda value: None)
        self.concurrency = max(1, int(concurrency or broker.concurrency))
        self.release = None
        self.groups, self.recordings, self.works = {}, {}, {}
        self.tracks = []
        self.depths, self.source_depths, self.provenance, self.discoveries = {}, {}, {}, {}
        self.assessments, self.errors = [], []
        self.routes = set()
        self.work_ids = set()
        self.queue = deque()
        self.active = 0
        self.pumping = self.done = False
        self.recording_pending = 0
        self.started = time.monotonic()
        self.metrics = {
            'cache_hits': 0, 'coalesced_lookups': 0, 'network_requests': 0,
            'completed_lookups': 0, 'embedded_recordings_reused': 0,
            'recording_fallback_requests': 0, 'embedded_release_groups_reused': 0,
        }

    def diagnostics(self):
        return dict(self.metrics, elapsed_seconds=round(time.monotonic() - self.started, 3),
                    recordings=len(self.recordings), works=len(self.works),
                    queued=len(self.queue), active=self.active,
                    concurrency=self.concurrency)

    def start(self):
        self._request('release', self.release_id, self._release)
        self._pump()

    def _request(self, entity, mbid, consume):
        self.queue.append((entity, mbid, consume))

    def _pump(self):
        if self.pumping or self.done:
            return
        self.pumping = True
        try:
            while self.queue and self.active < self.concurrency:
                entity, mbid, consume = self.queue.popleft()
                self.active += 1
                def received(data, error, entity=entity, mbid=mbid, consume=consume):
                    if self.done:
                        return
                    if error:
                        self.errors.append({'entity': entity, 'id': mbid, 'error': str(error)})
                    try:
                        consume(data)
                    except Exception as failure:
                        self.errors.append({'entity': entity, 'id': mbid, 'error': str(failure)})
                    finally:
                        self.active -= 1
                        self.metrics['completed_lookups'] += 1
                        self.progress('loading:' + json.dumps(self.diagnostics(), sort_keys=True))
                        self._pump()
                self.broker.get(entity, mbid, received, metrics=self.metrics)
        finally:
            self.pumping = False
        if not self.queue and not self.active and not self.done:
            self.done = True
            try:
                result = self._semantic() if self.release else None
            except Exception as error:
                self.errors.append({'entity': 'semantic', 'error': str(error)})
                result = None
            self.completed(result, self.errors)

    def _release(self, data):
        self.release = data
        if not data:
            return
        group = data.get('release-group') or {}
        if group.get('id'):
            self.groups[group['id']] = group
            # release-group-level-rels can make the embedded object complete
            # for Pop Extras and the optional external-link bridge. Reuse it
            # instead of issuing the standalone plugin's old fallback request.
            if isinstance(group.get('relations'), list):
                self.metrics['embedded_release_groups_reused'] += 1
            else:
                self._request('release-group', group['id'], lambda result: self.groups.update(
                    {group['id']: result}) if result else None)
        embedded = {}
        for mi, medium in enumerate(data.get('media', []), 1):
            tracks = list(medium.get('tracks') or []) + list(medium.get('data-tracks') or [])
            if medium.get('pregap'):
                tracks.insert(0, medium['pregap'])
            for ti, track in enumerate(tracks, 1):
                recording = track.get('recording') or {}
                rid = recording.get('id')
                if not rid:
                    continue
                embedded[rid] = recording
                self.tracks.append({
                    'medium_position': medium.get('position', mi),
                    'track_position': track.get('position', ti),
                    'track_number': track.get('number'), 'track_title': track.get('title'),
                    'track_length': track.get('length'), 'recording_id': rid,
                })
        fallback_recordings = []
        for rid, fallback in embedded.items():
            if self._usable_embedded_recording(fallback):
                self.recordings[rid] = fallback
                self.metrics['embedded_recordings_reused'] += 1
            else:
                fallback_recordings.append((rid, fallback))
        self.recording_pending = len(fallback_recordings)
        if not self.recording_pending:
            self._seed_performed_works(embedded)
            return
        for rid, fallback in fallback_recordings:
            def recording(result, rid=rid, fallback=fallback):
                self.recordings[rid] = result or fallback
                self.recording_pending -= 1
                if not self.recording_pending:
                    self._seed_performed_works(embedded)
            self.metrics['recording_fallback_requests'] += 1
            self._request('recording', rid, recording)

    @staticmethod
    def _usable_embedded_recording(recording):
        """Return whether the release response has projection-complete data."""
        if not isinstance(recording, dict):
            return False
        required = ('id', 'title', 'artist-credit', 'isrcs', 'relations')
        return all(key in recording for key in required) and isinstance(
            recording.get('relations'), list
        )

    def _seed_performed_works(self, recording_order):
        """Seed works in release order after all required recordings are ready."""
        for identity in recording_order:
            for rel in self.recordings[identity].get('relations', []):
                work = rel.get('work') or {}
                if rel.get('type') in {'performance', 'performance of'} and work.get('id'):
                    self._route(work['id'], 'performed', 0, 0, 0, 0)

    def _route(self, mbid, route, parent, canonical, contextual, component, discovery=None):
        key = mbid, route, parent, canonical, contextual, component
        if key not in self.routes and (len(self.routes) >= self.MAX_ROUTES or (mbid not in self.work_ids and len(self.work_ids) >= self.MAX_WORKS)):
            if not any(item.get('error') == 'work_budget_reached' for item in self.errors):
                self.errors.append({'entity': 'work', 'error': 'work_budget_reached'})
            return
        self.provenance.setdefault(mbid, set()).add(route)
        self.depths[mbid] = min(self.depths.get(mbid, parent), parent)
        self.source_depths[mbid] = min(self.source_depths.get(mbid, canonical+contextual), canonical+contextual)
        discovery = discovery or {'route': route, 'source_work_id': None, 'relationship_type': None}
        if discovery not in self.discoveries.setdefault(mbid, []):
            self.discoveries[mbid].append(discovery)
        # Coalesce traversal, not evidence: multiple performed movements can
        # point to the same source with exactly the same bounded route state.
        if key in self.routes:
            return
        self.routes.add(key)
        self.work_ids.add(mbid)
        def consume(data):
            if not data:
                return
            self.works[mbid] = data
            for rel in data.get('relations', []):
                target = (rel.get('work') or {}).get('id')
                if not target:
                    continue
                if parent < rules.MAX_PARENT_WORK_DEPTH and rules.is_upward_parent_relation(rel):
                    next_route = 'performed_ancestor' if route in {
                        'performed', 'performed_component', 'performed_ancestor'} else 'source_ancestor'
                    self._route(target, next_route, parent+1, canonical, contextual, component, {
                        'route': next_route, 'source_work_id': mbid,
                        'relationship_type': 'part of', 'direction': 'upward',
                    })
                if route == 'performed' and component < rules.MAX_PERFORMED_COMPONENT_DEPTH and rules.is_downward_child_relation(rel):
                    self._route(target, 'performed_component', 0, canonical, contextual, component+1, {
                        'route': 'performed_component', 'source_work_id': mbid,
                        'relationship_type': rel.get('type'), 'relationship_type_id': rel.get('type-id'),
                        'direction': 'downward', 'ordering_key': rel.get('ordering-key'),
                    })
                kind = rules.source_relation_kind(rel)
                if not kind:
                    continue
                assessment = rules.assess_source_relation(data, rel)
                assertion = {
                    'source_work_id': mbid, 'source_work_title': data.get('title'),
                    'target_work_id': target, 'target_work_title': rel['work'].get('title'),
                    'relationship_type': rel.get('type'), 'relationship_type_id': rel.get('type-id'),
                    'direction': rel.get('direction'), 'assessment': assessment,
                }
                if assertion not in self.assessments:
                    self.assessments.append(assertion)
                if assessment['decision'] != 'traverse':
                    continue
                nc, nx = canonical, contextual
                if kind == 'canonical_source':
                    if nc >= rules.MAX_CANONICAL_SOURCE_DEPTH:
                        continue
                    nc += 1
                else:
                    if nx >= rules.MAX_CONTEXTUAL_SOURCE_DEPTH:
                        continue
                    nx += 1
                self._route(target, kind, 0, nc, nx, 0, {
                    'route': kind, 'source_work_id': mbid,
                    'relationship_type': rel.get('type'), 'relationship_type_id': rel.get('type-id'),
                    'direction': rel.get('direction'), 'source_confidence': assessment['confidence'],
                    'source_evidence': assessment['supporting_evidence'],
                })
        self._request('work', mbid, consume)

    def _semantic(self):
        entities = [('release', self.release_id, 'release', self.release)]
        for kind, storage in [('release-group', self.groups), ('recording', self.recordings), ('work', self.works)]:
            entities.extend((kind, mbid, kind+':'+mbid, data) for mbid, data in storage.items())
        captured = []
        for kind, mbid, label, data in entities:
            rules.collect_entity_aware_relations(data, label, kind, mbid, data.get('title'), captured)
        result = rules.make_semantic_map(
            self.release, self.groups, self.recordings, self.works, self.tracks,
            self.depths, self.source_depths, self.provenance, self.discoveries,
            entities, len(captured), 'focused', 0, True, self.assessments,
        )
        result['server'] = self.broker.server
        result['retrieval_errors'] = self.errors
        return result
