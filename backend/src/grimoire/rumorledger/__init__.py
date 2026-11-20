"""063's authored-rumor authority ledger, readable below the stores that write it.

``rumorstore`` writes the hash-chained ``rumors/<campaign>`` ledger; ``evidencestore`` reads it here, through the
store's verified ledger read, to give every actor projection the senders' dispatches (``core.Dispatch``), so a
delayed delivery never changes what its sender knows it sent.
"""
from grimoire.core.knowledge import Dispatch
from grimoire.store.errors import LedgerInvalid

from .records import is_rumor_output, output_id
from .replay import replay

__all__ = ["campaign_dispatches", "is_rumor_output", "ledger_name", "output_id", "replay"]


def ledger_name(campaign):
    return f'rumors/{campaign}'


def campaign_dispatches(store, campaign):
    """Every completed send of the campaign's verified rumor ledger, at its enqueue Moment. ``LedgerBroken`` or
    ``LedgerInvalid`` for a ledger that cannot be trusted: the caller must then show no sends at all."""
    rows, _ = replay(campaign, store.read_ledger(ledger_name(campaign)).entries)
    found = []
    for rid, row in rows.items():
        if not row['enqueued']:
            continue
        r = row['rumor']
        try:
            found.append(Dispatch(campaign, output_id(campaign, rid, 'report'), r.sender, r.recipient,
                                  output_id(campaign, rid, 'proposition'), r.channel, row['attempts']['enqueue'][-1]))
        except ValueError:
            raise LedgerInvalid('invalid_rumor_ledger') from None
    return tuple(found)
