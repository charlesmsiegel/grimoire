// Device-local saved selections and bounded asynchronous workspace navigation.
export const SAVED_KEY = 'grimoire:workspace:filters:v1';
function validSaved(value) {
  return value && typeof value.name === 'string' && value.name.length <= 100 && value.name.trim()
    && value.selection && !Array.isArray(value.selection) && typeof value.selection === 'object'
    && Object.entries(value.selection).every(([key, item]) =>
      ['q', 'group', 'sort', 'filter', 'source', 'match', 'campaign', 'world'].includes(key)
      && (item === null || (typeof item === 'string' && item.length <= 20000)));
}
export function savedFilters(storage) {
  try {
    const value = JSON.parse(storage.getItem(SAVED_KEY) || '[]');
    if (!Array.isArray(value) || value.length > 20 || value.some(x => !validSaved(x))) throw Error('Malformed saved filters');
    return { values: value, error: null };
  } catch (error) { return { values: [], error: 'Saved filters unavailable: ' + error.message }; }
}
export function writeFilters(storage, values) {
  try {
    if (values.length > 20) throw Error('At most 20 saved filters');
    if (!values.every(validSaved)) throw Error('Malformed saved filter');
    storage.setItem(SAVED_KEY, JSON.stringify(values));
    return null;
  } catch (error) { return 'Filters were not saved: ' + error.message; }
}
export function makePager(api) {
  let generation = 0;
  return async (args) => {
    const mine = ++generation;
    try {
      const result = await api('chats.workspace', { ...args, limit: 50, match: args.match || 'exact' });
      return mine === generation ? { result } : null;
    } catch (error) { return mine === generation ? { error } : null; }
  };
}
