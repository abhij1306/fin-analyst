// Authored source files stay under 6,000 tokens (tokens = bytes / 4). Run from the repo root:
//   node tools/check_file_size.mjs            every source file under the current folder (CI)
//   node tools/check_file_size.mjs a.py b.sql the files named (the pre-commit hook passes the staged ones)
// Tests, lockfiles, generated code and migrations are not authored source and are skipped. The fix
// for a file over the limit is to split it along its seams, never to compress it or skip it here.
import { readdirSync, statSync } from "node:fs";
import { extname, join } from "node:path";

const MAX_TOKENS = 6000;
const SOURCE = new Set([".py", ".js", ".mjs", ".cjs", ".ts", ".svelte", ".sql", ".sh"]);
const SKIPPED_DIR = /(^|\/)(tests?|node_modules|migrations?|generated|dist|build|\.evidence|\.venv)\//;
const LOCKFILE = /(^|\/)(uv\.lock|package-lock\.json|pnpm-lock\.yaml|yarn\.lock)$/;

function isAuthoredSource(path) {
  const p = path.replaceAll("\\", "/");
  return SOURCE.has(extname(p)) && !SKIPPED_DIR.test(p) && !LOCKFILE.test(p);
}

function tokens(bytes) {
  return bytes / 4;
}

function walk(folder) {
  const found = [];
  for (const entry of readdirSync(folder, { withFileTypes: true })) {
    if (entry.name === ".git" || entry.name === "node_modules" || entry.name === ".venv") continue;
    const path = join(folder, entry.name);
    if (entry.isDirectory()) found.push(...walk(path));
    else found.push(path);
  }
  return found;
}

function main(argv) {
  const named = argv.filter((a) => !a.startsWith("--"));
  const files = named.length ? named : walk(".");
  const over = [];
  for (const file of files.filter(isAuthoredSource)) {
    let size;
    try {
      size = statSync(file).size;
    } catch {
      continue; // deleted or unreadable: nothing to measure
    }
    if (tokens(size) > MAX_TOKENS) over.push(`${file}: ${Math.round(tokens(size))} tokens (${size} bytes)`);
  }
  if (over.length) {
    console.error(`Source files over ${MAX_TOKENS} tokens (bytes / 4). Split each along its seams:`);
    for (const line of over) console.error(`  ${line}`);
    return 1;
  }
  console.log(`file size ok: ${files.filter(isAuthoredSource).length} source files under ${MAX_TOKENS} tokens`);
  return 0;
}

process.exit(main(process.argv.slice(2)));
