// Count the defect categories of merged PRs, for the retro (docs/process.md): reads the
// `Categories:` line at the end of each merged PR body through gh. Run from the repo root:
//   node tools/tally_categories.mjs              the last 100 merged PRs
//   node tools/tally_categories.mjs --limit 300  more of them
// Prints one line per category, most frequent first, then the PRs whose body has no such line.
import { execFileSync } from "node:child_process";

const LINE = /^Categories:\s*(.*)$/im;

function limitArg(argv) {
  const at = argv.indexOf("--limit");
  const limit = at === -1 ? 100 : Number(argv[at + 1]);
  if (!Number.isInteger(limit) || limit < 1) {
    throw new Error("--limit takes a whole number of PRs, e.g. --limit 300");
  }
  return limit;
}

function mergedPrs(limit) {
  const out = execFileSync(
    "gh",
    ["pr", "list", "--state", "merged", "--limit", String(limit), "--json", "number,body"],
    { encoding: "utf8", timeout: 60_000 },
  );
  return JSON.parse(out);
}

function categories(body) {
  const found = LINE.exec(body ?? "");
  if (!found) return null;
  return found[1]
    .replace(/<!--.*?-->/g, "")
    .split(/[,;]/)
    .map((word) => word.trim().toLowerCase())
    .filter((word) => word && word !== "none");
}

const counts = new Map();
const untagged = [];
for (const pr of mergedPrs(limitArg(process.argv))) {
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
