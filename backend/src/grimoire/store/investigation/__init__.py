"""Bounded agentic investigation's store half (roadmap 12).

Theory: an investigation is a tool-calling model reading a campaign through a
fixed, read-only toolset, until it names a result that is checked here, never
taken on its word. The package holds everything that is not a model call --
the tools and their envelope (`tools`), the readers behind them (`readers`),
the run-local state they share (`state`), the budgets (`budget`), the trace
(`trace`) and the validation of a final call (`final`). The loop that calls a
model lives in `routes/investigation.py`, the house split.

Nothing here writes. The one write an investigation may lead to -- one
proposal on one continuity candidate -- is `continuity.reconcile`'s
`persist_investigation`, called by the route. `test_investigation_writer_guard`
holds that, and the package sits in no `store/locks/_domain.py` list because it holds
no mutator to classify.
"""

from . import budget, final, readers, state, tools, trace

__all__ = ["budget", "final", "readers", "state", "tools", "trace"]
