import { useEffect, useState } from "react";
import { ApiError, api, type DataDirInfo } from "../api/client";

/** Where the library lives on disk — the Configuration page's "Storage
 *  location" block, lifted out so the first-run wizard (#194) asks the same
 *  question with the same markup instead of a second copy that drifts.
 *
 *  It owns its own state and talks to the API directly: the data dir is a
 *  property of the machine, not of whatever page happens to be showing it, and
 *  every caller wants the same read-then-write cycle. */
export function StorageLocation() {
  const [dataDir, setDataDir] = useState<DataDirInfo | null>(null);
  const [input, setInput] = useState("");
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    api.getDataDir().then((d) => {
      setDataDir(d);
      setInput(d.data_dir);
    });
  }, []);

  async function saveDataDir(value: string | null) {
    setMsg(null);
    try {
      const next = await api.putDataDir(value);
      setDataDir(next);
      setInput(next.data_dir);
      setMsg({ kind: "ok", text: `Storage now at ${next.data_dir}` });
    } catch (e) {
      const detail = e instanceof ApiError ? e.detail : "Could not update storage location";
      setMsg({ kind: "err", text: detail });
    }
  }

  return (
    <>
      <p className="field-hint" style={{ marginTop: 0 }}>
        The folder where all worlds, campaigns, and settings live. Point it at a
        synced folder (Syncthing, Dropbox/Drive desktop, iCloud…) to share the
        same library across devices. Changes take effect immediately.
      </p>
      <div className="joined">
        <input
          id="cfg-data-dir"
          aria-label="Storage location"
          className="mono-input"
          placeholder={dataDir?.default ?? "~/.grimoire"}
          value={input}
          disabled={dataDir?.source === "env"}
          onChange={(e) => setInput(e.target.value)}
        />
        <button
          className="btn-accent"
          onClick={() => saveDataDir(input)}
          disabled={dataDir?.source === "env" || input.trim() === dataDir?.data_dir}
        >
          Move
        </button>
      </div>
      {dataDir?.source === "env" && (
        <p className="field-hint">
          Set by the <code>GRIMOIRE_HOME</code> environment variable — unset it to edit here.
        </p>
      )}
      {dataDir && dataDir.source !== "env" && !dataDir.is_default && (
        <p className="field-hint">
          <button className="link" onClick={() => saveDataDir(null)}>
            Reset to default ({dataDir.default})
          </button>
        </p>
      )}
      {msg && (
        <p className={msg.kind === "err" ? "config-msg err" : "config-msg save-flash"}>
          {msg.text}
        </p>
      )}
    </>
  );
}
