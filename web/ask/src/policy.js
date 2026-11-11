// Imported first, before any chat package loads.
// Hub's content policy forbids building code from text. Saying so up front stops the schema library those
// packages use from trying it (it would only fail, and log a policy violation on every page load).
import { config } from "zod/v4/core";

config({ jitless: true });
