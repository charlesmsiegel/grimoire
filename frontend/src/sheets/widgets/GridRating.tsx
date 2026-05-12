import type { WidgetProps } from "../types";

type GridValue = Record<string, Record<string, number>>;

export function GridRatingWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<GridValue | null>) {
  const rows = (property.rowLabels ?? []) as ReadonlyArray<string>;
  const cols = (property.cols ?? []) as ReadonlyArray<string>;
  const grid = value ?? {};
  const min = property.min ?? 0;
  const max = property.max ?? 10;

  const set = (row: string, col: string, v: number) => {
    if (readOnly) return;
    const rowMap = { ...(grid[row] ?? {}), [col]: v };
    onChange({ ...grid, [row]: rowMap });
  };

  return (
    <table
      className="sheet-widget sheet-grid-rating"
      id={`sheet-${name}`}
      aria-label={property.title ?? name}
    >
      <thead>
        <tr>
          <th />
          {cols.map((c) => (
            <th key={c} scope="col">
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r}>
            <th scope="row">{r}</th>
            {cols.map((c) => (
              <td key={c}>
                <input
                  type="number"
                  min={min}
                  max={max}
                  value={grid[r]?.[c] ?? ""}
                  readOnly={readOnly}
                  onChange={(e) => {
                    const raw = e.target.value;
                    const parsed = raw === "" ? 0 : parseInt(raw, 10) || 0;
                    set(r, c, parsed);
                  }}
                />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
