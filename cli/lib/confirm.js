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
    process.stdout.write(promptText + '\n');
    rl.question(`Type '${expectedWord}' to continue, anything else to abort: `, (answer) => {
      rl.close();
      resolve(answer.trim() === expectedWord);
    });
  });
}

module.exports = { requireConfirmation };
