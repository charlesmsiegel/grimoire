"""Historical retrieval (09): documents rank scenes; viewed posts are evidence.

Independent admission survives selection and later reranking: a depth/budget
limit can remove a rendered item without removing its candidate. Later semantic
stages and query rounds add independent ranked lists, never a fused confidence.
Reuse: 08 owns live document keys and prompt expansion, continuity owns history
joins, 07 owns membership, and Jinja owns every rendered prompt byte. Accepted
read races can degrade one signal; retrieval neither locks nor writes campaigns.
"""

from . import merging as merge_module
from . import retrieval as retrieve_module

merge = merge_module.merge
retrieve_preview = retrieve_module.retrieve_preview
retrieve = retrieve_module.retrieve
