/** The app's route table, apart from the chrome around it (`App.tsx`).
 *
 *  Split out of `App.tsx` and into one group per area of the app -- setup,
 *  the campaign rooms, the library, settings -- so each group reads on its
 *  own. The groups are plain functions returning `<Route>` fragments, not
 *  components: `<Routes>` only accepts `<Route>` and fragments as children,
 *  and calling them inline keeps the table one `<Routes>` with one matcher. */
import { memo } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { GalleryStateProvider } from "./components/art/galleryState";
import { characterHref } from "./components/character/shared";
import CampaignsView from "./routes/CampaignsView";
import CampaignWizard from "./routes/CampaignWizard";
import OpenScene from "./routes/OpenScene";
import CampaignView from "./routes/CampaignView";
import CampaignHub from "./routes/CampaignHub";
import CampaignSettingsView from "./routes/CampaignSettingsView";
import ScenesView from "./routes/ScenesView";
import TodoView from "./routes/TodoView";
import CostsView from "./routes/CostsView";
import GlobalCostsView from "./routes/GlobalCostsView";
import LedgerView from "./routes/LedgerView";
import SheetsView from "./routes/SheetsView";
import StoryGraphView from "./routes/StoryGraphView";
import TimelineView from "./routes/TimelineView";
import LibraryView from "./routes/LibraryView";
import SearchView from "./routes/SearchView";
import WorldsView from "./routes/WorldsView";
import WorldView from "./routes/WorldView";
import CharacterPage from "./routes/CharacterPage";
import PCPage from "./routes/PCPage";
import ModulesView from "./routes/ModulesView";
import StyleGuidesView from "./routes/StyleGuidesView";
import CalendarsView from "./routes/CalendarsView";
import ClimatesView from "./routes/ClimatesView";
import ProvidersView from "./routes/ProvidersView";
import ModelsView from "./routes/ModelsView";
import PresetsView from "./routes/PresetsView";
import { RoutePageRedirect } from "./routes/ModelsRedirect";
import ConfigView from "./routes/ConfigView";
import StatsView from "./routes/StatsView";
import SetupWizard from "./routes/SetupWizard";

function ScopedTodoView() {
  const { cid = "" } = useParams();
  return <TodoView key={cid} cid={cid} />;
}

function ScopedStatsView() {
  const { cid = "" } = useParams();
  return <StatsView key={cid} cid={cid} />;
}

/** The address a campaign's character page used to have.
 *
 *  Carries the version and whatever `location.state` the link was followed
 *  with — `CharacterPage`'s own back link passes `{ reveal }` so a campaign
 *  grid does not swallow an unseated character, and a redirect that dropped it
 *  would make them read as deleted. */
export function LegacyCharacterRedirect() {
  const { cid = "", eid = "" } = useParams();
  const location = useLocation();
  const v = new URLSearchParams(location.search).get("v") ?? "";
  // Through the helper, not a template string: it is the only thing allowed to
  // know where a character's page lives, and it encodes segments the way the
  // rest of the app does.
  return <Navigate replace state={location.state as unknown}
                   to={characterHref({ kind: "campaign", id: cid }, eid, v || undefined)} />;
}

type AppRoutesProps = {
  inSetup: boolean; rerunSetup: boolean; ready: boolean; dataDir: string;
  onLeftSetup: (dir: string) => void;
};

/** The routes, apart from the chrome around them.
 *
 *  Memoized for the reason `Shell` is, one level down. Shell owns the rail's
 *  shell read, so every re-read -- each notification, each arrival at a
 *  campaign page -- re-renders it at least once for the request and once for
 *  the answer, and each of those used to rebuild every route element and
 *  re-render whichever page was open, the play view included, to draw nothing
 *  new. The props here are what the route table actually takes from the
 *  chrome; the pages that draw the shell's numbers read them from
 *  `ShellPayloadContext`, which reaches them through this boundary on its own. */
const AppRoutes = memo(function AppRoutes(props: AppRoutesProps) {
  const location = useLocation();
  // The play view's key: the campaign segment alone. See `campaignRoutes`.
  const campaignKey = location.pathname.split("/").slice(0, 3).join("/");
  return (
    <GalleryStateProvider><Routes>
      {setupRoutes(props)}
      {campaignRoutes(props.ready, campaignKey)}
      {libraryRoutes()}
      {settingsRoutes()}
    </Routes></GalleryStateProvider>
  );
});
export default AppRoutes;

/** The front door, the setup wizard, and the two ways into a campaign. */
function setupRoutes({ inSetup, rerunSetup, ready, dataDir, onLeftSetup }: AppRoutesProps) {
  return (
    <>
      {/* A fresh install lands on the wizard instead of an empty campaigns
          list. Only `/` is redirected: every other route stays reachable, so
          a deep link is never hijacked. The two guards are exact opposites
          of one `inSetup`, which is what keeps them from bouncing a redirect
          back and forth. Gating `/welcome` too is what stops a reload
          part-way through the wizard — after a world exists, so the server no
          longer calls it a first run — from restarting at step one and
          creating a second world. */}
      <Route path="/" element={inSetup ? <Navigate to="/welcome" replace /> : <CampaignsView />} />
      {/* `?again=1` is the explicit re-entry signal Configuration's own
          First-run setup row links with. Without one this route is a
          redirect the moment setup is done, so nothing could offer the
          wizard again -- and the four things it walks (storage, model, look,
          world) are exactly what somebody moving to a new machine wants to
          be walked through a second time. It has to be explicit rather than
          inferred: `inSetup` answers "has this library been set up", and
          re-running is a decision, not a state. */}
      <Route path="/welcome" element={
        inSetup || rerunSetup
          ? <SetupWizard onDone={(dir) => onLeftSetup(dir ?? dataDir)} />
          : <Navigate to="/" replace />} />
      <Route path="/campaigns/new" element={<CampaignWizard ready={ready} />} />
      {/* Where a completion-notification tap lands; see `OpenScene`. */}
      <Route path="/open" element={<OpenScene />} />
    </>
  );
}

/** A campaign's rooms: the hub, its scenes and play, and the pages read
 *  across the whole campaign. `campaignKey` is the play view's key. */
function campaignRoutes(ready: boolean, campaignKey: string) {
  return (
    <>
      {/* Keyed so a campaign→campaign move remounts. The palette made that
          transition reachable: it stays on this route and only changes the
          param, so React reuses the component, and CampaignView's [cid]
          effect refetches without synchronously dropping the scene list,
          transcript or activeId. Until those land -- or forever, if they
          fail -- it would show campaign A's scene while its handlers carry
          B's cid, and scene ids repeat across campaigns. The key is the
          campaign segment alone and NOT the whole pathname: this route
          matches deeper now (#87, below), and keying on the full path would
          remount on every scene jump -- exactly what the nested child exists
          to prevent. */}
      {/* The play view answers to two paths — with and without a scene
          (#87) — and they have to resolve to the SAME element instance.
          Sibling routes would remount CampaignView on
          /campaigns/A/scenes/s1 → /campaigns/B but not on
          A/scenes/s1 → B/scenes/s2, so the stale-response guards the view is
          built around (cidRef, the window token) would hold in one direction
          and be bypassed in the other. Nesting keeps one instance for every
          combination. The child renders nothing — CampaignView has no
          <Outlet /> — and exists only to put `:sid` in the matched path,
          where useMatch can read it. */}
      {/* The campaign's front door, and it is not the transcript. Opening a
          campaign used to resume whichever scene was played last, which is
          the complaint the hub answers -- so play now lives one segment
          deeper and the reader arrives somewhere that says what is waiting. */}
      <Route path="/campaigns/:cid" element={<CampaignHub />} />
      <Route path="/campaigns/:cid/settings" element={<CampaignSettingsView />} />
      {/* The list is the route; play is one scene inside it. They are
          separate elements rather than one component branching on `:sid`,
          because a list read top to bottom and a transcript being written
          into are not the same page wearing two states. */}
      <Route path="/campaigns/:cid/scenes" element={<ScenesView ready={ready} />} />
      <Route path="/campaigns/:cid/scenes/:sid" element={
        <CampaignView key={campaignKey} ready={ready} />} />
      {/* The review's own address. Same element, because a review is a mode
          of this page and not a second copy of it -- `useSceneReview` adopts
          whatever the scene is holding whichever of the two URLs you arrive
          on. What the separate path buys is that the rail's Wrap-up row can
          light, and that "the thing that is waiting" is something a reader
          can bookmark or be sent a link to. The `key` is deliberately the
          campaign and nothing more, exactly as above: moving between the
          transcript and its wrap-up must not remount the page and discard
          the review being judged. */}
      <Route path="/campaigns/:cid/scenes/:sid/wrap-up" element={
        <CampaignView key={campaignKey} ready={ready} />} />
      {/* A campaign's character used to live here, outside the world copy
          that holds its other seven record kinds. It moved so the two shapes
          are one shape; this keeps a link somebody already holds working,
          and is a redirect rather than a second live route so there is still
          exactly one address per screen. */}
      <Route path="/campaigns/:cid/characters/:eid" element={<LegacyCharacterRedirect />} />
      {/* The ledger is a room, not a drawer over the transcript (4e): it is a
          table read top to bottom, and the supersession chains it exists to
          show do not fit in a panel wedged above the scene. The splat is
          its sections, rows and continuity findings (`ledgerPaths.ts`). */}
      <Route path="/campaigns/:cid/ledger/*" element={<LedgerView />} />
      <Route path="/campaigns/:cid/costs" element={<CostsView />} />
      {/* The timeline is the ledger's other half and a room for the same
          reason (#198): the ledger says what is still open, this says what
          happened, and a play history read end to end is not a drawer over
          the scene it is a history of. */}
      <Route path="/campaigns/:cid/timeline" element={<TimelineView />} />
      {/* Sheet coverage across the cast (#201). A room for the ledger's
          reason: the play view's mechanics panel binds the module in six
          lines, but "who among forty characters has a sheet" is a list read
          top to bottom, and a drawer over the transcript is not where a list
          like that goes. */}
      <Route path="/campaigns/:cid/sheets" element={<SheetsView />} />
      {/* The story graph (capstone §19) is a room for the same reason: it is
          a projection read across the whole campaign -- every scene, thread,
          commitment and dated moment at once -- not a drawer over one scene.
          Not keyed on `cid`: a campaign switch keeps the page mounted, and the
          page drops the superseded read itself. */}
      <Route path="/campaigns/:cid/graph" element={<StoryGraphView />} />
      <Route path="/todo" element={<TodoView cid={null} />} />
      <Route path="/campaigns/:cid/todo" element={<ScopedTodoView />} />
      <Route path="/costs" element={<GlobalCostsView />} />
    </>
  );
}

/** The library: worlds, their records and characters, and search across them. */
function libraryRoutes() {
  return (
    <>
      <Route path="/library" element={<LibraryView />} />
      {/* Search keeps its query in the URL, so a result page is a link and
          the back button returns to it after following a hit. */}
      <Route path="/search" element={<SearchView />} />
      <Route path="/worlds" element={<WorldsView />} />
      {/* A character owns a screen rather than a third of one — see
          `CharacterPage`. Both scopes, because a campaign's copy of a
          character is a different record from the world's. Declared before
          the splats for a reader; the matcher ranks static segments above a
          splat on its own. */}
      <Route path="/worlds/:wid/characters/:eid" element={<CharacterPage />} />
      <Route path="/campaigns/:cid/world/characters/:eid" element={<CharacterPage campaign />} />
      <Route path="/worlds/:wid/pcs/:pid" element={<PCPage />} />
      <Route path="/campaigns/:cid/world/pcs/:pid" element={<PCPage campaign />} />
      {/* One splat per shape, so every section and record of a world is the
          same route object and React keeps ONE `WorldView` across all of
          them. Sibling routes per section would remount the page on every
          column click and re-read the world and its cover each time. */}
      <Route path="/worlds/:wid/*" element={<WorldView />} />
      <Route path="/campaigns/:cid/world/*" element={<WorldView campaign />} />
      <Route path="/modules" element={<ModulesView />} />
      <Route path="/styles" element={<StyleGuidesView />} />
      <Route path="/calendars" element={<CalendarsView />} />
      <Route path="/climates" element={<ClimatesView />} />
    </>
  );
}

/** Settings and readings: providers, models, presets, configuration, stats. */
function settingsRoutes() {
  return (
    <>
      <Route path="/providers" element={<ProvidersView />} />
      {/* Before `:id`, so `new` is never read as a provider. */}
      <Route path="/providers/new" element={<ProvidersView />} />
      <Route path="/providers/:id" element={<ProvidersView />} />
      {/* A splat: a model id carries its own slashes (`vendor/model`). */}
      <Route path="/providers/:id/models/*" element={<ProvidersView />} />
      <Route path="/connections" element={<Navigate to="/providers" replace />} />
      {/* Settings → Inference: the roles' summary, and the one form that
          edits every role and task. The old per-role and per-route pages
          are addresses older links carry. */}
      <Route path="/models" element={<ModelsView />} />
      <Route path="/models/edit" element={<ModelsView edit />} />
      <Route path="/models/role/:role" element={<Navigate to="/models" replace />} />
      <Route path="/models/route/:key" element={<RoutePageRedirect />} />
      <Route path="/presets" element={<PresetsView />} />
      <Route path="/presets/new" element={<PresetsView />} />
      <Route path="/presets/import" element={<PresetsView />} />
      <Route path="/presets/:id" element={<PresetsView />} />
      <Route path="/config" element={<ConfigView />} />
      {/* A room, not a tab inside Configuration (#154 asked for a "tab").
          Config is a page of settings; this is a page of readings, and the
          only thing on it that can be changed -- the log's own level -- is
          saved through Configuration like every other setting. */}
      <Route path="/stats" element={<StatsView />} />
      <Route path="/campaigns/:cid/stats" element={<ScopedStatsView />} />
    </>
  );
}
