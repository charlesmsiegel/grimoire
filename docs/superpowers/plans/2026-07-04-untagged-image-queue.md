# Untagged-Image Tagging Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A rail-launched stepper on the Greetings tab that walks every greeting image lacking a subjects entry, with "No subjects" persisting an explicit empty marker.

**Architecture:** Store semantics change (empty lists persist in `subjects.json`; key absent = unreviewed) + an `untagged(root)` scan; one enriched route; a `TaggingQueue` component hosted by GreetingEditor with a `▶ Tag images (N)` rail button.

**Tech Stack:** FastAPI + pytest; React + vitest.

**Spec:** `docs/superpowers/specs/2026-07-04-untagged-image-queue-design.md`

## Global Constraints

- "Reviewed, no subjects" = **explicit `[]` persisted** in the sidecar; key absent = unreviewed. The old drop-empty test flips to assert persistence.
- Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`. Frontend from `frontend/`: `npx vitest run`, `npx tsc -b`.
- Route `/worlds/{wid}/subjects/untagged` must register **before** the generic `/worlds/{wid}/{kind}/{eid}` entity routes (it does if placed in the greeting-subjects block).
- Work on a branch in a worktree under `.worktrees/`.

---

### Task 1: store — persist empty lists + `untagged(root)`

**Files:**
- Modify: `backend/src/grimoire/store/image_subjects.py`
- Test: `backend/tests/test_image_subjects_store.py`

**Interfaces:**
- Produces: `untagged(root) -> list[dict]` — `[{"gid": str, "name": str}]` sorted by (gid, name); `write_subjects`/`set_image_subjects` now persist `[]`; `read_subjects` returns `[]` entries.

- [ ] **Step 1: Flip the drop-empty expectation and add new failing tests**

In `test_write_rejects_unknown_image_and_drops_empty`, rename to `test_write_rejects_unknown_image_and_persists_empty` and change the final assertion:

```python
def test_write_rejects_unknown_image_and_persists_empty(tmp_path):
    cid, gid = _world(tmp_path)
    with pytest.raises(ValueError):
        image_subjects.write_subjects(tmp_path, gid, {"nope": [cid]})
    image_subjects.write_subjects(tmp_path, gid, {"art_1": [cid], "art_2": []})
    # explicit [] persists: "reviewed, nobody in it"
    assert image_subjects.read_subjects(tmp_path, gid) == {"art_1": [cid], "art_2": []}
```

Also update `test_set_image_subjects_updates_one_entry`'s final assertion to
`{"art_1": [], "art_2": [cid]}`. Append:

```python
def test_untagged_lists_only_unreviewed_images(tmp_path):
    cid, _vid = characters.create_character(tmp_path, "Mira", "main")
    g1 = greetings.create_greeting(tmp_path, "One", cid, "main", "x")
    g2 = greetings.create_greeting(tmp_path, "Two", cid, "main", "x")
    for gid, names in ((g1, ("a_tagged", "b_reviewed", "c_new")), (g2, ("d_new",))):
        for n in names:
            assets.put_image(tmp_path, gid, "default", n, b"p", "png", base="greetings")
    image_subjects.set_image_subjects(tmp_path, g1, "a_tagged", [cid])
    image_subjects.set_image_subjects(tmp_path, g1, "b_reviewed", [])  # reviewed, none
    got = image_subjects.untagged(tmp_path)
    assert got == sorted(got, key=lambda a: (a["gid"], a["name"]))
    assert {(a["gid"], a["name"]) for a in got} == {(g1, "c_new"), (g2, "d_new")}
```

- [ ] **Step 2: Run to verify failures**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_image_subjects_store.py -q`
Expected: persists-empty + set_image_subjects + untagged tests FAIL

- [ ] **Step 3: Implement**

In `write_subjects`, replace the trimming line and docstring:

```python
    """Strict: every key must be a stored image of this greeting. An explicit
    empty list persists — it means 'reviewed, no subjects' and keeps the image
    out of the untagged queue (key absent = unreviewed)."""
    trimmed = {n: list(subs) for n, subs in subjects.items()}
```

In `read_subjects`, keep entries whose kept list is empty (replace the tail of the loop):

```python
    for name, subs in raw.items():
        if name not in names or not isinstance(subs, list):
            continue
        out[name] = [c for c in subs if c in cids]
    return out
```

Append:

```python
def untagged(root: Path) -> list[dict]:
    """Every stored greeting image with NO sidecar entry — the tagging queue.
    Key absent = unreviewed; an explicit [] counts as reviewed."""
    out: list[dict] = []
    gdir = root / _BASE
    if not gdir.exists():
        return out
    for d in sorted(p for p in gdir.iterdir() if p.is_dir()):
        gid = d.name
        reviewed = set(read_subjects(root, gid))
        for name in sorted(_image_names(root, gid)):
            if name not in reviewed:
                out.append({"gid": gid, "name": name})
    return out
```

- [ ] **Step 4: Run tests, full suite** — both green.
- [ ] **Step 5: Commit** — `feat(store): persist reviewed-empty subjects + untagged scan`

---

### Task 2: route — `GET /worlds/{wid}/subjects/untagged`

**Files:**
- Modify: `backend/src/grimoire/routes.py` (in the greeting-subjects block, before the generic entity routes)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Produces: `GET /api/worlds/{wid}/subjects/untagged` → `[{"gid","greeting_name","name","url"}]`.

- [ ] **Step 1: Failing test** (append to `test_routes.py`):

```python
def test_untagged_images_route_and_empty_marker(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": cid, "version": "default"}).json()["id"]
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")

    r = client.get(f"/api/worlds/{wid}/subjects/untagged")
    assert r.json() == [{"gid": gid, "greeting_name": "Opener", "name": "embed-abc123def456",
                         "url": f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456"}]
    # an explicit [] PUT marks it reviewed and removes it from the queue
    client.put(f"/api/worlds/{wid}/greetings/{gid}/images/embed-abc123def456/subjects",
               json={"subjects": []})
    assert client.get(f"/api/worlds/{wid}/subjects/untagged").json() == []
```

- [ ] **Step 2: Run to verify failure** — the untagged GET is captured by the generic entity route → 404 → `.json()` mismatch.
- [ ] **Step 3: Implement** (after `put_world_greeting_image_subjects`):

```python
@router.get("/worlds/{wid}/subjects/untagged")
def get_world_untagged_images(wid: str):
    root = _world_root_or_404(wid)
    names = {g["id"]: g["name"] for g in store.greetings.list_greetings(root)}
    return [{**a, "greeting_name": names.get(a["gid"], a["gid"]),
             "url": f"/api/worlds/{wid}/greetings/{a['gid']}/images/{a['name']}"}
            for a in store.image_subjects.untagged(root)]
```

- [ ] **Step 4: Route tests + full backend suite** — green.
- [ ] **Step 5: Commit** — `feat(routes): list untagged greeting images`

---

### Task 3: frontend — TaggingQueue + rail button

**Files:**
- Create: `frontend/src/components/TaggingQueue.tsx`
- Modify: `frontend/src/api/client.ts`, `frontend/src/components/GreetingEditor.tsx`, `frontend/src/index.css`
- Test: `frontend/src/components/GreetingEditor.test.tsx` (append; extend mock with `listUntaggedImages`)

**Interfaces:**
- Consumes: `api.setImageSubjects`, `Appearance` type, `Greeting.present`.
- Produces: `api.listUntaggedImages(wid) -> Promise<Appearance[]>`; `<TaggingQueue wid chars greetings queue onClose onSaved />`.

- [ ] **Step 1: Failing tests** — extend the `vi.mock` api with `listUntaggedImages: vi.fn(),`, default `(api.listUntaggedImages as any).mockResolvedValue([]);` in `beforeEach`, and append:

```tsx
const UNTAGGED = [
  { gid: "open", greeting_name: "Open", name: "embed-one", url: "/api/worlds/w/greetings/open/images/embed-one" },
  { gid: "open", greeting_name: "Open", name: "embed-two", url: "/api/worlds/w/greetings/open/images/embed-two" },
];

test("rail button opens the tagging queue; save/no-subjects/skip advance it", async () => {
  (api.listUntaggedImages as any).mockResolvedValue(UNTAGGED);
  (api.listGreetings as any).mockResolvedValue([
    { id: "open", name: "Open", character: "seraphine", version: "default", present: ["seraphine"], requires_tags: [], predecessor_join: "all" },
  ]);
  render(<GreetingEditor wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /tag images \(2\)/i }));
  // stepper on image 1: tag Seraphine and save
  await screen.findByText(/tagging 1 \/ 2/i);
  fireEvent.click(screen.getByRole("button", { name: "Seraphine" }));
  fireEvent.click(screen.getByRole("button", { name: /save & next/i }));
  await waitFor(() => expect(api.setImageSubjects).toHaveBeenCalledWith("w", "open", "embed-one", ["seraphine"]));
  // image 2: mark as no-subjects
  await screen.findByText(/tagging 2 \/ 2/i);
  fireEvent.click(screen.getByRole("button", { name: /no subjects/i }));
  await waitFor(() => expect(api.setImageSubjects).toHaveBeenCalledWith("w", "open", "embed-two", []));
  await screen.findByText(/all images tagged/i);
});

test("skip advances without a PUT and close leaves the queue", async () => {
  (api.listUntaggedImages as any).mockResolvedValue(UNTAGGED);
  render(<GreetingEditor wid="w" />);
  fireEvent.click(await screen.findByRole("button", { name: /tag images \(2\)/i }));
  await screen.findByText(/tagging 1 \/ 2/i);
  fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
  await screen.findByText(/tagging 2 \/ 2/i);
  expect(api.setImageSubjects).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^close$/i }));
  expect(await screen.findByRole("button", { name: /new greeting/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure** — no "Tag images" button.
- [ ] **Step 3: Implement**

`client.ts` (greetings section):

```ts
  listUntaggedImages: (wid: string) =>
    request<Appearance[]>("GET", `/api/worlds/${wid}/subjects/untagged`),
```

`TaggingQueue.tsx`:

```tsx
import { useState } from "react";
import { api, type Appearance, type CharacterSummary, type Greeting } from "../api/client";

// Stepper over greeting images with no subjects entry. Save/No-subjects PUT
// then advance; Skip advances only (the image stays unreviewed).
export function TaggingQueue({ wid, chars, greetings, queue, onClose, onSaved }: {
  wid: string; chars: CharacterSummary[]; greetings: Greeting[];
  queue: Appearance[]; onClose: () => void; onSaved: (gid: string) => void;
}) {
  const [items, setItems] = useState<Appearance[]>(queue);
  const [sel, setSel] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const total = queue.length;
  const cur = items[0];

  if (!cur) {
    return (
      <div className="tagging-queue">
        <p>All images tagged 🎉</p>
        <div className="form-actions"><button className="primary" onClick={onClose}>Close</button></div>
      </div>
    );
  }

  const pos = total - items.length + 1;
  const present = greetings.find((g) => g.id === cur.gid)?.present ?? [];
  const presentChars = chars.filter((c) => present.includes(c.id));
  const others = chars.filter(
    (c) => !present.includes(c.id) && c.name.toLowerCase().includes(q.toLowerCase()));
  const toggle = (cid: string) =>
    setSel((s) => (s.includes(cid) ? s.filter((x) => x !== cid) : [...s, cid]));
  const chip = (c: CharacterSummary) => (
    <button key={c.id} className={"chip" + (sel.includes(c.id) ? " on" : "")}
            onClick={() => toggle(c.id)}>{c.name}</button>
  );

  function advance() { setItems((it) => it.slice(1)); setSel([]); setQ(""); setError(null); }

  async function save(subjects: string[]) {
    try {
      await api.setImageSubjects(wid, cur.gid, cur.name, subjects);
      onSaved(cur.gid);
      advance();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  return (
    <div className="tagging-queue">
      {error && <div className="banner">{error}</div>}
      <div className="field-hint">Tagging {pos} / {total} — {cur.greeting_name}</div>
      <img className="queue-image" alt={`${cur.greeting_name} art`} src={cur.url} />
      {presentChars.length > 0 && (
        <>
          <div className="field-hint">Present in this greeting</div>
          <div className="chips">{presentChars.map(chip)}</div>
        </>
      )}
      <input type="text" placeholder="Search all characters…" value={q}
             aria-label="Search characters" onChange={(e) => setQ(e.target.value)} />
      {others.length > 0 && <div className="chips">{others.map(chip)}</div>}
      <div className="form-actions">
        <button className="subtle" onClick={onClose}>Close</button>
        <button className="subtle" onClick={advance}>Skip</button>
        <button className="subtle" onClick={() => save([])}>No subjects</button>
        <button className="primary" onClick={() => save(sel)} disabled={sel.length === 0}>Save & next</button>
      </div>
    </div>
  );
}
```

`GreetingEditor.tsx`: import TaggingQueue; state `const [untagged, setUntagged] = useState<Appearance[]>([]);` `const [queueOpen, setQueueOpen] = useState(false);` (import `type Appearance`). In the initial-load effect add `api.listUntaggedImages(wid).then(setUntagged).catch(() => setUntagged([]));`. Add:

```tsx
  function closeQueue() {
    setQueueOpen(false);
    api.listUntaggedImages(wid).then(setUntagged).catch(() => setUntagged([]));
    reload();
  }

  function queueSaved(savedGid: string) {
    if (savedGid === gid) api.getGreetingSubjects(wid, savedGid).then(setSubjects).catch(() => {});
  }
```

Rail, under `+ New greeting`:

```tsx
        {untagged.length > 0 && (
          <button className="subtle new" onClick={() => setQueueOpen(true)}>
            ▶ Tag images ({untagged.length})
          </button>
        )}
```

Body: wrap the existing view/form ternary so `queueOpen` wins:

```tsx
        {queueOpen ? (
          <TaggingQueue wid={wid} chars={chars} greetings={greetings} queue={untagged}
                        onClose={closeQueue} onSaved={queueSaved} />
        ) : mode === "view" && gid ? ( ... existing ... )}
```

`index.css`:

```css
.tagging-queue { display: grid; gap: 10px; max-width: 640px; }
.tagging-queue .queue-image { max-width: 100%; border-radius: 8px; }
```

- [ ] **Step 4: Run GreetingEditor tests + `npx tsc -b`** — green; existing tests untouched.
- [ ] **Step 5: Full suites (frontend + backend)** — green.
- [ ] **Step 6: Commit** — `feat(frontend): untagged-image tagging queue on the greetings tab`
