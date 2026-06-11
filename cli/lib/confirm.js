'use strict';
// cli/lib/confirm.js — Interactive confirmation prompt via readline.

const readline = require('readline');

/**
 * Prompt the user to type a specific confirmation word.
 * Resolves true if the user types the expected word, false otherwise.
 *
 * @param {string} promptText    The text to print before the prompt.
 * @param {string} expectedWord  The exact word the user must type (default: 'WIPE').
 */
function requireConfirmation(promptText, expectedWord = 'WIPE') {
  return new Promise((resolve) => {
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
    let answered = false;
    // F-05: EOF or closed stdin (e.g. piped input that ends without the expected word)
    // must default to "not confirmed" rather than hanging or resolving true.
    // Guard: only resolve false here if the question callback has NOT already resolved.
    rl.once('close', () => { if (!answered) resolve(false); });
    process.stdout.write(promptText + '\n');
    rl.question(`Type '${expectedWord}' to continue, anything else to abort: `, (answer) => {
      answered = true;
      rl.close();
      resolve(answer.trim() === expectedWord);
    });
  });
}

module.exports = { requireConfirmation };
