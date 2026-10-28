// api.schema.json -> src/api/types.ts. The schema is the one contract between
// umbra serve and this page (ADR 0001); these types are never edited by hand.
import { readFileSync, writeFileSync } from "node:fs";
import { compile } from "json-schema-to-typescript";

const src = new URL("../../umbra/schema/api.schema.json", import.meta.url);
const schema = JSON.parse(readFileSync(src, "utf8"));

// The schema is a bag of $defs with no root type. Point a root at every def so
// each one is emitted under its own name, then drop the root itself.
const root = {
  ...schema,
  title: "Api",
  type: "object",
  additionalProperties: false,
  properties: Object.fromEntries(
    Object.keys(schema.$defs).map((k) => [k, { $ref: `#/$defs/${k}` }]),
  ),
};
let ts = await compile(root, "Api", {
  bannerComment:
    "/* Generated from umbra/schema/api.schema.json by scripts/gen-types.mjs. Do not edit. */",
  additionalProperties: false,
  unreachableDefinitions: true,
  strictIndexSignatures: true,
  format: true,
});
writeFileSync(new URL("../src/api/types.ts", import.meta.url), ts);
console.log("types: src/api/types.ts");
