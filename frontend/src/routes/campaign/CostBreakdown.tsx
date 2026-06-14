import { useCallback } from "react";

import { observabilityApi } from "../../api/observability";
import { useResource } from "../../api/useResource";
import { AsyncSection } from "../../components/AsyncSection";

interface Props {
  turnId: string;
}

function fmtUsd(value: number): string {
  return `$${value.toFixed(4)}`;
}

export function CostBreakdown({ turnId }: Props) {
  const state = useResource(useCallback(() => observabilityApi.turnCosts(turnId), [turnId]));

  return (
    <AsyncSection state={state} emptyMessage="No recorded cost for this turn.">
      {(rows) => {
        const totalUsd = rows.reduce((acc, r) => acc + r.total_usd, 0);
        const totalIn = rows.reduce((acc, r) => acc + r.input_tokens, 0);
        const totalOut = rows.reduce((acc, r) => acc + r.output_tokens, 0);
        const totalCalls = rows.reduce((acc, r) => acc + r.call_count, 0);

        return (
          <table className="cost-breakdown" aria-label="Per-task cost breakdown">
            <thead>
              <tr>
                <th>Task</th>
                <th>Calls</th>
                <th>Input tokens</th>
                <th>Output tokens</th>
                <th>USD</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.task || "(unspecified)"}>
                  <td>{r.task || "(unspecified)"}</td>
                  <td>{r.call_count}</td>
                  <td>{r.input_tokens}</td>
                  <td>{r.output_tokens}</td>
                  <td>{fmtUsd(r.total_usd)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <td>Total</td>
                <td>{totalCalls}</td>
                <td>{totalIn}</td>
                <td>{totalOut}</td>
                <td data-testid="cost-total-usd">{fmtUsd(totalUsd)}</td>
              </tr>
            </tfoot>
          </table>
        );
      }}
    </AsyncSection>
  );
}
