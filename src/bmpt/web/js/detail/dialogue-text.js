/**
 * The text of a dialogue row for a language. Chinese captions may share the other Chinese script, never an
 * unrelated spoken language.
 * @returns {{text: string, language: string}|null} The text and the language key it is in; null when the row
 *     has none for that language.
 */
export function dialogueText(row, language) {
  const keys =
    language === "zh-CN"
      ? ["zh-CN", "zh-TW"]
      : language === "zh-TW"
        ? ["zh-TW", "zh-CN"]
        : [language, "ja", "en", "zh-CN"];
  for (const key of keys) {
    const text = row?.[key];
    if (typeof text === "string" && text.trim()) return { text, language: key };
  }
  return null;
}
