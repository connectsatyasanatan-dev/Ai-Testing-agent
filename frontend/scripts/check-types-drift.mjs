/**
 * check-types-drift.mjs
 * CI drift check: regenerates types into a temp file, compares to committed
 * src/types/generated/api.d.ts, exits 1 if they differ.
 *
 * Usage: npm run check-types-drift  |  make check-types-drift
 */
import { execSync } from "child_process";
import { readFileSync, rmSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname   = dirname(fileURLToPath(import.meta.url));
const committed   = join(__dirname, "..", "src", "types", "generated", "api.d.ts");
const tmp         = `${committed}.tmp`;
const schemaUrl   = process.env.API_URL
  ? `${process.env.API_URL}/openapi.json`
  : "http://localhost:8000/openapi.json";

console.log("Checking for TypeScript type drift …");
execSync(`npx openapi-typescript "${schemaUrl}" --output "${tmp}"`, { stdio: "inherit" });

const a = readFileSync(committed, "utf-8");
const b = readFileSync(tmp, "utf-8");
rmSync(tmp, { force: true });

if (a === b) {
  console.log("✓ No drift — committed types are up to date.");
  process.exit(0);
} else {
  console.error("✗ Type drift detected!\n  Run `make generate-types` and commit the result.");
  process.exit(1);
}
