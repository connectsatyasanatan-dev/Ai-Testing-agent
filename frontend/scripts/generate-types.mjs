/**
 * generate-types.mjs
 * Fetches OpenAPI schema from the running FastAPI server and generates
 * TypeScript types into src/types/generated/api.d.ts.
 *
 * Prerequisites: API server must be running (make dev-api).
 * Usage: npm run generate-types
 *        or: make generate-types (from repo root)
 */
import { execSync } from "child_process";
import { mkdirSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";

const __dirname  = dirname(fileURLToPath(import.meta.url));
const outputDir  = join(__dirname, "..", "src", "types", "generated");
const outputFile = join(outputDir, "api.d.ts");
const schemaUrl  = process.env.API_URL
  ? `${process.env.API_URL}/openapi.json`
  : "http://localhost:8090/openapi.json";

mkdirSync(outputDir, { recursive: true });
console.log(`Fetching schema from : ${schemaUrl}`);
console.log(`Writing types to     : ${outputFile}`);
execSync(`npx openapi-typescript "${schemaUrl}" --output "${outputFile}"`, { stdio: "inherit" });
console.log("✓ Types generated.");
