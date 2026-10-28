// Record which sources this build came from. tests/test_web_build.py compares
// it with web/src + the schema, so a committed build that has fallen behind its
// sources fails CI instead of shipping.
import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const web = fileURLToPath(new URL("..", import.meta.url));
const repo = join(web, "..");

function walk(dir) {
  return readdirSync(dir).sort().flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

const files = [
  ...walk(join(web, "src")),
  join(web, "index.html"),
  join(web, "vite.config.ts"),
  join(web, "package.json"),
  join(repo, "umbra/schema/api.schema.json"),
];
const h = createHash("sha256");
for (const f of files) {
  h.update(relative(repo, f).split("\\").join("/"));
  h.update("\0");
  h.update(readFileSync(f));
  h.update("\0");
}
writeFileSync(join(repo, "umbra/web/SOURCE_HASH"), h.digest("hex") + "\n");
console.log("stamped umbra/web/SOURCE_HASH");
