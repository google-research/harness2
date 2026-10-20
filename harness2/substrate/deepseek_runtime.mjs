// Copyright 2026 Google LLC
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

export const inject = ["systemPrompt", "llm"];

export function apply(ctx, config) {
  ctx.effect(() => ctx.systemPrompt.section({
    name: "harness2:candidate",
    order: 0,
    text: config.prompt,
    interpolate: false,
  }));

  let calls = 0;
  ctx.on("llm/stream", async function* (options, next) {
    let message;
    let code;
    if (options.provider !== config.provider || options.model !== config.model) {
      code = "HARNESS2_MODEL_CHANGED";
      message = "HARNESS2_MODEL_CHANGED: all calls must use the configured fixed model";
    } else if (++calls > config.maxModelCalls) {
      code = "HARNESS2_BUDGET";
      message = `HARNESS2_BUDGET: exhausted ${config.maxModelCalls} model calls`;
    }
    if (message) {
      yield {
        type: "finish",
        reason: { kind: "error", failure: { code, message } },
      };
      return;
    }
    yield* next();
  });
  ctx.provide("harness2RuntimeReady", { ready: true });
}
