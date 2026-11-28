// Count the defect categories of merged PRs, for the retro (docs/process.md): reads the
// `Categories:` line at the end of each merged PR body, from gh's JSON on standard input. Run
// from the repo root:
//   gh pr list --state merged --limit 100 --json number,body | node tools/tally_categories.mjs
// Prints one line per category, most frequent first, then the PRs whose body has no such line.
import { readFileSync } from "node:fs";

const LINE = /^Categories:(.*)$/im;

function categories(body) {
  const found = LINE.exec(body ?? "");
  if (!found) return null;
  return found[1]
    .replace(/<!--.*?-->/g, "")
    .split(/[,;]/)
    .map((word) => word.trim().toLowerCase())
    .filter((word) => word && word !== "none");
}

const prs = JSON.parse(readFileSync(0, "utf8"));
if (!Array.isArray(prs)) {
  throw new Error("expected gh pr list --json number,body on standard input");
}
const counts = new Map();
const untagged = [];
for (const pr of prs) {
  const found = categories(pr.body);
  if (found === null) {
    untagged.push(pr.number);
    continue;
  }
  for (const word of found) counts.set(word, (counts.get(word) ?? 0) + 1);
}
for (const [word, n] of [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
  console.log(`${String(n).padStart(4)}  ${word}`);
}
if (untagged.length) console.log(`no Categories line: #${untagged.join(", #")}`);
