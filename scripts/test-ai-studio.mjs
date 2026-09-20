import { readFileSync } from "node:fs";

function loadApiKey() {
  const fromEnvironment = process.env.GEMINI_API_KEY?.trim();
  if (fromEnvironment) return fromEnvironment;

  try {
    const fromIgnoredFile = readFileSync(new URL("../API", import.meta.url), "utf8").trim();
    if (fromIgnoredFile) return fromIgnoredFile;
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }

  throw new Error(
    "Missing API key. Set GEMINI_API_KEY or put the key in the git-ignored API file.",
  );
}

const apiKey = loadApiKey();
const model = process.env.GEMINI_MODEL || "gemini-3-flash-preview";
const endpoint =
  `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`;

const response = await fetch(endpoint, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "x-goog-api-key": apiKey,
  },
  body: JSON.stringify({
    contents: [
      {
        role: "user",
        parts: [
          {
            text: "Reply with exactly: AI Studio smoke test passed",
          },
        ],
      },
    ],
    generationConfig: {
      temperature: 0,
      maxOutputTokens: 256,
    },
  }),
});

const body = await response.json();
if (!response.ok) {
  const reason = body?.error?.message || JSON.stringify(body);
  throw new Error(`Gemini API request failed (${response.status}): ${reason}`);
}

const text = body?.candidates?.[0]?.content?.parts
  ?.map((part) => part.text || "")
  .join("")
  .trim();

if (!text) {
  const finishReason = body?.candidates?.[0]?.finishReason || "unknown";
  throw new Error(
    `The request succeeded but returned no text (finishReason=${finishReason}).`,
  );
}

console.log(`model=${model}`);
console.log(`response=${text}`);
