"""Store-backed review and selective acceptance of world authoring proposals (087); an adapter over
``grimoire.store`` that writes through World Change Sets (043). See docs/AUTHORING.md."""

from .library import MAX_CONTEXT, AcceptanceDraft, AcceptedRecords, AuthoringLibrary, ProposalReview

__all__ = ["MAX_CONTEXT", "AcceptanceDraft", "AcceptedRecords", "AuthoringLibrary", "ProposalReview"]
