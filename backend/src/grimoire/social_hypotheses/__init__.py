"""060: bounded evidence-attributed theories of other minds, separate from truth."""
from .model import MAX_EVIDENCE, MAX_RECALL, MAX_RECORDS, TEXT_CHARS, Hypothesis, HypothesisError, HypothesisLedger, TacticChoice
from .policy import admit, choose_tactic, recall

__all__ = ['MAX_EVIDENCE', 'MAX_RECALL', 'MAX_RECORDS', 'TEXT_CHARS', 'Hypothesis',
           'HypothesisError', 'HypothesisLedger', 'TacticChoice', 'admit', 'choose_tactic', 'recall']
