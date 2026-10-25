/** A landed run can still have unanswered actors or unusable assets. */
export function expressionResult(result: Record<string, unknown> | null | undefined): string {
  if (!result) return 'Expression refresh completed.';
  if (result.applied === false || result.outcome === 'obsolete' || result.skipped === true) return 'Expression refresh was not applied because its source changed. Refresh again to use the current state.';
  const summary = result.expressions && typeof result.expressions === 'object'
    ? result.expressions as Record<string, unknown> : result;
  const counts = ['selected', 'skipped', 'failed'].flatMap(key =>
    typeof summary[key] === 'number' ? [`${summary[key]} ${key}`] : []);
  const errors = (Array.isArray(summary.errors) ? summary.errors
    : summary.errors && typeof summary.errors === 'object' ? Object.values(summary.errors) : [])
    .filter(e => typeof e === 'string');
  return `Expression refresh completed${counts.length ? `: ${counts.join(', ')}` : ''}.${errors.length ? ` ${errors.join('; ')}` : ''}`;
}
