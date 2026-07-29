import contentSrc from "./ModuleContentEditor.tsx?raw";
import displaySrc from "./ModuleDisplayEditor.tsx?raw";
import rulesSrc from "./ModuleRulesEditor.tsx?raw";
import schemaSrc from "./ModuleSchemaEditor.tsx?raw";
import sharedSrc from "./moduleEditShared.tsx?raw";

// The four section files and ModuleEditor once formed a five-file import
// cycle: ModuleEditor imported every section, and every section imported the
// shared save/dry-run helpers back out of ModuleEditor. The helpers now live
// here in moduleEditShared; this guards the arrow from pointing back.
const SECTIONS: [string, string][] = [
  ["ModuleSchemaEditor", schemaSrc],
  ["ModuleRulesEditor", rulesSrc],
  ["ModuleContentEditor", contentSrc],
  ["ModuleDisplayEditor", displaySrc],
];

describe("module-editor shared helpers", () => {
  it.each(SECTIONS)("%s does not import from ModuleEditor", (_name, src) => {
    expect(src).not.toMatch(/from ["']\.\/ModuleEditor["']/);
  });

  it("shared helpers do not import any section file", () => {
    expect(sharedSrc).not.toMatch(/from ["']\.\/Module\w*Editor["']/);
  });
});
