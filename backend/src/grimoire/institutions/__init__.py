"""Headless NPC institutional choices; no knowledge delivery or action executor."""
from .model import CollectiveDecision, DecisionRequest, Institution, InstitutionError, InstitutionState, MemberPreference
from .policy import decide

__all__ = ['CollectiveDecision', 'DecisionRequest', 'Institution', 'InstitutionError',
           'InstitutionState', 'MemberPreference', 'decide']
