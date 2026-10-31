"""A neutral, facet-agnostic record envelope standing in for 010's (test-only).

It knows an address, an owner, a schema id, a content revision and an opaque
JSON payload, and nothing about entities: it deliberately imports no grimoire
module, so a storage layer cannot come to depend on facet classes.
"""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class NeutralEnvelope:
    address: object  # a SourceRef or InstanceRef; stored as its text by a real store
    owner: str
    schema: str
    content_revision: str
    payload_text: str

    @classmethod
    def wrap(cls, address, owner, schema, content_revision, payload):
        return cls(address, owner, schema, content_revision,
                   json.dumps(payload, sort_keys=True, allow_nan=False))

    @property
    def payload(self):
        return json.loads(self.payload_text)
