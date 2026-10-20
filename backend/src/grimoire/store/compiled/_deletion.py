"""Durable delete intents, bounded purge and storage synchronization fences."""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import secrets
from pathlib import Path

from .. import atomic, locks, proclock
from . import _contracts, _fences, _ownership, _path_checks, _retention, _runtime, _sqlite_support


def _intent_lock(root: Path, token: str) -> int | None:
    return proclock.acquire(proclock.lock_path(root, 'compiled-delete', token), proclock.NO_WAIT)


def prepare_delete(root: Path) -> _runtime.DeleteIntent | None:
    """Durably fence optional storage before an authorized private delete."""
    _runtime._process_state()
    # Fork must not inherit the acquisition-to-registration descriptor gap.
    # This mutex protects intent resources only; preparation performs no SQL.
    with _runtime._state.intent_lock:
        return _prepare_delete(root)


def _prepare_delete(root: Path) -> _runtime.DeleteIntent | None:
    fd = None
    try:
        _release_finished_intents_locked()
        root = Path(root)
        parent = _path_checks._cache_parent(root)
        device = _runtime._device(root)
        if parent is None or device is None:
            return None
        directory = parent / 'delete-intents'
        if not _path_checks._directory(directory):
            return None
        token = secrets.token_hex(16)
        if not _path_checks._leaf(directory / token):
            return None
        fd = _intent_lock(root, token)
        if fd is None:
            return None
        owner = _runtime._state.owners[root]
        atomic.write_text(directory / token, json.dumps({'token': token, 'owner': owner}, separators=(',', ':')))
        intent = _runtime.DeleteIntent(token, owner, fd, root)
        _runtime._state.intents[token] = intent
        fd = None
        return intent
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('intent_publish')
        return None
    finally:
        if fd is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                proclock.release(fd)


def _release_intents() -> None:
    with _runtime._state.intent_lock:
        _release_intents_locked()


def _release_intents_locked() -> None:
    for intent in tuple(_runtime._state.intents.values()):
        if intent.descriptor is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                proclock.release(intent.descriptor)
            intent.descriptor = None
    _runtime._state.intents.clear()
    _runtime._state.finished_intents.clear()


def _release_finished_intents_locked() -> None:
    # A completion cannot wait behind another preparation's filesystem I/O.
    # Keep its descriptor registered for fork safety until this mutex is
    # available; releasing it makes the durable intent recoverable normally.
    # Completed resources have their own queue: 128 live preparations ahead
    # of one finished delete cannot permanently strand its descriptor.
    for _ in range(128):
        if not _runtime._state.finished_intents:
            break
        intent = _runtime._state.finished_intents[0]
        _release_finished_intent(intent)
        _runtime._state.finished_intents.popleft()
    # Allocation failure while queueing still leaves the flag on its already
    # registered resource. A bounded fallback can release those owners too.
    for intent in list(itertools.islice(_runtime._state.intents.values(), 128)):
        if intent.finished:
            _release_finished_intent(intent)


def _release_finished_intent(intent: _runtime.DeleteIntent) -> None:
    if intent.descriptor is not None:
        proclock.release(intent.descriptor)
        intent.descriptor = None
    _runtime._state.intents.pop(intent.token, None)


def _release_finished_intents() -> None:
    if not _runtime._state.intent_lock.acquire(blocking=False):
        return
    try:
        _release_finished_intents_locked()
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('intent_release')
    finally:
        _runtime._state.intent_lock.release()


def _retire_intent(intent: _runtime.DeleteIntent, *, no_wait: bool = False) -> bool:
    if not _runtime._state.intent_lock.acquire(blocking=not no_wait):
        return False
    try:
        _retire_intent_locked(intent)
        return True
    finally:
        _runtime._state.intent_lock.release()


def _retire_intent_locked(intent: _runtime.DeleteIntent) -> None:
    try:
        if (intent.token, intent.owner) in (_fences._read_intents(intent.root, token=intent.token) or []):
            (intent.root / '.cache' / 'compiled' / 'delete-intents' / intent.token).unlink()
    finally:
        _runtime._state.intents.pop(intent.token, None)
        if intent.descriptor is not None:
            proclock.release(intent.descriptor)
            intent.descriptor = None


def _quote_table(name: str) -> str:
    if not isinstance(name, str) or len(name) > 2 * _contracts.MAX_PAYLOAD_BYTES or '\x00' in name:
        raise ValueError('invalid owned table name')
    return '"' + name.replace('"', '""') + '"'


def _purge_connection(conn: _sqlite_support._sqlite_available.Connection, token: str) -> bool:
    # compiled-maintenance-sql: quote only bounded verified owned schema names
    try:
        conn.execute('PRAGMA secure_delete=ON')
        conn.execute('BEGIN IMMEDIATE')
        complete = (_purge_trigger_page(conn) and _purge_view_page(conn) and _purge_virtual_page(conn)
                    and _purge_table_page(conn, token))
        conn.commit()
        if not complete:
            return False
        # A failed vacuum leaves the old generation, so the next batch retries
        # physical erasure rather than treating a partial purge as complete.
        conn.execute('VACUUM')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE cache_meta SET value=? WHERE name=?', (token, 'purge_generation'))
        conn.commit()
        return True
    except _sqlite_support._CACHE_ERRORS as error:
        with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
            conn.rollback()
        # Corruption needs owned-file recovery, even with derivations disabled.
        # Returning False strands the old generation and prevents normal reads
        # from ever reaching their corruption recovery path after a delete.
        if _ownership._corruption(error):
            raise
        _runtime._warn_once('purge_busy')
        return False


def _purge_trigger_page(conn: _sqlite_support._sqlite_available.Connection) -> bool:
    # compiled-maintenance-sql: stream at most129 triggers from an already verified owned file
    # Old disposable schemas can contain DELETE triggers that repopulate an
    # earlier erased table. Remove every trigger before erasing any payload;
    # bounded pages commit under the same durable, still-pending generation.
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY rowid LIMIT 129")
    for offset, (name,) in enumerate(rows):
        if offset == _contracts.SOURCE_CHUNK:
            return False
        conn.execute(f'DROP TRIGGER {_quote_table(name)}')
    return True


def _purge_virtual_page(conn: _sqlite_support._sqlite_available.Connection) -> bool:
    # compiled-maintenance-sql: at most128 quoted virtual-table drops from a verified owned file
    # Contentless FTS cannot DELETE rows. Its disposable index and shadows
    # must be discarded before ordinary erasure. rootpage=0/NULL identifies
    # virtual tables without transferring or parsing arbitrary schema SQL:
    # https://www.sqlite.org/schematab.html
    for _ in range(_contracts.SOURCE_CHUNK):
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name)<>'cache_meta' AND (rootpage=0 OR rootpage IS NULL) ORDER BY rowid LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return True
        name = row[0]
        # A live sqlite_master cursor prevents DROP TABLE. LIMIT1 and close
        # transfer one bounded name and release it before dropping its shadows.
        conn.execute(f'DROP TABLE {_quote_table(name)}')
    return False


def _purge_view_page(conn: _sqlite_support._sqlite_available.Connection) -> bool:
    # compiled-maintenance-sql: at most128 quoted view drops from a verified owned file
    # Views can hold private literals even with no rows. Their definitions
    # must disappear before claiming physical erasure, like virtual indexes.
    for _ in range(_contracts.SOURCE_CHUNK):
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='view' ORDER BY rowid LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return True
        name = row[0]
        conn.execute(f'DROP VIEW {_quote_table(name)}')
    return False


def _purge_cursor(conn: _sqlite_support._sqlite_available.Connection, token: str) -> int | None:
    # compiled-maintenance-sql: fixed256-byte progress value in an already verified owned file
    row = conn.execute("SELECT CASE WHEN typeof(value)='text' AND length(CAST(value AS BLOB))<=256 THEN value END FROM cache_meta WHERE name=?", ('purge_progress',)).fetchone()
    if row is None or row[0] is None:
        return None
    try:
        value = json.loads(row[0])
        if (isinstance(value, dict) and set(value) == {'token', 'cursor', 'check'}
                and value['token'] == token and type(value['cursor']) is int
                and -(2**63) <= value['cursor'] < 2**63
                and value['check'] == _purge_progress_check(token, value['cursor'])):
            return value['cursor']
    except _sqlite_support._CACHE_ERRORS:
        pass
    return None


def _purge_progress_check(token: str, cursor: int | None) -> str:
    # Like artifact/source row checks, bind all progress fields: a valid-JSON
    # damaged integer must restart erasure instead of skipping owned tables.
    return hashlib.sha256(f'{token}\0{cursor}'.encode('ascii')).hexdigest()


def _purge_table_page(conn: _sqlite_support._sqlite_available.Connection, token: str) -> bool:
    # compiled-maintenance-sql: quote only streamed names from a verified owned file,128 deletions per pass
    after = _purge_cursor(conn, token)
    for offset, (identity, name) in enumerate(_ownership._owned_tables(conn, after)):
        if offset == _contracts.SOURCE_CHUNK:
            # Same transaction as the deletes: crashes never advance this
            # bounded integer cursor past work that did not commit. Rowid
            # includes legal empty names and avoids retaining long text cursors.
            conn.execute("INSERT INTO cache_meta(name,value) VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                         ('purge_progress', json.dumps({'token': token, 'cursor': after,
                                                       'check': _purge_progress_check(token, after)})))
            return False
        if name.lower() != 'cache_meta':
            if name.lower().startswith('sqlite_'):
                # SQLite owns the DDL of planner/sequence tables, which cannot
                # be dropped. Their orphaned statistics can still hold private
                # values, so erase their rows before the physical vacuum.
                conn.execute(f'DELETE FROM {_quote_table(name)}')
            else:
                conn.execute(f'DROP TABLE {_quote_table(name)}')
        after = identity
    # DEFAULT/CHECK expressions, column names and index definitions can also
    # hold private text. Keep ownership valid at every committed boundary,
    # then rebuild canonical public DDL in this final transaction, including
    # cache_meta itself. Unknown old layouts are disposable after a purge.
    schema = conn.execute('SELECT value FROM cache_meta WHERE name=?', ('schema',)).fetchone()
    generation = _fences._read_generation(conn)
    if schema is None or not isinstance(schema[0], str) or generation is None:
        raise ValueError('invalid owned metadata')
    conn.execute('DROP TABLE cache_meta')
    _ownership._create_schema(conn, schema[0], generation)
    return True


def _purge_if_needed(conn: _sqlite_support._sqlite_available.Connection, token: str) -> None:
    if _fences._read_generation(conn) != token:
        _purge_connection(conn, token)


def _purge_file(root: Path, path: Path, token: str, current: _runtime._Session | None) -> bool:
    """Erase one owned file up to `token`. True only when this pass leaves
    nothing for a later one: the file is at `token`, or is not ours to touch."""
    if _sqlite_support._sqlite is None:
        return False
    fd, conn = None, None
    recognized = False
    try:
        if not _path_checks._cache_file_safe(path):
            return False
        if not _ownership._probe_recognized(path):
            return True  # foreign or unreadable: no pass ever erases it
        recognized = True
        if current is not None and current.path == path:
            _purge_if_needed(current.conn, token)
            return True
        fd = _ownership._recovery_lock(root, path)
        if fd is None or not _path_checks._cache_file_safe(path):
            return False
        conn = _sqlite_support._sqlite.connect(path, timeout=0.005, isolation_level=None)
        conn.setlimit(_sqlite_support._sqlite.SQLITE_LIMIT_LENGTH, 2 * _contracts.MAX_PAYLOAD_BYTES)
        conn.execute('PRAGMA busy_timeout=5')
        _purge_if_needed(conn, token)
        return True
    except _sqlite_support._CACHE_ERRORS as error:
        if recognized and _ownership._corruption(error) and (fd is not None or (current is not None and current.path == path)):
            _erase_owned_corrupt(path, conn)
            conn = None
        else:
            _runtime._warn_once('purge')
        return False
    finally:
        if conn is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                conn.close()
        if fd is not None:
            with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                proclock.release(fd)


def _erase_owned_corrupt(path: Path, conn: _sqlite_support._sqlite_available.Connection | None) -> None:
    if conn is not None:
        with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
            conn.close()
    if _runtime._state.session is not None and _runtime._state.session.path == path:
        _runtime._close_locked(invalidate=False)
    # The caller holds the same NO_WAIT exclusion as ordinary recovery.
    # Erase a still-owned, still-corrupt disposable file rather than
    # trying SQL on its damaged pages. Do not create a replacement in
    # cache0 mode; the next enabled batch can initialize an empty one.
    # A hot journal has unproved before-images and remains untouched.
    if (_path_checks._cache_file_safe(path) and _ownership._probe_recognized(path)
            and not Path(str(path) + '-journal').exists() and _ownership._still_corrupt(path)):
        path.unlink()


def _purge_corrupt_aside(root: Path, path: Path) -> bool:
    # Failed ordinary recovery can leave the only payload copy in an aside.
    # Durable delete maintenance must erase it even with BUILD/cache0 off.
    # Re-prove ownership under the original file's operation exclusion;
    # foreign/unreadable/linked asides and unproved journals remain untouched.
    original = path.with_name(_path_checks._cache_filename(path).removesuffix('.corrupt'))
    fd = _ownership._recovery_lock(root, original)
    if fd is None:
        return False
    try:
        # An aside that survives this pass is retried by the next one.
        return _path_checks._cache_file_safe(original) and _retention._erase_recognized_aside(path)
    except _sqlite_support._CACHE_ERRORS:
        # One unproved neighbour must not strand owned copies after every delete.
        _runtime._warn_once('purge_aside')
        return False
    finally:
        with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
            proclock.release(fd)


def _purge_local_files(root: Path, token: str, current: _runtime._Session | None = None) -> bool:
    """Erase this device's files up to `token`; True when none is left behind."""
    device = _runtime._device(root)
    parent = _path_checks._cache_parent(root)
    if _sqlite_support._sqlite is None:
        _runtime._warn_once('purge_sqlite_unavailable')
        return False
    if device is None or parent is None:
        return False
    done = True
    for path in parent.iterdir():
        match = _contracts._CACHE_FILE.fullmatch(_path_checks._cache_filename(path).removesuffix('.corrupt'))
        if match is not None and match[1] == device:
            if _path_checks._cache_filename(path).endswith('.corrupt'):
                done = _purge_corrupt_aside(root, path) and done
            else:
                done = _purge_file(root, path, token, current) and done
    return done


def purge_for_delete(root: Path, *, intent: _runtime.DeleteIntent | None = None) -> bool:
    """Publish durable erasure work, then attempt owned in-place purges.
    True once the marker is published -- every device purges at its next open
    from then on -- and False when it could not be, so a caller that reports
    the purge (spec 05's sync) never claims one that was not requested."""
    _runtime._process_state()
    published_marker = False
    try:
        root = Path(root)
        token = _fences._publish_marker(root)
        if token is None:
            return False
        published_marker = True
        if _runtime._state.lock.acquire(blocking=False):
            try:
                _purge_local_files(root, token)
            finally:
                _runtime._state.lock.release()
        if intent is not None and intent.root == root:
            _retire_intent(intent, no_wait=True)
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('purge')
    finally:
        # Failed publication keeps the durable request recoverable. Release
        # only this call's process resource; never another delete's fence.
        if intent is not None:
            intent.finished = True
            if intent.descriptor is not None:
                with contextlib.suppress(*_sqlite_support._CACHE_ERRORS):
                    _runtime._state.finished_intents.append(intent)
            _release_finished_intents()
    return published_marker


def _recover_pending(root: Path, current: _runtime._Session | None = None) -> bool:
    _release_finished_intents()
    intents = _fences._read_intents(root)
    if intents is None:
        return False
    _runtime._device(root)
    owner = _runtime._state.owners.get(root)
    # A bounded page is work, not a malformed backlog. Erase owned payloads
    # even if active/foreign entries fill the page; those fences stay durable.
    foreign = [(token, device) for token, device in intents if device != owner]
    active = bool(foreign)
    generation = None
    for token, device in intents:
        if device != owner:
            continue
        fd = _intent_lock(root, token)
        if fd is None:
            active = True
            continue
        recovered = _runtime.DeleteIntent(token, device, fd, root)
        try:
            if generation is None:
                generation = _fences._publish_marker(root)
                if generation is None:
                    active = True
                    continue
                _purge_local_files(root, generation, current)
            # Preparation holds this mutex across durable I/O to close the
            # fork-registration gap. Optional readers must defer, never wait.
            if not _retire_intent(recovered, no_wait=True):
                active = True
        finally:
            if recovered.descriptor is not None:
                proclock.release(recovered.descriptor)
                recovered.descriptor = None
    if active:
        _purge_fenced_page(root, intents, current)
    return not active and _fences._read_intents(root) == []


def _purge_fenced_page(root: Path, intents: list[tuple[str, str]], current: _runtime._Session | None) -> None:
    # Reuse an unchanged page's completed erasure generation rather than
    # repeatedly vacuuming for foreign/active owners we may never retire.
    token = hashlib.sha256(json.dumps(sorted(intents), separators=(',', ':')).encode('ascii')).hexdigest()
    # Intent removal can sync before its producer's marker. Persist this
    # observed boundary locally so disappearance cannot restore an older
    # generation and let a pre-delete batch republish erased text.
    if _fences._read_marker(root) != token:
        _fences._publish_marker(root, token)
    _purge_local_files(root, token, current)


def _pending_maintenance(root: Path) -> None:
    # Disabled derivations still finish already durable erasure work. This
    # never creates a database, requires no BUILD and never waits for SQLite
    # or a process lock. Async/campaign-held callers defer to the next batch.
    if _runtime._on_loop() or locks.campaign_locks_held():
        return
    marker, intents = _fences._read_marker(root), _fences._read_intents(root)
    if marker is None or (marker == '' and intents == []):
        return
    if not _runtime._state.lock.acquire(blocking=False):
        return
    try:
        if not _recover_pending(root):
            return
        marker = _fences._read_marker(root)
        if marker:
            _purge_local_files(root, marker)
    except _sqlite_support._CACHE_ERRORS:
        _runtime._warn_once('pending_purge')
    finally:
        _runtime._state.lock.release()


def _synchronize(session: _runtime._Session) -> bool:
    # A pass that finished every file, over control paths that have not moved
    # since and had settled before it began, has nothing new to find.
    snapshot = _fences._control_snapshot(session.root)
    if snapshot is not None and snapshot == session.synced:
        try:
            if _fences._read_generation(session.conn) == session.synced_marker:
                return True
        except _sqlite_support._CACHE_ERRORS:
            pass  # a damaged handle is the full pass's to close and recover
    session.synced = session.synced_marker = None
    taken = _fences._wall_ns()
    snapshot = _fences._control_snapshot(session.root)
    if not _recover_pending(session.root, session):
        # A foreign fence records its own completed erasure generation.
        # Normalizing it to the local marker would force another VACUUM on
        # every unchanged batch. Incomplete local versions are still retried
        # by _recover_pending before it reports that the fence remains live.
        return False
    marker = _fences._read_marker(session.root)
    if marker is None:
        return False
    # Retry obsolete versions too: a busy old file remains durable work even
    # when the current schema has already recorded this marker.
    done = _purge_local_files(session.root, marker, session)
    current = _fences._read_generation(session.conn) == marker
    if current and done and snapshot is not None and _fences._settled(snapshot, taken):
        session.synced, session.synced_marker = snapshot, marker
    return current
