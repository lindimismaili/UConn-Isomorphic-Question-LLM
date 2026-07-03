import { DataTable } from '../../ui/index.js';
import { formatScore } from './evalData.js';

/**
 * Fallback table shown when only aggregate criterion scores are available
 * (no per-item detail export).
 *
 * @param {object} props
 * @param {Array<object>} props.rows - Aggregate criterion rows.
 */
export default function EvalAggregateTable({ rows }) {
  return (
    <div className="eval-table-shell">
      <DataTable variant="eval" className="eval-aggregate-table">
        <thead>
          <tr>
            <th>Criterion</th>
            <th>Category</th>
            <th>Score</th>
            <th>Items Scored</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name}>
              <td>{row.name}</td>
              <td>{row.category || 'n/a'}</td>
              <td>{formatScore(row.score)}</td>
              <td>{row.itemsScored ?? 'n/a'}</td>
            </tr>
          ))}
        </tbody>
      </DataTable>
    </div>
  );
}
