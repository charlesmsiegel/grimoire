// Hoisted factories must not import the api they replace. Each suite loads
// these factories through a deferred import, just like the greeting suites.
export function configApiMock() {
  return {
    ApiError: class ApiError extends Error {},
    api: {
      getConfig: vi.fn(), putConfig: vi.fn(), getDataDir: vi.fn(), putDataDir: vi.fn(),
      listBackups: vi.fn(), createBackup: vi.fn(), createImageBackup: vi.fn(),
      getStoreConflicts: vi.fn(),
      listStyles: vi.fn(),
      listCampaigns: vi.fn(), listScenes: vi.fn(),
      listScenePrompts: vi.fn(), getScenePrompt: vi.fn(),
      getPromptLayout: vi.fn(), putPromptLayout: vi.fn(),
      getPlayDecisionsInfo: vi.fn(),
    },
  };
}

export function responseTargetsMock() {
  return {
    ResponseTargetsPicker: () => <div data-testid="response-preset-picker" />,
  };
}

export function imageStoreMock() {
  return {
    ImageStoreCard: () => <div data-testid="image-store-card" />,
  };
}

export function regexRulesMock() {
  return {
    RegexRulesEditor: ({ scope }: { scope: unknown }) =>
      <div data-testid="regex-rules" data-scope={JSON.stringify(scope)} />,
  };
}
