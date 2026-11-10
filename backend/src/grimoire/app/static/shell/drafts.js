// Unsent drafts (108): whatever a person has typed and not sent survives navigating away, a reload and a
// failed request, until it is sent or cleared. Kept per browser tab (sessionStorage), never in the library.
// `storage` is injectable so the store runs under `node --test`.

const PREFIX = "grimoire-draft:";

export class Drafts {
  constructor(storage) {
    this.storage = storage || null;
    this.memory = new Map();  // when the tab's storage is unavailable or full
  }

  // What this page typed last wins (memory), then what an earlier load of the tab kept (storage).
  get(key) {
    const name = PREFIX + key;
    if (this.memory.has(name)) return this.memory.get(name);
    try {
      const value = this.storage ? this.storage.getItem(name) : null;
      if (value !== null && value !== undefined) return value;
    } catch (error) { /* nothing kept */ }
    return "";
  }

  set(key, value) {
    const name = PREFIX + key;
    if (!value) { this.clear(key); return; }
    this.memory.set(name, value);
    try { if (this.storage) this.storage.setItem(name, value); }
    catch (error) {
      // Storage is full: an older copy left there would come back after a reload in place of this one.
      try { this.storage.removeItem(name); } catch (ignored) { /* nothing more to do */ }
    }
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
