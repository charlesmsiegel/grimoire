import type { MigrationStatus } from "../../api/client";

/** What every model-settings surface says while its settings cannot be saved
 *  (spec 10, 11.3).
 *
 *  Two cases, both read from the server's migration status rather than worked
 *  out here:
 *  - **newer**: a newer Grimoire has written this library's model settings,
 *    and this build would overwrite what it does not understand -- so every
 *    model-settings write is refused (409 `newer_format`). Play continues.
 *  - **pending, running or failed**: the one-time move to the new layout has
 *    not finished, so the new editors cannot save yet (409 `not_migrated`).
 *    A failure carries the server's reason, which for the case worth naming
 *    -- the safety backup that has to come first -- says so in its own words.
 *
 *  Draws nothing once the upgrade is done, or before the status is known. */
export function InferenceBanner({ status }: { status: MigrationStatus | null | undefined }) {
  if (!status) return null;
  if (status.state === "newer") {
    return (
      <div className="banner" role="status">
        This library was upgraded by a newer Grimoire. Its model settings can only be
        changed there; play continues here.
      </div>
    );
  }
  if (status.state === "done") return null;
  return (
    <div className="banner" role="status">
      {status.state === "failed" && status.reason
        ? <>Upgrade pending: {status.reason}</>
        : <>Upgrade pending: model settings are being moved to the new layout.</>}
      {" "}They can be changed once that has finished; play continues meanwhile.
    </div>
  );
}
