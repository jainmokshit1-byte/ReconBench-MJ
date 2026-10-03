function delimiterOf(line: string): string {
  const candidates = [",", ";", "\t", "|"];
  let selected = ",";
  let max = -1;
  for (const candidate of candidates) {
    let count = 0;
    let quoted = false;
    for (const char of line) {
      if (char === '"') quoted = !quoted;
      else if (char === candidate && !quoted) count += 1;
    }
    if (count > max) {
      max = count;
      selected = candidate;
    }
  }
  return selected;
}

export function parseCsvPreviewRows(content: string): string[][] {
  const firstLine = content.split(/\r?\n/, 1)[0] ?? "";
  const delimiter = delimiterOf(firstLine);
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < content.length && rows.length < 100; index += 1) {
    const char = content[index] ?? "";
    if (char === '"') {
      if (quoted && content[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === delimiter && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((char === "\n" || char === "\r") && !quoted) {
      if (char === "\r" && content[index + 1] === "\n") index += 1;
      row.push(cell);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += char;
    }
  }
  if (cell || row.length) {
    row.push(cell);
    if (row.some((value) => value.trim())) rows.push(row);
  }
  if (rows[0]?.[0]?.startsWith("\uFEFF")) rows[0][0] = (rows[0][0] ?? "").slice(1);
  return rows;
}
