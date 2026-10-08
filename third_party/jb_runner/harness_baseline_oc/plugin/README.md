<!-- c8 — OpenCode plugins (placeholder). The evolve loop may add a plugin here as
     plugin/<name>.ts. render_opencode.py copies it to .opencode/plugin/<name>.ts in the temp
     workspace, where OpenCode auto-discovers it (config/plugin.ts globs `{plugin,plugins}/*.{ts,js}`
     from each .opencode dir up the working-dir tree).

     A plugin exports a server function returning Hooks; use it for what prose rules cannot do —
     a deterministic RECHECK in the tool loop rather than a static instruction:
       - "tool.execute.after"  — inspect/transform a tool's output (e.g. after a Write, verify the
                                 deliverable; may call the LLM via the plugin client for a semantic check)
       - "experimental.chat.system.transform" — inject dynamic, per-task guidance
       - "tool"               — register a brand-new deterministic tool (e.g. a workbook validator)
       - "chat.params"        — adjust model params

     Example skeleton (TypeScript):

       import type { Plugin } from "@opencode-ai/plugin"
       export const VerifyDeliverable: Plugin = async ({ client, $ }) => ({
         "tool.execute.after": async (input, output) => {
           if (input.tool !== "write") return
           // re-check output.* against the task's own requirements here; append a warning to
           // output.output if something is missing. Re-derive the check at runtime — never hardcode
           // a specific task's answer/value.
         },
       })

     None defined in the v0 baseline (stays byte-identical to the stock run when empty). -->
