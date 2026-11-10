// Unsent drafts (108): whatever a person has typed and not sent survives navigating away, a reload and a
// failed request, until it is sent or cleared. Kept per browser tab (sessionStorage), never in the library.
// `storage` is injectable so the store runs under `node --test`.

const PREFIX = "grimoire-draft:";

export class Drafts {
  constructor(storage) {
    this.storage = storage || null;
    this.memory = new Map();  // when the tab's storage is unavailable or full
  }

  get(key) {
    const name = PREFIX + key;
    try {
      const value = this.storage ? this.storage.getItem(name) : null;
      if (value !== null && value !== undefined) return value;
    } catch (error) { /* fall back to memory */ }
    return this.memory.has(name) ? this.memory.get(name) : "";
  }

  set(key, value) {
    const name = PREFIX + key;
    if (!value) { this.clear(key); return; }
    this.memory.set(name, value);
    try { if (this.storage) this.storage.setItem(name, value); } catch (error) { /* memory keeps it */ }
  }

  clear(key) {
    const name = PREFIX + key;
    this.memory.delete(name);
    try { if (this.storage) this.storage.removeItem(name); } catch (error) { /* nothing kept */ }
  }

  // A draft bound to one key: what a page gives a text box.
  slot(key) {
    return { key, get: () => this.get(key), set: (value) => this.set(key, value), clear: () => this.clear(key) };
  }
}
