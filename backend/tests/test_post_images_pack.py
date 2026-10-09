"""The packer and the inspector with image references in the history (#377).

Images are the first thing given up under budget pressure, so a route that
receives the text lowering gets exactly the packing today's code produces."""

from copy import deepcopy

from grimoire import content_parts as cp
from grimoire.store.context import assemble, pack
from tests import wire_kit

COUNT = len  # one token per character keeps the arithmetic legible


def _t(text):
    return {"type": "text", "text": text}


def _hist():
    return [
        {"role": "user", "content": [_t("old map"), cp.ref("/u/a", "map", False)]},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": [cp.ref("/u/b", "hall", True), _t("now")]},
    ]


SECTIONS = [{"id": "s", "label": "S", "text": "x" * 10, "tier": pack.BACKGROUND}]


def test_message_cost_charges_each_image():
    content = [_t("abcd"), cp.ref("/u", "a", False)]
    assert pack.message_cost(content, COUNT) == 4 + pack.IMAGE_TOKENS + pack.MESSAGE_OVERHEAD
    assert pack.message_cost("abcd", COUNT) == 4 + pack.MESSAGE_OVERHEAD


def test_unbounded_leaves_the_history_alone():
    hist = _hist()
    out = pack.pack(SECTIONS, hist, budget=0, count=COUNT)
    assert out["history"] == hist and out["images_dropped_tokens"] == 0


def _text_cost(hist):
    return sum(pack.message_cost(cp.text_of(m["content"]), COUNT)
               for m in cp.as_text(hist))


def test_images_go_first_oldest_first_and_collapse():
    hist = _hist()
    # Room for everything but one image.
    budget = 10 + _text_cost(hist) + pack.MESSAGE_OVERHEAD * 0 + pack.IMAGE_TOKENS
    out = pack.pack(SECTIONS, hist, budget=budget, count=COUNT)
    assert not any(s["dropped"] for s in out["sections"])
    assert out["history_trimmed"] == 0
    assert out["history"][0]["content"] == "old map"          # collapsed to today's string
    assert cp.image_refs(out["history"][2]["content"])         # the newer one survived
    assert out["images_dropped_tokens"] == pack.IMAGE_TOKENS


def test_with_every_image_gone_the_result_is_the_text_pack():
    hist = _hist()
    budget = 10 + _text_cost(hist) - 3   # forces trimming even after images go
    with_refs = pack.pack(SECTIONS, hist, budget=budget, count=COUNT)
    text_only = pack.pack(SECTIONS, cp.as_text(hist), budget=budget, count=COUNT)
    assert with_refs["sections"] == text_only["sections"]
    assert cp.as_text(with_refs["history"]) == text_only["history"]
    assert with_refs["history_trimmed"] == text_only["history_trimmed"]
    assert with_refs["history_trimmed_tokens"] == text_only["history_trimmed_tokens"]
    assert not cp.has_refs(with_refs["history"])
    assert all(isinstance(m["content"], str) for m in with_refs["history"])


def test_a_carrier_emptied_of_images_is_removed():
    hist = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "look"},
            {"role": "user", "content": [cp.ref("/u/b", "hall", True)], cp.CARRIER: True}]
    out = pack.pack(SECTIONS, hist, budget=10 + 100, count=COUNT)
    assert len(out["history"]) == 2
    assert out["history_trimmed"] == 0
    assert out["images_dropped_tokens"] == pack.IMAGE_TOKENS + pack.MESSAGE_OVERHEAD


def test_packing_never_edits_its_input():
    """Review Focus 5: `_prepare` packs one history per guidance profile."""
    hist = _hist()
    before = deepcopy(hist)
    first = pack.pack(SECTIONS, hist, budget=50, count=COUNT)
    assert hist == before
    assert pack.pack(SECTIONS, hist, budget=50, count=COUNT) == first


def _packed(hist, sections=SECTIONS):
    return {**pack.pack(sections, hist, budget=0, count=COUNT), "budget": 0}


def test_breakdown_reports_an_images_row_inside_the_history_total():
    hist = [{"role": "user", "content": [_t("see map"), cp.ref("/u/a", "map", False)]},
            {"role": "assistant", "content": "ok"}]
    detail = assemble._breakdown({"post_history": ""}, _packed(hist), count=COUNT)
    rows = {r["id"]: r for r in detail["sections"]}
    assert rows["history_images"]["tokens"] == pack.IMAGE_TOKENS
    assert rows["history_images"]["label"] == "Images (1)"
    assert rows["history_images"]["tier"] == pack.HISTORY
    assert "map" in rows["history_images"]["text"] and "/u/a" in rows["history_images"]["text"]
    assert rows["history"]["text"] == "see map\n\nok"
    assert rows["history"]["tokens"] == (7 + 5) + (2 + 5)
    system = COUNT(assemble._compose_system([s["text"] for s in SECTIONS]))
    assert detail["total_tokens"] == system + (7 + 5 + pack.IMAGE_TOKENS) + (2 + 5)


def test_breakdown_skips_a_carrier_in_the_history_text():
    hist = [{"role": "user", "content": "go"}, {"role": "assistant", "content": "look"},
            {"role": "user", "content": [cp.ref("/u/b", "hall", True)], cp.CARRIER: True}]
    detail = assemble._breakdown({"post_history": ""}, _packed(hist), count=COUNT)
    rows = {r["id"]: r for r in detail["sections"]}
    assert rows["history"]["text"] == "go\n\nlook"
    assert rows["history_images"]["label"] == "Images (1)"


def test_breakdown_without_images_has_no_images_row():
    hist = [{"role": "user", "content": "go"}]
    detail = assemble._breakdown({"post_history": ""}, _packed(hist), count=COUNT)
    assert "history_images" not in {r["id"] for r in detail["sections"]}


def test_breakdown_counts_images_given_up_as_dropped():
    hist = _hist()
    budget = 10 + _text_cost(hist) + pack.IMAGE_TOKENS
    p = {**pack.pack(SECTIONS, hist, budget=budget, count=COUNT), "budget": budget}
    detail = assemble._breakdown({"post_history": ""}, p, count=COUNT)
    assert detail["dropped_tokens"] == pack.IMAGE_TOKENS
    assert {r["id"]: r for r in detail["sections"]}["history"]["trimmed"] == 0


def test_a_steered_capture_counts_text_and_images(monkeypatch):
    """`character_turns._capture` builds rows itself for a variant with no
    breakdown (a steered reroll); a list content must not reach the tokenizer
    or the record as a list."""
    from grimoire import store
    from grimoire.routes import character_turns

    recorded = {}
    monkeypatch.setattr(store.prompt_log, "capturing", lambda: True)
    monkeypatch.setattr(character_turns, "_record_prompt",
                        lambda cid, sid, task, breakdown, **k: recorded.update(breakdown))
    monkeypatch.setattr(store.context, "budget_tokens", lambda: 0)
    msgs = [{"role": "user", "content": [_t("see map"), cp.ref("/u/a", "map", False)]}]
    character_turns._capture("c", "s", "chat", msgs, wire_kit.target(kind="openrouter", model="m"))
    row, = recorded["sections"]
    assert row["text"] == "see map"
    assert row["tokens"] == store.tokens.count_tokens("see map") + pack.IMAGE_TOKENS


class _CountingList(list):
    iterations = 0

    def __iter__(self):
        type(self).iterations += 1
        return super().__iter__()


def test_dropping_images_costs_nothing_when_there_are_none():
    """Every budgeted prompt passes through this step, images or not."""
    hist = [{"role": "user", "content": "x" * 10} for _ in range(3000)]
    costs = _CountingList(pack.message_cost(m["content"], COUNT) for m in hist)
    _CountingList.iterations = 0
    assert pack._drop_images(hist, costs, 0, COUNT) == 0
    assert _CountingList.iterations <= 1
