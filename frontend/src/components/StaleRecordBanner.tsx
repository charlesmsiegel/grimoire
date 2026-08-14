/** The refusal a save gets when the record moved on disk under it (#35).
 *
 *  Shared by every editor that carries a `rev`, because the choice it puts to
 *  the user is the same one everywhere and must read the same everywhere: keep
 *  what you typed, or take what is on disk. Nothing happens until one of the
 *  two is clicked — the form still holds the user's text, and this is only a
 *  refusal, not a loss.
 *
 *  `rev === null` means the record is gone rather than changed, so there is
 *  nothing to overwrite and only the dismissal is offered. */
export function StaleRecordBanner(
  { label, rev, onReload, onOverwrite }:
  { label: string; rev: string | null; onReload: () => void; onOverwrite: () => void },
) {
  return (
    <div className="banner stale-banner" role="alert">
      <p>
        <strong>This {label} changed on disk while you had it open.</strong>{" "}
        {rev === null
          ? "It has been deleted — there is nothing left to save over."
          : "Something outside grimoire — a sync client, or your own text editor — "
            + "wrote it after you opened it. Nothing has been saved."}
      </p>
      <div className="stale-actions">
        <button className="subtle" onClick={onReload}>
          {rev === null ? "Close" : "Discard mine and reload"}
        </button>
        {rev !== null && (
          <button className="subtle" onClick={onOverwrite}>Overwrite with mine</button>
        )}
      </div>
    </div>
  );
}
