import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** kaogong-assistant/ 根目录 */
export const ROOT = path.resolve(__dirname, "../..");
export const KAOGONG_DB = path.join(ROOT, "data", "kaogong.db");
export const USER_DB = path.join(ROOT, "data", "user.db");
export const SYSTEM_PROMPT_PATH = path.join(ROOT, "agent", "prompts", "system.md");
