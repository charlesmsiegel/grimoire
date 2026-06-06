import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { ChainBadge } from "../common";
import type { ResolutionSource } from "../../../api/types";

const librarySource: ResolutionSource = {
  layer: "library_live",
  scope: "library",
  library_id: null,
  world_id: "wod-london",
  version: 3,
  override_applied: false,
};

describe("ChainBadge", () => {
  it("renders a library badge from the top of the chain", () => {
    const { container } = render(<ChainBadge chain={[librarySource]} overrides={[]} />);
    expect(container.querySelector(".source-badge-library")).not.toBeNull();
  });

  it("does not crash when the chain is undefined", () => {
    // Regression: the World view's list endpoints returned raw library entities
    // with no `source_chain`, so `chain` arrived `undefined`. Reading `chain[0]`
    // threw and white-screened the entire app (blank page). ChainBadge must
    // degrade to an emergent badge instead of crashing.
    const { container } = render(<ChainBadge chain={undefined as unknown as ResolutionSource[]} />);
    expect(container.querySelector(".source-badge-emergent")).not.toBeNull();
  });

  it("renders an override badge when overrides are present", () => {
    const { container } = render(<ChainBadge chain={[librarySource]} overrides={["override"]} />);
    expect(container.querySelector(".source-badge-override")).not.toBeNull();
  });
});
