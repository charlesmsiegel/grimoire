import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type EntityScope } from "../../api/client";
import { Field } from "../Field";
import { ErrorNote } from "../ErrorNote";
import { useCreationRequest } from "../useCreationRequest";
import { characterHref } from "../character/shared";

/** The `?new=1` form: a name, and a character made in `scope` from it. The
 *  error is the grid's, so a failure here is still on the banner after
 *  Cancel. */
export function NewCharacterForm(
  { scope, worldScope, error, setError }: {
    scope: EntityScope;
    worldScope: boolean;
    error: unknown;
    setError: (err: unknown) => void;
  },
) {
  const navigate = useNavigate();
  const [newName, setNewName] = useState("");
  const { busy: creatingBusy, begin: beginCreation } = useCreationRequest(`${scope.kind}:${scope.id}`);
  useEffect(() => { setNewName(""); }, [scope.kind, scope.id]);

  async function newCharacter() {
    const name = newName.trim();
    if (!name || creatingBusy) return;
    const request = beginCreation();
    if (!request) return;
    // `scope`, not `wid`: in campaign scope this makes a character who exists
    // only in this campaign — an NPC who walked on mid-scene and was never in
    // the library (#60).
    setError(null);
    try {
      const { character } = await api.createCharacter(scope, { name });
      if (request.current()) void navigate(characterHref(scope, character));
    } catch (err) { if (request.current()) setError(err); }
    finally { request.finish(); }
  }

  return <div className="form">
    <h3>{worldScope ? "New character" : "New NPC in this campaign"}</h3>
    {error != null && <div className="banner"><ErrorNote err={error} /></div>}
    <Field label="Character name"><input type="text" value={newName} disabled={creatingBusy}
      onChange={(e) => setNewName(e.target.value)} /></Field>
    <div className="form-actions"><Link className="subtle" to="?">Cancel</Link>
      <button className="primary" disabled={creatingBusy || !newName.trim()} onClick={() => void newCharacter()}>Create character</button>
    </div>
  </div>;
}
