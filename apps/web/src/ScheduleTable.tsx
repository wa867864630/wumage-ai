import type { Column } from './ConfigAdmin';

export function ScheduleTable({ columns, rows }: { columns: Column[]; rows: Record<string, string>[] }) {
  const visible = columns.filter(column => column.visible);
  return <div className="table-scroll"><table><thead><tr>{visible.map(column => <th key={column.field}>{column.label}</th>)}</tr></thead>
    <tbody>{rows.map((row, index) => <tr key={row.id ?? index}>{visible.map(column => <td key={column.field}>{row[column.field] ?? ''}</td>)}</tr>)}</tbody>
  </table></div>;
}
