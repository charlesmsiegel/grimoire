"""Generate deterministic placeholder data in a genuinely empty target.

Developer measurements never read the user's library. This marker proves a
successful synthetic build; it never grants permission to overwrite a folder.
Run from backend/: python -m scripts.synth_library <empty-target>.
"""

from __future__ import annotations

import argparse
import json
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from grimoire.store import atomic, frontmatter

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', type=Path)
    for flag, field in [('worlds', 'worlds'), ('campaigns', 'campaigns_per_world'),
                        ('scenes', 'scenes_per_campaign'), ('records', 'records_per_world'), ('body-words', 'body_words')]:
        parser.add_argument('--' + flag, dest=field, type=int, default=getattr(Shape(), field))
    options = vars(parser.parse_args())
    target = options.pop('target')
    try:
        manifest = generate(target, Shape(**options))
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
