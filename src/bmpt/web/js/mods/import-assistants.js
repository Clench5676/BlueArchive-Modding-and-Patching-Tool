// Optional selection/matching providers. Suggestions never bypass the analyzer's eligibility checks.
export const importAssistants = new Set();

/**
 * Ask the optional selection and matching providers for suggestions. Suggestions never bypass the analyzer's
 * eligibility checks.
 * @returns {Promise<object[]>} The suggestions of the providers that answered.
 */
export async function suggestImports(context) {
  const replies = await Promise.allSettled(
    [...importAssistants].map((suggest) => Promise.resolve().then(() => suggest(context))),
  );
  return replies.filter((reply) => reply.status === "fulfilled").flatMap((reply) => reply.value || []);
}
