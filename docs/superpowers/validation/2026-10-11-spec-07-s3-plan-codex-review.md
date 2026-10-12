# S3 actual plan review

Frozen checkpoint: `c8892fe9c570144ea7dbf978704dedbf47deb040`. Actual installed
Codex thread: `01a12e29-527e-7053-bdb0-0be0a6b11f90`. Structured verdict:
**approve**, no findings. Descriptor order, field union, raw comparisons,
shared campaign naming and native detachment handling match5.3/17.3.

Immutable public source/spec/plan supplied through the installed adversarial
prompt/schema because the native sandbox helper is unavailable. Static plan
review only: no tools/tests, S1 gates and S3 code gates remain separate.
Implementation must verify strict-ledger HTTP mapping: current main.py has the
global GroupIdentityConflict409 handler, even though the campaign route itself
only catches CampaignNotFound. This is an existing handler, not a new route
exception design.
