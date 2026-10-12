"""Generate deterministic placeholder data in a genuinely empty target.

Developer measurements never read the user's library. This marker proves a
successful synthetic build; it never grants permission to overwrite a folder.
Run from backend/: python -m scripts.synth_library <empty-target>.

Two generators share the target rules (03-C9, extended by 04-C3b):

- `generate(target, Shape)` writes hand-shaped files for spec 03's compiled
  cache benchmark (`scripts/benchmark_compiled.py`), marked
  `.synth-library.json`.
- `generate_library(target, profile, seed)` builds a library through the
  store's own public writers -- worlds, characters, entities, greetings,
  campaigns, scenes and their posts, the ledgers, review sidecars, a
  continuity candidate cache, and solid-colour images through the image store
  -- for the overview benchmark (`scripts/bench_overview.py`). Every file is in
  the current format by construction. Names are the placeholder set with an
  index; prose is a fixed nonsense vocabulary shuffled by a seeded RNG; the
  store's clock is a seeded one; file AND directory mtimes are backdated past
  spec 03's `PERSIST_WINDOW` (deepest first). ctime cannot be backdated on
  POSIX, so a fresh library records no `sources` row until that window has
  passed in wall time. Marked `.synthetic-library`.

    python -m scripts.synth_library --out DIR --profile {small,medium,large} --seed N
"""

from __future__ import annotations

import argparse
import json
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from grimoire.store import atomic, frontmatter

SYNTHETIC_MARKER = '.synthetic-library'
GENERATOR_VERSION = 1

MARKER = '.synth-library.json'
STAMP = '2026-01-01T00:00:00Z'
WORDS = ('Realm', 'Saltmarch', 'Seraphine', 'Mara', 'Winifred')


@dataclass(frozen=True)
class Shape:
    worlds: int = 2
    campaigns_per_world: int = 2
    scenes_per_campaign: int = 8
    records_per_world: int = 16
    body_words: int = 2000


def _empty_target(target: Path) -> None:
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400
            or not stat.S_ISDIR(info.st_mode) or next(target.iterdir(), None) is not None):
        raise ValueError('synthetic target must be a plain empty directory')


def _write(target: Path, relative: str, data: bytes) -> None:
    path = target / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_bytes(path, data)


def _markdown(meta: dict, body: str, crlf: bool = False) -> bytes:
    text = frontmatter.dump_frontmatter(meta, body)
    return (text.replace('\n', '\r\n') if crlf else text).encode('utf-8')


def _records(target: Path, base: str, count: int, body: str) -> None:
    for position in range(count):
        if position % 2 == 0:
            rid = f'realm-{position:03}'
            _write(target, f'{base}/locations/{rid}.md',
                   _markdown({'id': rid, 'name': f'Realm {position:03}', 'keys': ['Realm']}, body, position % 4 == 0))
        else:
            cid = f'seraphine-{position:03}'
            directory = f'{base}/characters/{cid}'
            _write(target, f'{directory}/character.md', _markdown(
                {'id': cid, 'name': f'Seraphine {position:03}', 'default_version': 'v1'}, 'Mara'))
            card = {'spec': 'chara_card_v2', 'spec_version': '2.0',
                    'data': {'name': f'Seraphine {position:03}', 'description': body,
                             'personality': 'Mara', 'scenario': 'Saltmarch',
                             'first_mes': 'Winifred', 'alternate_greetings': [], 'tags': []}}
            _write(target, f'{directory}/v1.json', (json.dumps(card, sort_keys=True) + '\n').encode('utf-8'))


def generate(target: Path, shape: Shape) -> dict[str, object]:
    """Publish placeholder records, marking only a fully successful build."""
    sizes = asdict(shape)
    if any(type(value) is not int or value < 0 for value in sizes.values()):
        raise ValueError('synthetic sizes must be nonnegative integers')
    target = Path(target)
    _empty_target(target)
    body = ' '.join(WORDS[index % len(WORDS)] for index in range(shape.body_words))
    target.mkdir(parents=True, exist_ok=True)
    (target / 'worlds').mkdir()
    (target / 'campaigns').mkdir()
    for world in range(shape.worlds):
        wid = f'realm-{world:03}'
        base = f'worlds/{wid}'
        _write(target, f'{base}/world.md', _markdown(
            {'id': wid, 'name': f'Realm {world:03}', 'genre': 'Saltmarch',
             'created': STAMP, 'updated': STAMP}, body))
        _records(target, base, shape.records_per_world, body)
        for campaign in range(shape.campaigns_per_world):
            cid = f'saltmarch-{world:03}-{campaign:03}'
            base = f'campaigns/{cid}'
            _write(target, f'{base}/campaign.md', _markdown(
                {'id': cid, 'name': f'Saltmarch {world:03} {campaign:03}', 'world': wid,
                 'created': STAMP, 'updated': STAMP, 'module': 'none'}, body, campaign % 2 == 0))
            for scene in range(shape.scenes_per_campaign):
                sid = f'{scene + 1:04}--realm'
                transcript = '**Mara:** ' + body + '\n\n**Seraphine:** ' + body
                _write(target, f'{base}/scenes/{sid}.md', _markdown(
                    {'id': sid, 'title': f'Realm {scene:03}', 'created': STAMP, 'updated': STAMP,
                     'ended': True, 'identity': f'{world:08x}{campaign:08x}{scene:016x}'}, transcript, scene % 2 == 0))
    manifest: dict[str, object] = {'schema': 1, 'generator': 'grimoire.synthetic', 'shape': sizes}
    _write(target, MARKER, (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode('utf-8'))
    return manifest


# ---- the overview profiles (04-C3b) ----------------------------------------

#: Structural knobs per profile. No number is drawn from a real library; each
#: is chosen for the boundary it exercises (spec 04 section 9.1).
#:
#: - `small` is what `make check-py` runs the counter assertions on: two worlds
#:   and three campaigns, so a change confined to one campaign has others to
#:   leave alone, and enough open and closed scenes to tell their reads apart.
#: - `large` holds more scene files in total than `statcache.MAX_ENTRIES`
#:   (4096), so a run proves the scene and overview pools keep the shared FIFO
#:   intact, with one campaign far larger than the rest for the
#:   "one campaign changed in a large library" scenario, and enough posts per
#:   scene that a transcript parse visibly dominates a stat and a hash.
PROFILES: dict[str, dict[str, int]] = {
    'small': {'worlds': 2, 'characters': 3, 'entities': 3, 'greetings': 1, 'images': 1,
              'campaigns': 3, 'scenes': 4, 'big_scenes': 6, 'open_scenes': 2, 'posts': 6,
              'reviews': 1, 'ledger_rows': 2, 'candidates': 1},
    'medium': {'worlds': 3, 'characters': 12, 'entities': 20, 'greetings': 6, 'images': 4,
               'campaigns': 8, 'scenes': 40, 'big_scenes': 120, 'open_scenes': 3, 'posts': 60,
               'reviews': 2, 'ledger_rows': 20, 'candidates': 6},
    'large': {'worlds': 4, 'characters': 30, 'entities': 60, 'greetings': 12, 'images': 8,
              'campaigns': 20, 'scenes': 200, 'big_scenes': 600, 'open_scenes': 4, 'posts': 120,
              'reviews': 3, 'ledger_rows': 60, 'candidates': 12},
}

#: The prose vocabulary. Nonsense on purpose: nothing is ever read from a file,
#: a store or the network to write a library.
VOCABULARY = ('salt', 'lamp', 'quay', 'tide', 'bell', 'rope', 'gate', 'fog', 'oar', 'gull',
              'stone', 'wick', 'harbor', 'ledger', 'lantern', 'cinder', 'reed', 'brine')
PLACEHOLDERS = ('Seraphine', 'Mara', 'Winifred')
#: The seeded clock starts here, a fixed second, and advances one second per
#: stamp the store asks for.
CLOCK_START = 1_767_225_600   # 2026-01-01T00:00:00Z
#: How far before generation every mtime is put: well past PERSIST_WINDOW_NS.
BACKDATE_S = 3600


def _png(rgb: tuple[int, int, int]) -> bytes:
    import struct
    import zlib
    raw = b''.join(b'\x00' + bytes(rgb) * 4 for _ in range(4))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 4, 4, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class _Clock:
    """A deterministic `now_iso`, patched over every module that bound it."""

    def __init__(self, start: int) -> None:
        self.t = start

    def __call__(self) -> str:
        import time as _time
        self.t += 1
        return _time.strftime('%Y-%m-%dT%H:%M:%SZ', _time.gmtime(self.t))


def _patched_clock(clock: _Clock):
    import contextlib
    import sys as _sys

    from grimoire.store import paths as store_paths

    @contextlib.contextmanager
    def patched():
        real = store_paths.now_iso
        swapped = []
        for module in list(_sys.modules.values()):
            if getattr(module, '__name__', '').startswith('grimoire') and getattr(module, 'now_iso', None) is real:
                swapped.append(module)
                module.now_iso = clock
        try:
            yield
        finally:
            for module in swapped:
                module.now_iso = real
    return patched()


def _backdate(target: Path, when_ns: int) -> None:
    """Every file's and directory's mtime to `when_ns`, deepest first, so a
    child's backdating does not move its parent again."""
    import os as _os
    entries = sorted(target.rglob('*'), key=lambda p: len(p.parts), reverse=True)
    for entry in [*entries, target]:
        _os.utime(entry, ns=(when_ns, when_ns), follow_symlinks=False)


def _library_world(w: int, knobs: dict[str, int], prose) -> str:
    """One world, its cast (the first few with a portrait), entities and
    greetings, through the store's writers. Returns its id."""
    import random

    from grimoire.store import assets, characters, entities, greetings, worlds
    rng = random.Random(w)
    wid = worlds.create_world(f'Realm {w}', genre='Saltmarch', description=prose(30))
    root = worlds.world_root(wid)
    cast_ids = []
    for n in range(knobs['characters']):
        ch, vid = characters.create_character(root, f'{PLACEHOLDERS[n % len(PLACEHOLDERS)]} {n}', 'default')
        cast_ids.append((ch, vid))
        if n < knobs['images']:
            colour = (rng.randrange(256), rng.randrange(256), rng.randrange(256))
            assets.put_image(root, ch, vid, f'portrait-{n}', _png(colour), 'png')
    for n in range(knobs['entities']):
        entities.create_entity(root, 'locations' if n % 2 else 'lore', f'Saltmarch {n}', prose(40))
    for n, (ch, vid) in enumerate(cast_ids[:knobs['greetings']]):
        greetings.create_greeting(root, f'Saltmarch Greeting {n}', ch, vid, prose(20))
    return wid


def _library_campaign(c: int, wid: str, scene_count: int, knobs: dict[str, int], prose) -> None:
    """One campaign: its scenes and posts (all but the first few absorbed),
    both ledgers, review sidecars and a continuity candidate cache."""
    from grimoire.store import campaigns, commitments, pending_reviews, plot, scenes
    from grimoire.store.continuity import candidates
    cid = campaigns.create_campaign(f'Saltmarch {c}', wid)
    first = ''
    for n in range(scene_count):
        sid = scenes.create_scene(cid, f'Realm Scene {n}')
        first = first or sid
        speaker = PLACEHOLDERS[n % len(PLACEHOLDERS)]
        scenes.append_messages(cid, sid, [
            {'role': 'user' if k % 2 == 0 else 'assistant', 'content': prose(25),
             **({} if k % 2 == 0 else {'speaker': speaker})}
            for k in range(knobs['posts'])])
        if n >= knobs['open_scenes']:
            scenes.mark_absorbed(cid, sid, prose(6), prose(20))
    for n in range(knobs['ledger_rows']):
        commitments.set_movement(cid, f'oath-{n}', f'Oath {n}', 'promise', 'open',
                                 '2026-06-01' if n % 2 == 0 else '', prose(8), first)
        plot.set_movement(cid, f'thread-{n}', f'Thread {n}', 'open', prose(8), first)
    for n, row in enumerate(scenes.list_scenes(cid)[:knobs['reviews']]):
        pending_reviews.publish(cid, row['id'], f'gen-{n}', {'edits': [{'kind': 'fact'}]}, {'count': 1})
    data = candidates.empty()
    for n in range(knobs['candidates']):
        data['records'][f'cand-{n}'] = {
            'kind': 'possible_duplicate', 'refs': [f'commitment:oath-{n}', f'commitment:oath-{n + 1}'],
            'fingerprint': f'fp-{n}', 'proposal': None}
    candidates.write(cid, data)


def generate_library(target: Path, profile: str, seed: int) -> dict[str, object]:
    """Build a `profile` library in an empty `target` through the store's
    writers, mark it, and backdate it. Returns the marker's contents."""
    import random
    import time as _time

    from grimoire.store import paths

    if profile not in PROFILES:
        raise ValueError(f'unknown profile: {profile}')
    if type(seed) is not int:
        raise ValueError('seed must be an integer')
    knobs = PROFILES[profile]
    target = Path(target)
    _empty_target(target)
    rng = random.Random(seed)

    def prose(words: int) -> str:
        return ' '.join(rng.choice(VOCABULARY) for _ in range(words)).capitalize() + '.'

    target.mkdir(parents=True, exist_ok=True)
    with paths.pin_library(target), _patched_clock(_Clock(CLOCK_START)):
        world_ids = [_library_world(w, knobs, prose) for w in range(knobs['worlds'])]
        sizes = [knobs['big_scenes']] + [knobs['scenes']] * (knobs['campaigns'] - 1)
        for c, scene_count in enumerate(sizes):
            _library_campaign(c, world_ids[c % len(world_ids)], scene_count, knobs, prose)
    manifest: dict[str, object] = {'generator': 'grimoire.synthetic-library', 'version': GENERATOR_VERSION,
                                   'profile': profile, 'seed': seed}
    (target / SYNTHETIC_MARKER).write_text(json.dumps(manifest, sort_keys=True) + '\n', encoding='utf-8')
    _backdate(target, _time.time_ns() - BACKDATE_S * 1_000_000_000)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path, nargs='?')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--profile', choices=sorted(PROFILES))
    parser.add_argument('--seed', type=int, default=0)
    for flag, field in [('worlds', 'worlds'), ('campaigns', 'campaigns_per_world'),
                        ('scenes', 'scenes_per_campaign'), ('records', 'records_per_world'), ('body-words', 'body_words')]:
        parser.add_argument('--' + flag, dest=field, type=int, default=getattr(Shape(), field))
    options = vars(parser.parse_args())
    target = options.pop('target') or options.pop('out')
    options.pop('out', None)
    profile, seed = options.pop('profile'), options.pop('seed')
    if target is None:
        parser.error('a target is required (positional, or --out)')
    if profile is not None:
        try:
            print(json.dumps(generate_library(target, profile, seed), sort_keys=True))
        except (ValueError, OSError) as error:
            parser.error(str(error))
        return
    try:
        manifest = generate(target, Shape(**options))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
