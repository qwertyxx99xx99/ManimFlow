import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import {
  type AssistantMessage,
  type AssistantMessageEventStream,
  type Context,
  type Model,
  type SimpleStreamOptions,
  createAssistantMessageEventStream,
} from "@earendil-works/pi-ai";

const EXA_ENDPOINT =
  process.env.EXA_ENDPOINT || "https://demos.exa.ai/chatbot-demo/api/chat/stream";
const EXA_MODEL = process.env.EXA_MODEL || "google/gemini-2.5-flash";

function contentToText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return String(content ?? "");
  return content
    .map((part: any) => {
      if (part?.type === "text") return part.text || "";
      if (part?.type === "thinking") return part.thinking || "";
      if (part?.type === "toolCall")
        return `[tool call ${part.name}: ${JSON.stringify(part.arguments || {})}]`;
      if (part?.type === "toolResult")
        return `[tool result: ${contentToText(part.content)}]`;
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

function contextToPrompt(context: Context): string {
  const sections = [
    "You are called by the Pi coding agent.",
    "Follow the latest SYSTEM and USER instructions exactly.",
    "When asked for JSON, output exactly one strict JSON object and nothing else.",
    "Do not wrap JSON in markdown fences.",
    "To use a tool, output exactly one JSON object in this form: {\"tool\":\"tool_name\",\"arguments\":{...}}.",
    "For the write tool, arguments MUST be ordered as path first and content second: {\"tool\":\"write\",\"arguments\":{\"path\":\"file.py\",\"content\":\"...\"}}.",
    "Keep each file small and focused. Prefer multiple helper files over one write whose content exceeds 4000 characters.",
    "Never describe a tool call or place it in a markdown fence.",
  ];

  if (context.systemPrompt) sections.push(`SYSTEM:\n${context.systemPrompt}`);
  for (const message of context.messages) {
    sections.push(
      `${String(message.role || "user").toUpperCase()}:\n${contentToText(message.content)}`,
    );
  }
  return sections.join("\n\n");
}

function parseToolRequest(text: string, context: Context) {
  const candidate = text
    .trim()
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/i, "")
    .trim();

  let value: any;
  try {
    value = JSON.parse(candidate);
  } catch {
    // Some models fail to escape quotes inside a shell command. Recover only
    // the narrow, explicit tool envelope and leave all other malformed JSON as text.
    const toolMatch = candidate.match(/"(?:tool|name)"\s*:\s*"([^"]+)"/);
    const commandMatch = candidate.match(
      /"command"\s*:\s*"([\s\S]*)"\s*\}\s*\}\s*$/,
    );
    if (toolMatch?.[1] === "bash" && commandMatch) {
      value = { tool: "bash", arguments: { command: commandMatch[1] } };
    } else if (toolMatch?.[1] === "write") {
      const pathMatch = candidate.match(/"path"\s*:\s*"((?:\\.|[^"\\])*)"/);
      const contentStart = candidate.match(/"content"\s*:\s*"/);
      if (!pathMatch || !contentStart || contentStart.index === undefined) return null;
      const rawContent = candidate.slice(contentStart.index + contentStart[0].length);
      value = {
        tool: "write",
        arguments: {
          path: decodePartialJsonString(pathMatch[1]),
          content: decodePartialJsonString(rawContent),
        },
      };
    } else {
      return null;
    }
  }

  const name = value?.tool || value?.name;
  const args = value?.arguments;
  if (typeof name !== "string" || !args || typeof args !== "object" || Array.isArray(args))
    return null;
  if (!context.tools?.some((tool: any) => tool.name === name)) return null;
  return { name, arguments: args };
}

function decodePartialJsonString(raw: string): string {
  let result = "";
  let escaped = false;
  for (let index = 0; index < raw.length; index++) {
    const character = raw[index];
    if (!escaped) {
      if (character === "\\") {
        escaped = true;
      } else if (character === '"' && /^\s*[,}]/.test(raw.slice(index + 1))) {
        break;
      } else {
        result += character;
      }
      continue;
    }
    escaped = false;
    if (character === "n") result += "\n";
    else if (character === "r") result += "\r";
    else if (character === "t") result += "\t";
    else if (character === "b") result += "\b";
    else if (character === "f") result += "\f";
    else if (character === "u" && /^[0-9a-fA-F]{4}/.test(raw.slice(index + 1, index + 5))) {
      result += String.fromCharCode(parseInt(raw.slice(index + 1, index + 5), 16));
      index += 4;
    } else result += character;
  }
  if (escaped) result += "\\";
  return result;
}

function parseExaStream(raw: string): string {
  let text = "";
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("data:")) continue;
    const payload = trimmed.slice(5).trim();
    if (!payload || payload === "[DONE]") continue;
    try {
      const event = JSON.parse(payload);
      if (typeof event.content === "string") text += event.content;
    } catch {
      // Ignore non-JSON keepalives and malformed metadata events.
    }
  }

  if (!text && raw.trim() && !raw.includes("data:")) {
    try {
      const response = JSON.parse(raw);
      text =
        response.text ||
        response.response ||
        response.content ||
        response.choices?.[0]?.message?.content ||
        "";
    } catch {
      text = raw.trim();
    }
  }
  return text;
}

function cleanExaText(text: string): string {
  const cleaned = String(text || "")
    .replace(/```followups[\s\S]*?```/gi, "")
    .replace(/```followups[\s\S]*$/gi, "")
    .trim();

  if (!cleaned.startsWith("{")) return cleaned;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = 0; index < cleaned.length; index++) {
    const character = cleaned[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') inString = true;
    else if (character === "{") depth++;
    else if (character === "}" && --depth === 0) return cleaned.slice(0, index + 1);
  }
  return cleaned;
}

function streamExa(
  model: Model<any>,
  context: Context,
  options?: SimpleStreamOptions,
): AssistantMessageEventStream {
  const stream = createAssistantMessageEventStream();

  void (async () => {
    const output: AssistantMessage = {
      role: "assistant",
      content: [],
      api: model.api,
      provider: model.provider,
      model: model.id,
      usage: {
        input: 0,
        output: 0,
        cacheRead: 0,
        cacheWrite: 0,
        totalTokens: 0,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
      },
      stopReason: "stop",
      timestamp: Date.now(),
    };

    try {
      stream.push({ type: "start", partial: output });
      const response = await fetch(EXA_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: options?.signal,
        body: JSON.stringify({
          message: contextToPrompt(context),
          history: [],
          exaEnabled: false,
          model: EXA_MODEL,
          searchType: "instant",
        }),
      });
      const raw = await response.text();
      if (!response.ok) throw new Error(`Exa upstream ${response.status}: ${raw.slice(0, 500)}`);

      const text = cleanExaText(parseExaStream(raw));
      if (!text) throw new Error("Exa returned no assistant content");
      const toolRequest = parseToolRequest(text, context);
      if (toolRequest) {
        const toolCall = {
          type: "toolCall" as const,
          id: `exa-${crypto.randomUUID()}`,
          name: toolRequest.name,
          arguments: toolRequest.arguments,
        };
        output.content.push(toolCall);
        output.stopReason = "toolUse";
        stream.push({ type: "toolcall_start", contentIndex: 0, partial: output });
        stream.push({
          type: "toolcall_delta",
          contentIndex: 0,
          delta: JSON.stringify(toolRequest.arguments),
          partial: output,
        });
        stream.push({ type: "toolcall_end", contentIndex: 0, toolCall, partial: output });
        stream.push({ type: "done", reason: "toolUse", message: output });
      } else {
        output.content.push({ type: "text", text });
        stream.push({ type: "text_start", contentIndex: 0, partial: output });
        stream.push({ type: "text_delta", contentIndex: 0, delta: text, partial: output });
        stream.push({ type: "text_end", contentIndex: 0, content: text, partial: output });
        stream.push({ type: "done", reason: "stop", message: output });
      }
      stream.end();
    } catch (error) {
      output.stopReason = options?.signal?.aborted ? "aborted" : "error";
      output.errorMessage = error instanceof Error ? error.message : String(error);
      stream.push({ type: "error", reason: output.stopReason, error: output });
      stream.end();
    }
  })();

  return stream;
}

export default function (pi: ExtensionAPI) {
  pi.registerProvider("exa-direct", {
    name: "Exa Direct",
    baseUrl: EXA_ENDPOINT,
    apiKey: "exa-public",
    authHeader: false,
    api: "openai-completions",
    models: [
      {
        id: "google/gemini-2.5-flash",
        name: "Gemini 2.5 Flash (Exa Direct)",
        reasoning: false,
        input: ["text"],
        contextWindow: 128000,
        maxTokens: 8192,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      },
    ],
    streamSimple: streamExa,
  });
}
