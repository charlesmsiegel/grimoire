"""Persisted logs retain their logical module label."""

import logging

from grimoire.store import logs, overlay


def test_original_log_label_is_persisted_and_filterable(monkeypatch, tmp_path):
    monkeypatch.setenv('GRIMOIRE_HOME', str(tmp_path))
    logs.install()
    monkeypatch.setattr(overlay.log, 'level', logging.WARNING)
    overlay.log.warning('Package modularization probe')
    rows = logs.read(module='store.overlay', contains='Package modularization probe')['rows']
    assert [(row['module'], row['message']) for row in rows] == [
        ('store.overlay', 'Package modularization probe')]
