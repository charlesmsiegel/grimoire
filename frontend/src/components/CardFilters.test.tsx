import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { CardFilters } from "./CardFilters";
import { useCardFilters } from "../hooks/useCardFilters";

interface Item {
  id: string;
  name: string;
  description: string;
  tags: string[];
}

const ITEMS: Item[] = [
  { id: "fireball", name: "Fireball", description: "A ball of fire.", tags: ["spell", "fire"] },
  {
    id: "longsword",
    name: "Longsword",
    description: "A martial weapon.",
    tags: ["weapon", "martial"],
  },
  {
    id: "potion-fire",
    name: "Flame Potion",
    description: "Drink to breathe fire.",
    tags: ["item", "fire"],
  },
];

function Harness({ items }: { items: Item[] }) {
  const f = useCardFilters(items, {
    text: (i) => [i.name, i.id, i.description],
    tags: (i) => i.tags,
  });
  return (
    <div>
      <CardFilters
        search={f.search}
        onSearch={f.setSearch}
        availableTags={f.availableTags}
        selectedTags={f.selectedTags}
        onToggleTag={f.toggleTag}
        onClearTags={f.clearTags}
        searchLabel="Search test"
        resultSummary={`${f.filtered.length}/${items.length}`}
      />
      <ul>
        {f.filtered.map((i) => (
          <li key={i.id} data-testid="result">
            {i.name}
          </li>
        ))}
      </ul>
    </div>
  );
}

describe("CardFilters", () => {
  it("filters by search text across name, id, and description", () => {
    render(<Harness items={ITEMS} />);
    expect(screen.getAllByTestId("result")).toHaveLength(3);

    const input = screen.getByLabelText(/search test/i);
    fireEvent.change(input, { target: { value: "fire" } });

    const visible = screen.getAllByTestId("result").map((el) => el.textContent);
    expect(visible).toEqual(["Fireball", "Flame Potion"]);
  });

  it("filters by selected tag chips with AND semantics", () => {
    render(<Harness items={ITEMS} />);

    fireEvent.click(screen.getByRole("button", { name: "fire" }));
    let visible = screen.getAllByTestId("result").map((el) => el.textContent);
    expect(visible).toEqual(["Fireball", "Flame Potion"]);

    fireEvent.click(screen.getByRole("button", { name: "spell" }));
    visible = screen.getAllByTestId("result").map((el) => el.textContent);
    expect(visible).toEqual(["Fireball"]);

    fireEvent.click(screen.getByRole("button", { name: /clear tags/i }));
    expect(screen.getAllByTestId("result")).toHaveLength(3);
  });

  it("combines search and tag filters", () => {
    render(<Harness items={ITEMS} />);

    fireEvent.click(screen.getByRole("button", { name: "fire" }));
    fireEvent.change(screen.getByLabelText(/search test/i), {
      target: { value: "potion" },
    });

    const visible = screen.getAllByTestId("result").map((el) => el.textContent);
    expect(visible).toEqual(["Flame Potion"]);
  });

  it("only shows tags that exist on at least one item", () => {
    render(<Harness items={ITEMS} />);
    const buttons = screen
      .getAllByRole("button")
      .map((b) => b.textContent)
      .filter((t): t is string => !!t);
    expect(buttons).toEqual(expect.arrayContaining(["fire", "item", "martial", "spell", "weapon"]));
    expect(buttons).not.toContain("nonexistent");
  });
});
